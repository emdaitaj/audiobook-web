# Part of audiobook-web - https://github.com/emdaitaj/audiobook-web
# Copyright (C) 2026 emdaitaj. AGPL-3.0 with attribution terms (see NOTICE).
"""Web front end: drop a document in, get an audiobook you can stream.

Single user, single password. The password is exchanged once for a signed
cookie that lasts a year, so a phone only ever asks for it on the first visit.
"""
import asyncio
import json
import mimetypes
import os
import re
import shutil
import time

from fastapi import FastAPI, Form, Request, UploadFile, File
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response,
    StreamingResponse,
)
from itsdangerous import BadSignature, URLSafeTimedSerializer

import config
import engine

app = FastAPI(title="Audiobook", docs_url=None, redoc_url=None)

STATIC = os.path.join(config.BASE_DIR, "static")
COOKIE = "tts_auth"
COOKIE_MAX_AGE = 365 * 24 * 3600
_signer = URLSafeTimedSerializer(config.SECRET, salt="tts-login")
OPEN_PATHS = {"/login", "/api/login", "/favicon.ico", "/manifest.webmanifest"}


# ---------------------------------------------------------------------------
#  auth
# ---------------------------------------------------------------------------
def _authed(request):
    token = request.cookies.get(COOKIE)
    if not token:
        return False
    try:
        return _signer.loads(token, max_age=COOKIE_MAX_AGE) == "ok"
    except BadSignature:
        return False
    except Exception:
        return False


def _set_cookie(response):
    response.set_cookie(
        COOKIE, _signer.dumps("ok"), max_age=COOKIE_MAX_AGE,
        httponly=True, samesite="lax", path="/",
    )
    return response


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if path in OPEN_PATHS or _authed(request):
        return await call_next(request)
    # A bookmark carrying ?k=<password> logs in without showing the form.
    key = request.query_params.get("k")
    if key and key == config.PASSWORD:
        return _set_cookie(RedirectResponse(path, status_code=303))
    if path.startswith("/api/"):
        return JSONResponse({"error": "not signed in"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, bad: int = 0):
    if _authed(request):
        return RedirectResponse("/", status_code=303)
    with open(os.path.join(STATIC, "login.html"), encoding="utf-8") as fh:
        html = fh.read()
    return HTMLResponse(html.replace("{{ERROR}}", "wrong password" if bad else ""))


@app.post("/api/login")
async def login(password: str = Form("")):
    if password.strip() != config.PASSWORD:
        return RedirectResponse("/login?bad=1", status_code=303)
    return _set_cookie(RedirectResponse("/", status_code=303))


@app.post("/api/logout")
async def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE, path="/")
    return response


# ---------------------------------------------------------------------------
#  pages
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/app.js")
async def appjs():
    return FileResponse(os.path.join(STATIC, "app.js"), media_type="text/javascript")


@app.get("/style.css")
async def style():
    return FileResponse(os.path.join(STATIC, "style.css"), media_type="text/css")


@app.get("/manifest.webmanifest")
async def manifest():
    return JSONResponse({
        "name": "Audiobook", "short_name": "Audiobook",
        "start_url": "/", "display": "standalone",
        "background_color": "#0f1115", "theme_color": "#0f1115",
        "icons": [],
    })


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


# ---------------------------------------------------------------------------
#  conversion
# ---------------------------------------------------------------------------
@app.post("/api/convert")
async def convert(
    request: Request,
    file: UploadFile = File(None),
    ref_audio: UploadFile = File(None),
    text: str = Form(""),
    title: str = Form(""),
    fmt: str = Form("auto"),
    voice: str = Form(config.DEFAULT_VOICE),
    speed: float = Form(1.0),
    tts_engine: str = Form(config.DEFAULT_TTS_ENGINE),
    lang: str = Form(config.DEFAULT_LANG),
    ref_text: str = Form(""),
    wpm: float = Form(config.DEFAULT_WPM),
):
    if tts_engine not in config.TTS_ENGINES:
        return JSONResponse({"error": f"unknown voice engine '{tts_engine}'"},
                            status_code=400)
    if not config.engine_available(tts_engine):
        return JSONResponse(
            {"error": f"the {tts_engine} backend is not installed on this machine"},
            status_code=400,
        )

    # OmniVoice extras: a language, and either a described voice or a clip to
    # clone. Check them before anything gets written to disk.
    clip = None
    if tts_engine == "omnivoice":
        if lang not in config.LANGUAGE_IDS:
            return JSONResponse({"error": f"'{lang}' is not a language OmniVoice knows"},
                                status_code=400)
        if ref_audio is not None and ref_audio.filename:
            problem = engine.check_ref_ext(ref_audio.filename)
            if problem:
                return JSONResponse({"error": problem}, status_code=400)
            clip = engine.stage_ref_audio(ref_audio.filename, fileobj=ref_audio.file)
            if os.path.getsize(clip) > config.MAX_REF_AUDIO_MB * 1024 * 1024:
                shutil.rmtree(os.path.dirname(clip), ignore_errors=True)
                return JSONResponse(
                    {"error": f"the voice clip is over {config.MAX_REF_AUDIO_MB} MB - "
                              "a few seconds of speech is all it needs"},
                    status_code=400,
                )
        else:
            problem = engine.check_voice(voice)
            if problem:
                return JSONResponse({"error": problem}, status_code=400)

    if file is not None and file.filename:
        problem = engine.check_ext(file.filename)
        if problem:
            return JSONResponse({"error": problem}, status_code=400)
        limit = config.MAX_UPLOAD_MB * 1024 * 1024
        length = int(request.headers.get("content-length") or 0)
        if length > limit:
            return JSONResponse(
                {"error": f"file is larger than the {config.MAX_UPLOAD_MB} MB limit"},
                status_code=413,
            )
        src = engine.stage_upload(file.filename, fileobj=file.file)
        if os.path.getsize(src) == 0:
            shutil.rmtree(os.path.dirname(src), ignore_errors=True)
            return JSONResponse({"error": "that file is empty"}, status_code=400)
        label = file.filename
    elif text.strip():
        if len(text.split()) < 20:
            return JSONResponse(
                {"error": "that is too short to narrate - paste at least 20 words"},
                status_code=400,
            )
        src = engine.stage_text(text, fmt=fmt, title=title)
        label = os.path.basename(src)
    else:
        return JSONResponse({"error": "send a file or paste some text"}, status_code=400)

    job = engine.submit(src, voice=voice, speed=speed, origin="web", label=label,
                        tts_engine=tts_engine, lang=lang, ref_audio=clip,
                        ref_text=ref_text, wpm=wpm)
    return JSONResponse(job.public())


def describe_job(job):
    """The job as the clients see it, plus how much audio exists so far.

    The converter only prints a percentage every two hundred paragraphs, which
    for a short book is never. Minutes of finished audio is both easier to come
    by and more use to somebody deciding whether to start listening.
    """
    data = job.public()
    if job.state == "narrating":
        state = _audio_state(job)
        if state:
            data["stream_seconds"] = state["available_seconds"]
    return data


@app.get("/api/jobs")
async def jobs():
    items = sorted(engine.all_jobs(), key=lambda j: j.created, reverse=True)
    return JSONResponse({"jobs": [describe_job(j) for j in items[:40]]})


@app.get("/api/job/{job_id}")
async def job_status(job_id: str):
    job = engine.get(job_id)
    if not job:
        return JSONResponse({"error": "no such job"}, status_code=404)
    return JSONResponse(describe_job(job))


@app.get("/api/job/{job_id}/log")
async def job_log(job_id: str):
    job = engine.get(job_id)
    if not job:
        return JSONResponse({"error": "no such job"}, status_code=404)
    return JSONResponse({"log": job.log[-60:]})


@app.post("/api/job/{job_id}/cancel")
async def job_cancel(job_id: str):
    job = engine.get(job_id)
    if not job:
        return JSONResponse({"error": "no such job"}, status_code=404)
    job.cancel()
    return JSONResponse(job.public())


@app.delete("/api/job/{job_id}")
async def job_delete(job_id: str):
    job = engine.get(job_id)
    if not job:
        return JSONResponse({"error": "no such job"}, status_code=404)
    job.cancel()
    engine._jobs.pop(job_id, None)
    shutil.rmtree(job.dir, ignore_errors=True)
    shutil.rmtree(os.path.dirname(job.src), ignore_errors=True)
    return JSONResponse({"ok": True})


@app.get("/api/voices")
async def voices():
    """Everything the page needs to build the voice controls."""
    return JSONResponse({
        "engines": [
            {
                "id": "kokoro",
                "name": "Kokoro",
                "note": "fast, English only, fixed voices",
                "available": config.engine_available("kokoro"),
            },
            {
                "id": "omnivoice",
                "name": "OmniVoice",
                "note": "600+ languages, designed or cloned voices, slower",
                "available": config.engine_available("omnivoice"),
            },
        ],
        "default_engine": config.DEFAULT_TTS_ENGINE,
        "voices": [{"id": v, "name": n} for v, n in config.VOICES],
        "default": config.DEFAULT_VOICE,
        "omni_attrs": [{"category": c, "values": vs} for c, vs in config.OMNI_ATTRS],
        "omni_default": config.OMNI_DEFAULT_VOICE,
        "languages": config.LANGUAGES,
        "default_lang": config.DEFAULT_LANG,
        "default_wpm": config.DEFAULT_WPM,
    })


# ---------------------------------------------------------------------------
#  media
# ---------------------------------------------------------------------------
CHUNK = 512 * 1024
_RANGE = re.compile(r"bytes=(\d*)-(\d*)")


def _stream(path, start, end):
    remaining = end - start + 1
    with open(path, "rb") as fh:
        fh.seek(start)
        while remaining > 0:
            block = fh.read(min(CHUNK, remaining))
            if not block:
                break
            remaining -= len(block)
            yield block


def serve_file(request: Request, path: str, download_name=None):
    """Serve a file with byte-range support.

    Seeking in an <audio> element is a Range request; without this the browser
    can only ever play from the start.
    """
    size = os.path.getsize(path)
    media = mimetypes.guess_type(path)[0] or "application/octet-stream"
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=86400",
        "Content-Disposition": (
            f'attachment; filename="{download_name}"' if download_name
            else "inline"
        ),
    }
    rng = request.headers.get("range")
    start, end, status = 0, size - 1, 200
    if rng:
        m = _RANGE.match(rng.strip())
        if m:
            first, last = m.group(1), m.group(2)
            if first:
                start = int(first)
                if last:
                    end = min(int(last), size - 1)
            elif last:                       # suffix range: last N bytes
                start = max(0, size - int(last))
            if start >= size or start > end:
                return Response(status_code=416,
                                headers={"Content-Range": f"bytes */{size}"})
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(end - start + 1)
    if request.method == "HEAD":
        headers["Content-Length"] = str(size)
        return Response(status_code=200, headers=headers, media_type=media)
    return StreamingResponse(_stream(path, start, end), status_code=status,
                             headers=headers, media_type=media)


# ---------------------------------------------------------------------------
#  live playback: the audio of a book that is still being narrated
# ---------------------------------------------------------------------------
# MPEG audio frame header tables, enough for what the converter produces
# (Layer III, constant bit rate). They let a byte offset be turned into a
# position in seconds and back, exactly, which is what makes it possible to
# seek inside a file that has not finished being written.
_MPEG_RATES = {3: [44100, 48000, 32000],      # MPEG 1
               2: [22050, 24000, 16000],      # MPEG 2
               0: [11025, 12000, 8000]}       # MPEG 2.5
_L3_BITRATES = {
    3: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
    2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
}
_LAYOUT_CACHE = {}


def _id3_end(head):
    if head[:3] != b"ID3":
        return 0
    size = 0
    for byte in head[6:10]:                    # syncsafe: 7 bits per byte
        size = (size << 7) | (byte & 0x7F)
    end = 10 + size
    if head[5] & 0x10:                         # footer present
        end += 10
    return end


def _frame_at(head, off):
    """Parse the MPEG frame starting at off. Returns (length, bitrate, rate)."""
    if len(head) < off + 4 or head[off] != 0xFF or (head[off + 1] & 0xE0) != 0xE0:
        return None
    version = (head[off + 1] >> 3) & 0x03      # 3 = MPEG1, 2 = MPEG2, 0 = 2.5
    layer = (head[off + 1] >> 1) & 0x03
    if layer != 1 or version == 1:             # only Layer III, no reserved
        return None
    bitrate_index = (head[off + 2] >> 4) & 0x0F
    rate_index = (head[off + 2] >> 2) & 0x03
    padding = (head[off + 2] >> 1) & 0x01
    if bitrate_index in (0, 15) or rate_index == 3:
        return None
    table = _L3_BITRATES[3 if version == 3 else 2]
    bitrate = table[bitrate_index] * 1000
    rate = _MPEG_RATES[version][rate_index]
    coefficient = 144 if version == 3 else 72
    return coefficient * bitrate // rate + padding, bitrate, rate


def mp3_layout(path):
    """Where the audio starts, and how many bytes one second of it takes.

    Cached per path: the header never changes once the encoder has written it.
    """
    cached = _LAYOUT_CACHE.get(path)
    if cached:
        return cached
    with open(path, "rb") as fh:
        head = fh.read(8192)
    start = _id3_end(head)
    parsed = _frame_at(head, start)
    if not parsed:
        return None
    length, bitrate, _rate = parsed
    # ffmpeg puts a Xing/Info header in the first frame; it is not audio.
    if b"Xing" in head[start:start + length] or b"Info" in head[start:start + length]:
        start += length
    layout = {"data_start": start, "frame_bytes": length,
              "bytes_per_second": bitrate // 8}
    _LAYOUT_CACHE[path] = layout
    return layout


def _audio_state(job):
    """What is playable right now, described in seconds."""
    path, complete = engine.live_audio(job)
    if not path or not os.path.exists(path):
        return None
    layout = mp3_layout(path)
    if not layout:
        return None
    size = os.path.getsize(path)
    body = max(0, size - layout["data_start"])
    return {
        "path": path,
        "complete": complete,
        "size": size,
        "available_seconds": round(body / layout["bytes_per_second"], 2),
        **layout,
    }


@app.get("/api/job/{job_id}/audio-info")
async def audio_info(job_id: str):
    """How much audio exists so far, and how to map seconds onto bytes."""
    job = engine.get(job_id)
    if not job:
        return JSONResponse({"error": "no such job"}, status_code=404)
    state = _audio_state(job)
    if not state:
        return JSONResponse({"ready": False, "state": job.state,
                             "detail": job.detail})
    return JSONResponse({
        "ready": True,
        "state": job.state,
        "complete": state["complete"],
        "available_seconds": state["available_seconds"],
        # Only a guess until the encoder finishes, but it keeps the scrub bar
        # from rescaling every few seconds.
        "estimate_seconds": (job.estimate_minutes or 0) * 60,
        "data_start": state["data_start"],
        "frame_bytes": state["frame_bytes"],
        "bytes_per_second": state["bytes_per_second"],
    })


@app.api_route("/audio/{job_id}/stream", methods=["GET", "HEAD"])
async def audio_stream(job_id: str, request: Request, start: int = 0):
    """Play from a byte offset, following the file as it is still being written.

    A normal range request cannot do this: the length is not known yet, so the
    browser has nothing to seek against. Instead the client asks for a byte
    offset (which it works out from the constant bit rate), and this keeps
    feeding it until the narration ends.
    """
    job = engine.get(job_id)
    if not job:
        return Response(status_code=404)
    state = _audio_state(job)
    if not state:
        return Response(status_code=404)
    start = max(0, start)
    headers = {"Accept-Ranges": "none", "Cache-Control": "no-store",
               "Content-Disposition": "inline"}
    if request.method == "HEAD":
        return Response(status_code=200, headers=headers, media_type="audio/mpeg")

    async def follow():
        path = state["path"]
        handle = engine.open_shared(path)
        try:
            handle.seek(start)
            idle = 0.0
            while True:
                block = handle.read(CHUNK)
                if block:
                    idle = 0.0
                    yield block
                    continue
                # Caught up with the encoder. Either it is still working, or
                # the part file has just been renamed and the rest is in the
                # finished file.
                if job.state in ("done", "error", "cancelled"):
                    final, _ = engine.live_audio(job)
                    if final and final != path:
                        where = handle.tell()
                        handle.close()
                        path, handle = final, engine.open_shared(final)
                        handle.seek(where)
                        continue
                    if handle.tell() >= os.path.getsize(path):
                        return
                idle += 0.25
                if idle > 120:            # the encoder has stopped feeding us
                    return
                await asyncio.sleep(0.25)
        finally:
            try:
                handle.close()
            except OSError:
                pass

    return StreamingResponse(follow(), headers=headers, media_type="audio/mpeg")


def _audio_path(job_id):
    job = engine.get(job_id)
    if not job or not job.mp3 or not os.path.exists(job.mp3):
        return None, None
    return job, job.mp3


@app.api_route("/audio/{job_id}", methods=["GET", "HEAD"])
async def audio(job_id: str, request: Request):
    job, path = _audio_path(job_id)
    if not path:
        return Response(status_code=404)
    return serve_file(request, path)


@app.api_route("/audio/{job_id}/download", methods=["GET", "HEAD"])
async def audio_download(job_id: str, request: Request):
    job, path = _audio_path(job_id)
    if not path:
        return Response(status_code=404)
    name = re.sub(r'[^\w\s.-]', "", os.path.basename(path)) or "audiobook.mp3"
    return serve_file(request, path, download_name=name)


# ---------------------------------------------------------------------------
#  the text, lined up against the audio
# ---------------------------------------------------------------------------
# The converter separates paragraphs with a blank line and puts a deliberate
# gap between them when it narrates: this long if the paragraph is short, that
# long otherwise. Both numbers come straight from its TTS workers.
PARA_GAP_SHORT = 0.6
PARA_GAP_LONG = 0.45
SHORT_PARA_CHARS = 70

_SENTENCE_END = re.compile(
    r"(?<=[.!?…])[\"')\]”’]*\s+(?=[\"'(\[“‘]*[^\s])"
)


def split_sentences(text):
    parts = [p.strip() for p in _SENTENCE_END.split(text) if p.strip()]
    return parts or [text.strip()]


def read_paragraphs(path):
    with open(path, encoding="utf-8") as fh:
        return [p.strip() for p in re.split(r"\n\s*\n", fh.read()) if p.strip()]


def paragraph_gap(paragraph):
    """The silence the converter emits before this paragraph."""
    return PARA_GAP_SHORT if len(paragraph) < SHORT_PARA_CHARS else PARA_GAP_LONG


def load_exact_timings(folder, paragraphs):
    """Per-paragraph timings, if the converter left any.

    It writes `{"sample_rate": 24000, "offsets": [...]}`, where each offset is
    the sample at which that paragraph's speech begins - counted after the
    silence in front of it, not before. Seconds instead of samples, and the
    `{"paragraphs": [{"start", "end"}]}` shape, are both accepted too.
    """
    path = os.path.join(folder, "timings.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (ValueError, OSError):
        return None
    marks = data.get("paragraphs") or data.get("offsets")
    count = len(paragraphs)
    # A mismatched length means the file belongs to some other run of some
    # other text; estimating is better than lining the wrong words up.
    if not isinstance(marks, list) or len(marks) != count:
        return None

    rate = float(data.get("sample_rate") or 0) or 1.0
    spans = []
    for entry in marks:
        if isinstance(entry, dict):
            start, end = entry.get("start"), entry.get("end")
        else:                                   # a bare sample offset
            start, end = entry, None
        if not isinstance(start, (int, float)):
            return None
        spans.append([float(start) / rate,
                      None if not isinstance(end, (int, float)) else float(end) / rate])

    for i, span in enumerate(spans):
        if span[1] is not None:
            continue
        if i + 1 < len(spans):
            # The next paragraph's start already excludes the silence in front
            # of it, so hand that silence back rather than counting it as part
            # of this paragraph.
            span[1] = max(span[0], spans[i + 1][0] - paragraph_gap(paragraphs[i + 1]))
    if any(a[0] > b[0] for a, b in zip(spans, spans[1:])):
        return None                             # not in order: do not trust it
    return spans


def estimate_timings(paragraphs, duration):
    """Spread the known total across the paragraphs by word count.

    Measured against the silences the converter actually inserted, this lands
    within about a second: median 0.7 s, worst 2.3 s over a six minute book.
    Good enough to jump to a paragraph, and it is replaced by real timings the
    moment the converter starts writing them.
    """
    gaps = [paragraph_gap(p) for p in paragraphs[1:]]
    words = [max(1, len(p.split())) for p in paragraphs]
    speech = max(0.1, duration - sum(gaps))
    per_word = speech / sum(words)

    spans, at = [], 0.0
    for i, count in enumerate(words):
        if i:
            at += gaps[i - 1]
        spans.append([at, at + count * per_word])
        at += count * per_word
    return spans


@app.get("/api/job/{job_id}/outline")
async def outline(job_id: str):
    """The transcript, cut into paragraphs and sentences, each with a time."""
    job = engine.get(job_id)
    if not job:
        return JSONResponse({"error": "no such job"}, status_code=404)
    path = job.transcript or os.path.join(job.dir, "transcript.txt")
    if not os.path.exists(path):
        return JSONResponse({"ready": False, "reason": "no transcript yet"})
    paragraphs = read_paragraphs(path)
    if not paragraphs:
        return JSONResponse({"ready": False, "reason": "the transcript is empty"})

    state = _audio_state(job)
    duration = state["available_seconds"] if state else 0
    complete = bool(state and state["complete"])
    if not complete:
        # Still narrating: the finished length is a guess, so the mapping is
        # too. It gets recomputed once the real length is known.
        duration = max(duration, (job.estimate_minutes or 0) * 60)
    if duration <= 0:
        return JSONResponse({"ready": False, "reason": "no audio yet"})

    spans = load_exact_timings(job.dir, paragraphs)
    exact = spans is not None
    if not exact:
        spans = estimate_timings(paragraphs, duration)

    items = []
    for i, (text, span) in enumerate(zip(paragraphs, spans)):
        start, end = span[0], span[1] if span[1] is not None else duration
        sentences = split_sentences(text)
        weights = [max(1, len(s.split())) for s in sentences]
        total = sum(weights)
        at, cuts = start, []
        for sentence, weight in zip(sentences, weights):
            width = (end - start) * weight / total
            cuts.append({"start": round(at, 2), "text": sentence})
            at += width
        items.append({
            "i": i,
            "start": round(start, 2),
            "end": round(end, 2),
            "sentences": cuts,
        })
    return JSONResponse({
        "ready": True,
        "exact": exact,
        "complete": complete,
        "duration": round(duration, 2),
        "paragraphs": items,
    })


@app.get("/transcript/{job_id}")
async def transcript(job_id: str):
    job = engine.get(job_id)
    if not job or not job.transcript or not os.path.exists(job.transcript):
        return Response(status_code=404)
    return FileResponse(job.transcript, media_type="text/plain; charset=utf-8")


@app.get("/api/health")
async def health():
    return JSONResponse({
        "ok": True,
        "engine": os.path.exists(config.ENGINE),
        "python": os.path.exists(config.ENGINE_PYTHON),
        "jobs": len(engine.all_jobs()),
        "time": time.time(),
    })


def lan_ips():
    """Addresses a phone on the same network could actually reach, best first.

    A VPN or a VMware adapter will happily answer the usual 'connect a UDP
    socket and see which interface wins' trick with an address the phone
    cannot route to, so list every candidate and rank the home-network ones
    highest instead of guessing.
    """
    import socket

    found = []
    try:
        found += socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        pass
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        found.append(sock.getsockname()[0])
    except OSError:
        pass
    finally:
        sock.close()

    def rank(ip):
        # A host-side virtual adapter (VMware, WSL, Hyper-V) almost always
        # takes the .1 of its own little network; a normal Wi-Fi lease does
        # not, so an address ending in .1 is the least likely to be the one
        # the phone wants.
        virtual = 4 if ip.endswith(".1") else 0
        if ip.startswith("192.168."):
            return virtual + 0          # ordinary home Wi-Fi
        if ip.startswith("10."):
            return virtual + 1          # could be a VPN, so second
        if re.match(r"172\.(1[6-9]|2\d|3[01])\.", ip):
            return virtual + 2          # often Docker or WSL
        return virtual + 3

    usable = [ip for ip in dict.fromkeys(found)
              if not ip.startswith(("127.", "169.254."))]
    usable.sort(key=rank)
    return usable or ["127.0.0.1"]


def print_banner():
    ips = lan_ips()
    print("=" * 66)
    print(f"  Audiobook server   http://{ips[0]}:{config.PORT}")
    print(f"  password           {config.PASSWORD}")
    print(f"  one-tap link       http://{ips[0]}:{config.PORT}/?k={config.PASSWORD}")
    for other in ips[1:]:
        print(f"  also reachable at  http://{other}:{config.PORT}")
    print("  source             https://github.com/emdaitaj/audiobook-web")


if __name__ == "__main__":
    import uvicorn

    print_banner()
    print("=" * 66, flush=True)
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")
