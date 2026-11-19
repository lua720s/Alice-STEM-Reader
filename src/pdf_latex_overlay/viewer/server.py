"""ローカルビューアの HTTP サーバー。

- 127.0.0.1 にだけ bind する。Host ヘッダーを確認し（DNS リバインディング対策）、
  API は起動ごとのトークン（X-Token ヘッダー）を要求する。
- 静的ファイルはパッケージ同梱のものだけを返す。
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import secrets
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .session import SessionError, ViewerSession, valid_id

STATIC = Path(__file__).resolve().parent / "static"
STATIC_FILES = {"index.html", "app.js", "style.css"}


def make_handler(session: ViewerSession, token: str, port_ref: dict):
    class Handler(BaseHTTPRequestHandler):
        server_version = "pdf-latex-overlay-viewer"

        def log_message(self, fmt, *args):  # 標準エラーへ簡潔に
            sys.stderr.write("viewer: " + (fmt % args) + "\n")

        # ---- 共通 ----
        def _host_ok(self) -> bool:
            host = self.headers.get("Host", "")
            port = port_ref["port"]
            return host in (f"127.0.0.1:{port}", f"localhost:{port}")

        def _send(self, status: int, body: bytes, ctype: str, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj):
            self._send(status, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1_000_000:
                raise SessionError("リクエストが大きすぎる", 413)
            if n == 0:
                return {}
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                raise SessionError("Content-Type は application/json", 415)
            try:
                d = json.loads(self.rfile.read(n))
            except json.JSONDecodeError:
                raise SessionError("JSON が不正")
            if not isinstance(d, dict):
                raise SessionError("JSON オブジェクトが必要")
            return d

        def _api_ok(self) -> bool:
            return secrets.compare_digest(self.headers.get("X-Token", ""), token)

        def _dispatch(self, method: str):
            if not self._host_ok():
                return self._json(403, {"error": "host not allowed"})
            u = urlparse(self.path)
            path = u.path
            q = parse_qs(u.query)
            try:
                if method == "GET" and not path.startswith("/api/"):
                    name = "index.html" if path == "/" else path.lstrip("/")
                    if name not in STATIC_FILES:
                        return self._json(404, {"error": "not found"})
                    body = (STATIC / name).read_bytes()
                    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
                    if name.endswith(".html") or name.endswith(".js") or name.endswith(".css"):
                        ctype += "; charset=utf-8"
                    csp = ("default-src 'self'; img-src 'self' data: blob:;"
                           "script-src 'self' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                           "font-src https://cdn.jsdelivr.net; connect-src 'self'; frame-ancestors 'none'")
                    return self._send(200, body, ctype, {"Content-Security-Policy": csp})
                if not self._api_ok():
                    return self._json(401, {"error": "token required"})
                if method == "GET" and path == "/api/doc":
                    return self._json(200, session.doc_info())
                if method == "GET" and path == "/api/formulas":
                    return self._json(200, session.list())
                if method == "GET" and path == "/api/auto":
                    return self._json(200, session.auto_status())
                if method == "POST" and path == "/api/auto":
                    return self._json(200, session.start_auto())
                m = re.fullmatch(r"/api/page/(\d+)\.png", path)
                if method == "GET" and m:
                    dpi = int(q.get("dpi", ["110"])[0])
                    return self._send(200, session.page_png(int(m.group(1)), dpi), "image/png")
                m = re.fullmatch(r"/api/formulas/([^/]+)(/recognize|/crop\.png)?", path)
                if m and not valid_id(m.group(1)):
                    return self._json(400, {"error": "invalid id"})
                if method == "GET" and m and m.group(2) == "/crop.png":
                    return self._send(200, session.crop(m.group(1)), "image/png")
                if method == "POST" and path == "/api/formulas":
                    b = self._body()
                    return self._json(200, session.create(int(b.get("page", -1)), b.get("view_rect"),
                                                          latex=b.get("latex"), recognize=bool(b.get("recognize"))))
                if method == "POST" and m and m.group(2) == "/recognize":
                    return self._json(200, session.recognize(m.group(1)))
                if method == "POST" and m and m.group(2) is None:
                    b = self._body()
                    return self._json(200, session.update(m.group(1), latex=b.get("latex"), status=b.get("status"),
                                                          view_rect=b.get("view_rect"), reason=b.get("reason")))
                if method == "DELETE" and m and m.group(2) is None:
                    return self._json(200, session.delete(m.group(1)))
                if method == "POST" and path == "/api/export":
                    b = self._body()
                    mode = b.get("mode", "invisible")
                    if mode not in ("invisible", "actualtext"):
                        raise SessionError("mode が不正")
                    return self._json(200, session.export(mode=mode, only_confirmed=bool(b.get("only_confirmed"))))
                return self._json(404, {"error": "not found"})
            except SessionError as e:
                return self._json(e.status, {"error": str(e)})
            except (ValueError, TypeError) as e:
                return self._json(400, {"error": str(e)})

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_DELETE(self):
            self._dispatch("DELETE")

    return Handler


def default_workdir(input_path) -> Path:
    h = hashlib.sha256(str(Path(input_path).resolve()).encode()).hexdigest()[:10]
    return Path.cwd() / "runs" / f"viewer-{Path(input_path).stem}-{h}"


def make_server(session: ViewerSession, port: int = 0):
    token = secrets.token_urlsafe(24)
    port_ref = {"port": port}
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(session, token, port_ref))
    port_ref["port"] = httpd.server_address[1]
    return httpd, token


def serve(input_path, formulas_path=None, port=0, open_browser=True, backend="none", workdir=None, auto=True) -> int:
    session = ViewerSession(input_path, workdir or default_workdir(input_path), formulas_path, backend=backend)
    httpd, token = make_server(session, port)
    if auto and backend not in (None, "", "none"):
        session.start_auto()  # 開いた時点で全ページの数式を検出・認識する（裏で進め、画面は先に使える）
    url = f"http://127.0.0.1:{httpd.server_address[1]}/#token={token}"
    print(f"ビューア: {url}\n作業領域: {session.workdir}\n終了は Ctrl+C", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        session.close()
    return 0
