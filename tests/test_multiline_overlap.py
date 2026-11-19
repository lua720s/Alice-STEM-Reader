"""複数行の枠、表示用記法の整形、重なった枠（2026-11-19 のユーザー指示）。"""

import io
import shutil

import pymupdf
import pytest
from PIL import Image, ImageDraw

from pdf_latex_overlay import Options, copycheck, embed, process_pdf
from pdf_latex_overlay.models import Formula, FormulaSet
from pdf_latex_overlay.recognize import FixedBackend, Recognition
from pdf_latex_overlay.recognize.postprocess import clean_latex, looks_degenerate, split_lines
from pdf_latex_overlay.recognize.region import recognize_region
from pdf_latex_overlay.viewer.session import ViewerSession

USER_EXAMPLE = (r"\begin{array}{c}{{\overline{{{\bf\Pi}}}=\displaystyle{\frac{\bf\Pi}{2}}x^{2}(\log x)^{2}}\!\!-\!"
                r"\displaystyle{\frac{1}{2}}x^{2}\log x+\!\!\int\!\!\frac{1}{2}xdx}}\\{{\displaystyle{\frac{\bf\Pi}{2}}"
                r"x^{2}(\log x)^{2}}\!\!-\!\displaystyle{\frac{1}{2}}x^{2}\log x+\!\!\frac{1}{4}x^{2}\!+C}}\end{array}")


def test_clean_user_example_structure():
    out = clean_latex(USER_EXAMPLE).split("\n")
    assert len(out) == 2  # array の2行 → 改行で区切った2行
    for line in out:
        assert "\\displaystyle" not in line and "\\!" not in line and "{{" not in line and "array" not in line
    assert out[1] == r"\frac{\Pi}{2}x^{2}(\log x)^{2}-\frac{1}{2}x^{2}\log x+\frac{1}{4}x^{2}+C"


@pytest.mark.parametrize("raw,want", [
    (r"\textstyle x={\frac{-b\pm{\sqrt{b^{2}-4ac}}}{2a}}", r"x=\frac{-b\pm\sqrt{b^{2}-4ac}}{2a}"),
    (r"\begin{aligned}a&=b\\&=c\end{aligned}", "a=b\n=c"),
    (r"\int{x\log x}dx", r"\int x\log xdx"),
    (r"\int_{}^{}x", r"\int x"),
    (r"\int\!x\,dx\qquad\qquad", r"\int x\,dx"),
    (r"\sqrt[3]{x}+e^{\frac{\sqrt{x}}{2}}", r"\sqrt[3]{x}+e^{\frac{\sqrt{x}}{2}}"),
    (r"\left\{ x \right\}", r"\left\{x\right\}"),
    (r"\sum_{k\mathop{=}1}^{n}", r"\sum_{k\mathop{=}1}^{n}"),
    (r"x}}+y", r"x+y"),
    (r"\mathrm{~}", ""),
])
def test_clean_latex_cases(raw, want):
    assert clean_latex(raw) == want


def test_degenerate():
    assert looks_degenerate(r"\! " * 100)
    assert not looks_degenerate(r"= \frac { 1 } { 2 } x ^ { 2 }")


def _lines_png(n, dpi=200, frame=True, gap_pt=8):
    px = dpi / 72
    h = int((n * 14 + (n - 1) * gap_pt + 6) * px)
    img = Image.new("L", (int(160 * px), h), 255)
    d = ImageDraw.Draw(img)
    y = int(3 * px)
    for _ in range(n):
        d.rectangle([int(10 * px), y, int(150 * px), y + int(14 * px)], fill=0)
        y += int((14 + gap_pt) * px)
    if frame:  # 左端の点線（囲み枠）
        for yy in range(0, h, 6):
            d.rectangle([1, yy, 2, yy + 2], fill=0)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_split_lines_counts():
    assert len(split_lines(_lines_png(1), 200)) == 1
    assert len(split_lines(_lines_png(2), 200)) == 2
    assert len(split_lines(_lines_png(3), 200)) == 3
    blank = io.BytesIO()
    Image.new("L", (100, 100), 255).save(blank, format="PNG")
    assert len(split_lines(blank.getvalue(), 200)) == 1  # 白紙でも落ちない


def test_split_keeps_limits_on_fixture(fx):
    """∑ の上端・下端は行として分けない（合成フィクスチャの全式で1行）。"""
    from pdf_latex_overlay.recognize import crop_png
    for v in ("scan", "vector"):
        fs = FormulaSet.load(fx[f"{v}_json"])
        doc = pymupdf.open(fx[v])
        for f in fs.formulas:
            assert len(split_lines(crop_png(doc[f.page], f.rect, dpi=200), 200)) == 1, (v, f.id)


def _blank_pdf(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=412, height=594)
    pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 20, 20), False)
    pix.clear_with(235)
    page.insert_image(page.rect, pixmap=pix)
    p = tmp_path / "blank.pdf"
    doc.save(p)
    return p


def test_region_joins_lines_and_retries(tmp_path):
    class Seq:
        name, version = "seq", "1"

        def __init__(self):
            # 1回目は2行まとめて（1行目が崩れる）、2回目は1行目の読み直し
            self.out = [r"\! " * 60, r"b", r"a ^ { 2 }"]

        def recognize(self, imgs):
            return [Recognition(self.out.pop(0)) for _ in imgs]

    doc = pymupdf.open()
    page = doc.new_page(width=300, height=200)
    page.insert_image(pymupdf.Rect(10, 10, 170, 60), stream=_lines_png(2, frame=False))
    res = recognize_region(Seq(), page, (10, 10, 170, 60), dpi=200)
    assert res.lines == 2 and res.latex == "a^{2}\nb" and res.error is None


def test_multiline_embed_copies_with_newline(tmp_path):
    src = _blank_pdf(tmp_path)
    latex = "=\\frac{1}{2}x^{2}+C\n=\\frac{1}{4}x^{2}"
    f = Formula(id="m", page=0, rect=(85, 238, 226, 283), latex=latex, status="confirmed")
    rep = process_pdf(src, tmp_path / "o.pdf", FormulaSet(formulas=[f]), Options())
    assert rep.ok, (rep.errors, rep.checks)
    got = copycheck.mupdf_extract(tmp_path / "o.pdf", [f])["m"]
    assert got.splitlines() == latex.splitlines()
    if copycheck.pdfkit_available():
        pk = copycheck.pdfkit_select(tmp_path / "o.pdf", [f])["selections"]["m"]
        assert [x.strip() for x in pk.splitlines() if x.strip()] == latex.splitlines()


def test_overlapping_boxes_priority(tmp_path):
    src = _blank_pdf(tmp_path)
    fs = FormulaSet(formulas=[
        Formula(id="big", page=0, rect=(80, 230, 230, 290), latex="A\nB"),
        Formula(id="line1", page=0, rect=(85, 238, 226, 258), latex="A"),
        Formula(id="line2", page=0, rect=(85, 262, 226, 283), latex="B", status="confirmed"),
        Formula(id="side", page=0, rect=(300, 238, 380, 258), latex="C"),
    ])
    rep = process_pdf(src, tmp_path / "o.pdf", fs, Options())
    assert rep.ok, rep.errors
    st = {f["id"]: (f["status"], f["reason"]) for f in rep.formulas}
    assert st["line1"][0] == st["line2"][0] == st["side"][0] == "embedded"
    assert st["big"][0] == "excluded" and st["big"][1].startswith("overlaps:")
    # 大きい枠を確定すれば、そちらが優先される
    fs.formulas[0].status = "confirmed"
    fs.formulas[2].status = "pending"
    rep2 = process_pdf(src, tmp_path / "o2.pdf", fs, Options())
    st2 = {f["id"]: f["status"] for f in rep2.formulas}
    assert st2["big"] == "embedded" and st2["line1"] == st2["line2"] == "excluded"


def test_viewer_redraw_replaces_unconfirmed(tmp_path, fx):
    src = tmp_path / "s.pdf"
    shutil.copy(fx["scan"], src)
    s = ViewerSession(src, tmp_path / "w", backend="fixed", backend_obj=FixedBackend(["x"]))
    a = s.create(0, [100, 450, 200, 480])
    b = s.create(0, [101, 451, 201, 481])  # ほぼ同じ位置に描き直し → 置き換え
    ids = [f["id"] for f in s.list()["formulas"]]
    assert a["id"] not in ids and b["id"] in ids
    s.update(b["id"], status="confirmed")
    c = s.create(0, [100, 450, 200, 480])  # 確定済みは置き換えない
    ids = [f["id"] for f in s.list()["formulas"]]
    assert b["id"] in ids and c["id"] in ids
    d = s.create(0, [100, 450, 200, 465])  # 一部だけ重なる枠は残す
    assert d["id"] in [f["id"] for f in s.list()["formulas"]]
    s.close()


def test_saving_unchanged_multiline_is_not_edited(tmp_path, fx):
    """複数行の認識結果をそのまま保存しても「修正済み」にしない（実教材のビューアで発生）。"""
    src = tmp_path / "s.pdf"
    shutil.copy(fx["scan"], src)

    class TwoLine:
        name, version = "two", "1"

        def recognize(self, imgs):
            return [Recognition(r"\displaystyle a ^ { 2 }") for _ in imgs]

    s = ViewerSession(src, tmp_path / "w", backend="two", backend_obj=TwoLine())
    f = s.create(0, [40, 60, 300, 100], recognize=True)
    g = s.update(f["id"], latex=f["latex"])
    assert not g["edited"], g
    assert s.update(f["id"], latex=f["latex"] + "+1")["edited"]
    s.close()


def test_junk_line_from_recognizer_is_dropped():
    """空白だけの行（`\\mathrm{~}`）は、複数行の結果から落とす（実教材で発生）。"""
    from pdf_latex_overlay.recognize.postprocess import clean_lines
    assert clean_lines("=\\frac{1}{4}(e^{2}-1)\n\\mathrm{~}") == "=\\frac{1}{4}(e^{2}-1)"
