"""開いた時点での全ページ自動認識（検出 → 認識）とクリックでコピー（2026-11-19 のユーザー指示）。"""

import shutil
import time

import pymupdf
import pytest

from pdf_latex_overlay.detect.mfd import ROOT, MFDDetector, detect_page
from pdf_latex_overlay.models import FormulaSet
from pdf_latex_overlay.recognize import FixedBackend
from pdf_latex_overlay.viewer.session import ViewerSession


class FakeDetector:
    """見た目ページの既知の矩形を、検出結果（200 dpi のピクセル）として返す。"""

    info = {"versions": {"fake": "1"}}

    def __init__(self, fs, doc):
        self.by_page = {}
        for f in fs.formulas:
            v = pymupdf.Rect(f.rect) * doc[f.page].rotation_matrix
            self.by_page.setdefault(f.page, []).append([c * 200 / 72 for c in v] + [1, 0.9])
        self.calls = 0

    def detect(self, png, conf=0.1):
        pix = pymupdf.Pixmap(png)
        # ページ番号は呼ばれた順（全ページを順に処理する）
        page = self.calls
        self.calls += 1
        return self.by_page.get(page, [])

    def close(self):
        pass


def _wait(s, timeout=60):
    t0 = time.time()
    while s.auto_status()["state"] == "running" and time.time() - t0 < timeout:
        time.sleep(0.05)
    return s.auto_status()


def test_auto_detects_and_recognizes_all_pages(fx, tmp_path):
    src = tmp_path / "s.pdf"
    shutil.copy(fx["scan"], src)
    truth = FormulaSet.load(fx["scan_json"])
    det = FakeDetector(truth, pymupdf.open(src))
    s = ViewerSession(src, tmp_path / "w", backend="fixed", backend_obj=FixedBackend([r"x ^ { 2 }"]))
    manual = s.create(0, list(pymupdf.Rect(truth.formulas[0].rect)))  # 手で描いた枠は残し、重ねて追加しない
    s.start_auto(detector=det)
    st = _wait(s)
    assert st["state"] == "done", st
    fs = s.list()["formulas"]
    auto = [f for f in fs if f["id"].startswith("auto_")]
    assert len(auto) == len(truth.formulas) - 1  # 手で描いた1つと重なる分を除く
    assert all(f["latex"] == "x^{2}" and f["source"].startswith("mfd") for f in auto)
    assert any(f["id"] == manual["id"] for f in fs)
    # 再度実行しても、処理済みのページは検出し直さない
    v = st["version"]
    s.start_auto(detector=FakeDetector(truth, pymupdf.open(src)))
    st2 = _wait(s)
    assert len(s.list()["formulas"]) == len(fs)
    assert st2["found"] == 0
    s.close()
    # 開き直しても結果が残る
    s2 = ViewerSession(src, tmp_path / "w")
    assert len(s2.list()["formulas"]) == len(fs)
    s2.close()
    assert v > 0


def test_auto_without_backend_is_refused(fx, tmp_path):
    from pdf_latex_overlay.viewer.session import SessionError
    src = tmp_path / "s.pdf"
    shutil.copy(fx["scan"], src)
    s = ViewerSession(src, tmp_path / "w")
    with pytest.raises(SessionError):
        s.start_auto(detector=FakeDetector(FormulaSet(), pymupdf.open(src)))
    s.close()


HAVE_MFD = (ROOT / "models" / "mfd_yolo_v8" / "yolo_v8_ft.pt").is_file() and (ROOT / ".venv-ml" / "bin" / "python").exists()


@pytest.mark.model
@pytest.mark.skipif(not HAVE_MFD, reason="数式検出モデルが未導入")
def test_mfd_real_on_fixture(fx):
    """実モデル: 合成フィクスチャの表示数式の多くを見つける（回転ページを含む）。"""
    from pdf_latex_overlay.evaluate import compare_regions
    doc = pymupdf.open(fx["scan"])
    truth = FormulaSet.load(fx["scan_json"])
    det = MFDDetector()
    found = FormulaSet()
    from pdf_latex_overlay.models import Formula
    for pno in range(doc.page_count):
        for n, d in enumerate(detect_page(det, doc[pno]), 1):
            found.formulas.append(Formula(id=f"a{pno}_{n}", page=pno, rect=d["rect"]))
    det.close()
    target = FormulaSet(formulas=[f for f in truth.formulas if f.id not in ("p0_textlayer", "p5_clip")])
    r = compare_regions(target, found, threshold=0.3)
    assert len(r["matched"]) >= 6, r
