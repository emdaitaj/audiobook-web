#!/usr/bin/env sh
# Telegram bot only, no website.
cd "$(dirname "$0")" && exec .venv/bin/python bot.py "$@"
