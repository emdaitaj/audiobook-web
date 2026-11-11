# Part of audiobook-web - https://github.com/emdaitaj/audiobook-web
# Copyright (C) 2026 emdaitaj. AGPL-3.0 with attribution terms (see NOTICE).
"""Configuration for the TTS web app and Telegram bot.

Values are read from environment variables when present, otherwise from
config.json next to this file. config.json is created on first run with a
freshly generated password and cookie secret.
"""
import json
import os
import secrets
import string

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
JOBS_DIR = os.path.join(DATA_DIR, "jobs")
VOICE_CACHE = os.path.join(DATA_DIR, "voices")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
LANG_FILE = os.path.join(BASE_DIR, "omnivoice_languages.json")

def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


_FILE = _read_json(CONFIG_FILE)


def venv_python(env_dir):
    """The interpreter inside a virtualenv, on Windows or Linux/macOS."""
    for rel in (("Scripts", "python.exe"), ("python.exe",), ("bin", "python")):
        path = os.path.join(env_dir, *rel)
        if os.path.exists(path):
            return path
    return os.path.join(env_dir, "Scripts" if os.name == "nt" else "bin",
                        "python.exe" if os.name == "nt" else "python")


def _engine_dir():
    """Where the doc-to-audiobook checkout lives.

    TTS_ENGINE_DIR, then "engine_dir" in config.json, then a sibling folder
    named doc-to-audiobook (clone both repos side by side and it just works).
    """
    explicit = os.environ.get("TTS_ENGINE_DIR") or _FILE.get("engine_dir")
    if explicit:
        return os.path.abspath(os.path.join(BASE_DIR, explicit))
    return os.path.abspath(os.path.join(BASE_DIR, "..", "doc-to-audiobook"))


# The document -> MP3 converter (https://github.com/emdaitaj/doc-to-audiobook)
# and the interpreters it uses. Its install script creates one virtualenv per
# stage under <engine_dir>/envs/: core (PyMuPDF + OpenCV, runs the converter
# itself), kokoro, omnivoice and paddle.
ENGINE_DIR = _engine_dir()
ENGINE = os.environ.get("TTS_ENGINE",
                        os.path.join(ENGINE_DIR, "pdf_to_audiobook.py"))
ENGINE_PYTHON = os.environ.get("TTS_ENGINE_PYTHON",
                               venv_python(os.path.join(ENGINE_DIR, "envs", "core")))
# Only used to show which voice engines are installed; the converter finds
# them itself.
KOKORO_PYTHON = os.environ.get("KOKORO_PY",
                               venv_python(os.path.join(ENGINE_DIR, "envs", "kokoro")))
OMNIVOICE_PYTHON = os.environ.get("OMNIVOICE_PY",
                                  venv_python(os.path.join(ENGINE_DIR, "envs", "omnivoice")))

# From @BotFather. Put it in the environment or under "telegram_token" in
# config.json; never commit it. Without one, the bot is simply not started.
TELEGRAM_TOKEN = (os.environ.get("TTS_TELEGRAM_TOKEN")
                  or _FILE.get("telegram_token", ""))
# Telegram user ids allowed to use the bot. Nobody else gets a reply. Set it
# here, or with TTS_TELEGRAM_ADMINS="111 222", or run: python bot.py --admin 111
TELEGRAM_ADMINS = os.environ.get("TTS_TELEGRAM_ADMINS", "")

HOST = os.environ.get("TTS_HOST", "0.0.0.0")
PORT = int(os.environ.get("TTS_PORT", "8800"))

# Uploads larger than this are rejected before anything touches the disk.
MAX_UPLOAD_MB = int(os.environ.get("TTS_MAX_UPLOAD_MB", "80"))
# A voice clip to clone is a few seconds long; anything bigger is a mistake.
MAX_REF_AUDIO_MB = int(os.environ.get("TTS_MAX_REF_AUDIO_MB", "25"))
# Finished jobs older than this are deleted to keep the disk from filling up.
JOB_TTL_HOURS = int(os.environ.get("TTS_JOB_TTL_HOURS", "72"))


# ---------------------------------------------------------------------------
#  TTS backends
# ---------------------------------------------------------------------------
# Kokoro is fast and English-only with a fixed cast of voices. OmniVoice is
# slower, speaks 600+ languages, builds a voice out of a description, and can
# clone one from a recording.
TTS_ENGINES = ["kokoro", "omnivoice"]
DEFAULT_TTS_ENGINE = os.environ.get("TTS_DEFAULT_ENGINE", "kokoro")

# Kept in step with KOKORO_VOICES in pdf_to_audiobook.py.
VOICES = [
    ("af_heart", "Heart - US female"),
    ("af_bella", "Bella - US female"),
    ("af_nicole", "Nicole - US female"),
    ("af_sarah", "Sarah - US female"),
    ("am_michael", "Michael - US male"),
    ("am_adam", "Adam - US male"),
    ("am_eric", "Eric - US male"),
    ("am_liam", "Liam - US male"),
    ("am_onyx", "Onyx - US male"),
    ("bf_emma", "Emma - UK female"),
    ("bf_isabella", "Isabella - UK female"),
    ("bm_george", "George - UK male"),
    ("bm_lewis", "Lewis - UK male"),
]
VOICE_IDS = {v for v, _ in VOICES}
DEFAULT_VOICE = os.environ.get("TTS_DEFAULT_VOICE", "af_heart")

# OmniVoice does not take a free-form description: the voice is a comma-separated
# list drawn from this fixed vocabulary, at most one item per category. Kept in
# step with OMNIVOICE_INSTRUCT in pdf_to_audiobook.py.
OMNI_ATTRS = [
    ("gender", ["female", "male"]),
    ("age", ["child", "teenager", "young adult", "middle-aged", "elderly"]),
    ("pitch", ["very low pitch", "low pitch", "moderate pitch",
               "high pitch", "very high pitch"]),
    ("accent", ["american accent", "australian accent", "british accent",
                "canadian accent", "chinese accent", "indian accent",
                "japanese accent", "korean accent", "portuguese accent",
                "russian accent"]),
    ("effect", ["whisper"]),
]
OMNI_ATTR_CATEGORY = {value: category
                      for category, values in OMNI_ATTRS
                      for value in values}
OMNI_DEFAULT_VOICE = os.environ.get(
    "TTS_OMNI_DEFAULT_VOICE", "male, low pitch, middle-aged, american accent"
)
DEFAULT_LANG = os.environ.get("TTS_DEFAULT_LANG", "en")
# Left alone OmniVoice reads at podcast pace; 165 wpm is narration pace.
DEFAULT_WPM = float(os.environ.get("TTS_DEFAULT_WPM", "165"))
# A fixed seed keeps the designed voice identical from one book to the next.
VOICE_SEED = int(os.environ.get("TTS_VOICE_SEED", "1234"))

REF_AUDIO_EXT = {".wav", ".mp3", ".m4a", ".ogg", ".oga", ".flac", ".opus", ".aac"}


def _load_languages():
    """OmniVoice's own language list, dumped from the model at build time."""
    try:
        with open(LANG_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("common", []), set(data.get("all_ids", []))
    except (OSError, ValueError):
        return [{"id": "en", "name": "English"}], set()


LANGUAGES, LANGUAGE_IDS = _load_languages()


def engine_available(name):
    return os.path.exists(
        {"kokoro": KOKORO_PYTHON, "omnivoice": OMNIVOICE_PYTHON}.get(name, "")
    )


# ---------------------------------------------------------------------------
#  stored settings
# ---------------------------------------------------------------------------
def _new_password(n=6):
    """Short and lowercase: this gets typed once, on a phone keyboard."""
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def parse_ids(raw):
    """'111, 222' -> [111, 222]; anything unparseable is dropped."""
    out = []
    for chunk in str(raw).replace(",", " ").split():
        try:
            out.append(int(chunk))
        except ValueError:
            continue
    return out


def load():
    """Return the mutable settings dict, creating config.json if needed."""
    data = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as fh:
                data = json.load(fh)
        except (ValueError, OSError):
            data = {}
    changed = False
    if not data.get("password"):
        data["password"] = _new_password()
        changed = True
    if not data.get("secret"):
        data["secret"] = secrets.token_hex(32)
        changed = True
    if "telegram_admins" not in data:
        data["telegram_admins"] = parse_ids(TELEGRAM_ADMINS)
        changed = True
    if changed:
        save(data)
    return data


def save(data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


SETTINGS = load()
PASSWORD = os.environ.get("TTS_PASSWORD") or SETTINGS["password"]
SECRET = SETTINGS["secret"]


def admins():
    """Telegram ids allowed to use the bot. Empty means the bot is closed."""
    env = parse_ids(TELEGRAM_ADMINS)
    return set(env) | set(SETTINGS.get("telegram_admins") or [])


def add_admin(user_id):
    ids = list(SETTINGS.get("telegram_admins") or [])
    if int(user_id) not in ids:
        ids.append(int(user_id))
        SETTINGS["telegram_admins"] = ids
        save(SETTINGS)
    return ids


os.makedirs(JOBS_DIR, exist_ok=True)
os.makedirs(VOICE_CACHE, exist_ok=True)
