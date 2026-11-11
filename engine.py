# Part of audiobook-web - https://github.com/emdaitaj/audiobook-web
# Copyright (C) 2026 emdaitaj. AGPL-3.0 with attribution terms (see NOTICE).
"""Job queue around pdf_to_audiobook.py.

One background worker runs the conversions one at a time: the TTS step wants
the whole GPU, so running two at once only makes both slower. Everything the
web app and the Telegram bot need to know about a conversion lives in a Job.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections import OrderedDict
from queue import Queue

import config

SUPPORTED_EXT = {
    ".pdf", ".epub", ".md", ".markdown", ".mdown", ".mkd", ".mdx",
    ".html", ".htm", ".xhtml", ".xht", ".docx", ".docm",
    ".txt", ".text", ".log", ".rst", ".org",
}
# Formats the engine explicitly refuses, so we can say why up front.
REJECT_EXT = {".doc", ".rtf", ".odt", ".pages"}

_WORDS = re.compile(r"^\[transcript\] ([\d,]+) words")
_TTS_PROGRESS = re.compile(
    r"^\[tts\]\s+([\d,]+)/([\d,]+) paragraphs \| (\d+) min audio"
    r" \| (\d+) min elapsed \| ~(\d+) min left"
)
_TTS_START = re.compile(r"^\[tts\] (?:Kokoro voice|OmniVoice,|Demodokos )")
_DONE = re.compile(r"^DONE(?: \(transcript only\))? -> (.+)$")
# OmniVoice builds its narrator before it reads a word of the book, and on a
# cold model that is the longest silence in the whole run - worth narrating.
_TTS_STAGE = [
    (re.compile(r"^\[tts\] designing voice"), "designing the voice"),
    (re.compile(r"^\[tts\] anchor ->"), "voice ready, warming up"),
    (re.compile(r"^\[tts\] reusing voice prompt"), "reusing the saved voice"),
    (re.compile(r"^\[tts\] cloned "), "voice cloned, warming up"),
    (re.compile(r"^\[tts\] no --ref-text"), "transcribing your voice clip"),
    (re.compile(r"^\[tts\] device "), "loading the voice model"),
]

_jobs = OrderedDict()
_lock = threading.Lock()
_queue = Queue()
_worker = None


class Job:
    """One document -> one MP3. Mutated by the worker, read by everyone else."""

    def __init__(self, src, voice, speed, origin, label=None):
        self.id = uuid.uuid4().hex[:12]
        self.dir = os.path.join(config.JOBS_DIR, self.id)
        self.src = src                  # absolute path to the input document
        self.voice = voice
        self.speed = speed
        self.tts_engine = config.DEFAULT_TTS_ENGINE
        self.lang = config.DEFAULT_LANG     # omnivoice only
        self.ref_audio = None               # omnivoice: clip to clone
        self.ref_text = ""                  # what that clip says, if known
        self.wpm = config.DEFAULT_WPM       # omnivoice: narration pace
        self.origin = origin            # "web" or "telegram"
        self.label = label or os.path.basename(src)
        self.state = "queued"           # queued|reading|narrating|done|error|cancelled
        self.detail = "waiting in queue"
        self.percent = 0
        self.words = 0
        self.audio_minutes = 0     # audio actually encoded so far
        self.estimate_minutes = 0  # guess from the word count, before any audio
        self.eta_minutes = None
        self.mp3 = None
        self.transcript = None
        self.error = None
        self.created = time.time()
        self.started = None
        self.finished = None
        self.log = []                   # tail of engine output, for debugging
        self._proc = None
        self._cancel = False
        self._saved_state = None
        self.on_update = None           # optional callback(job) for the bot

    def public(self):
        return {
            "id": self.id,
            "label": self.label,
            "state": self.state,
            "detail": self.detail,
            "percent": self.percent,
            "words": self.words,
            "audio_minutes": self.audio_minutes,
            "estimate_minutes": self.estimate_minutes,
            "eta_minutes": self.eta_minutes,
            "voice": self.voice,
            "speed": self.speed,
            "tts_engine": self.tts_engine,
            "lang": self.lang,
            "cloned": bool(self.ref_audio),
            "error": self.error,
            "created": self.created,
            "queue_position": queue_position(self),
            "has_audio": bool(self.mp3 and os.path.exists(self.mp3)),
            # Enough of a still-running job exists to start listening to it.
            "can_stream": self.state == "narrating" and bool(live_audio(self)[0]),
            "has_transcript": bool(self.transcript and os.path.exists(self.transcript)),
        }

    def _touch(self):
        # Writing on every progress tick would be pointless churn; the state
        # transitions are the only part worth keeping.
        if self.state != self._saved_state:
            self._saved_state = self.state
            self.save()
        if self.on_update:
            try:
                self.on_update(self)
            except Exception:
                pass

    # -- survive a restart --------------------------------------------------
    def save(self):
        """Finished work outlives the process; a reboot should not lose it."""
        record = {
            "id": self.id, "src": self.src, "voice": self.voice,
            "speed": self.speed, "origin": self.origin, "label": self.label,
            "tts_engine": self.tts_engine, "lang": self.lang,
            "ref_audio": self.ref_audio, "ref_text": self.ref_text,
            "wpm": self.wpm,
            "state": self.state, "detail": self.detail, "percent": self.percent,
            "words": self.words, "audio_minutes": self.audio_minutes,
            "estimate_minutes": self.estimate_minutes,
            "mp3": self.mp3, "transcript": self.transcript, "error": self.error,
            "created": self.created, "finished": self.finished,
        }
        try:
            with open(os.path.join(self.dir, "job.json"), "w", encoding="utf-8") as fh:
                json.dump(record, fh)
        except OSError:
            pass

    @classmethod
    def restore(cls, record):
        job = cls(record["src"], record["voice"], record["speed"],
                  record["origin"], record["label"])
        job.id = record["id"]
        job.dir = os.path.join(config.JOBS_DIR, job.id)
        for key in ("state", "detail", "percent", "words", "audio_minutes",
                    "estimate_minutes", "mp3", "transcript", "error",
                    "created", "finished", "tts_engine", "lang", "ref_audio",
                    "ref_text", "wpm"):
            if record.get(key) is not None:
                setattr(job, key, record[key])
        # Anything that was mid-flight when the process died is not coming back.
        if job.state in ("queued", "reading", "narrating"):
            job.state = "error"
            job.error = "interrupted when the server restarted"
            job.detail = job.error
        return job

    def cancel(self):
        self._cancel = True
        proc = self._proc
        if proc and proc.poll() is None:
            # The engine spawns the Kokoro venv as a grandchild; only taskkill
            # /T takes the whole tree down on Windows.
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)
        if self.state in ("queued", "reading", "narrating"):
            self.state = "cancelled"
            self.detail = "cancelled"
            self._touch()


def queue_position(job):
    """0 = running or finished, 1 = next up, and so on."""
    if job.state != "queued":
        return 0
    pending = [j for j in _jobs.values() if j.state == "queued"]
    pending.sort(key=lambda j: j.created)
    try:
        return pending.index(job) + 1
    except ValueError:
        return 0


def get(job_id):
    return _jobs.get(job_id)


def all_jobs():
    return list(_jobs.values())


def submit(src, voice=None, speed=1.0, origin="web", label=None, on_update=None,
           tts_engine=None, lang=None, ref_audio=None, ref_text="", wpm=None):
    """Queue a document that already sits on disk. Returns the Job."""
    tts_engine = tts_engine if tts_engine in config.TTS_ENGINES \
        else config.DEFAULT_TTS_ENGINE
    if tts_engine == "kokoro":
        voice = voice if voice in config.VOICE_IDS else config.DEFAULT_VOICE
    else:
        # A clip to clone replaces the description entirely.
        voice = normalize_voice(voice) if not ref_audio else ""
    speed = min(max(float(speed), 0.5), 2.0)

    job = Job(src, voice, speed, origin, label)
    job.tts_engine = tts_engine
    job.lang = lang if lang in config.LANGUAGE_IDS else config.DEFAULT_LANG
    job.ref_audio = ref_audio or None
    job.ref_text = (ref_text or "").strip()
    job.wpm = float(wpm) if wpm is not None else config.DEFAULT_WPM
    job.on_update = on_update
    os.makedirs(job.dir, exist_ok=True)
    with _lock:
        _jobs[job.id] = job
    _ensure_worker()
    _queue.put(job)
    return job


def normalize_voice(voice):
    """Tidy an OmniVoice attribute list, falling back to the default."""
    problem = check_voice(voice)
    if problem:
        return config.OMNI_DEFAULT_VOICE
    return ", ".join(item.strip().lower()
                     for item in (voice or "").split(",") if item.strip())


def check_voice(voice):
    """Explain what is wrong with an OmniVoice attribute list, or None.

    The converter does this check too, but only after it has been launched;
    doing it here means a typo comes back as a message instead of a failed job.
    """
    items = [item.strip().lower() for item in (voice or "").split(",") if item.strip()]
    if not items:
        return "pick at least one voice attribute"
    seen = {}
    for item in items:
        category = config.OMNI_ATTR_CATEGORY.get(item)
        if category is None:
            return f"'{item}' is not a voice attribute"
        if category in seen:
            return f"'{item}' and '{seen[category]}' are both {category}; pick one"
        seen[category] = item
    return None


def _safe_stem(name, fallback="document"):
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    return re.sub(r"\s+", " ", stem).strip(" .")[:60] or fallback


def _staging_dir():
    folder = os.path.join(config.JOBS_DIR, "_in", uuid.uuid4().hex[:12])
    os.makedirs(folder, exist_ok=True)
    return folder


def stage_upload(filename, data=None, fileobj=None):
    """Write an incoming document into its own folder and return its path.

    The original filename is preserved: the engine names the MP3 after it, and
    the extension decides which reader runs.
    """
    ext = os.path.splitext(filename)[1].lower()
    path = os.path.join(_staging_dir(), _safe_stem(os.path.splitext(filename)[0]) + ext)
    with open(path, "wb") as fh:
        if data is not None:
            fh.write(data)
        else:
            shutil.copyfileobj(fileobj, fh, 1024 * 1024)
    return path


def stage_text(text, fmt="auto", title=None):
    """Write pasted text to a file whose extension picks the right reader."""
    if fmt == "auto":
        fmt = sniff_format(text)
    ext = {"md": ".md", "html": ".html", "txt": ".txt"}.get(fmt, ".txt")
    stem = _safe_stem(title or "", fallback="") or _title_from(text)
    path = os.path.join(_staging_dir(), stem + ext)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _title_from(text):
    """First few words make a friendlier filename than 'paste'."""
    words = re.sub(r"[^\w\s-]", " ", text[:200]).split()
    return (" ".join(words[:6]) or "pasted text")[:50]


# Markdown giveaways, and how much each one counts. An ATX heading or a fenced
# block is decisive on its own; a dash at the start of a line could be prose.
MD_SIGNALS = [
    (r"^#{1,6} ", 2),                   # # heading
    (r"^```", 2),                       # fenced code
    (r"\[[^\]\n]+\]\([^)\n]+\)", 2),    # [text](link)
    (r"^\s*[-*+] \S", 1),               # bullet list
    (r"^\s*\d+\. \S", 1),               # numbered list
    (r"\*\*[^*\n]+\*\*", 1),            # **bold**
    (r"^> ", 1),                        # > quote
    (r"^\s*(?:---|\*\*\*)\s*$", 1),     # horizontal rule
]


def sniff_format(text):
    """Guess whether pasted text is HTML, Markdown, or just prose."""
    head = text[:8000]
    if re.search(r"<(?:p|div|br|h[1-6]|html|body|article|span|table|li)\b", head, re.I):
        return "html"
    score = sum(weight for pattern, weight in MD_SIGNALS
                if re.search(pattern, head, re.M))
    return "md" if score >= 2 else "txt"


def stage_ref_audio(filename, data=None, fileobj=None):
    """Save a voice clip to clone. Same staging rules as a document."""
    ext = os.path.splitext(filename)[1].lower() or ".wav"
    path = os.path.join(_staging_dir(), "voice-reference" + ext)
    with open(path, "wb") as fh:
        if data is not None:
            fh.write(data)
        else:
            shutil.copyfileobj(fileobj, fh, 1024 * 1024)
    return path


def check_ref_ext(filename):
    """Return an error message for an unusable voice clip, or None."""
    ext = os.path.splitext(filename)[1].lower()
    if ext and ext not in config.REF_AUDIO_EXT:
        return (f"{ext} is not an audio file. Send a WAV, MP3, M4A, OGG, FLAC "
                "or OPUS clip of the voice to copy.")
    return None


def check_ext(filename):
    """Return an error message for unusable files, or None when it is fine."""
    ext = os.path.splitext(filename)[1].lower()
    if ext in REJECT_EXT:
        return f"{ext} is not supported - re-save it as .docx, .pdf, .md or .txt first."
    if ext and ext not in SUPPORTED_EXT:
        return (f"{ext} is not a supported document. Send a PDF, EPUB, DOCX, "
                "Markdown, HTML or plain text file.")
    return None


# ---------------------------------------------------------------------------
#  worker
# ---------------------------------------------------------------------------
def _ensure_worker():
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_run_loop, name="tts-worker", daemon=True)
        _worker.start()


def _run_loop():
    while True:
        job = _queue.get()
        try:
            if not job._cancel:
                _run(job)
        except Exception as exc:                      # never kill the worker
            job.state = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.detail = job.error
            job._touch()
        finally:
            job.finished = time.time()
            _queue.task_done()
            _sweep()


def _voice_key(job):
    """Identifies the narrator, so the same one can be reused across books."""
    if job.ref_audio:
        digest = hashlib.sha1()
        with open(job.ref_audio, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
        who = "clone-" + digest.hexdigest()[:16]
    else:
        who = "design-" + hashlib.sha1(job.voice.encode()).hexdigest()[:16]
    return f"{who}-{job.lang}-{config.VOICE_SEED}-{job.wpm:g}"


def _prime_voice(job):
    """Copy a cached OmniVoice narrator into the job folder, if we have one.

    Designing a voice means generating an anchor clip and cloning from it. The
    converter caches that inside its own output folder, which is per job, so
    without this every book would pay for it again - and, more importantly,
    two books asked for the same voice would not sound quite alike.
    """
    cached = os.path.join(config.VOICE_CACHE, _voice_key(job), "voice_prompt.omni")
    if os.path.exists(cached):
        try:
            shutil.copy2(cached, os.path.join(job.dir, "voice_prompt.omni"))
            return True
        except OSError:
            pass
    return False


def _store_voice(job):
    """Keep the narrator this job designed, for the next book to reuse."""
    made = os.path.join(job.dir, "voice_prompt.omni")
    if not os.path.exists(made):
        return
    folder = os.path.join(config.VOICE_CACHE, _voice_key(job))
    try:
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(folder, "voice_prompt.omni")
        if not os.path.exists(target):
            shutil.copy2(made, target)
    except OSError:
        pass


def _build_command(job):
    """The converter invocation for this job's backend."""
    cmd = [config.ENGINE_PYTHON, config.ENGINE, job.src,
           "--speed", str(job.speed), "--outdir", job.dir,
           "--engine", job.tts_engine]
    if job.tts_engine == "kokoro":
        cmd += ["--voice", job.voice]
        return cmd

    cmd += ["--lang", job.lang, "--wpm", str(job.wpm),
            "--seed", str(config.VOICE_SEED)]
    if job.ref_audio:
        cmd += ["--ref-audio", job.ref_audio]
        if job.ref_text:
            cmd += ["--ref-text", job.ref_text]
    else:
        cmd += ["--voice", job.voice]
    return cmd


def _run(job):
    job.state = "reading"
    job.detail = "reading the document"
    job.started = time.time()
    job._touch()
    if job.tts_engine == "omnivoice":
        _prime_voice(job)

    cmd = _build_command(job)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    job._proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        cwd=job.dir, env=env,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    for line in job._proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        job.log.append(line)
        del job.log[:-200]
        _consume(job, line)
    code = job._proc.wait()

    if job._cancel:
        job.state = "cancelled"
        job.detail = "cancelled"
        job._touch()
        return
    if code != 0:
        job.state = "error"
        job.error = _last_error(job.log) or f"the converter exited with code {code}"
        job.detail = job.error
        job._touch()
        return

    mp3 = _find_mp3(job.dir)
    if not mp3:
        job.state = "error"
        job.error = "the converter finished but produced no audio"
        job.detail = job.error
        job._touch()
        return
    if job.tts_engine == "omnivoice":
        _store_voice(job)
    job.mp3 = mp3
    transcript = os.path.join(job.dir, "transcript.txt")
    job.transcript = transcript if os.path.exists(transcript) else None
    job.state = "done"
    job.percent = 100
    job.detail = "ready"
    job.eta_minutes = 0
    job.finished = time.time()
    job._touch()
    job.save()


def _consume(job, line):
    """Turn one line of engine output into progress the UI can show."""
    m = _WORDS.match(line)
    if m:
        job.words = int(m.group(1).replace(",", ""))
        # 155 words a minute is the engine's own estimate of narrated speed.
        # It gets replaced by the real figure once the audio exists, but until
        # then it is the only sense of scale there is.
        job.estimate_minutes = max(1, round(job.words / 155))
        job.detail = (f"{job.words:,} words - about {job.estimate_minutes} min "
                      "of audio")
        job._touch()
        return
    if _TTS_START.match(line):
        job.state = "narrating"
        job.detail = "loading the voice model"
        job._touch()
        return
    for pattern, detail in _TTS_STAGE:
        if pattern.match(line):
            job.state = "narrating"
            job.detail = detail
            job._touch()
            return
    m = _TTS_PROGRESS.match(line)
    if m:
        done, total = (int(x.replace(",", "")) for x in m.group(1, 2))
        job.state = "narrating"
        job.percent = min(99, int(done * 100 / max(total, 1)))
        job.audio_minutes = int(m.group(3))
        job.eta_minutes = int(m.group(5))
        job.detail = f"narrating {done:,}/{total:,} paragraphs"
        job._touch()
        return
    if _DONE.match(line):
        job.percent = 99
        return
    if line.startswith("[") and job.state == "reading":
        job.detail = line[:120]
        job._touch()


def _last_error(lines):
    for line in reversed(lines):
        if "[error]" in line or "Error" in line or "Traceback" in line:
            return line.replace("[error]", "").strip()[:300]
    return None


def live_audio(job):
    """(path, complete) for whatever audio this job has right now.

    The converter encodes into <name>.mp3.part and renames it when the book is
    finished, so the part file is the audio that already exists while the rest
    is still being narrated.
    """
    if job.mp3 and os.path.exists(job.mp3):
        return job.mp3, True
    if not os.path.isdir(job.dir):
        return None, False
    for name in os.listdir(job.dir):
        if name.lower().endswith(".mp3.part"):
            return os.path.join(job.dir, name), False
    final = _find_mp3(job.dir)
    return (final, True) if final else (None, False)


def open_shared(path):
    """Open a file for reading without blocking a rename or delete of it.

    Windows refuses to rename a file that somebody has open unless they asked
    for FILE_SHARE_DELETE, and a plain open() does not. Streaming the part file
    to a listener would then make the converter's final rename fail and throw
    away the whole book at the last step.
    """
    if os.name != "nt":
        return open(path, "rb")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    GENERIC_READ = 0x80000000
    SHARE_READ_WRITE_DELETE = 0x1 | 0x2 | 0x4
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    INVALID_HANDLE = ctypes.c_void_p(-1).value

    create = ctypes.windll.kernel32.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                       wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(path, GENERIC_READ, SHARE_READ_WRITE_DELETE, None,
                    OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
    if not handle or handle == INVALID_HANDLE:
        raise FileNotFoundError(path)
    fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    return os.fdopen(fd, "rb")


def _find_mp3(folder):
    """The biggest MP3 in the output folder is the audiobook."""
    best = None
    for name in os.listdir(folder):
        if name.lower().endswith(".mp3"):
            path = os.path.join(folder, name)
            if os.path.getsize(path) > 1024 and (
                best is None or os.path.getsize(path) > os.path.getsize(best)
            ):
                best = path
    return best


def _sweep():
    """Delete finished jobs past their TTL so the disk does not creep up."""
    cutoff = time.time() - config.JOB_TTL_HOURS * 3600
    with _lock:
        stale = [j for j in _jobs.values()
                 if j.state in ("done", "error", "cancelled")
                 and (j.finished or j.created) < cutoff]
        for job in stale:
            _jobs.pop(job.id, None)
    for job in stale:
        shutil.rmtree(job.dir, ignore_errors=True)
        shutil.rmtree(os.path.dirname(job.src), ignore_errors=True)
    staging = os.path.join(config.JOBS_DIR, "_in")
    if os.path.isdir(staging):
        for name in os.listdir(staging):
            path = os.path.join(staging, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                pass


def restore_all():
    """Pull previously finished jobs back into the list at startup."""
    if not os.path.isdir(config.JOBS_DIR):
        return
    found = []
    for name in sorted(os.listdir(config.JOBS_DIR)):
        folder = os.path.join(config.JOBS_DIR, name)
        record_path = os.path.join(folder, "job.json")
        if name == "_in" or not os.path.isfile(record_path):
            continue
        try:
            with open(record_path, encoding="utf-8") as fh:
                job = Job.restore(json.load(fh))
        except (ValueError, OSError, KeyError, TypeError):
            continue
        if job.mp3 and not os.path.exists(job.mp3):
            job.mp3 = _find_mp3(folder)          # folder may have been moved
        found.append(job)
    found.sort(key=lambda j: j.created or 0)
    with _lock:
        for job in found:
            _jobs.setdefault(job.id, job)
    _sweep()


restore_all()
