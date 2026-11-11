# Part of audiobook-web - https://github.com/emdaitaj/audiobook-web
# Copyright (C) 2026 emdaitaj. AGPL-3.0 with attribution terms (see NOTICE).
"""Run the website and the Telegram bot together in one process.

They share a single job queue that way, so a document sent to the bot and one
uploaded on the phone take turns on the GPU instead of fighting over it.

    python run.py            website + bot
    python app.py            website only
    python bot.py            bot only
"""
import asyncio
import contextlib

import uvicorn

import bot as bot_module
import config
from app import app, print_banner


@contextlib.asynccontextmanager
async def lifespan(_app):
    if not config.TELEGRAM_TOKEN:
        yield                      # no token configured: website only
        return
    telegram = bot_module.build()
    await bot_module.start_polling(telegram)
    try:
        yield
    finally:
        await bot_module.stop_polling(telegram)


app.router.lifespan_context = lifespan


def main():
    print_banner()
    if config.TELEGRAM_TOKEN:
        bot_module.banner()
    else:
        print("  telegram bot       off (no TTS_TELEGRAM_TOKEN / telegram_token)")
    print("=" * 66, flush=True)
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
