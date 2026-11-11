# audiobook-web

A phone-friendly website and a Telegram bot for turning documents into
audiobooks on your own PC. Send a document, then stream the MP3 on your phone
without saving it anywhere. You can start listening while it is still being
narrated, and you can tap any sentence in the transcript to jump there.

It is a front end for the converter
[**doc-to-audiobook**](https://github.com/emdaitaj/doc-to-audiobook), which does
the OCR and narration on your GPU.

Supported inputs: **PDF** (text or scanned, with OCR), **EPUB**, **DOCX**,
**Markdown**, **HTML** and plain text. You can also paste text straight into
the page or the chat.

Two voice engines, picked per job:

| | Kokoro | OmniVoice |
| --- | --- | --- |
| speed | fast | slower |
| languages | English only | 600+ |
| voice | a fixed cast of 13 | designed from attributes, or cloned from a recording |

---

## Install

Clone both repositories **side by side**. The site finds the converter in a
sibling folder called `doc-to-audiobook`:

```
some-folder/
  doc-to-audiobook/     the converter, plus its envs/
  audiobook-web/        this repository
```

**1. The converter.** Its installer sets up Python, CUDA torch, the voice
engines and OCR on Windows or Linux:

```bash
git clone https://github.com/emdaitaj/doc-to-audiobook
cd doc-to-audiobook
./install.sh            # Linux / macOS
install.cmd             # Windows
cd ..
```

**2. This site.** It needs Python 3.10 or newer and a few small packages:

```bash
git clone https://github.com/emdaitaj/audiobook-web
cd audiobook-web
python -m venv .venv
.venv/bin/pip install -r requirements.txt          # Linux / macOS
.venv\Scripts\pip install -r requirements.txt      # Windows
```

If the converter is somewhere else, set `"engine_dir": "/path/to/doc-to-audiobook"`
in `config.json`, or set the `TTS_ENGINE_DIR` environment variable.

## Running it

```
start.bat          (Windows)
./start.sh         (Linux / macOS)
```

That runs the website and the bot in one process, so they share a single job
queue. The GPU narrates one document at a time and the rest wait in line.

- `start-web-only.bat` / `start-web-only.sh`: website, no bot
- `start-bot-only.bat` / `start-bot-only.sh`: bot, no website
- `allow-firewall-ADMIN.bat`: on Windows, right-click it and choose *Run as
  administrator*, once, so your phone can reach port 8800

On startup the console prints the address, the password and a one-tap link.
If no Telegram token is configured, only the website starts.

## Signing in

The password is generated on first run and stored in `config.json`. Enter it
once and the browser keeps a signed cookie for a year, so the phone never asks
again. The printed one-tap link (`http://<ip>:8800/?k=<password>`) signs in
without showing the form. Bookmark it, or add it to the home screen.

To change the password, edit `password` in `config.json` and restart. To sign
every device out, delete `secret` from `config.json` instead.

## Choosing a voice

**Kokoro** is the default: pick a name from the list and go. `af_heart` is the
default narrator.

**OmniVoice** builds a narrator instead of picking one. Choose at most one value
per category (gender, age, pitch, accent, and optionally whisper) and it
designs a voice to match, e.g. *"female, elderly, moderate pitch, british
accent"*. Pick the language separately: 74 common ones are in the picker, and
the other 570 or so can be typed as a code.

**Cloning.** Give OmniVoice a few seconds of clear speech and it copies that
voice instead of designing one. On the website that is the *Clone a voice* tab.
In Telegram, send the bot a voice message and every book after it is read in
that voice. You can say what the clip contains, but you don't have to: it
transcribes the clip otherwise.

A designed voice comes out different on every run, so a narrator would change
from book to book. To prevent that, the finished narrator is cached under
`data/voices/`, keyed by its attributes, language, seed and pace, and reused.
The same description always gives the same person, and the second book skips
the design step entirely.

The **pace** slider (OmniVoice only) is in words per minute, default 165. Left
to itself the model reads at about 225, which is podcast pace rather than
narration. Below roughly 170 it starts ignoring the request, so treat it as a
nudge rather than a dial.

## The player

Everything streams over HTTP byte ranges, so seeking works without downloading
the file first. Jump buttons cover **5s, 10s, 30s, 1m, 2m and 10m** in both
directions, with a scrub bar and lock-screen controls on a phone. It remembers
where you stopped in each book.

Playback speed has a slider, **-0.05x / +0.05x** buttons for small corrections,
and a **reset to 1.00x** button. This is separate from narration speed, which
is baked into the file when it is made.

## Reading along, and tapping to jump

**Show text** in the player opens the transcript. Tap any sentence and the audio
jumps to it. The sentence being read is highlighted and scrolls into view;
**Follow** turns that off if you'd rather read ahead. Scrolling by hand pauses
the auto-scroll for a few seconds so the page doesn't fight you.

### Where the timings come from

The converter writes a `timings.json` beside the MP3. It records the sample at
which each paragraph's audio begins, so the mapping is exact rather than
guessed:

```json
{"sample_rate": 24000, "offsets": [0, 49800, 350400, 395400, 667200, 721800]}
```

Checked against the silences actually in the audio, those offsets are accurate
to within **7 ms**. They sit about 210 ms before the first syllable, which is
the voice model's own lead-in, so a tap lands just before the words rather than
clipping them.

If the file is missing, or doesn't match the transcript, the times are
estimated instead. The total length is known exactly from the file size and is
spread across the paragraphs by word count. That is out by about a second
(median 0.7 s, worst 2.3 s), and the player says *timings estimated* when it is
in use.

Inside a paragraph, sentence positions are always estimated by word count, so
they drift a little more than paragraph starts do.

## Listening while it is still being narrated

A book doesn't have to be finished to be played. As soon as there is audio, its
card offers **Listen now**, and the rest arrives as the converter produces it.
Seeking, pausing and the jump buttons work exactly as they do on a finished
file.

This works because the converter writes constant-bitrate MP3 to a `.part` file
as it goes. Every second of audio is the same number of bytes, so a position in
seconds is an exact byte offset. The player shows a `~` on the total until the
real length is known. Seeking past the narrated part stops at its end. When
narration finishes, playback carries on without a break.

## Telegram bot

Create a bot with [@BotFather](https://t.me/BotFather) and give the server its
token, either as `TTS_TELEGRAM_TOKEN` or as `"telegram_token"` in
`config.json`. **Never commit the token.**

**The bot replies to nobody until you allow an id:**

```
.venv\Scripts\python.exe bot.py --admin 123456789
```

Anyone not on the list gets a refusal that shows their own Telegram id, so you
can just ask them for it. `bot.py --list-admins` shows the current list, and
the ids are also stored under `telegram_admins` in `config.json`.

Commands: `/engine`, `/voice`, `/lang`, `/speed`, `/settings`, `/status`,
`/cancel`. Send a voice message to clone your own voice.

There is no live listening in Telegram. A chat gets one finished file, so the
bot waits for the whole book. Use the website if you want to start listening
early.

Telegram's limits apply: a bot can download files up to 20 MB and send audio up
to 50 MB, which is about 70 minutes of narration. Anything bigger has to go
through the website.

## Layout

| file | what it does |
| --- | --- |
| `run.py` | website + bot in one process |
| `app.py` | HTTP: auth, uploads, job API, range-served audio |
| `bot.py` | Telegram handlers and the admin allowlist |
| `engine.py` | job queue and progress parsing around the converter |
| `config.py` | paths, password, voices, languages, limits |
| `omnivoice_languages.json` | OmniVoice's language list, dumped from the model |
| `static/` | the page, its stylesheet and its script |
| `data/jobs/` | one folder per conversion: MP3, transcript, `timings.json`, `job.json` |
| `data/voices/` | cached OmniVoice narrators, so a voice stays the same book to book |
| `test_bot.py` | bot handlers against stand-in Telegram objects |

Finished jobs survive a restart (`job.json` is re-read on startup) and are
deleted after 72 hours. Change that with `TTS_JOB_TTL_HOURS`.

## Settings

Environment variables override `config.json`:

| variable | default |
| --- | --- |
| `TTS_PORT` | 8800 |
| `TTS_HOST` | 0.0.0.0 |
| `TTS_PASSWORD` | generated into `config.json` |
| `TTS_TELEGRAM_TOKEN` | `telegram_token` in `config.json`; no token, no bot |
| `TTS_TELEGRAM_ADMINS` | empty; `"111 222"` to allow ids |
| `TTS_MAX_UPLOAD_MB` | 80 |
| `TTS_JOB_TTL_HOURS` | 72 |
| `TTS_DEFAULT_ENGINE` | `kokoro` |
| `TTS_DEFAULT_VOICE` | `af_heart` (Kokoro) |
| `TTS_OMNI_DEFAULT_VOICE` | `male, low pitch, middle-aged, american accent` |
| `TTS_DEFAULT_LANG` | `en` |
| `TTS_DEFAULT_WPM` | 165 |
| `TTS_VOICE_SEED` | 1234 |
| `TTS_MAX_REF_AUDIO_MB` | 25 |
| `TTS_ENGINE_DIR` | `engine_dir` in `config.json`, else `../doc-to-audiobook` |
| `TTS_ENGINE` | `<engine_dir>/pdf_to_audiobook.py` |
| `TTS_ENGINE_PYTHON` | `<engine_dir>/envs/core` interpreter (PyMuPDF + OpenCV) |
| `KOKORO_PY` / `OMNIVOICE_PY` | `<engine_dir>/envs/kokoro`, `envs/omnivoice`; used to show which engines are installed |

A full `config.json` looks like this:

```json
{
  "password": "abc123",
  "secret": "generated",
  "telegram_token": "123456:ABC...",
  "telegram_admins": [123456789],
  "engine_dir": "../doc-to-audiobook"
}
```

## Tests

```
.venv\Scripts\python.exe test_bot.py --full
```

Without `--full` it checks the handlers and the access rules only. With it, one
document is converted end to end and handed to a stand-in `send_audio`.

## Notes

The password is deliberately simple: it is meant for a home network, not the
open internet. Don't port-forward this to the outside world as it stands.

Kokoro is English-only and will mispronounce anything else, so use OmniVoice
with the right language for other scripts. An engine whose virtualenv is
missing is shown greyed out instead of being offered and then failing.

OmniVoice reads numbers and dates as written, because the library that spells
them out has no Windows build.

## License

Copyright © 2026 [emdaitaj](https://github.com/emdaitaj).

Licensed under the **GNU Affero General Public License v3.0** ([LICENSE](LICENSE)),
with additional attribution terms under section 7 ([NOTICE](NOTICE)). In short:

- **Credit the source.** Any copy, fork or derived work must visibly credit and
  link to <https://github.com/emdaitaj>, in its README and in its user
  interface (the page footer is enough), e.g.
  *"Based on audiobook-web by emdaitaj — https://github.com/emdaitaj"*.
- **Share your changes.** If you run a modified version as a website or bot
  that others use, or distribute it, you must publish its full source code
  under the same licence.
- Modified versions must be marked as modified.

The OmniVoice model weights used through the converter are **CC-BY-NC-4.0
(non-commercial)**.
