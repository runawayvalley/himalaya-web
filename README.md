# himalaya-web

A lightweight, read-only web interface for [himalaya](https://github.com/pimalaya/himalaya) CLI email client. Designed for browser-based AI agents that need to read emails (e.g. verification codes) without terminal access.

## Features

- **Read-only** — no send, delete, or write operations exposed
- **Token auth** — via `?token=` query param or `Authorization: Bearer ***` header
- **Auto-generated secure token** — no need to fill token parameter; always cryptographically random
- **Token rotation** — view and rotate the token at runtime via `/api/token` with admin password
- **Token management webpage** — `/token` page to enter password and view/rotate token in browser
- **Base64 config** — supply himalaya config via `HIMALAYA_CONFIG_BASE64` env var (no local config file needed)
- **Timing-safe auth** — constant-time comparison for token and password
- **Search** — bare keywords search across subject/from/to/body; structured queries supported (`to X`, `from X`, `subject X and body Y`)
- **Clean message view** — `?body=1` strips headers for easy parsing
- **JSON opt-in** — `?format=json` for structured message output
- **Zero dependencies** — Python stdlib only (gunicorn optional for production)
- **Docker support** — Dockerfile included for PaaS/self-hosting

## Quick start

```bash
# Set admin password (enables token management)
export HIMALAYA_ADMIN_PASSWORD="your-secret-password"

# Run with gunicorn (recommended for production)
pip install gunicorn
gunicorn himalaya_web:app --bind 127.0.0.1:8877

# Or run with stdlib server (local use only)
python3 himalaya_web.py --port 8877

# Or use the start script
bash start.sh          # default port 8877
bash start.sh 9000     # custom port
```

## Docker

### Build

```bash
docker build -t himalaya-web .
```

### Run

```bash
docker run -p 8877:8877 \
  -e HIMALAYA_ADMIN_PASSWORD="your-secret-password" \
  -e HIMALAYA_CONFIG_BASE64="$(cat ~/.config/himalaya/config.toml | base64 -w0)" \
  himalaya-web
```

The auto-generated token is printed at startup — check `docker logs <container>`.
You can also retrieve it with your admin password:

```bash
curl -X POST "http://localhost:8877/api/token" \
  -H "Content-Type: application/json" \
  -d '{"password": "your-secret-password", "action": "view"}'
```

### Docker Compose

```yaml
services:
  himalaya-web:
    build: .
    ports:
      - "8877:8877"
    environment:
      HIMALAYA_ADMIN_PASSWORD: "your-secret-password"
      HIMALAYA_CONFIG_BASE64: "base64-encoded-himalaya-config"
    restart: unless-stopped
```

### Environment variables

| Variable | Description |
|---|---|
| `HIMALAYA_TOKEN` | Initial token (auto-generated if not set) |
| `HIMALAYA_ADMIN_PASSWORD` | Password to view/rotate token via `/api/token` and `/token` |
| `HIMALAYA_CONFIG_BASE64` | Base64-encoded himalaya config — decoded to a temp file and passed to himalaya via its `--config` flag (himalaya v2 ignores env vars for config lookup) |
| `HIMALAYA_BIN` | Path to himalaya binary (default: `himalaya`) |
| `HIMALAYA_ACCOUNT` | Default himalaya account to use |

## Token management

The token is auto-generated on startup using `secrets.token_urlsafe(24)` — always cryptographically random, no user input needed.

### Web UI

Navigate to `/token` in your browser:

```
http://localhost:8877/token
```

Enter your admin password to view or rotate the token. The page lets you copy the token with one click.

### API

```bash
# View current token
curl -X POST "http://localhost:8877/api/token" \
  -H "Content-Type: application/json" \
  -d '{"password": "your-secret-password", "action": "view"}'

# Rotate token (generates a new one, old token immediately invalidated)
curl -X POST "http://localhost:8877/api/token" \
  -H "Content-Type: application/json" \
  -d '{"password": "your-secret-password", "action": "rotate"}'
```

This lets you revoke a token you gave to an AI agent — just rotate it and the old one stops working.

## Endpoints

| Endpoint | Returns |
|---|---|
| `GET /` | HTML inbox with folder switcher + search |
| `GET /api` | API reference page (this doc) |
| `GET /api/envelopes` | JSON list of emails |
| `GET /api/message/<id>` | Message body (plain text). Add `&body=1` (no headers), `&format=json` |
| `GET /api/search?q=<query>` | JSON search results |
| `GET /api/folders` | JSON list of mailboxes |
| `GET /health` | `{"status":"ok"}` (no auth) |
| `GET /token` | Token management webpage |
| `POST /api/token` | View or rotate token (admin only, JSON body) |

Optional params: `&folder=Sent`, `&page=2`, `&page_size=10`

## Search DSL

Bare keywords search all fields automatically. For targeted queries:

```
?q=to user@example.com
?q=from noreply@service.com and subject verification
?q=body OTP or subject code
?q=date 2026-08-19
?q=flag seen
```

## Find a verification code

```
1. GET /api/search?token=...&q=verification → get email ID
2. GET /api/message/<id>?token=...&body=1 → read body, extract code
```

## Requirements

- Python 3.8+
- [himalaya](https://github.com/pimalaya/himalaya) CLI configured with an email account
- gunicorn (optional, for production deployment)

## Filter by recipient (catch-all / forwarded mailboxes)

If your mailbox receives forwarded emails for multiple addresses, filter by recipient:

```
/api/search?token=...&q=to user@example.com
/api/search?token=...&q=to user@example.com and subject verification
```
