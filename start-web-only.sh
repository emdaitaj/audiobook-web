#!/usr/bin/env sh
# Website only, no Telegram bot.
cd "$(dirname "$0")" && exec .venv/bin/python app.py "$@"
