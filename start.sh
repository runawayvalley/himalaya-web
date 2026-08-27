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
  echo "📧 Starting himalaya-web on :$PORT with gunicorn..."
  gunicorn himalaya_web:app --bind "0.0.0.0:$PORT" --workers 2
else
  echo "📧 Starting himalaya-web on :$PORT with stdlib server..."
  echo "   (Install gunicorn for production use: pip install gunicorn)"
  python3 "$SCRIPT_DIR/himalaya_web.py" --port "$PORT"
fi
