"""名前の変更・移動・ゴミ箱・フォルダ作成、OCR ジョブ、ページ単位の認識、接続を切らない 500（2026-11-19 のユーザー指示）。

すべて一時フォルダの中で行う。利用者の実際のファイルは使わない。
"""

import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.request

import pymupdf
import sys

import pytest

from pdf_latex_overlay import prepare, safety
from pdf_latex_overlay.models import FormulaSet
from pdf_latex_overlay.recognize import FixedBackend
from pdf_latex_overlay.viewer.fsops import clean_name, move_to_trash
from pdf_latex_overlay.viewer.hub import Hub, file_id
from pdf_latex_overlay.viewer.server import make_server
from pdf_latex_overlay.viewer.session import SessionError, ViewerSession


def _make_pdf(path, fx, title, pages=1):
    doc = pymupdf.open(fx["scan"])
    if pages < doc.page_count:
        doc.select(list(range(pages)))
    doc.set_metadata({"title": title})
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()


@pytest.fixture
def shelf(fx, tmp_path):
    root = tmp_path / "教材"
    for rel in ["数学/第1章/a.pdf", "数学/第3章/b.pdf", "数学/第3章/c.pdf", "物理/d.pdf", "top.pdf"]:
        _make_pdf(root / rel, fx, rel)
    (root / "空のフォルダ").mkdir()
    return root


@pytest.fixture
def hub(shelf, tmp_path):
    h = Hub([shelf], library=tmp_path / "lib", trash_dir=tmp_path / "trash")
    h.tree()
    yield h
    h.close()


def ids(h):
    h.scan(force=True)
    return {p.name: i for i, p in h.index.items()}


def keys(h):
    h.scan(force=True)
    return {str(d.relative_to(h.roots[ri].parent)): k for k, d in h.folders.items() for ri in [int(k[1:].split("/")[0])]}


def names(node):
    return [c["name"] for c in node["children"]]


# ------------------------------------------------------------------ 部品
@pytest.mark.parametrize("bad", ["", "  ", ".", "..", "a/b", "a\\b", "a:b", ".hidden", "x" * 300, None, 5, "a\x00b"])
def test_clean_name_rejects(bad):
    with pytest.raises(SessionError):
        clean_name(bad, False)


def test_clean_name_ok():
    assert clean_name(" 第3章 ", False) == "第3章"
    assert clean_name("new", True) == "new.pdf"
    assert clean_name("New.PDF", True) == "New.PDF"


def test_move_to_trash_does_not_overwrite(tmp_path):
    t = tmp_path / "trash"
    for _ in range(2):
        p = tmp_path / "x.pdf"
        p.write_bytes(b"data")
        move_to_trash(p, t)
    assert len(list(t.iterdir())) == 2 and not (tmp_path / "x.pdf").exists()  # 同じ名前でも、上書きしない


# ------------------------------------------------------------------ フォルダ作成
def test_empty_folders_appear_in_tree(hub):
    t = hub.tree(refresh=True)
    assert "空のフォルダ" in names(t["roots"][0])
    assert t["readonly"] is False and t["ocr"] is None


def test_mkdir(hub, shelf):
    k = keys(hub)
    r = hub.mkdir(k["教材/数学"], "新しい章")
    assert (shelf / "数学/新しい章").is_dir() and r["key"].endswith("数学/新しい章")
    assert "新しい章" in names(next(c for c in hub.tree()["roots"][0]["children"] if c["name"] == "数学"))
    for bad, status in [("新しい章", 409), ("a/b", 400), ("", 400), (".x", 400)]:
        with pytest.raises(SessionError) as e:
            hub.mkdir(k["教材/数学"], bad)
        assert e.value.status == status
    with pytest.raises(SessionError):
        hub.mkdir(ids(hub)["a.pdf"], "x")  # ファイルの中には作れない
    with pytest.raises(SessionError) as e:
        hub.mkdir("r0/../../etc", "x")  # 一覧にないフォルダ
    assert e.value.status == 404
    assert not (shelf.parent / "etc").exists()


# ------------------------------------------------------------------ 名前の変更
def test_rename_pdf_and_folder_keep_content_and_results(hub, shelf, fx):
    i = ids(hub)
    before = safety.sha256_file(shelf / "数学/第1章/a.pdf")
    r = hub.rename(i["a.pdf"], "代数")  # 拡張子は自動で付く
    assert (shelf / "数学/第1章/代数.pdf").exists() and not (shelf / "数学/第1章/a.pdf").exists()
    assert safety.sha256_file(shelf / "数学/第1章/代数.pdf") == before  # 中身は変わらない
    assert r["moved"] == {i["a.pdf"]: file_id(shelf / "数学/第1章/代数.pdf")}
    # フォルダ名の変更: 中の PDF の ID が付け替わる
    k = keys(hub)
    r = hub.rename(k["教材/数学/第3章"], "第三章")
    assert (shelf / "数学/第三章/b.pdf").exists()
    assert set(r["moved"].values()) == {file_id(shelf / "数学/第三章/b.pdf"), file_id(shelf / "数学/第三章/c.pdf")}


def test_rename_rules(hub, shelf):
    i, k = ids(hub), keys(hub)
    with pytest.raises(SessionError) as e:
        hub.rename(i["c.pdf"], "b.pdf")  # 同じフォルダ（第3章）に同じ名前がある
    assert e.value.status == 409 and (shelf / "数学/第3章/c.pdf").exists() and (shelf / "数学/第3章/b.pdf").exists()
    with pytest.raises(SessionError):
        hub.rename(i["a.pdf"], "a.pdf")  # 変わっていない
    with pytest.raises(SessionError):
        hub.rename(k["教材"], "別名")  # 一番上のフォルダは変えない
    with pytest.raises(SessionError) as e:
        hub.rename("0123456789ab", "x")
    assert e.value.status == 404
    (shelf / "数学/第1章/b.pdf").write_bytes(b"x")
    with pytest.raises(SessionError) as e:
        hub.rename(i["a.pdf"], "b")  # 上書きしない（拡張子の補完後に衝突）
    assert e.value.status == 409 and (shelf / "数学/第1章/b.pdf").read_bytes() == b"x"


def test_rename_case_only(hub, shelf):
    i = ids(hub)
    hub.rename(i["top.pdf"], "TOP.pdf")
    assert "TOP.pdf" in os.listdir(shelf)


def test_rename_updates_hash_cache_without_rehash(hub, shelf):
    t0 = time.time()
    while hub.hashing_remaining() and time.time() - t0 < 30:
        time.sleep(0.05)
    hub.rename(ids(hub)["top.pdf"], "トップ")
    hub.tree()
    assert hub.hashing_remaining() == 0 and hub._cached_sha(shelf / "トップ.pdf")


def test_rename_open_pdf_reopens_under_new_name(hub, shelf):
    i = ids(hub)
    s = hub.open(i["d.pdf"])
    wd = s.workdir
    r = hub.rename(i["d.pdf"], "力学")
    assert hub.current_id == r["moved"][i["d.pdf"]] and hub.session is not s
    assert hub.session.workdir == wd and hub.session.input.name == "力学.pdf"  # 結果は同じ作業領域のまま
    assert (wd / ".viewer.lock").exists()


# ------------------------------------------------------------------ 移動
def test_move_files_and_folders(hub, shelf):
    i, k = ids(hub), keys(hub)
    r = hub.move([i["a.pdf"], i["d.pdf"]], k["教材/空のフォルダ"])
    assert r["count"] == 2 and (shelf / "空のフォルダ/a.pdf").exists() and (shelf / "空のフォルダ/d.pdf").exists()
    assert set(r["moved"]) == {i["a.pdf"], i["d.pdf"]}
    r = hub.move([k["教材/数学/第3章"]], k["教材/物理"])  # フォルダごと
    assert (shelf / "物理/第3章/b.pdf").exists() and not (shelf / "数学/第3章").exists()
    assert set(r["moved"].values()) == {file_id(shelf / "物理/第3章/b.pdf"), file_id(shelf / "物理/第3章/c.pdf")}


def test_move_refusals(hub, shelf):
    i, k = ids(hub), keys(hub)
    with pytest.raises(SessionError):
        hub.move([k["教材/数学"]], k["教材/数学/第1章"])  # 自分の中へ
    with pytest.raises(SessionError):
        hub.move([k["教材/数学"]], k["教材/数学"])
    with pytest.raises(SessionError):
        hub.move([i["a.pdf"]], k["教材/数学/第1章"])  # すでにその場所
    (shelf / "物理/a.pdf").write_bytes(b"keep")
    with pytest.raises(SessionError) as e:
        hub.move([i["a.pdf"]], k["教材/物理"])  # 同じ名前があれば上書きしない
    assert e.value.status == 409 and (shelf / "物理/a.pdf").read_bytes() == b"keep" and (shelf / "数学/第1章/a.pdf").exists()
    with pytest.raises(SessionError):
        hub.move([k["教材"]], k["教材/物理"])  # 一番上は動かせない
    with pytest.raises(SessionError):
        hub.move([i["b.pdf"], i["a.pdf"]], i["top.pdf"])  # 移動先はフォルダだけ
    # 1 つでも検査に通らなければ、何も動かさない
    (shelf / "物理/b.pdf").write_bytes(b"x")
    with pytest.raises(SessionError):
        hub.move([i["a.pdf"], i["b.pdf"]], k["教材/物理"])
    assert (shelf / "数学/第1章/a.pdf").exists() and (shelf / "数学/第3章/b.pdf").exists()


def test_move_nested_targets_move_only_the_folder(hub, shelf):
    i, k = ids(hub), keys(hub)
    r = hub.move([k["教材/数学/第3章"], i["b.pdf"]], k["教材/物理"])
    assert r["count"] == 1 and (shelf / "物理/第3章/b.pdf").exists()


def test_move_open_pdf_inside_moved_folder(hub, shelf):
    i, k = ids(hub), keys(hub)
    s = hub.open(i["b.pdf"])
    wd = s.workdir
    r = hub.move([k["教材/数学/第3章"]], k["教材/物理"])
    assert hub.session is not s and hub.session.workdir == wd and hub.current_id == r["moved"][i["b.pdf"]]


# ------------------------------------------------------------------ ゴミ箱
def test_trash_moves_to_trash_not_delete(hub, shelf, tmp_path):
    i, k = ids(hub), keys(hub)
    data = (shelf / "物理/d.pdf").read_bytes()
    r = hub.trash([i["d.pdf"]])
    trash = tmp_path / "trash"
    assert r["trashed"] == ["d.pdf"] and not (shelf / "物理/d.pdf").exists()
    assert (trash / "d.pdf").read_bytes() == data  # 永久削除ではなく、ゴミ箱に残る
    r = hub.trash([k["教材/数学/第3章"]])  # フォルダごと
    assert (trash / "第3章/b.pdf").exists() and not (shelf / "数学/第3章").exists()
    tree = hub.tree(refresh=True)
    assert "第3章" not in json.dumps(tree, ensure_ascii=False)


def test_trash_open_pdf_releases_and_keeps_results(hub, shelf):
    i = ids(hub)
    s = hub.open(i["top.pdf"])
    wd = s.workdir
    hub.trash([i["top.pdf"]])
    assert hub.session is None and hub.current_id is None and not (wd / ".viewer.lock").exists()
    assert (wd / "formulas.json").exists()  # 認識の結果は残る


def test_trash_refusals(hub, shelf):
    k = keys(hub)
    with pytest.raises(SessionError):
        hub.trash([k["教材"]])
    with pytest.raises(SessionError):
        hub.trash([])
    with pytest.raises(SessionError) as e:
        hub.trash(["../../etc"])
    assert e.value.status == 404 and (shelf / "top.pdf").exists()


# ------------------------------------------------------------------ 読み取り専用・記録・安全
def test_readonly_blocks_all_file_operations(shelf, tmp_path):
    h = Hub([shelf], library=tmp_path / "lib", readonly=True, trash_dir=tmp_path / "trash")
    h.tree()
    i, k = ids(h), keys(h)
    for fn in (lambda: h.mkdir(k["教材"], "x"), lambda: h.rename(i["a.pdf"], "x"),
               lambda: h.move([i["a.pdf"]], k["教材/物理"]), lambda: h.trash([i["a.pdf"]])):
        with pytest.raises(SessionError) as e:
            fn()
        assert e.value.status == 403
    assert (shelf / "数学/第1章/a.pdf").exists() and h.tree()["readonly"] is True
    h.close()


def test_operations_are_logged(hub, shelf, tmp_path):
    i, k = ids(hub), keys(hub)
    hub.mkdir(k["教材"], "新")
    hub.rename(i["top.pdf"], "t2")
    hub.trash([i["d.pdf"]])
    log = [json.loads(x) for x in (tmp_path / "lib" / "operations.jsonl").read_text().splitlines()]
    assert [x["action"] for x in log] == ["mkdir", "move", "trash"]


@pytest.mark.skipif(sys.platform == "win32", reason="シンボリックリンク（Windows では管理者権限が要る）")
def test_symlinked_folders_and_files_are_not_reachable(shelf, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.pdf").write_bytes(b"%PDF-1.4")
    os.symlink(outside, shelf / "link_dir")
    h = Hub([shelf], library=tmp_path / "lib", trash_dir=tmp_path / "trash")
    t = json.dumps(h.tree(), ensure_ascii=False)
    assert "secret.pdf" not in t
    with pytest.raises(SessionError):
        h.trash([file_id(outside / "secret.pdf")])
    assert (outside / "secret.pdf").exists()
    h.close()


# ------------------------------------------------------------------ OCR のジョブ
class FakeDetector:
    info = {"versions": {"fake": "1"}}

    def __init__(self, delay=0.0):
        self.delay, self.calls = delay, 0

    def detect(self, png, conf=0.1):
        self.calls += 1
        time.sleep(self.delay)
        return [[120, 150, 420, 230, 1, 0.9]]

    def close(self):
        pass


def _ocr_hub(shelf, tmp_path, delay=0.0):
    h = Hub([shelf], library=tmp_path / "lib", backend_obj=FixedBackend(["x ^ { 2 }"]), detector=FakeDetector(delay),
            trash_dir=tmp_path / "trash")
    h.backend_name = "fixed"
    h.tree()
    return h


def _wait_job(h, timeout=60):
    t0 = time.time()
    while h.ocr_status()["state"] == "running" and time.time() - t0 < timeout:
        time.sleep(0.05)
    return h.ocr_status()


def test_ocr_folder_job_processes_every_pdf_once(shelf, tmp_path):
    h = _ocr_hub(shelf, tmp_path)
    i, k = ids(h), keys(h)
    h.ocr_start([k["教材/数学"], i["b.pdf"]])  # フォルダと、その中の PDF の両方を選んでも、1 回だけ
    st = _wait_job(h)
    assert st["state"] == "done" and st["total"] == 3 and st["ok"] == 3 and st["failed"] == 0
    assert [x["name"] for x in st["items"]] == ["a.pdf", "b.pdf", "c.pdf"]
    for p in (shelf / "数学/第1章/a.pdf", shelf / "数学/第3章/c.pdf"):
        d, _ = prepare.entry_dir(p, tmp_path / "lib")
        assert prepare.read_meta(d)["state"] == "done"
        fs = FormulaSet.load(d / "formulas.json")
        assert fs.formulas and all(f.latex == "x^{2}" for f in fs.formulas)
    node = next(c for c in h.tree()["roots"][0]["children"] if c["name"] == "物理")["children"][0]
    assert node["state"] == "none"  # 選んでいない PDF は未処理のまま
    h.close()


def test_ocr_cancel_and_failure_isolation(shelf, tmp_path):
    (shelf / "壊れた.pdf").write_bytes(b"not a pdf")
    h = _ocr_hub(shelf, tmp_path, delay=0.3)
    k = keys(h)
    h.ocr_start([k["教材"]])
    time.sleep(0.4)
    h.ocr_cancel()
    st = _wait_job(h)
    assert st["state"] == "cancelled" and any(x["state"] == "cancelled" for x in st["items"])
    # 壊れた PDF 1 つで、ほかの処理が止まらない
    h2 = _ocr_hub(shelf, tmp_path / "x")
    h2.ocr_start([keys(h2)["教材"]])
    st = _wait_job(h2)
    bad = next(x for x in st["items"] if x["name"] == "壊れた.pdf")
    assert bad["state"] in ("skipped", "error") and st["ok"] >= 5 and st["state"] == "done"
    h.close()
    h2.close()


def test_ocr_refusals(shelf, tmp_path):
    h = Hub([shelf], library=tmp_path / "lib")  # モデルなし
    h.tree()
    with pytest.raises(SessionError) as e:
        h.ocr_start([ids(h)["a.pdf"]])
    assert e.value.status == 409
    h.close()
    h = _ocr_hub(shelf, tmp_path / "y", delay=0.3)
    h.ocr_start([ids(h)["a.pdf"]])
    with pytest.raises(SessionError) as e:
        h.ocr_start([ids(h)["b.pdf"]])  # 実行中は、もう 1 つ始められない
    assert e.value.status == 409
    with pytest.raises(SessionError):
        h.ocr_start([])
    h.ocr_cancel()
    _wait_job(h)
    with pytest.raises(SessionError) as e:
        h.ocr_start(["../etc"])
    assert e.value.status == 404
    h.close()


def test_ocr_of_open_pdf_goes_through_the_viewer_session(shelf, tmp_path):
    h = _ocr_hub(shelf, tmp_path)
    i = ids(h)
    s = h.open(i["a.pdf"])
    h.ocr_start([i["a.pdf"], i["b.pdf"]])
    st = _wait_job(h)
    assert st["ok"] == 2 and s.auto_status()["state"] == "done"
    d, _ = prepare.entry_dir(shelf / "数学/第3章/b.pdf", tmp_path / "lib")
    assert prepare.read_meta(d)["state"] == "done"
    h.close()


# ------------------------------------------------------------------ このページを認識
def test_recognize_this_page_only(fx, tmp_path):
    src = tmp_path / "s.pdf"
    shutil.copy(fx["scan"], src)
    s = ViewerSession(src, tmp_path / "w", backend="fixed", backend_obj=FixedBackend(["x"]))
    det = FakeDetector()
    s.start_auto(detector=det, pages=[2], force=True)
    while s.auto_status()["state"] == "running":
        time.sleep(0.02)
    assert det.calls == 1 and s.fs.meta["auto_pages"] == [2]
    assert {f.page for f in s.fs.formulas} == {2} and all(f.latex for f in s.fs.formulas)
    n = len(s.fs.formulas)
    s.start_auto(detector=det, pages=[2], force=True)  # 同じページをもう一度: 既存の枠と重なるものは追加しない
    while s.auto_status()["state"] == "running":
        time.sleep(0.02)
    assert len(s.fs.formulas) == n and s.fs.meta["auto_pages"] == [2]
    for bad in ([], [99], [-1], ["a"], [True], "0"):
        with pytest.raises(SessionError):
            s.start_auto(detector=det, pages=bad)
    s.close()


# ------------------------------------------------------------------ HTTP
def _req(url, token=None, method="GET", body=None):
    headers = {"X-Token": token} if token else {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def test_http_file_operations_and_status_codes(shelf, tmp_path):
    h = _ocr_hub(shelf, tmp_path)
    httpd, tok = make_server(h, 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        i, k = ids(h), keys(h)
        for path in ("/api/fs/mkdir", "/api/fs/rename", "/api/fs/move", "/api/fs/trash", "/api/ocr", "/api/ocr/cancel"):
            assert _req(base + path, None, "POST", {})[0] == 401  # トークンなしは拒否
        assert _req(base + "/api/ocr", None)[0] == 401
        st, d = _req(base + "/api/fs/mkdir", tok, "POST", {"parent": k["教材"], "name": "新"})
        assert st == 200 and (shelf / "新").is_dir()
        assert _req(base + "/api/fs/mkdir", tok, "POST", {"parent": k["教材"], "name": "新"})[0] == 409
        assert _req(base + "/api/fs/rename", tok, "POST", {"target": i["top.pdf"], "name": "a/b"})[0] == 400
        st, d = _req(base + "/api/fs/rename", tok, "POST", {"target": i["top.pdf"], "name": "t"})
        assert st == 200 and (shelf / "t.pdf").exists()
        st, d = _req(base + "/api/fs/move", tok, "POST", {"targets": [i["d.pdf"]], "dest": d_key(h, "新")})
        assert st == 200 and (shelf / "新/d.pdf").exists()
        assert _req(base + "/api/fs/trash", tok, "POST", {"targets": ["nope"]})[0] == 404
        st, d = _req(base + "/api/fs/trash", tok, "POST", {"targets": [file_id(shelf / "新/d.pdf")]})
        assert st == 200 and (tmp_path / "trash/d.pdf").exists()
        st, d = _req(base + "/api/ocr", tok, "POST", {"targets": [k["教材/物理"]]})
        assert st in (200, 400)  # 物理フォルダは空になっている → 400
        assert _req(base + "/api/ocr", tok)[0] == 200
        st, t = _req(base + "/api/tree", tok)
        assert st == 200 and "ocr" in t and t["readonly"] is False
    finally:
        httpd.shutdown()
        httpd.server_close()
        h.close()


def d_key(h, name):
    h.scan(force=True)
    return next(k for k, d in h.folders.items() if d.name == name)


def test_http_this_page_endpoint(fx, tmp_path):
    src = tmp_path / "s.pdf"
    shutil.copy(fx["scan"], src)
    s = ViewerSession(src, tmp_path / "w", backend="fixed", backend_obj=FixedBackend(["x"]), detector_obj=FakeDetector())
    httpd, tok = make_server(s, 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        assert _req(base + "/api/auto", tok, "POST", {"pages": [99]})[0] == 400
        assert _req(base + "/api/auto", tok, "POST", {"pages": [0], "force": True})[0] == 200
        while _req(base + "/api/auto", tok)[1]["state"] == "running":
            time.sleep(0.05)
        assert s.fs.meta["auto_pages"] == [0]
    finally:
        httpd.shutdown()
        httpd.server_close()
        s.close()


def test_unexpected_errors_return_500_json_not_a_dropped_connection(fx, tmp_path, monkeypatch):
    """Failed to fetch の原因: 想定外の例外でサーバーが接続を切っていた。JSON の 500 で返す。"""
    from pdf_latex_overlay.models import Formula

    src = tmp_path / "s.pdf"
    shutil.copy(fx["scan"], src)
    s = ViewerSession(src, tmp_path / "w")
    s.fs.formulas.append(Formula(id="out", page=0, rect=(5000, 5000, 5100, 5100), latex="x"))  # ページの外の枠
    s._save()
    httpd, tok = make_server(s, 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        st, body = _req(base + "/api/formulas/out/crop.png", tok)
        assert st == 400 and "ページの外" in body["error"]  # 以前は例外で接続が切れていた
        monkeypatch.setattr(ViewerSession, "list", lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
        st, body = _req(base + "/api/formulas", tok)
        assert st == 500 and "RuntimeError" in body["error"]  # 想定外も、JSON で返す
        assert _req(base + "/api/doc", tok)[0] == 200  # サーバーは生きている
    finally:
        httpd.shutdown()
        httpd.server_close()
        s.close()


def test_static_ui_has_menus_dialogs_and_renamed_button():
    """画面の部品（⋯メニュー、新しいフォルダ、複数選択、OCRの進み具合、接続切れ）と、ボタン名の変更。"""
    from pathlib import Path

    static = Path(__file__).resolve().parents[1] / "src" / "pdf_latex_overlay" / "viewer" / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    js = (static / "app.js").read_text(encoding="utf-8")
    assert "このページを認識" in html and "全ページを認識" not in html and "全ページを認識" not in js
    for ident in ("doc-menu", "tree-newfolder", "tree-select", "selbar", "sel-ocr", "sel-move", "sel-trash",
                  "ocrbar", "ocr-cancel", "dlg", "dlg-input", "dlg-list", "conn", "conn-retry"):
        assert f'id="{ident}"' in html, ident
    for label in ("名前を変更", "別の場所へ移動", "ゴミ箱へ移動", "OCRを行う", "新しいフォルダ"):
        assert label in js, label
    for endpoint in ("/api/fs/mkdir", "/api/fs/rename", "/api/fs/move", "/api/fs/trash", "/api/ocr", "/api/ocr/cancel"):
        assert endpoint in js, endpoint
    assert '{ pages: [n], force: true }' in js  # 「このページを認識」は、いま表示しているページだけ
    assert "Failed to fetch" in js and "setConn(false)" in js  # 接続できないときの扱い
