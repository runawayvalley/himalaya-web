#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-8877}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Set admin password if not already set
if [ -z "${HIMALAYA_ADMIN_PASSWORD:-}" ]; then
  echo "⚠️  HIMALAYA_ADMIN_PASSWORD not set. Token management via /api/token will be disabled."
  echo "   Set it to enable viewing/rotating the token at runtime."
fi

# Start himalaya web server (token auto-generated if HIMALAYA_TOKEN not set)
echo "📧 Starting himalaya-web on :$PORT ..."
python3 "$SCRIPT_DIR/himalaya_web.py" --port "$PORT"
