#!/usr/bin/env sh
set -eu

repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo"

command -v python3 >/dev/null 2>&1 || { echo "python3 was not found" >&2; exit 1; }
command -v ffmpeg >/dev/null 2>&1 || { echo "ffmpeg was not found" >&2; exit 1; }
command -v ffprobe >/dev/null 2>&1 || { echo "ffprobe was not found" >&2; exit 1; }

test -d .venv || python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-dev.txt
make -C src/native_encoder
.venv/bin/python -m pytest -q
