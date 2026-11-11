# Part of audiobook-web - https://github.com/emdaitaj/audiobook-web
# Copyright (C) 2026 emdaitaj. AGPL-3.0 with attribution terms (see NOTICE).
"""Drive the bot handlers with stand-in Telegram objects.

Telegram itself is never contacted: the point is to prove the handler wiring,
the access check, and the document/text paths behave before a real chat does.
Run it with the server stopped so the two do not share the job folder.

    .venv\\Scripts\\python.exe test_bot.py
"""
import asyncio
import os
import sys

import bot
import config
import engine

SENT = []


class FakeMessage:
    def __init__(self, text=None, document=None, caption=None, voice=None,
                 audio=None):
        self.text = text
        self.caption = caption
        self.document = document
        self.voice = voice
        self.audio = audio
        self.edits = []

    async def reply_text(self, text, **kw):
        SENT.append(("reply", text))
        return FakeSentMessage(self)

    async def reply_html(self, text, **kw):
        SENT.append(("reply_html", text[:40]))
        return FakeSentMessage(self)


class FakeSentMessage:
    def __init__(self, parent):
        self.parent = parent

    async def edit_text(self, text, **kw):
        self.parent.edits.append(text)
        SENT.append(("edit", text))
        return self

    async def delete(self):
        SENT.append(("delete", ""))


class FakeDocument:
    def __init__(self, name, size, path=None):
        self.file_name = name
        self.file_size = size
        self.file_id = "fake"
        self._path = path


class FakeVoice:
    """A Telegram voice message: no filename, just an ogg blob."""

    def __init__(self, size=12345):
        self.file_size = size
        self.file_id = "fake-voice"


class FakeFile:
    def __init__(self, path):
        self._path = path

    async def download_as_bytearray(self):
        with open(self._path, "rb") as fh:
            return bytearray(fh.read())


class FakeBot:
    def __init__(self, path=None):
        self._path = path
        self.audio_sent = []

    async def get_file(self, file_id):
        return FakeFile(self._path)

    async def send_audio(self, chat_id, audio, **kw):
        data = audio.read()
        self.audio_sent.append((kw.get("filename"), len(data), kw.get("caption")))
        SENT.append(("audio", f"{kw.get('filename')} {len(data)} bytes"))


class FakeContext:
    def __init__(self, path=None):
        self.user_data = {}
        self.args = []
        self.bot = FakeBot(path)


class FakeUpdate:
    def __init__(self, message, user_id=1234, chat_id=1234):
        self.effective_message = message
        self.effective_user = type("U", (), {"id": user_id})()
        self.effective_chat = type("C", (), {"id": chat_id})()


def check(label, condition, extra=""):
    print(("PASS  " if condition else "FAIL  ") + label + ("  " + extra if extra else ""))
    return bool(condition)


async def main():
    results = []
    owner = 1234
    saved_admins = list(config.SETTINGS.get("telegram_admins") or [])
    config.SETTINGS["telegram_admins"] = []

    # With an empty allowlist the bot serves nobody, including the owner.
    SENT.clear()
    await bot.on_text(FakeUpdate(FakeMessage(text="word " * 40), owner), FakeContext())
    results.append(check("empty allowlist refuses everyone",
                         any("private" in t for _, t in SENT), str(SENT)))
    results.append(check("refusal shows the caller their own id",
                         any(str(owner) in t for _, t in SENT), str(SENT)))

    config.SETTINGS["telegram_admins"] = [owner]

    SENT.clear()
    await bot.cmd_start(FakeUpdate(FakeMessage(text="/start"), owner), FakeContext())
    results.append(check("an admin gets the help text",
                         any(k == "reply_html" for k, _ in SENT), str(SENT)))

    SENT.clear()
    stranger = FakeMessage(text="hello there " * 10)
    await bot.on_text(FakeUpdate(stranger, 999), FakeContext())
    results.append(check("a non-admin is turned away",
                         any("private" in t for _, t in SENT), str(SENT)))

    SENT.clear()
    short = FakeMessage(text="too short")
    await bot.on_text(FakeUpdate(short, owner), FakeContext())
    results.append(check("short text is rejected",
                         any("too short" in t for _, t in SENT), str(SENT)))

    SENT.clear()
    bad = FakeMessage(document=FakeDocument("report.rtf", 100))
    await bot.on_document(FakeUpdate(bad, owner), FakeContext())
    results.append(check("unsupported extension is rejected",
                         any("not supported" in t for _, t in SENT), str(SENT)))

    SENT.clear()
    huge = FakeMessage(document=FakeDocument("big.pdf", 30 * 1024 * 1024))
    await bot.on_document(FakeUpdate(huge, owner), FakeContext())
    results.append(check("oversized document is rejected",
                         any("20 MB" in t for _, t in SENT), str(SENT)))

    # Voice and speed buttons.
    context = FakeContext()
    bot.prefs(context)["voice"] = "am_michael"
    query = type("Q", (), {})()
    query.data = "v:bf_emma"
    query.from_user = type("U", (), {"id": owner})()
    query.answer = lambda *a, **k: asyncio.sleep(0)
    query.edit_message_text = lambda *a, **k: asyncio.sleep(0)
    await bot.on_button(type("U2", (), {"callback_query": query})(), context)
    results.append(check("voice button updates the preference",
                         context.user_data["voice"] == "bf_emma"))

    query.data = "s:1.25"
    await bot.on_button(type("U2", (), {"callback_query": query})(), context)
    results.append(check("speed button updates the preference",
                         context.user_data["speed"] == 1.25))

    # --- OmniVoice ------------------------------------------------------
    def press(data):
        query.data = data
        return bot.on_button(type("U2", (), {"callback_query": query})(), context)

    await press("e:omnivoice")
    results.append(check("engine button switches to OmniVoice",
                         context.user_data["engine"] == "omnivoice"))

    await press("a:accent")           # open the accent list
    await press("p:british accent")   # pick one
    await press("p:female")           # and a gender
    voice = context.user_data["omni_voice"]
    results.append(check("attribute buttons build a voice string",
                         "british accent" in voice and "female" in voice, voice))
    results.append(check("the built voice is one the converter accepts",
                         engine.check_voice(voice) is None,
                         str(engine.check_voice(voice))))

    await press("p:male")             # same category, so it should replace
    voice = context.user_data["omni_voice"]
    results.append(check("picking a second gender replaces the first",
                         "male" in voice.split(", ") and "female" not in voice.split(", "),
                         voice))

    await press("p:-accent")          # clear a category
    results.append(check("'none' clears a category",
                         "british accent" not in context.user_data["omni_voice"],
                         context.user_data["omni_voice"]))

    await press("l:de")
    results.append(check("language button applies",
                         context.user_data["lang"] == "de"))

    SENT.clear()
    ctx2 = FakeContext()
    ctx2.args = ["swedish"]
    await bot.cmd_lang(FakeUpdate(FakeMessage(text="/lang swedish"), owner), ctx2)
    results.append(check("/lang accepts a language name",
                         ctx2.user_data["lang"] == "sv", str(SENT)))

    SENT.clear()
    ctx3 = FakeContext()
    ctx3.args = ["klingon"]
    await bot.cmd_lang(FakeUpdate(FakeMessage(text="/lang klingon"), owner), ctx3)
    results.append(check("/lang rejects an unknown language",
                         any("do not know" in t for _, t in SENT), str(SENT)))

    # A voice message becomes the narrator.
    clip = os.path.join(config.BASE_DIR, "_ref_sample.ogg")
    with open(clip, "wb") as fh:
        fh.write(b"OggS" + bytes(2048))
    SENT.clear()
    ctx4 = FakeContext(clip)
    ctx4.user_data["engine"] = "kokoro"
    await bot.on_voice(FakeUpdate(FakeMessage(voice=FakeVoice()), owner), ctx4)
    results.append(check("a voice message is saved as the narrator",
                         bool(ctx4.user_data.get("ref_audio")), str(SENT)))
    results.append(check("cloning switches the engine to OmniVoice",
                         ctx4.user_data["engine"] == "omnivoice"))
    stored = ctx4.user_data.get("ref_audio")
    results.append(check("the saved clip is on disk",
                         bool(stored) and os.path.exists(stored), str(stored)))

    SENT.clear()
    query.data = "c:off"
    await bot.on_button(type("U2", (), {"callback_query": query})(), ctx4)
    results.append(check("'stop cloning' goes back to a designed voice",
                         ctx4.user_data["ref_audio"] is None))
    os.remove(clip)

    # An mp3 sent as a document is a voice, not a book.
    SENT.clear()
    ctx5 = FakeContext(None)
    ctx5._clip = None
    doc_clip = os.path.join(config.BASE_DIR, "_ref_doc.mp3")
    with open(doc_clip, "wb") as fh:
        fh.write(b"ID3" + bytes(2048))
    ctx5 = FakeContext(doc_clip)
    await bot.on_document(
        FakeUpdate(FakeMessage(document=FakeDocument("me.mp3", 2051, doc_clip)), owner),
        ctx5)
    results.append(check("an audio file sent as a document is treated as a voice",
                         bool(ctx5.user_data.get("ref_audio")), str(SENT)))
    os.remove(doc_clip)

    if "--full" in sys.argv:
        # The real thing: a document goes in, an MP3 comes back.
        sample = os.path.join(config.BASE_DIR, "_bot_sample.md")
        with open(sample, "w", encoding="utf-8") as fh:
            fh.write("# Bot end to end\n\nThe bot downloads the document, hands it "
                     "to the same queue the website uses, and uploads the finished "
                     "audio back into the chat when the voice model is done with "
                     "it.\n\nA second paragraph makes sure there is enough text "
                     "here to clear the twenty word minimum comfortably.\n")
        SENT.clear()
        context = FakeContext(sample)
        context.user_data["speed"] = 1.0
        doc = FakeMessage(document=FakeDocument("bot end to end.md",
                                                os.path.getsize(sample), sample))
        await bot.on_document(FakeUpdate(doc, owner), context)
        results.append(check("document converted and audio uploaded",
                             bool(context.bot.audio_sent),
                             str(context.bot.audio_sent or SENT[-3:])))
        os.remove(sample)
    else:
        print("SKIP  end-to-end conversion (pass --full to run it)")

    config.SETTINGS["telegram_admins"] = saved_admins
    config.save(config.SETTINGS)
    print()
    print("%d/%d passed" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
