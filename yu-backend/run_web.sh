#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export AIASSISTANCE_ROOT="$ROOT_DIR"

if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  exec "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/web/app.py"
fi

exec python3 "$ROOT_DIR/web/app.py"
