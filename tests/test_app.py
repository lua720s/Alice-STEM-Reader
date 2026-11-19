"""macOS の .app（Finder の「このアプリケーションで開く」）と、常駐ビューアの再利用。2026-11-19 のユーザー指示。"""

import json
import os
import plistlib
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

import pytest

from pdf_latex_overlay import applaunch
from pdf_latex_overlay.viewer.hub import Hub
from pdf_latex_overlay.viewer.server import make_server, serve
from pdf_latex_overlay.viewer.session import SessionError


def _pdf(fx, d, name):
    p = d / name
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(fx["scan"], p)
    return p


def _req(base, token, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"X-Token": token}
    if data:
        h["Content-Type"] = "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, data=data, headers=h, method=method), timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_hub_add_paths(fx, tmp_path):
    a = _pdf(fx, tmp_path / "in", "a.pdf")
    other = _pdf(fx, tmp_path / "elsewhere", "b.pdf")
    h = Hub([a.parent], library=tmp_path / "lib")
    assert h.add_paths([str(a)]) == [h.id_of(a)]  # すでに一覧にある PDF は、ルートを増やさない
    assert len(h.roots) == 1
    ids = h.add_paths([str(other)])
    assert ids == [h.id_of(other)] and len(h.roots) == 2 and ids[0] in h.index
    (tmp_path / "x.txt").write_text("x")
    for bad in ([str(tmp_path / "x.txt")], [str(tmp_path / "none.pdf")], [], "a.pdf", [1]):
        with pytest.raises(SessionError):
            h.add_paths(bad)
    h.close()


def test_http_ping_addfiles_quit(fx, tmp_path):
    a = _pdf(fx, tmp_path / "in", "a.pdf")
    other = _pdf(fx, tmp_path / "elsewhere", "b.pdf")
    h = Hub([a.parent], library=tmp_path / "lib")
    httpd, token = make_server(h, 0)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        assert _req(base, "wrong", "GET", "/api/ping")[0] == 401  # トークンが要る
        st, info = _req(base, token, "GET", "/api/ping")
        assert st == 200 and info["pid"] == os.getpid() and info["idle_s"] < 5
        st, r = _req(base, token, "POST", "/api/addfiles", {"paths": [str(other)]})
        assert st == 200 and len(r["ids"]) == 1
        st, _ = _req(base, token, "POST", "/api/open", {"id": r["ids"][0], "auto": False})
        assert st == 200 and h.current_id == r["ids"][0]
        assert _req(base, token, "POST", "/api/addfiles", {"paths": ["/etc/passwd"]})[0] == 400
        assert _req(base, token, "POST", "/api/quit", {})[0] == 200
        th.join(timeout=10)
        assert not th.is_alive()  # 止まった
    finally:
        httpd.server_close()
        h.close()


def test_serve_writes_private_state_file_and_exits_when_idle(fx, tmp_path, monkeypatch):
    monkeypatch.setenv("PLO_LIBRARY", str(tmp_path / "lib"))
    a = _pdf(fx, tmp_path / "in", "a.pdf")
    state = tmp_path / "s" / "server.json"
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("rc", serve([str(a.parent)], open_browser=False, backend="none",
                                                                      state_file=state, idle_exit_min=0.02)), daemon=True)
    t.start()
    for _ in range(100):
        if state.exists():
            break
        time.sleep(0.05)
    st = json.loads(state.read_text())
    assert st["pid"] == os.getpid() and st["token"] and oct(state.stat().st_mode & 0o777) == "0o600"
    t.join(timeout=20)
    assert not t.is_alive() and out["rc"] == 0  # 操作がなく、自分で終了した
    assert not state.exists()  # 状態ファイルも消えた


def test_app_open_reuses_the_running_viewer_and_stops(fx, tmp_path, monkeypatch):
    monkeypatch.setenv("PLO_APP_DIR", str(tmp_path / "support"))
    monkeypatch.setenv("PLO_APP_LOG", str(tmp_path / "app.log"))
    monkeypatch.setenv("PLO_LIBRARY", str(tmp_path / "lib"))
    a = _pdf(fx, tmp_path / "in", "a.pdf")
    b = _pdf(fx, tmp_path / "other", "b.pdf")
    opened = []
    try:
        r1 = applaunch.app_open(files=[str(a)], backend="none", opener=opened.append)
        assert r1["fresh"] and r1["opened"] and len(opened) == 1  # 新しく起動したので、ブラウザを開く
        r2 = applaunch.app_open(files=[str(b)], backend="none", opener=opened.append)
        assert not r2["fresh"] and r2["pid"] == r1["pid"] and r2["opened"] != r1["opened"]  # 同じビューアに加える
        assert len(opened) == 1  # すでに画面が開いている（直前に通信があった）ので、新しいタブは開かない
        st = applaunch.running_state()
        _, tree = _req(f"http://127.0.0.1:{st['port']}", st["token"], "GET", "/api/tree")[0], None
        assert applaunch.state_path().stat().st_mode & 0o077 == 0  # 自分だけが読める
        assert applaunch.last_folder() is None
    finally:
        assert applaunch.app_stop() in (True, False)
    for _ in range(100):
        if applaunch.running_state() is None:
            break
        time.sleep(0.1)
    assert applaunch.running_state() is None and not applaunch.state_path().exists()


def test_app_open_rejects_bad_input(tmp_path, monkeypatch):
    monkeypatch.setenv("PLO_APP_DIR", str(tmp_path / "support"))
    with pytest.raises(ValueError):
        applaunch.app_open(files=[str(tmp_path / "none.pdf")], backend="none", opener=lambda u: None)
    with pytest.raises(ValueError):
        applaunch.app_open(folder=str(tmp_path / "nofolder"), backend="none", opener=lambda u: None)
    with pytest.raises(ValueError):
        applaunch.app_open(backend="none", opener=lambda u: None)  # 開くものがない


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("osacompile"), reason="macOS のみ")
def test_build_app_has_open_with_pdf_and_is_signed(tmp_path):
    from pdf_latex_overlay.appbuild import BUNDLE_ID, build_app

    app = build_app(tmp_path, python="/path with space/python", register=False)
    assert app.name == "PDF LaTeX Overlay.app"
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == BUNDLE_ID
    types = {t["LSItemContentTypes"][0]: t for t in info["CFBundleDocumentTypes"]}
    assert types["com.adobe.pdf"]["LSHandlerRank"] == "Alternate"  # 既定のPDFアプリは奪わない
    assert types["com.adobe.pdf"]["CFBundleTypeRole"] == "Viewer"
    assert subprocess.run(["codesign", "--verify", "--deep", str(app)], capture_output=True).returncode == 0
    assert (app / "Contents" / "Resources" / "Scripts" / "main.scpt").exists()


def test_launch_dialog_offers_pdf_and_folder():
    from pdf_latex_overlay.appbuild import applescript

    src = applescript("/x/python", "/x/log")
    assert 'choose file of type {"com.adobe.pdf"}' in src and "multiple selections allowed" in src  # PDFを直接選べる
    assert "choose folder" in src and '"PDFを選ぶ"' in src and '"フォルダを選ぶ"' in src
    assert "(" not in src.split("buttons", 1)[1].split("default button", 1)[0]  # 括弧が混ざっていない（f-string の波括弧の取り違え）
    assert "app-open --folder" not in src and '" --folder "' in src
