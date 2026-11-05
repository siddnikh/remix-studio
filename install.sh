#!/usr/bin/env bash
# Safe to run repeatedly. Installs into .venv in this folder and only adds symlinks elsewhere.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

dry=""
for flag in "$@"; do
  if [ "$flag" = "--dry-run" ]; then dry=1; fi
done

if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
  echo "ffmpeg and ffprobe are required and this script won't install them. On macOS: brew install ffmpeg" >&2
  exit 1
fi

if [ -n "$dry" ] && [ ! -x .venv/bin/remix-studio ]; then
  echo "Dry run: would create .venv here, then link the skills into each agent found." >&2
  exit 0
fi

if [ -z "$dry" ]; then
  if command -v uv >/dev/null; then
    [ -x .venv/bin/python ] || uv venv --quiet --python 3.12 .venv
    uv pip install --quiet --python .venv/bin/python -e '.[dev]'
  else
    [ -x .venv/bin/python ] || python3 -m venv .venv
    .venv/bin/python -m pip install --quiet -e '.[dev]'
  fi
fi

status=0
.venv/bin/remix-studio install-skills "$@" || status=$?

if [ -z "$dry" ]; then
  echo
  .venv/bin/remix-studio doctor || status=$?
fi
exit "$status"
