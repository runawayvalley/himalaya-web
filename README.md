# himalaya-web

A lightweight, read-only web interface for [himalaya](https://github.com/pimalaya/himalaya) CLI email client. Designed for browser-based AI agents that need to read emails (e.g. verification codes) without terminal access.

## Features

- **Read-only** — no send, delete, or write operations exposed
- **Token auth** — via `?token=` query param or `Authorization: Bearer ***` header
- **Auto-generated secure token** — no need to fill token parameter; always cryptographically random
- **Token rotation** — view and rotate the token at runtime via `/api/token` with admin password
- **Token management webpage** — `/token` page to enter password and view/rotate token in browser
- **Search** — bare keywords search across subject/from/to/body; structured queries supported (`to X`, `from X`, `subject X and body Y`)
- **Clean message view** — `?body=1` strips headers for easy parsing
- **JSON opt-in** — `?format=json` for structured message output
- **Zero dependencies** — Python stdlib only

## Quick start

```bash
# Set admin password (enables token management)
export HIMALAYA_ADMIN_PASSWORD="your-secret-password"

# Run (token auto-generated)
python3 himalaya_web.py --port 8877

# Or use the start script
bash start.sh          # default port 8877
bash start.sh 9000     # custom port
```

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
curl "http://localhost:8877/api/token?password=your-secret-password"

# Rotate token (generates a new one, old token immediately invalidated)
curl -X POST "http://localhost:8877/api/token?password=your-secret-password"
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
| `GET /api/token?password=...` | View current token (admin only) |
| `POST /api/token?password=...` | Rotate token (admin only) |

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

## Filter by recipient (catch-all / forwarded mailboxes)

If your mailbox receives forwarded emails for multiple addresses, filter by recipient:

```
/api/search?token=...&q=to user@example.com
/api/search?token=...&q=to user@example.com and subject verification
```

## Environment variables

| Variable | Description |
|---|---|
| `HIMALAYA_TOKEN` | Initial token (auto-generated if not set) |
| `HIMALAYA_ADMIN_PASSWORD` | Password to view/rotate token via `/api/token` and `/token` |
| `HIMALAYA_BIN` | Path to himalaya binary (default: `himalaya`) |
| `HIMALAYA_ACCOUNT` | Default himalaya account to use |
