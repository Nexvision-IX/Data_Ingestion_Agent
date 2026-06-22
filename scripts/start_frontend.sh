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
export FRONTEND_PORT="${FRONTEND_PORT:-8501}"

cd "$REPO_ROOT"
exec streamlit run app.py \
  --server.address "${FRONTEND_HOST:-0.0.0.0}" \
  --server.port "$FRONTEND_PORT" \
  --server.headless true
