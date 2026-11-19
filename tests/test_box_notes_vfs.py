"""枠の削除と元に戻す、PDFの外のメモ、ビューア専用のフォルダ構造（2026-11-19 のユーザー指示）。"""

import json
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pymupdf
import pytest

from pdf_latex_overlay.models import Formula
from pdf_latex_overlay.viewer.hub import Hub
from pdf_latex_overlay.viewer.server import make_server
from pdf_latex_overlay.viewer.session import SessionError, ViewerSession
from pdf_latex_overlay.viewer.vfs import VFS, VFSError

STATIC = Path(__file__).resolve().parents[1] / "src" / "pdf_latex_overlay" / "viewer" / "static"


def _pdf(fx, d, name, title=None):
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    doc = pymupdf.open(fx["scan"])
    doc.set_metadata({"title": title or name})  # 中身を変える（同じ中身は同じ PDF とみなされるため）
    doc.save(p)
    doc.close()
    return p


def _session(fx, tmp_path):
    p = _pdf(fx, tmp_path / "in", "a.pdf")
    return ViewerSession(p, tmp_path / "work")


# ------------------------------------------------------------------ 枠の削除
def test_delete_returns_the_box_and_restore_brings_it_back(fx, tmp_path):
    s = _session(fx, tmp_path)
    f = s.create(0, (40, 60, 200, 90), latex="x^{2}")
    r = s.delete(f["id"])
    assert r["deleted"] == f["id"] and r["formula"]["latex"] == "x^{2}" and s.list()["formulas"] == []
    out = s.restore([r["formula"]])
    assert out["restored"] == [f["id"]] and s.list()["formulas"][0]["latex"] == "x^{2}"
    assert s.restore([r["formula"]])["restored"] == []  # すでにある枠は、重ねて戻さない
    for bad in ([], "x", [{"id": "a"}]):
        with pytest.raises(SessionError):
            s.restore(bad)
    s.close()


def test_delete_many_by_scope_and_restore(fx, tmp_path):
    s = _session(fx, tmp_path)
    s.create(0, (40, 60, 200, 90), latex="x")
    t = s.create(0, (40, 100, 200, 130), latex="文章")
    with s.lock:  # 1つを文章の枠にする
        s._get(t["id"]).kind = "text"
    r = s.delete_many(0, "text")
    assert r["deleted"] == [t["id"]] and len(s.list()["formulas"]) == 1  # 数式の枠は残る
    s.restore(r["formulas"])
    r2 = s.delete_many(0, "all")
    assert len(r2["deleted"]) == 2 and s.list()["formulas"] == []
    assert s.delete_many(0, "all")["deleted"] == []
    for bad in (("x", "all"), (0, "nope"), (99, "all"), (True, "all")):
        with pytest.raises(SessionError):
            s.delete_many(*bad)
    s.close()


# ------------------------------------------------------------------ メモ
def test_notes_are_saved_outside_the_pdf(fx, tmp_path):
    s = _session(fx, tmp_path)
    before = (tmp_path / "in" / "a.pdf").read_bytes()
    assert s.notes_get() == {"doc": "", "pages": {}, "updated": None}
    s.notes_set("doc", "この教材について\n補足")
    s.notes_set("page", "p1 のメモ", page=0)
    d = s.notes_get()
    assert d["doc"].startswith("この教材") and d["pages"] == {"0": "p1 のメモ"} and d["updated"]
    assert (s.workdir / "notes.json").is_file() and (tmp_path / "in" / "a.pdf").read_bytes() == before  # PDF は変わらない
    s.notes_set("page", "  ", page=0)  # 空にすると、そのページのメモは消える
    assert s.notes_get()["pages"] == {}
    s2 = ViewerSession(tmp_path / "in" / "a.pdf", tmp_path / "work")  # 開き直しても残る
    assert s2.notes_get()["doc"].startswith("この教材")
    s2.close()
    for bad in (("x", "t", None), ("doc", 5, None), ("page", "t", 99), ("page", "t", None), ("doc", "a" * (ViewerSession.NOTES_MAX + 1), None)):
        with pytest.raises(SessionError):
            s.notes_set(*bad)
    s.close()


# ------------------------------------------------------------------ ビューア専用のフォルダ構造
def test_vfs_basic_operations_and_persistence(tmp_path):
    p = tmp_path / "c.json"
    v = VFS(p)
    a = v.mkdir("v", "数学")
    b = v.mkdir(a, "微分積分")
    with pytest.raises(VFSError):
        v.mkdir("v", "数学")  # 同じ名前は不可
    with pytest.raises(VFSError):
        v.mkdir("v", "a/b")
    v.rename(b, "積分")
    assert v.add(b, [("s1", "x.pdf"), ("s1", "x.pdf"), ("s2", "y.pdf")]) == 2  # 同じフォルダに、同じ PDF は1つだけ
    assert v.add("v", [("s1", "x.pdf")]) == 1  # 別のフォルダになら入れられる
    assert [i["sha"] for i in v.folder_items(a)] == ["s1", "s2"]  # 親フォルダは、中のフォルダの登録も含む
    with pytest.raises(VFSError):
        v.move([], [a], b)  # 自分の中には移せない
    c = v.mkdir("v", "物理")
    assert v.move([{"folder": b, "sha": "s2"}], [], c) == 1 and [i["sha"] for i in v.folder_items(c)] == ["s2"]
    assert v.move([], [b], "v") == 1  # フォルダを移す
    v2 = VFS(p)  # 保存されている
    assert {f["name"] for f in v2.data["folders"].values()} == {"数学", "積分", "物理"}
    assert v2.remove([{"folder": "v", "sha": "s1"}]) == 1
    assert v2.delete(a) == {"folders": 1, "items": 0}
    with pytest.raises(VFSError):
        v2.delete("v")
    with pytest.raises(VFSError):
        v2.fid_of("v/f999")


def _hub(shelf_root, tmp_path):
    return Hub([shelf_root], library=tmp_path / "lib")


def _wait_hash(h, n):
    for _ in range(400):
        t = h.tree()
        if t["hashing"] == 0:
            return t
        time.sleep(0.05)
    raise AssertionError("ハッシュ計算が終わらない")


def test_hub_virtual_folders_follow_the_file_by_content(fx, tmp_path):
    root = tmp_path / "教材"
    a = _pdf(fx, root / "x", "a.pdf")
    _pdf(fx, root / "y", "b.pdf")
    h = _hub(root, tmp_path)
    t = _wait_hash(h, 2)
    vroot = next(r for r in t["roots"] if r.get("vroot"))
    assert vroot["key"] == "v" and vroot["children"] == []
    fid = h.id_of(a)
    key = h.vmkdir("v", "お気に入り")["key"]
    assert h.vadd(key, [fid])["added"] == 1
    assert h.vadd(key, [fid])["added"] == 0  # 重ねない
    # フォルダごと登録できる（中の PDF すべて）
    key2 = h.vmkdir("v", "全部")["key"]
    assert h.vadd(key2, [next(r for r in t["roots"] if not r.get("virtual"))["key"]])["added"] == 2
    t = h.tree()
    vroot = next(r for r in t["roots"] if r.get("vroot"))
    fav = next(c for c in vroot["children"] if c["name"] == "お気に入り")
    (item,) = fav["children"]
    assert item["virtual"] and item["id"] == fid and item["rowKey"].startswith("vf:v/") and item["sha"]
    # 実際のファイルを移動・名前変更しても、中身（ハッシュ）で追従する
    h.rename(fid, "renamed")
    t = _wait_hash(h, 2)
    h.scan(force=True)
    t = _wait_hash(h, 2)
    fav = next(c for c in next(r for r in t["roots"] if r.get("vroot"))["children"] if c["name"] == "お気に入り")
    assert fav["children"][0]["name"] == "renamed.pdf" and not fav["children"][0].get("missing")
    # ビューアのフォルダの OCR 対象は、実在する PDF だけ
    assert len(h._ocr_targets([key2])) == 2 and len(h._ocr_targets(["v"])) == 2
    # ファイルを消すと「見つからない」行になる（登録は残る）
    (root / "y" / "b.pdf").unlink()
    h.scan(force=True)
    t = h.tree()
    allv = next(c for c in next(r for r in t["roots"] if r.get("vroot"))["children"] if c["name"] == "全部")
    assert sorted(bool(c.get("missing")) for c in allv["children"]) == [False, True]
    h.close()


def test_hub_vfs_errors_are_400_and_files_are_never_touched(fx, tmp_path):
    root = tmp_path / "c"
    a = _pdf(fx, root, "a.pdf")
    before = a.read_bytes()
    h = _hub(root, tmp_path)
    with pytest.raises(SessionError) as e:
        h.vmkdir("v/f404", "x")
    assert e.value.status == 400
    with pytest.raises(SessionError):
        h.vadd("v", [])
    with pytest.raises(SessionError):
        h.vadd("v", ["../../etc/passwd"])  # 一覧にないものは、登録できない
    h.vadd(h.vmkdir("v", "k")["key"], [h.id_of(a)])
    h.vdelete("v/f1")
    assert a.read_bytes() == before and a.exists()  # フォルダを消しても、PDF は残る
    h.close()


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


def test_http_vfs_notes_and_delete_routes(fx, tmp_path):
    root = tmp_path / "c"
    a = _pdf(fx, root, "a.pdf")
    h = _hub(root, tmp_path)
    httpd, token = make_server(h, 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        st, r = _req(base, token, "POST", "/api/vfs/mkdir", {"parent": "v", "name": "F"})
        assert st == 200 and r["key"].startswith("v/f")
        assert _req(base, token, "POST", "/api/vfs/mkdir", {"parent": "v", "name": "F"})[0] == 400
        assert _req(base, token, "POST", "/api/vfs/add", {"folder": r["key"], "targets": [h.id_of(a)]})[1]["added"] == 1
        assert _req(base, token, "POST", "/api/vfs/nope", {})[0] == 404
        _req(base, token, "POST", "/api/open", {"id": h.id_of(a), "auto": False})
        # メモ
        assert _req(base, token, "POST", "/api/notes", {"scope": "doc", "text": "補足"})[0] == 200
        assert _req(base, token, "GET", "/api/notes")[1]["doc"] == "補足"
        assert _req(base, token, "POST", "/api/notes", {"scope": "page", "page": 99, "text": "x"})[0] == 400
        # 枠の削除と元に戻す（/api/formulas/delete_many は、枠の ID と取り違えない）
        st, f = _req(base, token, "POST", "/api/formulas", {"page": 0, "view_rect": [40, 60, 200, 90], "latex": "x"})
        assert st == 200
        st, d = _req(base, token, "DELETE", f"/api/formulas/{f['id']}")
        assert st == 200 and d["formula"]["latex"] == "x"
        assert _req(base, token, "POST", "/api/formulas/restore", {"formulas": [d["formula"]]})[1]["restored"] == [f["id"]]
        assert _req(base, token, "POST", "/api/formulas/delete_many", {"page": 0, "scope": "all"})[1]["deleted"] == [f["id"]]
    finally:
        httpd.shutdown()
        httpd.server_close()
        h.close()


def test_static_ui_has_delete_notes_and_virtual_folder_controls():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    for ident in ("notes", "notetext", "note-doc", "note-page", "sel-vadd"):
        assert f'id="{ident}"' in html, ident
    for token in ('case "Delete"', "box-x", "deleteFormula", "restoreFormulas", "deletePageFormulas", "actVAdd", "actVMove",
                  "attachDrag", "/api/vfs/", "/api/notes"):
        assert token in js, token
