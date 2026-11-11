#!/usr/bin/env sh
# Website + Telegram bot in one process, sharing one job queue.
cd "$(dirname "$0")" && exec .venv/bin/python run.py "$@"
