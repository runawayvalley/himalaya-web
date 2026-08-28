#!/usr/bin/env python3
"""
Himalaya Web — read-only email viewer for browser agents.

Usage:
    gunicorn himalaya_web:app --bind 127.0.0.1:8877
    python3 himalaya_web.py  (falls back to stdlib for local use)

Environment variables:
    HIMALAYA_TOKEN — initial token (auto-generated if not set)
    HIMALAYA_ADMIN_PASSWORD — password to view/rotate token via /api/token
    HIMALAYA_CONFIG_BASE64 — base64-encoded himalaya config (decoded to a temp file
                          and passed to himalaya via --config; himalaya v2 ignores
                          env vars for config lookup. Uses default config if not set)

Endpoints (all read-only):
    GET /                          — HTML inbox view (human/agent friendly)
    GET /api/envelopes             — JSON list of recent emails
    GET /api/envelopes?folder=Sent — JSON list from specific folder
    GET /api/message/<id>          — JSON: full message body
    GET /api/message/<id>?folder=X — JSON: full message body from folder
    GET /api/search?q=<query>      — JSON: search results
    GET /api/search?q=<query>&folder=X — JSON: search in folder
    GET /api/folders               — JSON: list folders
    GET /health                    — 200 OK (no auth needed)
    POST /api/token                — view or rotate token (admin only, JSON body)
    GET /token                     — token management webpage

Auth: pass ?token=<TOKEN> query param, or Authorization: Bearer *** header.
"""

import argparse
import base64
import html
import json
import os
import secrets
import subprocess
import sys
import tempfile
from urllib.parse import urlparse, parse_qs

HIMALAYA = os.environ.get("HIMALAYA_BIN", "himalaya")
DEFAULT_ACCOUNT = os.environ.get("HIMALAYA_ACCOUNT", "")
ADMIN_PASSWORD = os.environ.get("HIMALAYA_ADMIN_PASSWORD", "")

# Module-level token state — can be rotated at runtime
_current_token = None

# Token is ALSO persisted to a file so all gunicorn workers (separate
# processes) see the same token, and rotation propagates to all of them.
TOKEN_FILE = os.path.join(tempfile.gettempdir(), "himalaya_web_token")

# Path to the decoded himalaya config file (set by init at import time)
CONFIG_PATH = None

_init_done = False

# ─── Config loading ─────────────────────────────────────────────────────────


def setup_config():
    """Decode base64 config from env and write to temp file if provided.

    Returns the path, or None. The path must be passed to himalaya via the
    global `--config` flag — himalaya v2 does NOT read a HIMALAYA_CONFIG
    environment variable.
    """
    config_b64 = os.environ.get("HIMALAYA_CONFIG_BASE64")
    if config_b64:
        try:
            config_data = base64.b64decode(config_b64)
            # Create temp file that persists for process lifetime
            tmp = tempfile.NamedTemporaryFile(
                prefix="himalaya_config_", suffix=".toml", delete=False
            )
            tmp.write(config_data)
            tmp.close()
            return tmp.name
        except Exception as e:
            print(f"⚠️  Failed to decode HIMALAYA_CONFIG_BASE64: {e}")
    return None


def init_app():
    """Initialize config path and token.

    Runs at module import time (not just in main()) because gunicorn imports
    this module without ever calling main(). Idempotent.
    """
    global _current_token, CONFIG_PATH, _init_done
    if _init_done:
        return _current_token
    _init_done = True

    CONFIG_PATH = setup_config()
    if CONFIG_PATH:
        os.environ["HIMALAYA_CONFIG"] = CONFIG_PATH

    # Token precedence: env var > shared file (other worker created it) > new
    _current_token = os.environ.get("HIMALAYA_TOKEN")
    if _current_token:
        try:
            _write_token_file(_current_token)
        except OSError:
            pass
    else:
        _current_token = get_current_token()
        if _current_token is None:
            _current_token = generate_token()
            try:
                _write_token_file(_current_token)
            except OSError as e:
                print(f"⚠️  Could not persist token file: {e}")

    print("📧 Himalaya Web initialized.", flush=True)
    if CONFIG_PATH:
        print(f"   Config: decoded from HIMALAYA_CONFIG_BASE64 → {CONFIG_PATH}", flush=True)
    else:
        print("   Config: no HIMALAYA_CONFIG_BASE64 — himalaya will use its default config paths", flush=True)
    print(f"   Token: {_current_token}  (view/rotate via POST /api/token)", flush=True)
    return _current_token


def generate_token():
    """Generate a secure random token."""
    return "tok_" + secrets.token_urlsafe(24)


def _write_token_file(token):
    """Atomically write the token file with owner-only permissions."""
    fd = os.open(TOKEN_FILE + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, token.encode())
    finally:
        os.close(fd)
    os.replace(TOKEN_FILE + ".tmp", TOKEN_FILE)


def get_current_token():
    """Return the live token, reading the shared file so all workers agree."""
    try:
        with open(TOKEN_FILE) as f:
            token = f.read().strip()
            if token:
                return token
    except OSError:
        pass
    return _current_token


def set_current_token(token):
    """Rotate the token in memory and in the shared file (all workers)."""
    global _current_token
    _current_token = token
    try:
        _write_token_file(token)
    except OSError as e:
        print(f"⚠️  Could not persist rotated token: {e}")


# Initialize on import so gunicorn workers get config + token too
init_app()


def run_himalaya(*args, account=None):
    """Run a himalaya command and return stdout."""
    cmd = [HIMALAYA]
    # himalaya v2 only reads config from --config flag or default paths;
    # there is no HIMALAYA_CONFIG env var support.
    if CONFIG_PATH:
        cmd += ["--config", CONFIG_PATH]
    acct = account or DEFAULT_ACCOUNT
    if acct:
        cmd += ["--account", acct]
    cmd += list(args)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            # himalaya v2 puts JSON error details in stdout
            return result.stdout, result.stderr.strip() or result.stdout.strip() or "himalaya error"
        return result.stdout, None
    except subprocess.TimeoutExpired:
        return None, "himalaya timed out"
    except FileNotFoundError:
        return None, "himalaya not found"


def get_envelopes(folder="INBOX", page=1, page_size=20):
    out, err = run_himalaya(
        "envelope", "list",
        "-m", folder,
        "--page", str(page),
        "--page-size", str(page_size),
        "--json",
    )
    if err:
        return None, err
    try:
        data = json.loads(out)
        return data.get("envelopes", data), None
    except json.JSONDecodeError:
        return None, f"Failed to parse himalaya output: {out[:200]}"


def get_message(msg_id, folder="INBOX", as_json=False, body_only=False):
    args = ["message", "read", str(msg_id), "-m", folder]
    if as_json:
        args.append("--json")
    out, err = run_himalaya(*args)
    if err:
        return None, err
    if as_json:
        try:
            data = json.loads(out)
            return json.dumps(data, indent=2, ensure_ascii=False), None
        except json.JSONDecodeError:
            return out, None
    if body_only and out:
        # Strip everything before the first blank line (headers)
        parts = out.split("\n\n", 1)
        if len(parts) > 1:
            return parts[1].strip(), None
    return out, None


def search_envelopes(query, folder="INBOX"):
    # himalaya search DSL requires structured queries like "subject X", "from X"
    # If bare keyword, search across subject, from, to, and body
    q = query.strip()
    prefixes = ("subject ", "from ", "to ", "body ", "date ", "after ", "flag ", "not ", "(")
    if not any(q.startswith(p) for p in prefixes):
        q = f"(subject {q} or from {q} or to {q} or body {q})"
    out, err = run_himalaya(
        "envelope", "search",
        "-m", folder,
        "--json",
        "--", q,
    )
    if err:
        # Check if error output is actually JSON with an error field
        try:
            err_data = json.loads(err if err.startswith("{") else out or "")
            return None, err_data.get("error", err)
        except (json.JSONDecodeError, TypeError):
            return None, err
    if not out:
        return [], None
    try:
        data = json.loads(out)
        return data.get("envelopes", data), None
    except json.JSONDecodeError:
        return None, f"Failed to parse: {out[:200]}"


def get_folders():
    out, err = run_himalaya("mailbox", "list", "--json")
    if err:
        return None, err
    try:
        data = json.loads(out)
        return data.get("mailboxes", data), None
    except json.JSONDecodeError:
        return None, f"Failed to parse: {out[:200]}"


# ─── HTML templates ──────────────────────────────────────────────────────────


def html_inbox(folder="INBOX", page=1, query="", token=""):
    """Render a clean HTML inbox page."""
    if query:
        envelopes, err = search_envelopes(query, folder=folder)
    else:
        envelopes, err = get_envelopes(folder=folder, page=page)
    folders_data, _ = get_folders()

    folder_opts = ""
    if folders_data:
        for f in folders_data:
            name = f.get("name", f.get("id", "?"))
            sel = "selected" if name == folder else ""
            esc = html.escape(name)
            folder_opts += f'<option value="{esc}" {sel}>{esc}</option>\n'

    rows = ""
    if envelopes:
        for env in envelopes:
            eid = html.escape(str(env.get("id", "")))
            # v2: from is an array of {name, email}
            from_list = env.get("from", [])
            if isinstance(from_list, list) and from_list:
                fr = html.escape(from_list[0].get("name", "") or from_list[0].get("email", "?"))
            elif isinstance(from_list, dict):
                fr = html.escape(from_list.get("name", "") or from_list.get("addr", "?"))
            else:
                fr = html.escape(str(from_list))
            subj = html.escape(str(env.get("subject", "(no subject)")))
            date = html.escape(str(env.get("date", "")))
            flags = env.get("flags", [])
            unread = "●" if "Seen" not in str(flags) else ""
            rows += f"""<tr>
  <td style="padding:4px 8px">{unread}</td>
  <td style="padding:4px 8px"><a href="/api/message/{eid}?folder={html.escape(folder)}&token={html.escape(token)}" style="color:#58a6ff">{subj}</a></td>
  <td style="padding:4px 8px;color:#8b949e">{fr}</td>
  <td style="padding:4px 8px;color:#8b949e;font-size:0.85em">{date}</td>
</tr>\n"""
    else:
        rows = f'<tr><td colspan="4" style="padding:16px;color:#8b949e">No emails found. {html.escape(err or "")}</td></tr>'

    prev_page = max(1, page - 1)
    next_page = page + 1

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Himalaya Web</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #0d1117; color: #c9d1d9; margin: 0; padding: 16px; }}
  a {{ color: #58a6ff; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  table {{ width: 100%; border-collapse: collapse; }}
  tr:hover {{ background: #161b22; }}
  .header {{ display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }}
  select, input[type=text] {{ background: #161b22; color: #c9d1d9; border: 1px solid #30363d;
    border-radius: 6px; padding: 6px 10px; }}
  .pagination {{ margin-top: 12px; display: flex; gap: 8px; }}
  .pagination a {{ padding: 4px 12px; border: 1px solid #30363d; border-radius: 4px; }}
  .search {{ display: flex; gap: 8px; align-items: center; }}
</style>
</head><body>
<div class="header">
  <h2 style="margin:0">📧 Himalaya Web</h2>
  <form class="search" method="get" action="/">
    <select name="folder" onchange="this.form.submit()">{folder_opts}</select>
    <input type="text" name="q" placeholder="Search emails…" value="">
    <input type="hidden" name="token" value="{html.escape(token)}">
    <button type="submit" style="background:#238636;color:#fff;border:none;padding:6px 14px;border-radius:6px;cursor:pointer">Search</button>
  </form>
</div>
<table>
<thead><tr>
  <th style="text-align:left;padding:4px 8px;width:20px"></th>
  <th style="text-align:left;padding:4px 8px">Subject</th>
  <th style="text-align:left;padding:4px 8px">From</th>
  <th style="text-align:left;padding:4px 8px">Date</th>
</tr></thead>
<tbody>{rows}</tbody>
</table>
<div class="pagination">
  <a href="/?folder={html.escape(folder)}&page={prev_page}&token={html.escape(token)}">← Prev</a>
  <span style="color:#8b949e;padding:4px">Page {page}</span>
  <a href="/?folder={html.escape(folder)}&page={next_page}&token={html.escape(token)}">Next →</a>
</div>
</body></html>"""


def html_message(msg_id, folder="INBOX", token=""):
    """Render a single message as clean HTML."""
    body, err = get_message(msg_id, folder)
    content = html.escape(body or err or "No content")
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Message {html.escape(str(msg_id))}</title>
<style>
  body {{ font-family: monospace; background: #0d1117; color: #c9d1d9;
         margin: 0; padding: 20px; max-width: 900px; }}
  pre {{ white-space: pre-wrap; word-wrap: break-word; line-height: 1.5; }}
  a {{ color: #58a6ff; }}
  .back {{ margin-bottom: 16px; }}
</style>
</head><body>
<div class="back"><a href="/?folder={html.escape(folder)}&token={html.escape(token)}">← Back to inbox</a></div>
<pre>{content}</pre>
</body></html>"""


def html_docs(token=""):
    """API reference page for browser agents."""
    t = html.escape(token)
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Himalaya Web — API Reference</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #0d1117; color: #c9d1d9; margin: 0; padding: 24px; max-width: 800px; }}
  a {{ color: #58a6ff; text-decoration: none; }}
  code {{ background: #161b22; padding: 2px 6px; border-radius: 4px; font-size: 0.9em; }}
  pre {{ background: #161b22; padding: 12px; border-radius: 8px; overflow-x: auto; }}
  table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
  th, td {{ text-align: left; padding: 6px 12px; border-bottom: 1px solid #21262d; }}
  th {{ color: #8b949e; }}
  h1 {{ margin-bottom: 4px; }}
  h2 {{ margin-top: 24px; border-bottom: 1px solid #21262d; padding-bottom: 6px; }}
  .note {{ background: #161b22; border-left: 3px solid #58a6ff; padding: 10px 14px; border-radius: 4px; margin: 12px 0; }}
</style>
</head><body>
<h1>📧 Himalaya Web API</h1>
<p>Read-only email access. Pass <code>token={t}</code> on every request.</p>

<h2>Endpoints</h2>
<table>
<tr><th>Endpoint</th><th>Returns</th></tr>
<tr><td><a href="/?token={t}">GET /</a></td><td>HTML inbox</td></tr>
<tr><td><a href="/api/envelopes?token={t}">GET /api/envelopes</a></td><td>JSON list of emails</td></tr>
<tr><td><code>GET /api/message/&lt;id&gt;</code></td><td>Message body. Add <code>&amp;body=1</code> (no headers), <code>&amp;format=json</code></td></tr>
<tr><td><a href="/api/search?token={t}&amp;q=verification">GET /api/search?q=</a></td><td>JSON search results</td></tr>
<tr><td><a href="/api/folders?token={t}">GET /api/folders</a></td><td>JSON list of mailboxes</td></tr>
<tr><td><a href="/health">GET /health</a></td><td>No auth needed</td></tr>
</table>

<p>Optional: <code>&amp;folder=Sent</code> (default: <code>INBOX</code>), <code>&amp;page=2</code>, <code>&amp;page_size=10</code></p>

<h2>Search DSL</h2>
<p>Bare keywords search all fields (subject, from, to, body). For targeted queries:</p>
<table>
<tr><th>Query</th><th>Filters by</th></tr>
<tr><td><code>to user@example.com</code></td><td>Recipient — useful for catch-all/forwarded mailboxes</td></tr>
<tr><td><code>from noreply@service.com</code></td><td>Sender</td></tr>
<tr><td><code>subject verification</code></td><td>Subject line</td></tr>
<tr><td><code>body OTP</code></td><td>Body text</td></tr>
<tr><td><code>date 2026-08-19</code></td><td>Exact date</td></tr>
<tr><td><code>to user@x.com and subject code</code></td><td>Combine with <code>and</code> / <code>or</code></td></tr>
</table>

<h2>Find a verification code</h2>
<div class="note">
<strong>Step 1:</strong> <a href="/api/search?token={t}&amp;q=verification">/api/search?token=...&amp;q=verification</a> — get latest email ID<br>
<strong>Step 2:</strong> <code>/api/message/&lt;id&gt;?token=...&amp;body=1</code> — read body, extract code<br><br>
<strong>Filter by recipient</strong> (catch-all / forwarded mailboxes):<br>
<code>/api/search?token=...&amp;q=to user@example.com and subject verification</code>
</div>

<h2>Quick start for agents</h2>
<pre>GET /api/envelopes?token={t}&amp;page_size=5
→ [{{"id":"176", "subject":"...", "from":[{{"name":"Sail","email":"..."}}], "date":"..."}}]

GET /api/message/176?token={t}
→ full email body text here</pre>
</body></html>"""


def html_token_page():
    """Token management page — enter password to view/rotate token."""
    return """<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Himalaya Web — Token Management</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #0d1117; color: #c9d1d9; margin: 0; padding: 24px;
         display: flex; justify-content: center; align-items: center; min-height: 100vh; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 12px;
          padding: 32px; width: 100%; max-width: 420px; }
  h1 { margin: 0 0 8px; font-size: 1.3em; }
  p { color: #8b949e; margin: 0 0 20px; font-size: 0.9em; }
  label { display: block; margin-bottom: 6px; font-size: 0.85em; color: #8b949e; }
  input[type=password] { width: 100%; padding: 10px 12px; margin-bottom: 16px;
    background: #0d1117; color: #c9d1d9; border: 1px solid #30363d;
    border-radius: 6px; box-sizing: border-box; font-size: 1em; }
  input[type=password]:focus { outline: none; border-color: #58a6ff; }
  .buttons { display: flex; gap: 8px; }
  button { flex: 1; padding: 10px 16px; border: none; border-radius: 6px;
           font-size: 0.95em; cursor: pointer; font-weight: 500; }
  .btn-primary { background: #238636; color: #fff; }
  .btn-primary:hover { background: #2ea043; }
  .btn-danger { background: #da3633; color: #fff; }
  .btn-danger:hover { background: #f85149; }
  .result { margin-top: 16px; padding: 12px; border-radius: 6px; font-size: 0.9em;
            display: none; word-break: break-all; }
  .result.success { display: block; background: #1b4332; border: 1px solid #2ea043; color: #56d364; }
  .result.error { display: block; background: #3d1f1f; border: 1px solid #f85149; color: #f85149; }
  .token-display { font-family: monospace; font-size: 0.85em; margin-top: 8px;
                   padding: 8px; background: #0d1117; border-radius: 4px; cursor: pointer; }
  .token-display:hover { background: #21262d; }
  .hint { font-size: 0.8em; color: #8b949e; margin-top: 4px; }
</style>
</head><body>
<div class="card">
  <h1>🔑 Token Management</h1>
  <p>Enter admin password to view or rotate the API token.</p>

  <label for="password">Admin Password</label>
  <input type="password" id="password" placeholder="Enter admin password..." autofocus>

  <div class="buttons">
    <button class="btn-primary" onclick="viewToken()">View Token</button>
    <button class="btn-danger" onclick="rotateToken()">Rotate Token</button>
  </div>

  <div id="result" class="result"></div>
</div>

<script>
async function viewToken() {
  const pw = document.getElementById('password').value;
  const res = document.getElementById('result');
  try {
    const r = await fetch('/api/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pw, action: 'view' })
    });
    const data = await r.json();
    if (r.ok) {
      res.className = 'result success';
      res.innerHTML = 'Current token:<div class="token-display" onclick="copyToken(this)" title="Click to copy">' + data.token + '</div><div class="hint">Click token to copy</div>';
    } else {
      res.className = 'result error';
      res.textContent = data.error || 'Failed to get token';
    }
  } catch (e) {
    res.className = 'result error';
    res.textContent = 'Request failed: ' + e.message;
  }
}

async function rotateToken() {
  const pw = document.getElementById('password').value;
  const res = document.getElementById('result');
  if (!confirm('Rotate token? The old token will stop working immediately.')) return;
  try {
    const r = await fetch('/api/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pw, action: 'rotate' })
    });
    const data = await r.json();
    if (r.ok) {
      res.className = 'result success';
      res.innerHTML = 'New token (old one revoked):<div class="token-display" onclick="copyToken(this)" title="Click to copy">' + data.token + '</div><div class="hint">Click token to copy</div>';
    } else {
      res.className = 'result error';
      res.textContent = data.error || 'Failed to rotate token';
    }
  } catch (e) {
    res.className = 'result error';
    res.textContent = 'Request failed: ' + e.message;
  }
}

function copyToken(el) {
  navigator.clipboard.writeText(el.textContent);
  el.style.background = '#2ea043';
  setTimeout(() => el.style.background = '', 500);
}

document.getElementById('password').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') viewToken();
});
</script>
</body></html>"""



# ─── Auth helpers ────────────────────────────────────────────────────────────


def check_auth(environ, qs):
    """
    Check token auth using timing-safe comparison.
    Returns (is_valid, failure_type) where failure_type is 'token' or None.
    """
    if not _current_token and not os.path.exists(TOKEN_FILE):
        return True, None  # no token configured = open access

    live_token = get_current_token()
    if not live_token:
        return True, None

    # Check Authorization header
    auth = environ.get('HTTP_AUTHORIZATION', '')
    if auth.startswith('Bearer '):
        provided = auth[7:]
        if secrets.compare_digest(provided, live_token):
            return True, None

    # Check query param
    token_param = qs.get('token', [None])[0]
    if token_param is not None and secrets.compare_digest(token_param, live_token):
        return True, None

    return False, 'token'


def check_admin_password(body):
    """
    Check admin password from JSON body using timing-safe comparison.
    Password is never accepted from query params.
    """
    if not ADMIN_PASSWORD:
        return False
    if not body:
        return False
    try:
        data = json.loads(body)
        pw = data.get('password', '')
        return secrets.compare_digest(pw, ADMIN_PASSWORD)
    except json.JSONDecodeError:
        return False


# ─── WSGI application ────────────────────────────────────────────────────────


def app(environ, start_response):
    """WSGI application entry point for gunicorn."""
    method = environ['REQUEST_METHOD']
    path = environ['PATH_INFO'].rstrip('/') or '/'
    qs = parse_qs(environ.get('QUERY_STRING', ''))
    content_length = int(environ.get('CONTENT_LENGTH', 0))
    body = environ['wsgi.input'].read(content_length) if content_length else b''

    # Helper to send JSON response
    def send_json(data, status=200, extra_headers=None):
        body_out = json.dumps(data, ensure_ascii=False).encode()
        headers = [('Content-Type', 'application/json; charset=utf-8'),
                   ('Content-Length', str(len(body_out)))]
        if extra_headers:
            headers.extend(extra_headers)
        start_response(f'{status} _', headers)
        return [body_out]

    def send_html(content, status=200):
        body_out = content.encode()
        headers = [('Content-Type', 'text/html; charset=utf-8'),
                   ('Content-Length', str(len(body_out)))]
        start_response(f'{status} _', headers)
        return [body_out]

    # Health check — no auth
    if path == '/health':
        return send_json({'status': 'ok'})

    # Token management webpage — no auth needed (password entered in page)
    if path == '/token':
        return send_html(html_token_page())

    # Token management API — POST only, password in JSON body
    if path == '/api/token':
        if method != 'POST':
            return send_json({'error': 'Method not allowed. Use POST.'}, 405)

        if not check_admin_password(body):
            return send_json({'error': 'Unauthorized.'}, 401)

        global _current_token
        data = json.loads(body) if body else {}
        if data.get('action') == 'rotate':
            set_current_token(generate_token())

        return send_json({'token': get_current_token()})

    # Auth check
    is_valid, failure_type = check_auth(environ, qs)
    if not is_valid:
        return send_json({'error': 'Unauthorized. Pass ?token=... or Authorization: Bearer ***'}, 401)

    folder = qs.get('folder', ['INBOX'])[0]
    page = int(qs.get('page', ['1'])[0])

    if path == '/':
        q = qs.get('q', [''])[0]
        tok = qs.get('token', [''])[0] or get_current_token()
        return send_html(html_inbox(folder, page, query=q, token=tok))

    elif path == '/api':
        tok = qs.get('token', [''])[0] or get_current_token()
        return send_html(html_docs(token=tok))

    elif path == '/api/envelopes':
        data, err = get_envelopes(folder=folder, page=page,
                                   page_size=int(qs.get('page_size', ['20'])[0]))
        if err:
            return send_json({'error': err}, 500)
        return send_json(data)

    elif path.startswith('/api/message/'):
        msg_id = path.split('/')[-1]
        tok = qs.get('token', [''])[0] or get_current_token()
        as_json = qs.get('format', [''])[0].lower() == 'json'
        body_only = qs.get('body', [''])[0] == '1'
        data, err = get_message(msg_id, folder=folder, as_json=as_json, body_only=body_only)
        if err:
            return send_json({'error': err}, 500)
        if as_json:
            return send_json(json.loads(data))
        body_out = (data or '').encode()
        headers = [('Content-Type', 'text/plain; charset=utf-8'),
                   ('Content-Length', str(len(body_out)))]
        start_response('200 _', headers)
        return [body_out]

    elif path == '/api/search':
        q = qs.get('q', [''])[0]
        if not q:
            return send_json({'error': 'Missing ?q= parameter'}, 400)
        data, err = search_envelopes(q, folder=folder)
        if err:
            return send_json({'error': err}, 500)
        return send_json(data)

    elif path == '/api/folders':
        data, err = get_folders()
        if err:
            return send_json({'error': err}, 500)
        return send_json(data)

    return send_json({'error': 'Not found'}, 404)


# ─── Entry point ─────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Himalaya Web — read-only email viewer")
    parser.add_argument("--port", type=int, default=8877, help="Port to listen on")
    parser.add_argument("--bind", default="127.0.0.1", help="Bind address (use 0.0.0.0 for external)")
    parser.add_argument("--gunicorn", action="store_true", help="Use gunicorn instead of stdlib server")
    args = parser.parse_args()

    # Init is idempotent — config/token are already set at import time
    init_app()

    bind_addr = f"{args.bind}:{args.port}"
    print(f"📧 Himalaya Web running on http://{bind_addr}")
    print(f"   Token auth enabled. URL: http://{bind_addr}/?token={_current_token}")
    if CONFIG_PATH:
        print(f"   Config: loaded from HIMALAYA_CONFIG_BASE64 ({CONFIG_PATH})")
    if ADMIN_PASSWORD:
        print(f"   Token management: POST /api/token (JSON body)")
    else:
        print("   ⚠️  No HIMALAYA_ADMIN_PASSWORD set — /api/token disabled")
    print("   Endpoints: / | /api/envelopes | /api/message/<id> | /api/search?q= | /api/folders | /api/token | /token")
    print("   Press Ctrl+C to stop.\n")

    if args.gunicorn:
        # Use gunicorn programmatically
        from gunicorn.app.base import BaseApplication

        class GunicornApp(BaseApplication):
            def __init__(self, app, options=None):
                self.options = options or {}
                self.application = app
                super().__init__()

            def load_config(self):
                for key, value in self.options.items():
                    if key in self.cfg.settings and value is not None:
                        self.cfg.set(key.lower(), value)

            def load(self):
                return self.application

        options = {
            'bind': bind_addr,
            'workers': 2,
            'timeout': 120,
        }
        GunicornApp(app, options).run()
    else:
        # Fallback to stdlib for local use
        from http.server import HTTPServer

        class WSGIHandler:
            """Simple WSGI-to-Bridge for stdlib HTTPServer."""
            def __init__(self, app):
                self.app = app

            def __call__(self, environ, start_response):
                return self.app(environ, start_response)

        server = HTTPServer((args.bind, args.port), _make_stdlib_handler())
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down.")
            server.shutdown()

    # This is unreachable but kept for clarity
    try:
        pass
    except KeyboardInterrupt:
        print("\nShutting down.")


def _make_stdlib_handler():
    """Create a BaseHTTPRequestHandler subclass that bridges to the WSGI app."""
    from http.server import BaseHTTPRequestHandler
    app_ref = app

    class WSGIBridgeHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self._handle()

        def do_POST(self):
            self._handle()

        def _handle(self):
            from io import BytesIO
            environ = {
                'REQUEST_METHOD': self.command,
                'PATH_INFO': self.path.split('?')[0],
                'QUERY_STRING': self.path.split('?', 1)[1] if '?' in self.path else '',
                'CONTENT_TYPE': self.headers.get('Content-Type', ''),
                'CONTENT_LENGTH': self.headers.get('Content-Length', '0'),
                'HTTP_AUTHORIZATION': self.headers.get('Authorization', ''),
                'HTTP_X_FORWARDED_FOR': self.headers.get('X-Forwarded-For', ''),
                'REMOTE_ADDR': self.client_address[0],
                'SERVER_NAME': self.server.server_name,
                'SERVER_PORT': str(self.server.server_port),
                'wsgi.input': BytesIO(self.rfile.read(int(self.headers.get('Content-Length', 0)))),
                'wsgi.errors': sys.stderr,
            }

            def start_response(status, headers):
                self.send_response(int(status.split(' ')[0]))
                for h, v in headers:
                    self.send_header(h, v)
                self.end_headers()

            result = app_ref(environ, start_response)
            for chunk in result:
                self.wfile.write(chunk)

        def log_message(self, format, *args):
            pass

    return WSGIBridgeHandler


if __name__ == "__main__":
    main()
