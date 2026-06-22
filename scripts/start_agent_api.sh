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

export PYTHONPATH="$REPO_ROOT:$REPO_ROOT/agent_app${PYTHONPATH:+:$PYTHONPATH}"
export AGENT_API_PORT="${AGENT_API_PORT:-8000}"

cd "$REPO_ROOT/agent_app"
exec uvicorn app.main:app \
  --host "${AGENT_API_HOST:-0.0.0.0}" \
  --port "$AGENT_API_PORT"
