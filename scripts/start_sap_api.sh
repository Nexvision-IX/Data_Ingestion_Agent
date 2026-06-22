#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${VENV_DIR:-$REPO_ROOT/.venv}"

mkdir -p "$REPO_ROOT/logs"

if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
  echo "Python virtual environment not found: $VENV_DIR" >&2
  exit 1
fi

set -a
if [[ -f "$REPO_ROOT/.env" ]]; then
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
fi
set +a

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export SAP_API_PORT="${SAP_API_PORT:-8001}"

cd "$REPO_ROOT"
exec uvicorn mock_api.main_api:app \
  --host "${SAP_API_HOST:-0.0.0.0}" \
  --port "$SAP_API_PORT"
