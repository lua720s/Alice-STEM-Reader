"""ローカルビューア: セッション操作と HTTP の境界（トークン・Host・静的ファイル）。"""

import json
import shutil
import threading
import urllib.error
import urllib.request

import numpy as np
import pymupdf
import pytest

from pdf_latex_overlay import safety
from pdf_latex_overlay.models import FormulaSet
from pdf_latex_overlay.recognize import FixedBackend, crop_png
from pdf_latex_overlay.viewer.server import make_server
from pdf_latex_overlay.viewer.session import SessionError, ViewerSession


@pytest.fixture
def sess(fx, tmp_path):
    src = tmp_path / "scan.pdf"
    shutil.copy(fx["scan"], src)
    s = ViewerSession(src, tmp_path / "work", formulas_path=fx["scan_json"],
                      backend="fixed", backend_obj=FixedBackend([r"\frac{a}{b}"]))
    yield s
    s.close()


def test_session_keeps_input_and_given_json(fx, sess):
    before_json = fx["scan_json"].read_bytes()
    sess.update("p0_quad", latex="x")
    assert fx["scan_json"].read_bytes() == before_json  # 指定 JSON は書き換えない
    assert (sess.workdir / "formulas.json").exists()
    assert safety.sha256_file(sess.input) == sess.input_sha


def test_view_rect_roundtrip_rotated(sess):
    lst = sess.list()["formulas"]
    rot = next(f for f in lst if f["id"] == "p_rot90")
    created = sess.create(rot["page"], rot["view_rect"])
    assert np.allclose(created["rect"], rot["rect"], atol=0.01)


def test_edit_keeps_ocr_and_history(sess):
    f = sess.create(0, [100, 100, 160, 120], recognize=True)
    assert f["ocr_latex"] == r"\frac{a}{b}" and f["latex"] == r"\frac{a}{b}" and not f["edited"]
    g = sess.update(f["id"], latex=r"\frac{a}{c}", status="confirmed")
    assert g["ocr_latex"] == r"\frac{a}{b}" and g["edited"] and g["status"] == "confirmed"
    hist = [json.loads(x) for x in (sess.workdir / "history.jsonl").read_text().splitlines()]
    assert [h["action"] for h in hist][-3:] == ["create", "recognize", "update"]
    with pytest.raises(SessionError):
        sess.update(f["id"], status="embedded")  # 内部状態は直接設定できない
    with pytest.raises(SessionError):
        sess.create(0, [10, 10, 11, 11])


def test_reload_restores_workdir_state(fx, sess):
    sess.update("p0_quad", latex="y=1")
    s2 = ViewerSession(sess.input, sess.workdir, formulas_path=fx["scan_json"])
    assert next(f for f in s2.fs.formulas if f.id == "p0_quad").latex == "y=1"
    s2.close()


def test_export_uses_corrections(sess):
    sess.update("p0_quad", latex=r"x=\dfrac{1}{2}")
    r = sess.export()
    assert r["ok"], r
    doc = pymupdf.open(r["output"])
    q = next(f for f in sess.fs.formulas if f.id == "p0_quad")
    assert doc[0].get_text("text", clip=pymupdf.Rect(q.rect)).strip() == r"x=\dfrac{1}{2}"
    r2 = sess.export(only_confirmed=True)  # 全件 confirmed のフィクスチャ
    assert r2["ok"] and r2["output"] != r["output"]


def test_crop_upright_on_rotated_page(fx):
    """回転ページの切り出しが、見た目ページの同じ式と一致する（認識器に正立画像を渡す）。"""
    doc = pymupdf.open(fx["scan"])
    vis = pymupdf.open(fx["visual"])
    fs = FormulaSet.load(fx["scan_json"])
    for fid in ("p_rot90", "p_rot180", "p_rot270"):
        f = next(x for x in fs.formulas if x.id == fid)
        a = pymupdf.Pixmap(crop_png(doc[f.page], f.rect, dpi=100, pad=0))
        b = pymupdf.Pixmap(crop_png(vis[f.page], fs.meta["visual_rects"][fid], dpi=100, pad=0))
        assert abs(a.width - b.width) <= 1 and abs(a.height - b.height) <= 1
        h, w = min(a.height, b.height), min(a.width, b.width)
        aa = np.frombuffer(a.samples, np.uint8).reshape(a.height, a.width, a.n)[:h, :w, :3].astype(int)
        bb = np.frombuffer(b.samples, np.uint8).reshape(b.height, b.width, b.n)[:h, :w, :3].astype(int)
        assert np.abs(aa - bb).mean() < 30, fid


@pytest.fixture
def server(sess):
    httpd, token = make_server(sess, 0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", token
    httpd.shutdown()
    httpd.server_close()


def _req(url, token=None, method="GET", body=None, host=None):
    headers = {}
    if token:
        headers["X-Token"] = token
    if host:
        headers["Host"] = host
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


def test_http_security(server):
    base, token = server
    assert _req(base + "/api/doc")[0] == 401
    assert _req(base + "/api/doc", token="wrong")[0] == 401
    assert _req(base + "/api/doc", token=token, host="evil.example:80")[0] == 403
    st, body, hdr = _req(base + "/")
    assert st == 200 and b"PDF LaTeX Overlay" in body and "frame-ancestors 'none'" in hdr["Content-Security-Policy"]
    assert _req(base + "/../pyproject.toml")[0] == 404
    assert _req(base + "/session.py")[0] == 404
    assert _req(base + "/api/formulas/..%2Fx/recognize", token=token, method="POST")[0] in (400, 404)


def test_http_flow(server):
    base, token = server
    st, body, _ = _req(base + "/api/doc", token=token)
    assert st == 200 and len(json.loads(body)["pages"]) == 6
    st, body, hdr = _req(base + "/api/page/2.png", token=token)
    assert st == 200 and hdr["Content-Type"] == "image/png"
    st, body, _ = _req(base + "/api/formulas", token=token, method="POST",
                       body={"page": 0, "view_rect": [100, 450, 200, 480], "recognize": True})
    f = json.loads(body)
    assert st == 200 and f["latex"] == r"\frac{a}{b}"
    st, body, _ = _req(base + f"/api/formulas/{f['id']}", token=token, method="POST", body={"latex": "z"})
    assert json.loads(body)["edited"] is True
    st, body, _ = _req(base + "/api/export", token=token, method="POST", body={"mode": "invisible"})
    r = json.loads(body)
    assert st == 200 and r["ok"] and r["counts"]["embedded"] == 11
    assert _req(base + "/api/export", token=token, method="POST", body={"mode": "rm -rf"})[0] == 400
    st, body, _ = _req(base + f"/api/formulas/{f['id']}", token=token, method="DELETE")
    assert st == 200
