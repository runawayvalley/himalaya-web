#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-8877}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Kill any existing processes on the port
fuser -k "$PORT/tcp" 2>/dev/null || true
pkill -f "himalaya_web.py" 2>/dev/null || true
pkill -f "cloudflared tunnel" 2>/dev/null || true
sleep 1

# Generate token
TOKEN=$(python3 -c "import secrets; print('tok_' + secrets.token_urlsafe(24))")

# Start himalaya web server
echo "📧 Starting himalaya-web on :$PORT ..."
python3 "$SCRIPT_DIR/himalaya_web.py" --port "$PORT" --token "$TOKEN" &
SERVER_PID=$!

# Start cloudflare quick tunnel, capture stderr to a temp file for URL extraction
TUNNEL_LOG=$(mktemp)
cloudflared tunnel --url "http://localhost:$PORT" --no-autoupdate 2>"$TUNNEL_LOG" &
TUNNEL_PID=$!

# Wait for tunnel URL
echo "⏳ Waiting for tunnel URL..."
TUNNEL_URL=""
for i in $(seq 1 30); do
  sleep 1
  TUNNEL_URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1 || true)
  if [ -n "$TUNNEL_URL" ]; then
    break
  fi
done

# Cleanup temp file
rm -f "$TUNNEL_LOG"

echo ""
echo "============================================"
echo "  ✅ himalaya-web is running!"
echo "============================================"
echo ""
echo "  Local:  http://localhost:$PORT/?token=$TOKEN"
if [ -n "$TUNNEL_URL" ]; then
  echo "  Public: $TUNNEL_URL/?token=$TOKEN"
  echo ""
  echo "  API docs: $TUNNEL_URL/api?token=$TOKEN"
else
  echo "  ⚠️  Tunnel URL not found yet — check cloudflared output"
fi
echo ""
echo "  Token: $TOKEN"
echo "  Server PID:  $SERVER_PID"
echo "  Tunnel PID:  $TUNNEL_PID"
echo ""
echo "  Stop: kill $SERVER_PID $TUNNEL_PID"
echo "============================================"

# Wait for either to exit
wait -n "$SERVER_PID" "$TUNNEL_PID" 2>/dev/null || true
echo "Shutting down..."
kill "$SERVER_PID" "$TUNNEL_PID" 2>/dev/null || true
