#!/bin/sh
set -eu
PROJECT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"
if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi
exec "$PROJECT_DIR/.venv/bin/python" -m vibe_usage_ssh
