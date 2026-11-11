# Part of audiobook-web - https://github.com/emdaitaj/audiobook-web
# Copyright (C) 2026 emdaitaj. AGPL-3.0 with attribution terms (see NOTICE).
"""Telegram front end for the same converter the website uses.

Send the bot a document, or just paste text at it, and it replies with an MP3
you can scrub through inside Telegram's own player. The first person to /start
claims the bot; everyone after that is turned away.
"""
import asyncio
import logging
import os
import sys

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, filters,
)

import config
import engine

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

# Telegram refuses to send documents past 50 MB from a bot.
TG_SEND_LIMIT = 49 * 1024 * 1024
# Bots can only download files up to 20 MB through the standard API.
TG_FETCH_LIMIT = 20 * 1024 * 1024

HELP = (
    "<b>Audiobook</b>\n\n"
    "Send me a <b>PDF, EPUB, DOCX, Markdown, HTML or text file</b> and I will "
    "narrate it with a local voice model and send back an MP3.\n\n"
    "You can also just <b>paste text</b> - plain, Markdown or HTML, I work out "
    "which it is.\n\n"
    "<b>Two voice engines</b>\n"
    "<b>Kokoro</b> is fast, English only, with a fixed cast of voices.\n"
    "<b>OmniVoice</b> speaks 600+ languages, builds a voice out of a "
    "description, and can copy a real one: send me a <b>voice message</b> and "
    "every book after it is read in that voice.\n\n"
    "<b>Commands</b>\n"
    "/engine - Kokoro or OmniVoice\n"
    "/voice - pick or design the narrator\n"
    "/lang - language (OmniVoice)\n"
    "/speed - narration speed\n"
    "/settings - what is set right now\n"
    "/status - what I am working on\n"
    "/cancel - stop the current job\n"
)


# ---------------------------------------------------------------------------
#  access control
# ---------------------------------------------------------------------------
def is_admin(user_id):
    """Only ids on the allowlist get served. An empty list serves nobody."""
    return user_id in config.admins()


async def deny(update, user_id):
    # Telling someone their own id is harmless, and it is the number that has
    # to be added to config.json before they can use anything.
    await update.effective_message.reply_text(
        "This bot is private.\n\n"
        f"Your Telegram id is {user_id}.\n"
        f"An admin can allow it with:\n    python bot.py --admin {user_id}"
    )


def guard(handler):
    async def wrapper(update, context):
        user = update.effective_user
        if not user or not is_admin(user.id):
            log.warning("refused telegram user %s", user.id if user else "?")
            await deny(update, user.id if user else 0)
            return
        return await handler(update, context)

    return wrapper


# ---------------------------------------------------------------------------
#  per-user settings
# ---------------------------------------------------------------------------
def prefs(context):
    data = context.user_data
    data.setdefault("engine", config.DEFAULT_TTS_ENGINE)
    data.setdefault("voice", config.DEFAULT_VOICE)          # kokoro
    data.setdefault("omni_voice", config.OMNI_DEFAULT_VOICE)  # omnivoice
    data.setdefault("lang", config.DEFAULT_LANG)
    data.setdefault("speed", 1.0)
    data.setdefault("ref_audio", None)      # a clip to clone, once one is sent
    data.setdefault("ref_name", "")
    return data


def omni_selection(context):
    """The chosen OmniVoice attributes, as {category: value}."""
    chosen = {}
    for item in prefs(context)["omni_voice"].split(","):
        item = item.strip().lower()
        category = config.OMNI_ATTR_CATEGORY.get(item)
        if category:
            chosen[category] = item
    return chosen


def describe_voice(context):
    """One line saying who is going to read the book."""
    data = prefs(context)
    if data["engine"] == "kokoro":
        return "Kokoro, " + dict(config.VOICES).get(data["voice"], data["voice"])
    if data["ref_audio"]:
        return f"OmniVoice, cloned from {data['ref_name'] or 'your clip'}"
    return f"OmniVoice, \"{data['omni_voice']}\""


def lang_name(code):
    for entry in config.LANGUAGES:
        if entry["id"] == code:
            return entry["name"]
    return code


# ---------------------------------------------------------------------------
#  commands
# ---------------------------------------------------------------------------
@guard
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_html(HELP)


@guard
async def cmd_engine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = prefs(context)["engine"]
    row = []
    for eid, label in (("kokoro", "Kokoro"), ("omnivoice", "OmniVoice")):
        if not config.engine_available(eid):
            continue
        mark = "* " if eid == current else ""
        row.append(InlineKeyboardButton(mark + label, callback_data="e:" + eid))
    await update.effective_message.reply_text(
        "Kokoro is fast and English only.\n"
        "OmniVoice speaks 600+ languages and can copy a real voice.",
        reply_markup=InlineKeyboardMarkup([row]),
    )


@guard
async def cmd_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = prefs(context)
    if data["engine"] == "kokoro":
        await update.effective_message.reply_text(
            "Pick a narrator:", reply_markup=kokoro_keyboard(data["voice"])
        )
        return
    if data["ref_audio"]:
        await update.effective_message.reply_text(
            f"Currently cloning {data['ref_name'] or 'your clip'}. Send another "
            "voice message to change it, or tap below to design a voice instead.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Stop cloning", callback_data="c:off")]]),
        )
        return
    await update.effective_message.reply_text(
        omni_voice_text(context), reply_markup=omni_keyboard(context)
    )


def kokoro_keyboard(current):
    rows, row = [], []
    for vid, name in config.VOICES:
        mark = "* " if vid == current else ""
        row.append(InlineKeyboardButton(mark + name.split(" - ")[0],
                                        callback_data="v:" + vid))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def omni_voice_text(context):
    return ("OmniVoice builds a narrator out of these attributes.\n\n"
            f"Right now: \"{prefs(context)['omni_voice']}\"\n\n"
            "Tap a category to change it. To copy a real voice instead, just "
            "send me a voice message.")


def omni_keyboard(context):
    """One button per category, showing what is currently picked."""
    chosen = omni_selection(context)
    rows = []
    for category, _ in config.OMNI_ATTRS:
        label = f"{category}: {chosen.get(category, '-')}"
        rows.append([InlineKeyboardButton(label, callback_data="a:" + category)])
    return InlineKeyboardMarkup(rows)


def omni_values_keyboard(context, category):
    """The values inside one category, plus a way to clear it."""
    chosen = omni_selection(context).get(category)
    values = dict(config.OMNI_ATTRS)[category]
    rows, row = [], []
    for value in values:
        mark = "* " if value == chosen else ""
        row.append(InlineKeyboardButton(mark + value, callback_data="p:" + value))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("none", callback_data="p:-" + category),
                 InlineKeyboardButton("< back", callback_data="a:")])
    return InlineKeyboardMarkup(rows)


def set_omni_attr(context, category, value):
    """Replace one category in the stored attribute list."""
    chosen = omni_selection(context)
    if value:
        chosen[category] = value
    else:
        chosen.pop(category, None)
    ordered = [chosen[c] for c, _ in config.OMNI_ATTRS if c in chosen]
    prefs(context)["omni_voice"] = ", ".join(ordered)


@guard
async def cmd_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = prefs(context)
    wanted = " ".join(context.args).strip().lower() if context.args else ""
    if wanted:
        code = wanted
        if code not in config.LANGUAGE_IDS:
            match = [e for e in config.LANGUAGES if e["name"].lower() == wanted]
            code = match[0]["id"] if match else None
        if not code:
            near = [e["name"] for e in config.LANGUAGES if wanted in e["name"].lower()]
            await update.effective_message.reply_text(
                f"I do not know '{wanted}'."
                + (" Did you mean: " + ", ".join(near[:6]) + "?" if near else
                   " Try a code like 'de', or a name like 'German'.")
            )
            return
        data["lang"] = code
        await update.effective_message.reply_text("Language set to " + lang_name(code) + ".")
        return

    rows, row = [], []
    for entry in config.LANGUAGES[:12]:
        mark = "* " if entry["id"] == data["lang"] else ""
        row.append(InlineKeyboardButton(mark + entry["name"],
                                        callback_data="l:" + entry["id"]))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    await update.effective_message.reply_text(
        f"Language: {lang_name(data['lang'])}.\n\n"
        f"These are the common ones. For any of OmniVoice's "
        f"{len(config.LANGUAGE_IDS)} languages, type it: /lang swedish, /lang sv",
        reply_markup=InlineKeyboardMarkup(rows),
    )


@guard
async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = prefs(context)
    lines = [f"Narrator: {describe_voice(context)}",
             f"Narration speed: {data['speed']}x"]
    if data["engine"] == "omnivoice":
        lines.append(f"Language: {lang_name(data['lang'])}")
    await update.effective_message.reply_text("\n".join(lines))


@guard
async def cmd_speed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = prefs(context)["speed"]
    row = [
        InlineKeyboardButton(("* " if abs(current - s) < 0.01 else "") + f"{s}x",
                             callback_data=f"s:{s}")
        for s in (0.8, 0.9, 1.0, 1.1, 1.25)
    ]
    await update.effective_message.reply_text(
        "Narration speed (this is baked into the file - Telegram's player has "
        "its own speed control too):",
        reply_markup=InlineKeyboardMarkup([row]),
    )


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("private bot", show_alert=True)
        return
    kind, _, value = query.data.partition(":")
    data = prefs(context)

    if kind == "v" and value in config.VOICE_IDS:
        data["voice"] = value
        name = dict(config.VOICES)[value]
        await query.answer("voice: " + name)
        await query.edit_message_text("Narrator set to " + name + ".")

    elif kind == "s":
        data["speed"] = float(value)
        await query.answer("speed: " + value)
        await query.edit_message_text("Narration speed set to " + value + "x.")

    elif kind == "e" and value in config.TTS_ENGINES:
        data["engine"] = value
        await query.answer(value)
        await query.edit_message_text("Now using " + describe_voice(context) + ".")

    elif kind == "a":                       # open a category, or go back
        await query.answer()
        if value:
            await query.edit_message_text(
                f"Pick a {value}:",
                reply_markup=omni_values_keyboard(context, value))
        else:
            await query.edit_message_text(omni_voice_text(context),
                                          reply_markup=omni_keyboard(context))

    elif kind == "p":                       # pick a value, or clear a category
        if value.startswith("-"):
            set_omni_attr(context, value[1:], None)
        elif value in config.OMNI_ATTR_CATEGORY:
            set_omni_attr(context, config.OMNI_ATTR_CATEGORY[value], value)
        await query.answer(data["omni_voice"] or "cleared")
        await query.edit_message_text(omni_voice_text(context),
                                      reply_markup=omni_keyboard(context))

    elif kind == "l" and value in config.LANGUAGE_IDS:
        data["lang"] = value
        await query.answer(lang_name(value))
        await query.edit_message_text("Language set to " + lang_name(value) + ".")

    elif kind == "c" and value == "off":
        data["ref_audio"] = None
        data["ref_name"] = ""
        await query.answer("back to a designed voice")
        await query.edit_message_text(omni_voice_text(context),
                                      reply_markup=omni_keyboard(context))
    else:
        await query.answer()


@guard
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mine = [j for j in engine.all_jobs() if j.origin == "telegram"]
    active = [j for j in mine if j.state in ("queued", "reading", "narrating")]
    if not active:
        await update.effective_message.reply_text("Nothing in the queue.")
        return
    lines = []
    for job in active:
        line = f"{job.label} - {job.state}, {job.percent}%"
        if job.eta_minutes:
            line += f", about {job.eta_minutes} min left"
        lines.append(line)
    await update.effective_message.reply_text("\n".join(lines))


@guard
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mine = [j for j in engine.all_jobs()
            if j.origin == "telegram" and j.state in ("queued", "reading", "narrating")]
    if not mine:
        await update.effective_message.reply_text("Nothing to cancel.")
        return
    for job in mine:
        job.cancel()
    await update.effective_message.reply_text(f"Cancelled {len(mine)} job(s).")


# ---------------------------------------------------------------------------
#  documents and text
# ---------------------------------------------------------------------------
@guard
async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A voice message or audio file means: read my books in this voice."""
    message = update.effective_message
    clip = message.voice or message.audio
    name = getattr(clip, "file_name", None) or "your voice message"
    await save_reference(update, context, clip, name)


async def save_reference(update, context, clip, name):
    message = update.effective_message
    if not config.engine_available("omnivoice"):
        await message.reply_text(
            "Copying a voice needs the OmniVoice backend, which is not "
            "installed on this machine."
        )
        return
    if clip.file_size and clip.file_size > config.MAX_REF_AUDIO_MB * 1024 * 1024:
        await message.reply_text(
            f"That clip is over {config.MAX_REF_AUDIO_MB} MB - a few seconds of "
            "clear speech is all it needs."
        )
        return
    note = await message.reply_text("Saving that voice...")
    try:
        tg_file = await context.bot.get_file(clip.file_id)
        data = bytes(await tg_file.download_as_bytearray())
    except BadRequest as exc:
        await note.edit_text("Telegram would not give me that clip: " + str(exc))
        return

    suffix = os.path.splitext(name)[1].lower()
    if suffix not in config.REF_AUDIO_EXT:
        suffix = ".ogg" if update.effective_message.voice else ".mp3"
    path = engine.stage_ref_audio("reference" + suffix, data=data)

    settings = prefs(context)
    settings["engine"] = "omnivoice"        # only OmniVoice can clone
    settings["ref_audio"] = path
    settings["ref_name"] = name
    await note.edit_text(
        f"Saved. Every book from now on is read in that voice, in "
        f"{lang_name(settings['lang'])}.\n\n"
        "I will work out what the clip says by myself. /voice to go back to a "
        "designed voice, /lang to change the language."
    )


@guard
async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    doc = message.document
    name = doc.file_name or "document.txt"

    # An audio file sent as a document is a voice to copy, not a book to read.
    if os.path.splitext(name)[1].lower() in config.REF_AUDIO_EXT:
        await save_reference(update, context, doc, name)
        return

    problem = engine.check_ext(name)
    if problem:
        await message.reply_text(problem)
        return
    if doc.file_size and doc.file_size > TG_FETCH_LIMIT:
        await message.reply_text(
            "Telegram only lets bots download files up to 20 MB. Use the "
            "website for anything bigger."
        )
        return

    note = await message.reply_text("Downloading...")
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        data = bytes(await tg_file.download_as_bytearray())
    except BadRequest as exc:
        await note.edit_text("Telegram would not give me that file: " + str(exc))
        return
    src = engine.stage_upload(name, data=data)
    await start_job(update, context, src, name, note)


@guard
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    text = message.text or message.caption or ""
    if len(text.split()) < 20:
        await message.reply_text(
            "That is too short to narrate - send at least 20 words, or attach "
            "a document."
        )
        return
    src = engine.stage_text(text, fmt="auto")
    note = await message.reply_text("Queued...")
    await start_job(update, context, src, os.path.basename(src), note)


async def start_job(update, context, src, label, note):
    """Queue the conversion and keep one status message updated until it ends."""
    settings = prefs(context)
    omni = settings["engine"] == "omnivoice"
    job = engine.submit(
        src,
        voice=settings["omni_voice"] if omni else settings["voice"],
        speed=settings["speed"], origin="telegram", label=label,
        tts_engine=settings["engine"], lang=settings["lang"],
        ref_audio=settings["ref_audio"] if omni else None,
    )

    last = {"text": ""}

    async def push(text):
        # Telegram rejects an edit that does not change anything, and rate
        # limits the rest, so only send a genuinely new line.
        if text == last["text"]:
            return
        last["text"] = text
        try:
            await note.edit_text(text)
        except BadRequest:
            pass

    await push(describe(job))
    while job.state in ("queued", "reading", "narrating"):
        await asyncio.sleep(4)
        await push(describe(job))

    if job.state == "cancelled":
        await note.edit_text("Cancelled.")
        return
    if job.state != "done" or not job.mp3:
        await note.edit_text("Failed: " + (job.error or "unknown error"))
        return

    size = os.path.getsize(job.mp3)
    if size > TG_SEND_LIMIT:
        await note.edit_text(
            f"The MP3 came out at {size / 1048576:.0f} MB, past Telegram's 50 MB "
            "limit for bots. Grab it from the website instead."
        )
        return

    await note.edit_text("Uploading the audio...")
    caption = f"{job.words:,} words"
    if job.audio_minutes:
        caption += f" - about {job.audio_minutes} min"
    caption += "\n" + describe_voice(context)
    try:
        with open(job.mp3, "rb") as fh:
            await context.bot.send_audio(
                chat_id=update.effective_chat.id,
                audio=fh,
                filename=os.path.basename(job.mp3),
                title=os.path.splitext(os.path.basename(job.mp3))[0][:60],
                performer=describe_voice(context)[:60],
                duration=int((job.audio_minutes or 0) * 60) or None,
                caption=caption,
                read_timeout=300, write_timeout=600, connect_timeout=60,
            )
        await note.delete()
    except Exception as exc:
        log.exception("send_audio failed")
        await note.edit_text("Made the audio but could not upload it: " + str(exc))


def describe(job):
    if job.state == "queued":
        pos = engine.queue_position(job)
        return "Queued" + (f" (#{pos} in line)" if pos > 1 else "") + "..."
    if job.state == "reading":
        return "Reading the document: " + job.detail
    if job.state == "narrating":
        line = f"Narrating... {job.percent}%"
        if job.eta_minutes:
            line += f" - about {job.eta_minutes} min left"
        return line
    if job.state == "done":
        return "Done, packaging the audio..."
    return job.detail or job.state


async def on_error(update, context):
    log.error("handler failed", exc_info=context.error)


def build():
    """The configured Application, ready for either run style."""
    app = (
        Application.builder()
        .token(config.TELEGRAM_TOKEN)
        .read_timeout(60)
        .write_timeout(600)
        .connect_timeout(30)
        .build()
    )
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("engine", cmd_engine))
    app.add_handler(CommandHandler("voice", cmd_voice))
    app.add_handler(CommandHandler("lang", cmd_lang))
    app.add_handler(CommandHandler("speed", cmd_speed))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    return app


def banner():
    ids = config.admins()
    print("  telegram bot       polling")
    print("  bot admins        ",
          ", ".join(str(i) for i in sorted(ids)) if ids
          else "NONE - the bot replies to nobody until you add an id "
               "(python bot.py --admin <id>)")


async def start_polling(app):
    """Start the bot inside an event loop somebody else owns (see run.py)."""
    await app.initialize()
    await app.start()
    await app.updater.start_polling(
        allowed_updates=Update.ALL_TYPES, drop_pending_updates=True
    )


async def stop_polling(app):
    if app.updater and app.updater.running:
        await app.updater.stop()
    if app.running:
        await app.stop()
    await app.shutdown()


def main():
    args = sys.argv[1:]
    if args and args[0] in ("--admin", "--add-admin"):
        if len(args) < 2:
            sys.exit("usage: python bot.py --admin <telegram-user-id>")
        for value in args[1:]:
            try:
                config.add_admin(value)
            except ValueError:
                sys.exit(f"'{value}' is not a Telegram user id")
        print("admins:", ", ".join(str(i) for i in sorted(config.admins())))
        return
    if args and args[0] == "--list-admins":
        ids = sorted(config.admins())
        print(", ".join(str(i) for i in ids) if ids else "(none)")
        return

    if not config.TELEGRAM_TOKEN:
        sys.exit("No bot token. Set TTS_TELEGRAM_TOKEN or put \"telegram_token\" "
                 "in config.json (get one from @BotFather).")
    app = build()
    print("=" * 62)
    banner()
    print("=" * 62, flush=True)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
