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
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..recognize.base import RecognizerUnavailable
from .hub import Hub
from .session import SessionError, ViewerSession, valid_id

STATIC = Path(__file__).resolve().parent / "static"
STATIC_FILES = {"index.html", "app.js", "style.css", "theme.js"}


def make_handler(hub: Hub, token: str, port_ref: dict):
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
                # ---- コレクション（フォルダの階層）と、開く PDF の切り替え ----
                if method == "GET" and path == "/api/tree":
                    return self._json(200, hub.tree(refresh=q.get("refresh", ["0"])[0] == "1"))
                if method == "POST" and path == "/api/open":
                    b = self._body()
                    hub.open(b.get("id"))
                    return self._json(200, {"id": hub.current_id})
                # ---- ファイル操作（フォルダ作成・名前の変更・移動・ゴミ箱へ）と、OCR のジョブ ----
                if method == "POST" and path == "/api/fs/mkdir":
                    b = self._body()
                    return self._json(200, hub.mkdir(b.get("parent"), b.get("name")))
                if method == "POST" and path == "/api/fs/rename":
                    b = self._body()
                    return self._json(200, hub.rename(b.get("target"), b.get("name")))
                if method == "POST" and path == "/api/fs/move":
                    b = self._body()
                    return self._json(200, hub.move(b.get("targets"), b.get("dest")))
                if method == "POST" and path == "/api/fs/trash":
                    b = self._body()
                    return self._json(200, hub.trash(b.get("targets")))
                if method == "POST" and path == "/api/ocr":
                    b = self._body()
                    return self._json(200, hub.ocr_start(b.get("targets")))
                if method == "GET" and path == "/api/ocr":
                    return self._json(200, hub.ocr_status() or {"state": "idle"})
                if method == "POST" and path == "/api/ocr/cancel":
                    return self._json(200, hub.ocr_cancel() or {"state": "idle"})
                session = hub.require()  # 以降は、いま開いている PDF への操作
                if method == "GET" and path == "/api/doc":
                    return self._json(200, dict(session.doc_info(), id=hub.current_id))
                if method == "GET" and path == "/api/formulas":
                    return self._json(200, session.list())
                if method == "GET" and path == "/api/auto":
                    return self._json(200, session.auto_status())
                if method == "POST" and path == "/api/auto":
                    b = self._body()  # {"pages": [0 始まりのページ番号], "force": true}（省略すると全ページ）
                    return self._json(200, session.start_auto(pages=b.get("pages"), force=bool(b.get("force"))))
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
            except (BrokenPipeError, ConnectionResetError):
                return  # ブラウザ側が先に閉じた（ページ移動など）。応答する相手がいない
            except Exception as e:  # 想定外でも接続を切らず、原因を返す（接続を切ると、ブラウザでは Failed to fetch になる）
                sys.stderr.write("viewer: 想定外のエラー\n" + traceback.format_exc())
                try:
                    return self._json(500, {"error": f"内部エラー: {type(e).__name__}: {e}"})
                except OSError:
                    return

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_DELETE(self):
            self._dispatch("DELETE")

    return Handler


def default_workdir(input_path) -> Path:
    """PDF の中身のハッシュで引くライブラリの作業領域（prepare で事前処理した結果をそのまま使う）。"""
    from ..prepare import entry_dir

    return entry_dir(input_path)[0]


from ..prepare import library_root  # noqa: E402


class _Server(ThreadingHTTPServer):
    request_queue_size = 128  # 既定の 5 だと、ページ画像を一度に多く読み込んだとき接続が拒否されうる
    daemon_threads = True


def make_server(target, port: int = 0):
    """target は Hub、または 1 つの ViewerSession（単体利用・試験用）。"""
    hub = target if isinstance(target, Hub) else Hub.from_session(target)
    token = secrets.token_urlsafe(24)
    port_ref = {"port": port}
    httpd = _Server(("127.0.0.1", port), make_handler(hub, token, port_ref))
    port_ref["port"] = httpd.server_address[1]
    return httpd, token


def serve(paths, formulas_path=None, port=0, open_browser=True, backend="none", workdir=None, auto=True,
          readonly=False, verify=False) -> int:
    """paths は PDF またはフォルダ（複数可）。フォルダなら左のコレクションに階層で並ぶ。"""
    paths = [paths] if isinstance(paths, (str, Path)) else list(paths)
    try:
        hub = Hub(paths, backend=backend, auto_on_open=False, readonly=readonly, verify=verify)
    except RecognizerUnavailable as e:  # 検証用のモデルがない
        print(str(e), file=sys.stderr)
        return 8
    except SessionError as e:
        print(str(e), file=sys.stderr)
        return 2
    single = len(hub.roots) == 1 and hub.roots[0].is_file()
    if (formulas_path or workdir) and not single:
        print("--formulas と --workdir は、PDF を1つだけ指定したときに使える", file=sys.stderr)
        return 2
    httpd, token = make_server(hub, port)
    try:
        if single:  # 1 つの PDF を指定したときは、そのまま開く（開いた時点で全ページを認識する）
            hub.scan(force=True)
            hub.open(hub.id_of(hub.roots[0]), formulas_path=formulas_path, workdir=workdir,
                     auto=bool(auto and backend not in (None, "", "none")))
    except SessionError as e:
        print(str(e), file=sys.stderr)
        httpd.server_close()
        hub.close()
        return 3
    url = f"http://127.0.0.1:{httpd.server_address[1]}/#token={token}"
    where = hub.session.workdir if hub.session else library_root()
    print(f"ビューア: {url}\n作業領域: {where}\n終了は Ctrl+C", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        hub.close()
    return 0
