#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-8877}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Set admin password if not already set
if [ -z "${HIMALAYA_ADMIN_PASSWORD:-}" ]; then
  echo "⚠️  HIMALAYA_ADMIN_PASSWORD not set. Token management via /api/token will be disabled."
  echo "   Set it to enable viewing/rotating the token at runtime."
fi

# Check if gunicorn is available
if command -v gunicorn &>/dev/null; then
  # Auto-size workers: (2 * CPU) + 1, unless overridden via env
  WORKERS="${GUNICORN_WORKERS:-${WEB_CONCURRENCY:-}}"
  if ! [[ "$WORKERS" =~ ^[0-9]+$ ]] || [ "$WORKERS" -lt 1 ]; then
    if command -v nproc &>/dev/null; then
      WORKERS=$((2 * $(nproc) + 1))
    else
      WORKERS=2
    fi
  fi
  echo "📧 Starting himalaya-web on :$PORT with gunicorn ($WORKERS workers)..."
  gunicorn himalaya_web:app --bind "0.0.0.0:$PORT" --workers "$WORKERS"
else
  echo "📧 Starting himalaya-web on :$PORT with Flask's dev server..."
  echo "   (Install gunicorn for production use: pip install gunicorn)"
  python3 "$SCRIPT_DIR/himalaya_web.py" --port "$PORT"
fi
