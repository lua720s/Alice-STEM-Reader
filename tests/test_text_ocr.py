"""日本語などの文章の認識（PaddleOCR）。2026-11-19 のユーザー指示。実際のOCRは使わず、偽のバックエンドで流れを調べる。"""

import json
import shutil
import threading
import time
import urllib.request

import pymupdf
import pytest

from pdf_latex_overlay import pipeline
from pdf_latex_overlay.models import Formula, FormulaSet
from pdf_latex_overlay.prepare import process_text
from pdf_latex_overlay.recognize.textocr import looks_like_text, ocr_page_text
from pdf_latex_overlay.viewer.session import SessionError, ViewerSession


class FakeOCR:
    """決まった行を返す。渡された画像も記録する（数式の枠が塗りつぶされているか調べるため）。"""

    version = "fake"

    def __init__(self, lines=None):
        self.lines = lines or [{"text": "次の定積分を求めよ", "score": 0.97, "box": [100, 100, 500, 140]},
                               {"text": "x4", "score": 0.99, "box": [100, 200, 160, 240]},  # 数式の断片: 文章ではない
                               {"text": "あいうえお", "score": 0.30, "box": [100, 300, 400, 340]}]  # 信頼度が低い
        self.images = []

    def read(self, png):
        self.images.append(png)
        return self.lines


def _pdf(fx, tmp_path):
    p = tmp_path / "a.pdf"
    shutil.copy(fx["scan"], p)
    return p


def test_looks_like_text():
    assert looks_like_text("次の定積分を求めよ") and looks_like_text("Find the integral") and looks_like_text("ひらがな")
    for s in ("x4", "16", "****", "∫e−dt=− e−1+ C", "(1)"):
        assert not looks_like_text(s), s


def test_ocr_page_text_masks_formula_boxes_and_filters(fx, tmp_path):
    doc = pymupdf.open(_pdf(fx, tmp_path))
    page = doc[0]
    fake = FakeOCR()
    out = ocr_page_text(fake, page, avoid_view_rects=[(20, 20, 60, 60)], dpi=150)
    assert [o["text"] for o in out] == ["次の定積分を求めよ"]  # 数式の断片と、低い信頼度は除く
    k = 72 / 150
    assert out[0]["view"][0] == pytest.approx(100 * k, abs=0.1) and out[0]["rect"][2] > out[0]["rect"][0]
    pix = pymupdf.Pixmap(fake.images[0])
    px = int(40 * 150 / 72)
    assert pix.pixel(px, px) == (255, 255, 255)  # 数式の枠の中は、塗りつぶされている
    doc.close()


def test_process_text_adds_text_boxes_and_respects_edits(fx, tmp_path):
    pdf = _pdf(fx, tmp_path)
    fs = FormulaSet(formulas=[Formula(id="f1", page=0, rect=(20, 20, 60, 60), latex="x", status="confirmed")])
    be = FakeOCR()
    r = process_text(pdf, fs, text_backend=be, only_pages=[0])
    assert r == {"state": "done", "lines": 1}
    t = [f for f in fs.formulas if f.kind == "text"]
    assert len(t) == 1 and t[0].latex == "次の定積分を求めよ" and t[0].source == "ppocr" and t[0].status == "pending"
    assert fs.meta["text_pages"] == [0] and len(be.images) == 1
    # 処理済みのページは、読み直さない
    assert process_text(pdf, fs, text_backend=be, only_pages=[0])["lines"] == 0 and len(be.images) == 1
    # 修正した文章は、force で読み直しても残る
    t[0].latex, t[0].edited = "直した文章", True
    process_text(pdf, fs, text_backend=be, only_pages=[0], force=True)
    texts = [f for f in fs.formulas if f.kind == "text"]
    assert [f.latex for f in texts] == ["直した文章"]  # 重なる新しい読み取りは足さない
    assert any(f.id == "f1" for f in fs.formulas)  # 数式は消えない


def _texts(fx):
    page = pymupdf.open(fx["scan"])[0].rect
    w = page.width - 40
    texts = ["次の定積分を求めよ", "(1) 1回の部分積分では求まらないので，再度，部分積分をする.", "例题156 定積分の部分積分法(2)", "解答", "解答"]
    return [Formula(id=f"t{i}", page=0, rect=(20, 60 + i * 45, 20 + w * (0.5 if i == 0 else 1), 88 + i * 45), latex=t,
                    kind="text", status="confirmed") for i, t in enumerate(texts)]


def test_japanese_text_is_embedded_invisibly_and_fully_verified(fx, tmp_path):
    """日本語の文章を不可視テキストとして埋め込み、外観・本文・MuPDF・PDFKit の検証がすべて通る（同じ文が2回出ても可）。"""
    from pdf_latex_overlay import copycheck

    fs = FormulaSet(formulas=_texts(fx) + [Formula(id="f1", page=0, rect=(20, 330, 150, 356), latex="x^{2}+1", status="confirmed")])
    out = tmp_path / "ja.pdf"
    rep = pipeline.process_pdf(fx["scan"], out, fs, pipeline.Options(pdfkit="require" if copycheck.pdfkit_available() else "off"))
    assert rep.exit_code == 0 if hasattr(rep, "exit_code") else True
    assert all(v.get("ok") is not False for v in rep.checks.values()), rep.checks
    assert rep.checks["appearance"]["ok"] and rep.checks["mupdf_copy"]["ok"]
    assert [f["status"] for f in rep.formulas] == ["embedded"] * 6
    doc = pymupdf.open(out)
    got = doc[0].get_text("text", clip=pymupdf.Rect(20, 60, 400, 90)).strip()
    assert got == "次の定積分を求めよ"  # 実際に選択・検索できる
    assert doc[0].search_for("例题156")  # 簡体字の「题」も入る
    fonts = [f for f in doc.get_page_fonts(0) if "PLOSUBS" in f[3] or "Droid" in f[3]]
    assert fonts, doc.get_page_fonts(0)  # サブセットフォントが入っている
    assert out.stat().st_size - fx["scan"].stat().st_size < 150_000  # サブセットなので、小さい
    doc.close()


def test_text_unsupported_char_and_priority(fx):
    doc = pymupdf.open(fx["scan"])
    bad = Formula(id="b", page=0, rect=(20, 60, 200, 88), latex="これは😀です", kind="text", status="confirmed")
    ok = Formula(id="o", page=0, rect=(20, 120, 200, 148), latex="文章", kind="text", status="confirmed")
    out = {f.id: f for f in pipeline.classify(doc, [bad, ok], pipeline.Options())}
    assert out["b"].status == "excluded" and out["b"].reason == "unsupported_char"
    assert out["o"].status == "embedded"
    # 数式と重なる文章は、数式を優先する（数式を残し、文章を除く）
    fm = Formula(id="m", page=0, rect=(30, 125, 90, 145), latex="x^{2}", status="pending")
    txt = Formula(id="t", page=0, rect=(20, 120, 200, 148), latex="文章", kind="text", status="confirmed")
    res = {f.id: f for f in pipeline.classify(doc, [txt, fm], pipeline.Options())}
    assert res["m"].status == "embedded" and res["t"].status == "excluded" and res["t"].reason.startswith("overlaps:")
    doc.close()


def _session(fx, tmp_path):
    pdf = _pdf(fx, tmp_path)
    return ViewerSession(pdf, tmp_path / "work")


def test_session_start_text_runs_and_reports(fx, tmp_path):
    s = _session(fx, tmp_path)
    be = FakeOCR()
    st = s.start_text(pages=[0], backend=be)
    assert st["state"] == "running"
    for _ in range(200):
        if s.auto_status()["text"]["state"] != "running":
            break
        time.sleep(0.05)
    ts = s.auto_status()["text"]
    assert ts["state"] == "done" and ts["lines"] == 1
    fl = s.list()["formulas"]
    assert [f["kind"] for f in fl] == ["text"] and fl[0]["latex"] == "次の定積分を求めよ"
    with pytest.raises(SessionError) as e:  # 文章の枠は、数式として再認識しない
        s.recognize(fl[0]["id"])
    assert e.value.status == 409
    for bad in ([], [99], ["a"], "0"):
        with pytest.raises(SessionError):
            s.start_text(pages=bad, backend=be)
    s.close()


def test_session_export_includes_text(fx, tmp_path):
    s = _session(fx, tmp_path)
    s.start_text(pages=[0], backend=FakeOCR())
    for _ in range(200):
        if s.auto_status()["text"]["state"] != "running":
            break
        time.sleep(0.05)
    r = s.export()
    assert r["ok"] is True and r["counts"]["embedded"] == 1, r
    doc = pymupdf.open(r["output"])
    assert "次の定積分を求めよ" in doc[0].get_text()
    doc.close()
    s.close()


def test_too_small_text_is_excluded(fx):
    doc = pymupdf.open(fx["scan"])
    tiny = Formula(id="n", page=0, rect=(20, 60, 25, 65), latex="二", kind="text", status="confirmed")  # 「=」の読み違いなどのノイズ
    out = pipeline.classify(doc, [tiny], pipeline.Options())
    assert out[0].status == "excluded" and out[0].reason == "too_small"
    doc.close()


def test_failed_text_lines_are_dropped_but_other_failures_still_stop():
    emb = [Formula(id="t", page=0, rect=(0, 0, 50, 20), latex="a", kind="text"), Formula(id="f", page=0, rect=(0, 30, 50, 50), latex="x")]
    # 文章だけが選択の検証に落ちた: その行だけを除いてやり直す
    ids = pipeline._failed_text_ids({"mupdf_copy": {"ok": False, "failed": ["t"]}, "pdfkit_copy": {"ok": True}}, ["mupdf_copy"], emb)
    assert ids == {"t"}
    # 数式が落ちた、外観が落ちた、PDFKit がエラー: 除かずに止める（失敗時停止）
    assert pipeline._failed_text_ids({"mupdf_copy": {"ok": False, "failed": ["f"]}}, ["mupdf_copy"], emb) is None
    assert pipeline._failed_text_ids({"mupdf_copy": {"ok": False, "failed": ["t"]}, "appearance": {"ok": False}},
                                     ["mupdf_copy", "appearance"], emb) is None
    assert pipeline._failed_text_ids({"pdfkit_copy": {"ok": False, "error": "x"}}, ["pdfkit_copy"], emb) is None
