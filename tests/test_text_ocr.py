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


def test_text_boxes_are_never_embedded(fx, tmp_path):
    doc = pymupdf.open(_pdf(fx, tmp_path))
    f = Formula(id="t1", page=0, rect=(100, 100, 300, 130), latex="Ascii text only", kind="text", status="confirmed")
    out = pipeline.classify(doc, [f], pipeline.Options(mode="invisible"))
    assert out[0].status == "excluded" and out[0].reason == "text_not_embedded"  # ASCII だけの文章でも、埋め込まない
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
