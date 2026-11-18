"""段階2c: 既知 LaTeX による埋め込み 2 方式の比較（外観・構造・コピー）。"""

import pymupdf
import pytest

from pdf_latex_overlay import copycheck, embed, pdfcheck, safety
from pdf_latex_overlay.models import FormulaSet


def _embeddable(fx, variant):
    fs = FormulaSet.load(fx[f"{variant}_json"])
    return [f for f in fs.formulas if fs.meta["expect"][f.id] == "embed"]


@pytest.fixture(scope="module")
def outputs(fx, tmp_path_factory):
    d = tmp_path_factory.mktemp("embed")
    res = {}
    for v in ("vector", "scan"):
        before = safety.sha256_file(fx[v])
        for mode in ("invisible", "actualtext"):
            out = d / f"{v}_{mode}.pdf"
            ids = embed.embed_formulas(fx[v], out, _embeddable(fx, v), mode=mode,
                                       actualtext_inner="ALT" if mode == "actualtext" else None)
            assert len(ids) == len(_embeddable(fx, v))
            res[(v, mode)] = out
        assert safety.sha256_file(fx[v]) == before  # 原本不変
    return res


@pytest.mark.parametrize("variant", ["vector", "scan"])
@pytest.mark.parametrize("mode", ["invisible", "actualtext"])
def test_appearance_and_structure_preserved(fx, outputs, variant, mode):
    out = outputs[(variant, mode)]
    for dpi in (72, 150):
        res = pdfcheck.compare_appearance(fx[variant], out, dpi=dpi)
        assert res.identical, res.page_diffs
    assert pdfcheck.page_attributes(fx[variant]) == pdfcheck.page_attributes(out)
    assert pdfcheck.structure_inventory(fx[variant]) == pdfcheck.structure_inventory(out)


@pytest.mark.parametrize("variant", ["vector", "scan"])
def test_original_words_preserved(fx, outputs, variant):
    a, b = pymupdf.open(fx[variant]), pymupdf.open(outputs[(variant, "invisible")])
    rects = {}
    for f in _embeddable(fx, variant):
        rects.setdefault(f.page, []).append(pymupdf.Rect(f.rect))
    for pno in range(a.page_count):
        wa = pdfcheck.words(a[pno])
        wb = pdfcheck.words(b[pno])
        assert all(w in wb for w in wa)
        extra = [w for w in wb if w not in wa]
        for w in extra:  # 追加語は全て式の矩形内
            c = pymupdf.Rect(w[:4])
            center = pymupdf.Point((c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2)
            assert any(center in r for r in rects.get(pno, [])), w


@pytest.mark.parametrize("variant", ["vector", "scan"])
def test_mupdf_extracts_latex_both_modes(fx, outputs, variant):
    fs = _embeddable(fx, variant)
    for mode in ("invisible", "actualtext"):
        got = copycheck.mupdf_extract(outputs[(variant, mode)], fs)
        for f in fs:
            assert got[f.id] == f.latex, (mode, f.id, got[f.id])  # MuPDF は ActualText を採用


@pytest.mark.pdfkit
@pytest.mark.skipif(not copycheck.pdfkit_available(), reason="swift/PDFKit なし")
@pytest.mark.parametrize("variant", ["vector", "scan"])
def test_pdfkit_copy(fx, outputs, variant):
    fs = _embeddable(fx, variant)
    rows = copycheck.copy_table(outputs[(variant, "invisible")], fs)
    for r in rows:
        assert r["pdfkit_norm"], r
        assert r["pdfkit_page_count"] == 1, r  # 二重コピーなし
    # PDFKit は ActualText を使わず、内側の代替文字を返す（実測、2026-11-18）
    rows = copycheck.copy_table(outputs[(variant, "actualtext")], fs)
    assert all(copycheck.normalize(r["pdfkit"]) == "ALT" for r in rows)


@pytest.mark.pdfkit
@pytest.mark.skipif(not copycheck.pdfkit_available(), reason="swift/PDFKit なし")
def test_pdfkit_body_line_has_no_latex(fx, outputs):
    """本文行だけを選ぶと LaTeX が混入しない。"""
    from pdf_latex_overlay.models import Formula
    out = outputs[("vector", "invisible")]
    # 式 p0_inline（x=112〜）の直前の本文「Pythagoras says」だけを選ぶ
    body = Formula(id="body", page=0, rect=(38, 171, 109, 183), latex=None)
    r = copycheck.pdfkit_select(out, [body])
    got = r["selections"]["body"] or ""
    assert "Pythagoras" in got and "\\" not in got and "^" not in got, got


def test_layer_text_normalization():
    assert embed.normalize_for_layer("a\n+\tb") == "a + b"
    assert embed.is_winansi_ascii(r"\frac{a}{b}")
    assert not embed.is_winansi_ascii("\\text{面積}")
