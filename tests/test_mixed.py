"""文章と数式が混ざった領域を、Mathpix Snip のように読む（2026-11-19 のユーザー指示）。偽のOCR・検出器・認識器で流れを調べる。"""

import shutil

import pymupdf
import pytest

from pdf_latex_overlay.detect.mfd import DETECT_DPI, detect_page
from pdf_latex_overlay.recognize.base import FixedBackend
from pdf_latex_overlay.recognize.mixed import group_rows, join_row, recognize_mixed
from pdf_latex_overlay.viewer.session import ViewerSession

DPI = 200
K = DPI / 72.0
REGION = (20, 60, 300, 140)  # 見た目の座標（pt）


def px(x0, y0, x1, y1):
    """領域の切り抜き画像（200 dpi）での位置。"""
    return [int((x0 - REGION[0]) * K), int((y0 - REGION[1]) * K), int((x1 - REGION[0]) * K), int((y1 - REGION[1]) * K)]


class FakeOCR:
    version = "fake"

    def __init__(self, lines):
        self.lines = lines
        self.calls = 0

    def read(self, png, words=False):
        self.calls += 1
        return self.lines


class FakeDetector:
    """数式の枠（ページ全体の座標 pt）を、検出器の出力（DETECT_DPI の画素）として返す。"""
    info = {}

    def __init__(self, boxes_pt):
        self.boxes = boxes_pt

    def detect(self, png, conf=0.1):
        k = DETECT_DPI / 72.0
        return [(x0 * k, y0 * k, x1 * k, y1 * k, cls, 0.9) for x0, y0, x1, y1, cls in self.boxes]

    def close(self):
        pass


def _page(fx, tmp_path):
    p = tmp_path / "a.pdf"
    shutil.copy(fx["scan"], p)
    return p


def _two_row_ocr():
    """1行目: 「次の問いに答えよ」。2行目: 「(1) [式] として、 [誤検出の枠の中の漢字]」。"""
    return FakeOCR([
        {"text": "次の問いに答えよ", "score": 0.97, "box": px(30, 62, 200, 78),
         "words": [{"text": "次の問い", "box": px(30, 62, 110, 78)}, {"text": "に答えよ", "box": px(110, 62, 200, 78)}]},
        {"text": "(1) 21=1+20 として 二項定理", "score": 0.95, "box": px(30, 90, 290, 108),
         "words": [{"text": "(1)", "box": px(30, 90, 50, 108)}, {"text": "21=1+20", "box": px(60, 90, 120, 108)},
                   {"text": "として", "box": px(130, 90, 170, 108)}, {"text": "二項定理", "box": px(180, 90, 240, 108)}]},
    ])


def test_group_rows_and_join():
    items = [{"kind": "text", "text": "b", "view": (60, 12, 70, 30)}, {"kind": "text", "text": "a", "view": (10, 10, 20, 30)},
             {"kind": "text", "text": "c", "view": (10, 50, 20, 70)}]
    rows = group_rows(items)
    assert [[i["text"] for i in r] for r in rows] == [["a", "b"], ["c"]]
    assert join_row([{"kind": "text", "text": "次の", "view": (0, 0, 10, 10)}, {"kind": "text", "text": "問い", "view": (10, 0, 20, 10)}]) == "次の問い"
    assert join_row([{"kind": "text", "text": "(1)", "view": (0, 0, 10, 10)},
                     {"kind": "formula", "text": r"\(x\)", "view": (14, 0, 24, 10)}]) == r"(1) \(x\)"


def test_mixed_region_becomes_text_with_inline_math_and_drops_false_formula_boxes(fx, tmp_path):
    doc = pymupdf.open(_page(fx, tmp_path))
    boxes = detect_page(FakeDetector([(60, 90, 120, 108, 0), (180, 90, 240, 108, 0)]), doc[0])  # 2つ目は、漢字を式と誤検出した枠
    r = recognize_mixed(doc[0], REGION, boxes, FixedBackend(["21=1+20"]), _two_row_ocr())
    assert r is not None
    assert r.latex.split("\n") == ["次の問いに答えよ", r"(1) \(21=1+20\) として二項定理"]
    assert r.dropped_boxes == 1  # 漢字を含む枠は、文章として扱う
    doc.close()


def test_formula_only_region_is_left_to_the_formula_path(fx, tmp_path):
    doc = pymupdf.open(_page(fx, tmp_path))
    ocr = _two_row_ocr()
    # 枠が領域のほとんどを占める: OCR もせずに None
    boxes = detect_page(FakeDetector([(22, 62, 298, 138, 1)]), doc[0])
    assert recognize_mixed(doc[0], REGION, boxes, FixedBackend(["x"]), ocr) is None and ocr.calls == 0
    # 文章がない: None
    only_math = FakeOCR([{"text": "x2", "score": 0.9, "box": px(30, 62, 60, 78), "words": [{"text": "x2", "box": px(30, 62, 60, 78)}]}])
    assert recognize_mixed(doc[0], REGION, [], FixedBackend(["x"]), only_math) is None
    doc.close()


def _session(fx, tmp_path, boxes, ocr, answers):
    p = _page(fx, tmp_path)
    s = ViewerSession(p, tmp_path / "work", backend="fixed", backend_obj=FixedBackend(answers), detector_obj=FakeDetector(boxes))
    s._text_backend_override = ocr
    return s


def test_drawn_mixed_box_is_read_like_snip_and_can_be_reread(fx, tmp_path):
    s = _session(fx, tmp_path, [(60, 90, 120, 108, 0)], _two_row_ocr(), ["21=1+20"])
    f = s.create(0, REGION, recognize=True)
    assert f["kind"] == "text" and f["source"] == "mixed"
    assert f["latex"].split("\n")[0] == "次の問いに答えよ" and r"\(21=1+20\)" in f["latex"]
    f2 = s.recognize(f["id"])  # 混在の枠は、読み直せる
    assert f2["latex"] == f["latex"]
    s.close()


def test_drawn_formula_box_keeps_the_old_formula_path(fx, tmp_path):
    ocr = FakeOCR([])
    s = _session(fx, tmp_path, [(22, 62, 298, 138, 1)], ocr, ["x^{2}"])
    f = s.create(0, REGION, recognize=True)
    assert f["kind"] is None and f["latex"].startswith("x^{2}") and ocr.calls == 0  # 数式だけ: 従来どおり、OCRなし
    s.close()


def test_math_runs_and_punctuation_are_kept():
    from pdf_latex_overlay.recognize.mixed import math_runs

    def w(t, x0, x1):
        return {"text": t, "view": (x0, 0, x1, 10), "score": 0.9}

    words = [w("(", 0, 3), w("1", 3, 8), w(")", 8, 11), w("次の", 14, 30), w("21=1+20", 34, 80), w("として", 84, 100)]
    runs, loose = math_runs(words)
    assert [[x["text"] for x in r] for r in runs] == [["21=1+20"]]  # 数式らしい連なり
    assert [x["text"] for x in loose] == ["(", "1", ")", "次の", "として"]  # 番号「(1)」と括弧は、文章のまま残る


def test_join_spacing_and_display_becomes_inline_with_text():
    def t(s, x0, x1):
        return {"kind": "text", "text": s, "view": (x0, 0, x1, 10)}

    # 「(」「1」「)」は詰め、日本語どうしも詰める
    assert join_row([t("(", 0, 3), t("1", 3.5, 8), t(")", 8.5, 11), t("次", 11.5, 21), t("の", 21.5, 31)]) == "(1)次の"
