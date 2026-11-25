"""ローカルビューアの HTTP サーバー。

- 127.0.0.1 にだけ bind する。Host ヘッダーを確認し（DNS リバインディング対策）、
  API は起動ごとのトークン（X-Token ヘッダー）を要求する。
- 静的ファイルはパッケージ同梱のものだけを返す。
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import secrets
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..recognize.base import RecognizerUnavailable
from ..snip import SnipError
from .hub import Hub
from .session import SessionError, ViewerSession, valid_id

STATIC = Path(__file__).resolve().parent / "static"
STATIC_FILES = {"index.html", "app.js", "style.css", "theme.js", "i18n.js", "icon.svg",
                "vendor/katex/katex.min.js", "vendor/katex/katex.min.css"}
_VENDOR_FONT = re.compile(r"^vendor/katex/fonts/KaTeX_[A-Za-z0-9_-]+\.woff2$")


def make_handler(hub: Hub, token: str, port_ref: dict):
    class Handler(BaseHTTPRequestHandler):
        server_version = "alice-stem-reader"

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

        def _body(self, limit: int = 1_000_000) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > limit:
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
            hub.touch()
            u = urlparse(self.path)
            path = u.path
            q = parse_qs(u.query)
            try:
                if method == "GET" and not path.startswith("/api/"):
                    name = "index.html" if path == "/" else path.lstrip("/")
                    if name not in STATIC_FILES and not _VENDOR_FONT.match(name):
                        return self._json(404, {"error": "not found"})
                    try:
                        body = (STATIC / name).read_bytes()
                    except OSError:
                        return self._json(404, {"error": "not found"})
                    ctype = "font/woff2" if name.endswith(".woff2") else (mimetypes.guess_type(name)[0] or "application/octet-stream")
                    if name.endswith(".html") or name.endswith(".js") or name.endswith(".css"):
                        ctype += "; charset=utf-8"
                    # 外部への通信はしない（KaTeX も同梱）。資料の内容がこの PC の外に出ない
                    csp = ("default-src 'self'; img-src 'self' data: blob:; script-src 'self'; "
                           "style-src 'self' 'unsafe-inline'; font-src 'self'; connect-src 'self'; frame-ancestors 'none'")
                    return self._send(200, body, ctype, {"Content-Security-Policy": csp})
                if not self._api_ok():
                    return self._json(401, {"error": "token required"})
                # ---- コレクション（フォルダの階層）と、開く PDF の切り替え ----
                if method == "GET" and path == "/api/tree":
                    return self._json(200, hub.tree(refresh=q.get("refresh", ["0"])[0] == "1"))
                if method == "GET" and path == "/api/ping":
                    return self._json(200, {"ok": True, "pid": os.getpid(), "idle_s": round(hub.idle_s(), 1)})
                if method == "POST" and path == "/api/addroot":
                    b = self._body()
                    return self._json(200, {"root": hub.add_root(b.get("path"))})
                if method == "POST" and path == "/api/pick":
                    b = self._body()
                    return self._json(200, hub.pick(b.get("kind")))
                if method == "POST" and path == "/api/quit":
                    self._json(200, {"ok": True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                if method == "POST" and path == "/api/addfiles":
                    b = self._body()
                    return self._json(200, {"ids": hub.add_paths(b.get("paths"))})
                if method == "POST" and path == "/api/open":
                    b = self._body()
                    hub.open(b.get("id"), auto=(b["auto"] if isinstance(b.get("auto"), bool) else None))
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
                if method == "POST" and path == "/api/engine":
                    b = self._body()
                    return self._json(200, hub.set_engine(b.get("name"), confirm_external=b.get("confirm_external") is True))
                if method == "POST" and path.startswith("/api/vfs/"):  # ビューア専用のフォルダ構造
                    b = self._body()
                    op = path[len("/api/vfs/"):]
                    if op == "mkdir":
                        return self._json(200, hub.vmkdir(b.get("parent"), b.get("name")))
                    if op == "rename":
                        return self._json(200, hub.vrename(b.get("target"), b.get("name")))
                    if op == "delete":
                        return self._json(200, hub.vdelete(b.get("target")))
                    if op == "add":
                        return self._json(200, hub.vadd(b.get("folder"), b.get("targets")))
                    if op == "remove":
                        return self._json(200, hub.vremove(b.get("items")))
                    if op == "move":
                        return self._json(200, hub.vmove(b.get("items"), b.get("folders"), b.get("dest")))
                    return self._json(404, {"error": "not found"})
                # ---- Snip（画面・画像の一部を読む。資料を開いていなくても使える）
                if method == "GET" and path == "/api/formats":
                    from ..docformats import describe_support

                    return self._json(200, describe_support())
                if method == "GET" and path == "/api/snips":
                    lim = int(q.get("limit", ["200"])[0])
                    return self._json(200, {"items": hub.snips().list(lim), "engine": hub.backend_name})
                if method == "POST" and path == "/api/snip/image":
                    b = self._body(limit=40_000_000)  # base64 の画像（25MB まで）
                    return self._json(200, hub.snip_image(b.get("image"), mode=b.get("mode", "auto"),
                                                          source=b.get("source", "image"), name=b.get("name")))
                if method == "POST" and path == "/api/snip/screen":
                    b = self._body()
                    return self._json(200, hub.snip_screen(mode=b.get("mode", "auto"), copy=b.get("copy") is not False,
                                                           delim=b.get("delim", "paren"), single=b.get("single") is True))
                if method == "POST" and path == "/api/snips/export":
                    b = self._body()
                    text, ctype, fname = hub.snips().export(b.get("fmt"), b.get("ids"), delim=b.get("delim", "paren"))
                    return self._send(200, text.encode("utf-8"), ctype + "; charset=utf-8",
                                      {"Content-Disposition": f'attachment; filename="{fname}"'})
                m = re.fullmatch(r"/api/snips/([A-Za-z0-9-]+)(\.png)?", path)
                if m and method == "GET" and m.group(2):
                    return self._send(200, hub.snips().image(m.group(1)), "image/png")
                if m and method == "POST" and not m.group(2):
                    b = self._body()
                    return self._json(200, hub.snips().update(m.group(1), latex=b.get("latex"), note=b.get("note")))
                if m and method == "DELETE" and not m.group(2):
                    return self._json(200, hub.snips().delete(m.group(1)))
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
                if method == "POST" and path == "/api/snip/region":
                    b = self._body()
                    return self._json(200, hub.snip_region(b.get("page"), b.get("view_rect"), mode=b.get("mode", "auto")))
                if method == "GET" and path == "/api/notes":
                    return self._json(200, session.notes_get())
                if method == "POST" and path == "/api/notes":
                    b = self._body()
                    return self._json(200, session.notes_set(b.get("scope"), b.get("text"), b.get("page")))
                if method == "POST" and path == "/api/text":
                    b = self._body()  # {"pages": [0 始まり], "force": true}: 文章（日本語など）を読む
                    return self._json(200, session.start_text(pages=b.get("pages"), force=bool(b.get("force"))))
                m = re.fullmatch(r"/api/page/(\d+)\.png", path)
                if method == "GET" and m:
                    dpi = int(q.get("dpi", ["110"])[0])
                    return self._send(200, session.page_png(int(m.group(1)), dpi), "image/png")
                if method == "POST" and path == "/api/formulas/delete_many":
                    b = self._body()
                    return self._json(200, session.delete_many(b.get("page"), b.get("scope")))
                if method == "POST" and path == "/api/formulas/restore":
                    b = self._body()
                    return self._json(200, session.restore(b.get("formulas")))
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
                if method == "POST" and path == "/api/export/text":
                    b = self._body()
                    text, ctype, fname = session.export_text(b.get("fmt", "md"), only_confirmed=bool(b.get("only_confirmed")))
                    from urllib.parse import quote

                    return self._send(200, text.encode("utf-8"), ctype + "; charset=utf-8",
                                      {"Content-Disposition": f"attachment; filename=\"export.{b.get('fmt', 'md')}\"; filename*=UTF-8''{quote(fname)}"})
                if method == "POST" and path == "/api/export":
                    b = self._body()
                    mode = b.get("mode", "invisible")
                    if mode not in ("invisible", "actualtext"):
                        raise SessionError("mode が不正")
                    return self._json(200, session.export(mode=mode, only_confirmed=bool(b.get("only_confirmed")),
                                                          delim=b.get("delim", "paren")))
                return self._json(404, {"error": "not found"})
            except SessionError as e:
                return self._json(e.status, {"error": str(e)})
            except SnipError as e:
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


def serve(paths, formulas_path=None, port=0, open_browser=True, backend="none", workdir=None, auto=False,
          readonly=False, verify=False, allow_external=False, state_file=None, idle_exit_min=None,
          roots_file=None) -> int:
    """paths は PDF またはフォルダ（複数可）。フォルダなら左のコレクションに階層で並ぶ。"""
    paths = [paths] if isinstance(paths, (str, Path)) else list(paths)
    if roots_file:  # 前に画面から追加したフォルダも、並べる
        try:
            saved = json.loads(Path(roots_file).read_text(encoding="utf-8"))
            paths += [p for p in saved if isinstance(p, str) and Path(p).is_dir() and p not in [str(x) for x in paths]]
        except (OSError, ValueError):
            pass
    try:
        hub = Hub(paths, backend=backend, auto_on_open=False, readonly=readonly, verify=verify, allow_external=allow_external, roots_file=roots_file)
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
        if single:  # 1 つの PDF を指定したときは、そのまま開く（auto=True のときだけ、開いた時点で全ページを認識する）
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
    if state_file:  # アプリ（.app）から再利用するための、接続情報。自分だけが読める
        _write_state(Path(state_file), {"pid": os.getpid(), "port": httpd.server_address[1], "token": token, "url": url})
    if idle_exit_min:  # 画面からの通信が一定時間なければ終了する（ブラウザを閉じたまま残らないように）
        def _watch():
            while True:
                time.sleep(min(15.0, max(0.2, idle_exit_min * 15)))
                if hub.idle_s() > idle_exit_min * 60:
                    print(f"{idle_exit_min} 分間操作がないので終了します", file=sys.stderr, flush=True)
                    httpd.shutdown()
                    return

        threading.Thread(target=_watch, daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        hub.close()
        if state_file:
            try:
                st = json.loads(Path(state_file).read_text(encoding="utf-8"))
                if st.get("pid") == os.getpid():
                    Path(state_file).unlink()
            except (OSError, ValueError):
                pass
    return 0


def _write_state(path: Path, obj: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)
