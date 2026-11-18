"""段階0: 合成フィクスチャ、座標変換、原本保護、外観検査。"""

import os
import shutil

import numpy as np
import pymupdf
import pytest

from pdf_latex_overlay import coords, pdfcheck, safety
from pdf_latex_overlay.models import FormulaSet


def _crop(pix, r):
    a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    x0, y0, x1, y1 = (int(round(v)) for v in r)
    return a[max(0, y0):y1, max(0, x0):x1, :3].astype(int)


@pytest.mark.parametrize("variant", ["vector", "scan"])
def test_fixture_page_set(fx, variant):
    doc = pymupdf.open(fx[variant])
    assert doc.page_count == 6
    assert [p.rotation for p in doc] == [0, 0, 90, 180, 270, 0]
    assert tuple(doc[5].cropbox_position) != (0, 0)  # 非ゼロ CropBox
    fs = FormulaSet.load(fx[f"{variant}_json"])
    assert {f.page for f in fs.formulas} == {0, 2, 3, 4, 5}
    assert any("\\" in f.latex and "{" in f.latex for f in fs.formulas)
    if variant == "scan":
        assert all(not p.get_text().strip() for p in doc)  # 画像のみ
    else:
        assert "selectable" in doc[0].get_text()


@pytest.mark.parametrize("variant", ["vector", "scan"])
def test_expected_rects_hit_formula_ink(fx, variant):
    """期待矩形を描画座標へ写すと、見た目ページの同じ位置と一致する（回転・CropBox の往復確認）。"""
    doc = pymupdf.open(fx[variant])
    vis = pymupdf.open(fx["visual"])
    fs = FormulaSet.load(fx[f"{variant}_json"])
    dpi = 144
    for f in fs.formulas:
        if f.id == "p5_clip":
            continue
        page = doc[f.page]
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
        rt = coords.RenderTransform.for_page(page, dpi)
        px = rt.to_pixels(f.rect)
        vr = fs.meta["visual_rects"][f.id]
        vpix = vis[f.page].get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
        a = _crop(pix, px)
        b = _crop(vpix, [v * dpi / 72 for v in vr])
        h, w = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
        assert h > 5 and w > 5, f.id
        assert (a[:h, :w] < 128).any(), f"{f.id}: 期待矩形にインクがない"
        tol = 40 if variant == "scan" else 20
        assert np.abs(a[:h, :w] - b[:h, :w]).mean() < tol, f.id
        # 往復
        back = rt.from_pixels(px)
        assert np.allclose(back, f.rect, atol=1e-6)


def test_render_transform_with_clip_origin(fx):
    doc = pymupdf.open(fx["vector"])
    for page in doc:
        rt = coords.RenderTransform.for_page(page, 200, clip_origin=(12.5, 30.0))
        r = (10.0, 20.0, 60.0, 45.0)
        assert np.allclose(rt.from_pixels(rt.to_pixels(r)), r, atol=1e-6)


def test_stored_to_pdf_user_nonzero_cropbox(fx):
    doc = pymupdf.open(fx["vector"])
    page = doc[5]
    ox, oy = page.cropbox_position
    r = coords.stored_to_pdf_user(page, (0, 0, 10, 10))
    # 保存座標 (0,0) は CropBox 左上 = PDF ユーザー空間 (cropbox.x0, cropbox.y1)
    cb = page.cropbox
    assert abs(r[0] - ox) < 1e-6
    mb_h = page.mediabox.height
    assert abs(r[3] - (mb_h - oy)) < 1e-6
    assert cb.width > 0


def test_clamp(fx):
    page = pymupdf.open(fx["vector"])[5]
    fs = FormulaSet.load(fx["vector_json"])
    clip = next(f for f in fs.formulas if f.id == "p5_clip")
    _, clipped = coords.clamp_to_page(page, clip.rect)
    assert clipped
    ok = next(f for f in fs.formulas if f.id == "p5_limit")
    assert not coords.clamp_to_page(page, ok.rect)[1]


def test_iou():
    assert coords.iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert coords.iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    assert abs(coords.iou((0, 0, 10, 10), (5, 0, 15, 10)) - 1 / 3) < 1e-9


def test_safety_paths(fx, tmp_path):
    src = tmp_path / "in.pdf"
    shutil.copy(fx["vector"], src)
    with pytest.raises(safety.SafetyError):
        safety.check_paths(src, src)
    with pytest.raises(safety.SafetyError):
        safety.check_paths(src, tmp_path / "missing_dir" / "o.pdf")
    existing = tmp_path / "exists.pdf"
    existing.write_bytes(b"x")
    with pytest.raises(safety.SafetyError):
        safety.check_paths(src, existing)
    link = tmp_path / "link.pdf"
    os.symlink(src, link)
    with pytest.raises(safety.SafetyError):
        safety.check_paths(src, link)
    linkdir = tmp_path / "linkdir"
    os.symlink(tmp_path, linkdir)
    with pytest.raises(safety.SafetyError):  # ディレクトリのリンク経由で同一実体
        safety.check_paths(src, linkdir / "in.pdf")
    with pytest.raises(safety.SafetyError):
        safety.check_paths(src, tmp_path / "out.txt")
    safety.check_paths(src, tmp_path / "ok.pdf")


def test_publish_no_overwrite(tmp_path):
    out = tmp_path / "o.pdf"
    t1 = safety.temp_path_for(out)
    t1.write_bytes(b"one")
    safety.publish(t1, out)
    assert out.read_bytes() == b"one" and not t1.exists()
    t2 = safety.temp_path_for(out)
    t2.write_bytes(b"two")
    with pytest.raises(safety.SafetyError):
        safety.publish(t2, out)
    assert out.read_bytes() == b"one" and not t2.exists()


def test_appearance_identical_and_diff(fx, tmp_path):
    same = pdfcheck.compare_appearance(fx["vector"], fx["vector"])
    assert same.identical
    # 1ページに描画を足した複製は差分として検出される
    doc = pymupdf.open(fx["vector"])
    doc[1].draw_rect(pymupdf.Rect(10, 10, 20, 20), color=(1, 0, 0))
    mod = tmp_path / "mod.pdf"
    doc.save(mod)
    res = pdfcheck.compare_appearance(fx["vector"], mod)
    assert not res.identical
    assert res.page_diffs[0]["page"] == 1 and res.page_diffs[0]["count"] > 0


def test_preflight_and_inventory(fx, tmp_path):
    pf = pdfcheck.preflight(fx["vector"])
    assert pf.ok, pf.reasons
    enc = tmp_path / "enc.pdf"
    doc = pymupdf.open(fx["vector"])
    doc.save(enc, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    assert not pdfcheck.preflight(enc).ok
    inv = pdfcheck.structure_inventory(fx["vector"])
    assert inv["page_count"] == 6
    attrs = pdfcheck.page_attributes(fx["vector"])
    assert [a["rotate"] for a in attrs] == [0, 0, 90, 180, 270, 0]


def test_text_conflict_detection(fx):
    page = pymupdf.open(fx["vector"])[0]
    fs = FormulaSet.load(fx["vector_json"])
    by = {f.id: f for f in fs.formulas}
    assert pdfcheck.chars_in_rect(page, by["p0_textlayer"].rect)
    assert pdfcheck.chars_in_rect(page, by["p0_overlap"].rect)
    assert not pdfcheck.chars_in_rect(page, by["p0_quad"].rect)
    assert not pdfcheck.chars_in_rect(page, by["p0_inline"].rect)
