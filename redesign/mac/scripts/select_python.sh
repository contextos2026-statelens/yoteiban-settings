#!/usr/bin/env bash
set -euo pipefail

DEST="${1:-$HOME/yoteiban-watch}"
if [[ -n "${YOTEIBAN_PYTHON:-}" ]]; then
  [[ -x "$YOTEIBAN_PYTHON" ]] || { echo 'YOTEIBAN_PYTHON is not executable' >&2; exit 2; }
  printf '%s\n' "$YOTEIBAN_PYTHON"
  exit 0
fi
SAVED="$DEST/state/python-path"
if [[ -r "$SAVED" ]]; then
  IFS= read -r PYTHON_BIN < "$SAVED" || true
  if [[ -n "${PYTHON_BIN:-}" && -x "$PYTHON_BIN" ]]; then
    printf '%s\n' "$PYTHON_BIN"
    exit 0
  fi
fi
if [[ -x "$DEST/venv/bin/python" ]]; then
  printf '%s\n' "$DEST/venv/bin/python"
  exit 0
fi
command -v python3
