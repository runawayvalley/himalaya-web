#!/usr/bin/env python3
"""
Himalaya Web — read-only email viewer for browser agents.

Usage:
    python3 himalaya_web.py [--port 8877] [--token <token>] [--bind 0.0.0.0]

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

Auth: pass ?token=<TOKEN> query param, or Authorization: Bearer <TOKEN> header.
"""

import argparse
import html
import json
import os
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

HIMALAYA = os.environ.get("HIMALAYA_BIN", "himalaya")
DEFAULT_ACCOUNT = os.environ.get("HIMALAYA_ACCOUNT", "")


def run_himalaya(*args, account=None):
    """Run a himalaya command and return stdout."""
    cmd = [HIMALAYA]
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


def json_response(data, status=200):
    return status, "application/json", json.dumps(data, ensure_ascii=False)


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

<h2>Find a verification code</h2>
<div class="note">
<strong>Step 1:</strong> <a href="/api/search?token={t}&amp;q=verification">/api/search?token=...&amp;q=verification</a> — get latest email ID<br>
<strong>Step 2:</strong> <code>/api/message/&lt;id&gt;?token=...&amp;body=1</code> — read body, extract code
</div>

<h2>Quick start for agents</h2>
<pre>GET /api/envelopes?token={t}&amp;page_size=5
→ [{{"id":"176", "subject":"...", "from":[{{"name":"Sail","email":"..."}}], "date":"..."}}]

GET /api/message/176?token={t}
→ full email body text here</pre>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    token = None  # set by main()

    def log_message(self, format, *args):
        # Quiet logs
        pass

    def check_auth(self):
        if not self.token:
            return True  # no token configured = open access (local only)

        # Check Authorization header
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and auth[7:] == self.token:
            return True

        # Check query param
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if qs.get("token", [None])[0] == self.token:
            return True

        return False

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, content, status=200):
        body = content.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        # Health check — no auth
        if path == "/health":
            self.send_json({"status": "ok"})
            return

        # Auth check
        if not self.check_auth():
            self.send_json({"error": "Unauthorized. Pass ?token=... or Authorization: Bearer ..."}, 401)
            return

        folder = qs.get("folder", ["INBOX"])[0]
        page = int(qs.get("page", ["1"])[0])

        if path == "/":
            q = qs.get("q", [""])[0]
            tok = qs.get("token", [""])[0] or (self.token or "")
            self.send_html(html_inbox(folder, page, query=q, token=tok))

        elif path == "/api":
            tok = qs.get("token", [""])[0] or (self.token or "")
            self.send_html(html_docs(token=tok))

        elif path == "/api/envelopes":
            data, err = get_envelopes(folder=folder, page=page, page_size=int(qs.get("page_size", ["20"])[0]))
            if err:
                self.send_json({"error": err}, 500)
            else:
                self.send_json(data)

        elif path.startswith("/api/message/"):
            msg_id = path.split("/")[-1]
            tok = qs.get("token", [""])[0] or (self.token or "")
            as_json = qs.get("format", [""])[0].lower() == "json"
            body_only = qs.get("body", [""])[0] == "1"
            data, err = get_message(msg_id, folder=folder, as_json=as_json, body_only=body_only)
            if err:
                self.send_json({"error": err}, 500)
            else:
                if as_json:
                    self.send_json(json.loads(data))
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    body = (data or "").encode()
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

        elif path == "/api/search":
            q = qs.get("q", [""])[0]
            if not q:
                self.send_json({"error": "Missing ?q= parameter"}, 400)
                return
            data, err = search_envelopes(q, folder=folder)
            if err:
                self.send_json({"error": err}, 500)
            else:
                self.send_json(data)

        elif path == "/api/folders":
            data, err = get_folders()
            if err:
                self.send_json({"error": err}, 500)
            else:
                self.send_json(data)

        else:
            self.send_json({"error": "Not found"}, 404)


def main():
    parser = argparse.ArgumentParser(description="Himalaya Web — read-only email viewer")
    parser.add_argument("--port", type=int, default=8877, help="Port to listen on")
    parser.add_argument("--bind", default="127.0.0.1", help="Bind address (use 0.0.0.0 for external)")
    parser.add_argument("--token", default="", help="Auth token (empty = no auth, local only)")
    args = parser.parse_args()

    Handler.token = args.token or None

    server = HTTPServer((args.bind, args.port), Handler)
    print(f"📧 Himalaya Web running on http://{args.bind}:{args.port}")
    if Handler.token:
        print(f"   Token auth enabled. URL: http://{args.bind}:{args.port}/?token={Handler.token}")
    else:
        print("   ⚠️  No token set — open access (local use only)")
    print("   Endpoints: / | /api/envelopes | /api/message/<id> | /api/search?q= | /api/folders")
    print("   Press Ctrl+C to stop.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
