"""コレクション（フォルダの階層）、PDF の切り替え、テーマ用の静的ファイル（2026-11-19 のユーザー指示）。"""

import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.request

import pymupdf
import pytest

from pdf_latex_overlay import prepare
from pdf_latex_overlay.models import FormulaSet
from pdf_latex_overlay.recognize import FixedBackend
from pdf_latex_overlay.viewer.hub import Hub, natural_key
from pdf_latex_overlay.viewer.server import make_server
from pdf_latex_overlay.viewer.session import SessionError


@pytest.fixture
def shelf(fx, tmp_path):
    """教材フォルダの見立て: 階層・自然順・隠しフォルダ・PDF 以外・リンクを含む。"""
    root = tmp_path / "教材"
    for rel in ["数学/第3章/p331-360.pdf", "数学/第3章/p31-40.pdf", "数学/第3章/p2.pdf", "数学/第3章/p10.pdf",
                "数学/第1章/a.pdf", "物理/b.pdf", "top.pdf", ".hidden/c.pdf"]:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        doc = pymupdf.open(fx["scan"])  # 中身が違う PDF にする（同じ中身はライブラリの同じ結果を共有するため）
        doc.set_metadata({"title": rel})
        doc.save(p)
        doc.close()
    (root / "数学" / "note.docx").write_text("x")  # 対応していない形式は出さない
    try:
        os.symlink(root / "top.pdf", root / "link.pdf")  # リンクは一覧に出さない
    except OSError:  # Windows では、権限がないと作れない
        pass
    return root


def _names(node):
    return [c["name"] for c in node["children"]]


def _hub(shelf, tmp_path, **kw):
    return Hub([shelf], library=tmp_path / "lib", **kw)


def test_natural_key():
    assert sorted(["p331-360", "p31-40", "p2", "p10"], key=natural_key) == ["p2", "p10", "p31-40", "p331-360"]


def test_tree_hierarchy_and_order(shelf, tmp_path):
    h = _hub(shelf, tmp_path)
    t = h.tree()
    (root,) = [r for r in t["roots"] if not r.get("virtual")]  # 一番下のビューアのフォルダは、別に調べる
    assert root["name"] == "教材" and t["files"] == 7  # .hidden とリンクは除く
    assert _names(root) == ["数学", "物理", "top.pdf"]  # フォルダが先、あとはファイル
    math = root["children"][0]
    assert _names(math) == ["第1章", "第3章"]
    assert _names(math["children"][1]) == ["p2.pdf", "p10.pdf", "p31-40.pdf", "p331-360.pdf"]  # 数字は数として並べる
    assert math["children"][1]["key"] == "r0/数学/第3章"
    assert t["current"] is None and "link.pdf" not in json.dumps(t)
    h.close()


def test_states_from_library_and_hash_cache(shelf, tmp_path):
    h = _hub(shelf, tmp_path)
    h.tree()
    t0 = time.time()
    while h.hashing_remaining() and time.time() - t0 < 30:
        time.sleep(0.05)
    t = h.tree()
    files = [c for c in t["roots"][0]["children"] if c["type"] == "file"]
    assert files[0]["state"] == "none"  # 結果がない PDF は未処理
    assert (tmp_path / "lib" / ".hashcache.json").exists()
    # ライブラリに「準備済み」の記録があれば、そう表示する
    p = shelf / "top.pdf"
    d, sha = prepare.entry_dir(p, tmp_path / "lib")
    d.mkdir(parents=True)
    prepare.write_meta(d, {"state": "done", "pages": 6, "counts": {"formulas": 11, "with_latex": 11}})
    t = h.tree()
    node = next(c for c in t["roots"][0]["children"] if c["name"] == "top.pdf")
    assert node["state"] == "done" and node["formulas"] == 11 and node["pages"] == 6
    h.close()
    # 同じ (サイズ, 更新時刻) なら、次回の起動で再計算しない
    h2 = _hub(shelf, tmp_path)
    h2.tree()
    assert h2.hashing_remaining() == 0
    h2.close()


def test_open_switch_locks_and_models_are_shared(shelf, tmp_path):
    be = FixedBackend(["x"])
    h = _hub(shelf, tmp_path, backend_obj=be)
    h.tree()
    ids = {p.name: i for i, p in h.index.items()}
    a = h.open(ids["a.pdf"])
    assert h.current_id == ids["a.pdf"] and h.open(ids["a.pdf"]) is a  # 同じ PDF を開き直しても同じセッション
    lock_a = a.workdir / ".viewer.lock"
    assert lock_a.exists()
    b = h.open(ids["b.pdf"])
    assert b is not a and not lock_a.exists() and (b.workdir / ".viewer.lock").exists()
    assert b._backend is be and a._backend is be  # 認識モデルは切り替えても使い回す
    with pytest.raises(Exception):
        a.page_png(0)  # 閉じたセッション
    h.close()
    assert not (b.workdir / ".viewer.lock").exists() and h.session is None


def test_open_rejects_unknown_ids_and_foreign_viewer(shelf, tmp_path):
    h = _hub(shelf, tmp_path)
    h.tree()
    for bad in ("../../etc/passwd", "zzzzzzzzzzzz", "0123456789ab", "", None, 5):
        with pytest.raises(SessionError):
            h.open(bad)
    pid_names = {p.name: i for i, p in h.index.items()}
    fid = pid_names["a.pdf"]
    d, _ = prepare.entry_dir(shelf / "数学/第1章/a.pdf", tmp_path / "lib")
    d.mkdir(parents=True, exist_ok=True)
    (d / ".viewer.lock").write_text("1")  # 別のビューア（pid 1）が使用中
    with pytest.raises(SessionError) as e:
        h.open(fid)
    assert e.value.status == 409
    assert h.session is None
    h.close()


def test_switch_stops_background_recognition(shelf, tmp_path):
    class Slow:
        info = {"versions": {}}

        def detect(self, png, conf=0.1):
            time.sleep(0.2)
            return []

        def close(self):
            pass

    h = _hub(shelf, tmp_path, backend_obj=FixedBackend(["x"]), detector=Slow())
    h.backend_name = "fixed"
    h.tree()
    ids = {p.name: i for i, p in h.index.items()}
    a = h.open(ids["a.pdf"], auto=True)
    time.sleep(0.3)
    assert a.auto_status()["state"] == "running"
    t0 = time.time()
    h.open(ids["b.pdf"])  # 切り替えると、前の PDF の処理を区切りのよいところで止める
    assert time.time() - t0 < 10
    assert not (a._auto_thread and a._auto_thread.is_alive())
    h.close()


def test_hub_root_can_be_single_files(fx, tmp_path):
    p = tmp_path / "one.pdf"
    shutil.copy(fx["scan"], p)
    h = Hub([p], library=tmp_path / "lib")
    t = h.tree()
    assert t["roots"][0]["name"] == "開いたPDF" and _names(t["roots"][0]) == ["one.pdf"]
    h.close()


def _req(url, token=None, method="GET", body=None):
    headers = {"X-Token": token} if token else {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def test_http_tree_open_and_theme_assets(shelf, tmp_path):
    h = _hub(shelf, tmp_path)
    httpd, token = make_server(h, 0)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        # 静的ファイル: テーマの切り替えと画面
        st, body, hdr = _req(base + "/theme.js")
        assert st == 200 and b"ploTheme" in body and "javascript" in hdr["Content-Type"]
        st, body, _ = _req(base + "/")
        html = body.decode()
        assert 'data-theme-set="dark"' in html and 'data-theme-set="light"' in html and 'id="tree"' in html
        css = _req(base + "/style.css")[1].decode()
        assert ':root[data-theme="dark"]' in css and "prefers-color-scheme: dark" in css and "scroll-snap-type" in css
        # API はトークンが要る
        assert _req(base + "/api/tree")[0] == 401
        assert _req(base + "/api/open", method="POST", body={"id": "0123456789ab"})[0] == 401
        # PDF を選ぶまでは、ページの API は 409
        assert _req(base + "/api/doc", token)[0] == 409
        st, body, _ = _req(base + "/api/tree", token)
        t = json.loads(body)
        assert st == 200 and t["current"] is None
        ids = {p.name: i for i, p in h.index.items()}
        assert _req(base + "/api/open", token, "POST", {"id": "0123456789ab"})[0] == 404
        assert _req(base + "/api/open", token, "POST", {"id": "../x"})[0] == 400
        st, body, _ = _req(base + "/api/open", token, "POST", {"id": ids["a.pdf"]})
        assert st == 200
        st, body, _ = _req(base + "/api/doc", token)
        d = json.loads(body)
        assert d["id"] == ids["a.pdf"] and d["name"] == "a.pdf" and len(d["pages"]) == 6
        assert json.loads(_req(base + "/api/tree", token)[1])["current"] == ids["a.pdf"]
        # 切り替えても、そのまま使える
        assert _req(base + "/api/open", token, "POST", {"id": ids["b.pdf"]})[0] == 200
        assert json.loads(_req(base + "/api/doc", token)[1])["name"] == "b.pdf"
        assert _req(base + "/api/page/0.png", token)[0] == 200
    finally:
        httpd.shutdown()
        httpd.server_close()
        h.close()


def test_identical_copies_share_one_result(fx, tmp_path):
    """同じ中身の PDF（コピー）は、ライブラリの同じ結果を使う。"""
    root = tmp_path / "c"
    for rel in ("x/a.pdf", "y/b.pdf"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(fx["scan"], root / rel)
    h = Hub([root], library=tmp_path / "lib")
    h.tree()
    ids = {p.name: i for i, p in h.index.items()}
    a = h.open(ids["a.pdf"])
    wd = a.workdir
    b = h.open(ids["b.pdf"])
    assert b.workdir == wd
    h.close()
