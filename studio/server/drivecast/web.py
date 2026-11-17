"""HTTP server: routes requests to api.py, serves the web panel, sets security headers."""
import hmac
import json
import logging
import mimetypes
import os
import re
import time
import traceback
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import api, db, security

log = logging.getLogger("drivecast.web")
WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
MAX_BODY = 256 * 1024
COOKIE = "dc_session"
CSP = ("default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'self' https://accounts.google.com")


class Req:
    def __init__(self, h, path, query, body):
        self.method, self.path, self.query = h.command, path, query
        self.headers = h.headers
        self.secure = (h.headers.get("X-Forwarded-Proto") == "https")
        fwd = h.headers.get("X-Forwarded-For")
        self.ip = (fwd.split(",")[0].strip() if fwd else h.client_address[0])
        self.is_local = h.client_address[0] in ("127.0.0.1", "::1") and not fwd
        self.raw = body
        self.user = None
        self.session_token = None
        self.set_cookie = None
        self.clear_cookie = False

    @property
    def json(self):
        if not self.raw:
            return {}
        try:
            v = json.loads(self.raw)
            return v if isinstance(v, dict) else {}
        except ValueError:
            raise api.HttpError(400, "Invalid JSON")

    @property
    def ws(self):
        return self.user["workspace_id"]


class Handler(BaseHTTPRequestHandler):
    server_version = "DriveCast"
    sys_version = ""

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    def do_GET(self):
        self._handle()

    def do_HEAD(self):
        self._handle(head=True)

    def do_POST(self):
        self._handle()

    def do_PATCH(self):
        self._handle()

    def do_PUT(self):
        self._handle()

    def do_DELETE(self):
        self._handle()

    def _send(self, status, body, ctype="application/json; charset=utf-8", headers=None, head=False, req=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, default=str).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", CSP)
        if req is not None and req.secure:
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        if ctype.startswith("application/json"):
            self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if req is not None and req.set_cookie:
            sec = "; Secure" if req.secure else ""
            self.send_header("Set-Cookie", f"{COOKIE}={req.set_cookie}; Path=/; HttpOnly; SameSite=Lax; Max-Age={30*86400}{sec}")
        if req is not None and req.clear_cookie:
            self.send_header("Set-Cookie", f"{COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _handle(self, head=False):
        u = urlsplit(self.path)
        path = u.path
        query = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > (80 * 1024 * 1024 if path == "/api/restore" else MAX_BODY):
                return self._send(413, {"error": "Request too large"})
            body = self.rfile.read(n) if n else b""
            req = Req(self, path, query, body)
            if path.startswith("/media/"):
                return self._media(path[7:], head)
            if not (path.startswith("/api/") or path.startswith("/connect/") or path.startswith("/oauth/")
                    or path.startswith("/local/") or path in ("/healthz", "/phone")):
                return self._static(path, head)
            for method, rx, auth, fn in api.ROUTES:
                m = rx.match(path)
                if not m or method != ("GET" if self.command == "HEAD" else self.command):
                    continue
                self._authenticate(req)
                if auth == "local":
                    from . import config
                    tok = self.headers.get("X-Local-Token") or ""
                    if not req.is_local or not config.LOCAL_TOKEN or not hmac.compare_digest(tok, config.LOCAL_TOKEN):
                        raise api.HttpError(403, "Local only")
                if auth in ("user", "owner"):
                    if not req.user:
                        raise api.HttpError(401, "Please sign in")
                    if auth == "owner" and req.user["role"] != "owner":
                        raise api.HttpError(403, "Owner only")
                if self.command in ("POST", "PATCH", "PUT", "DELETE") and path.startswith("/api/") \
                        and self.headers.get("X-DC") != "1":
                    raise api.HttpError(403, "Missing request header")
                out = fn(req, *m.groups())
                if isinstance(out, api.FileOut):
                    return self._send(200, out.data, out.ctype, {"Content-Disposition": f'attachment; filename="{out.name}"',
                                                                  "Cache-Control": "no-store"}, head, req)
                if isinstance(out, tuple):
                    status, payload = out
                    if isinstance(payload, dict) and "Location" in payload and status in (301, 302):
                        return self._send(status, b"", "text/plain", {"Location": payload["Location"]}, head, req)
                    return self._send(status, payload, "text/html; charset=utf-8", None, head, req)
                return self._send(200, out, head=head, req=req)
            raise api.HttpError(404, "Not found")
        except api.HttpError as e:
            return self._send(e.status, {"error": e.message}, head=head)
        except Exception as e:  # never let one request kill the server
            log.error("request %s failed: %s\n%s", path, e, traceback.format_exc())
            db.event("error", "web", f"Panel request failed: {path}: {e}")
            return self._send(500, {"error": "Something went wrong on the server. It was logged."}, head=head)

    def _media(self, tok, head):
        """Short-lived public file links that Instagram fetches posts from. Range requests supported."""
        from . import igpost
        full = igpost.resolve_link(tok)
        if not full:
            return self._send(404, {"error": "Not found"})
        size = os.path.getsize(full)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range") or ""
        m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head:
            return
        with open(full, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)

    def _authenticate(self, req):
        c = SimpleCookie(self.headers.get("Cookie") or "")
        if COOKIE not in c:
            return
        tok = c[COOKIE].value
        row = db.one("""SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
                        WHERE s.token_hash=? AND s.expires_at>? AND u.status='active'""",
                     (security.token_hash(tok), time.time()))
        if row:
            req.user, req.session_token = row, tok

    def _static(self, path, head):
        if path in ("/", "") or not os.path.splitext(path)[1]:
            path = "/index.html"
        full = os.path.normpath(os.path.join(WEB_DIR, path.lstrip("/")))
        if not full.startswith(WEB_DIR) or not os.path.isfile(full):
            return self._send(404, {"error": "Not found"})
        with open(full, "rb") as f:
            data = f.read()
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        cache = "no-cache" if full.endswith(".html") else "public, max-age=300"
        return self._send(200, data, ctype, {"Cache-Control": cache}, head)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(host, port):
    httpd = Server((host, port), Handler)
    log.info("DriveCast panel on http://%s:%s", host, port)
    return httpd
