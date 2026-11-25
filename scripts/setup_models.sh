#!/usr/bin/env bash
# Install the recognition models (macOS / Linux). On Windows run:  .venv\Scripts\python -m alice_stem_reader setup-models
#   scripts/setup_models.sh [--tiny] [--no-paddle]
set -euo pipefail
cd "$(dirname "$0")/.."
PY=".venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3.12 || command -v python3)"
exec "$PY" -m alice_stem_reader setup-models "$@"
