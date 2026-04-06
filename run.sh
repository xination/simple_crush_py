#!/usr/bin/env bash
set -euo pipefail

export CRUSH_PY_CALLER_CWD="$(pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

python -m crush_py "$@"
