# himalaya-web

A lightweight, read-only web interface for [himalaya](https://github.com/pimalaya/himalaya) CLI email client. Designed for browser-based AI agents that need to read emails (e.g. verification codes) without terminal access.

## Features

- **Read-only** — no send, delete, or write operations exposed
- **Token auth** — via `?token=` query param or `Authorization: Bearer` header
- **Search** — bare keywords search across subject/from/to/body; structured queries supported (`to X`, `from X`, `subject X and body Y`)
- **Clean message view** — `?body=1` strips headers for easy parsing
- **JSON opt-in** — `?format=json` for structured message output
- **Zero dependencies** — Python stdlib only

## Quick start

```bash
# Generate a token
TOKEN=$(python3 -c "import secrets; print('tok_' + secrets.token_urlsafe(24))")

# Run
python3 himalaya_web.py --port 8877 --token $TOKEN

# Expose via Cloudflare quick tunnel (optional)
cloudflared tunnel --url http://localhost:8877 --no-autoupdate
```

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

## One-command start

Generates a random token, starts the server, and opens a Cloudflare quick tunnel:

```bash
bash start.sh          # default port 8877
bash start.sh 9000     # custom port
```

Prints local + public URLs with the token pre-filled.
