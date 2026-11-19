"""開く前に認識しておく（prepare）とライブラリ（2026-11-19 のユーザー指示）。"""

import json
import os
import shutil
import time

import pymupdf
import pytest

from pdf_latex_overlay import prepare
from pdf_latex_overlay.models import FormulaSet
from pdf_latex_overlay.recognize import FixedBackend
from pdf_latex_overlay.viewer.session import SessionError, ViewerSession


class FakeDetector:
    """ページ画像の大きさからページを特定し、既知の矩形を検出結果として返す。"""

    info = {"versions": {"fake": "1"}}

    def __init__(self, fs, pdf):
        doc = pymupdf.open(pdf)
        self.by_size, self.calls = {}, 0
        for f in fs.formulas:
            page = doc[f.page]
            v = pymupdf.Rect(f.rect) * page.rotation_matrix
            key = (round(page.rect.width), round(page.rect.height), f.page)
            self.by_size.setdefault(key, []).append([c * 200 / 72 for c in v] + [1, 0.9])
        self.order = [k for k in sorted({k for k in self.by_size}, key=lambda k: k[2])]
        self.pages = doc.page_count

    def detect(self, png, conf=0.1):
        page = self.calls % self.pages
        self.calls += 1
        return next((v for k, v in self.by_size.items() if k[2] == page), [])

    def close(self):
        pass


@pytest.fixture
def lib(tmp_path):
    return tmp_path / "library"


def _setup(fx, tmp_path, name="book.pdf"):
    src = tmp_path / name
    shutil.copy(fx["scan"], src)
    truth = FormulaSet.load(fx["scan_json"])
    return src, truth


def test_prepare_then_viewer_opens_without_processing(fx, tmp_path, lib):
    src, truth = _setup(fx, tmp_path)
    before = src.read_bytes()
    det, be = FakeDetector(truth, src), FixedBackend([r"x ^ { 2 }"])
    r = prepare.prepare_one(src, detector=det, backend=be, library=lib, log=lambda m: None)
    assert r["meta_state"] == "done" and r["found"] == len(truth.formulas) and r["recognized"] == len(truth.formulas)
    assert src.read_bytes() == before  # 原本は読むだけ
    d, sha = prepare.entry_dir(src, lib)
    meta = json.loads((d / "meta.json").read_text())
    assert meta["state"] == "done" and meta["pages"] == 6 and meta["counts"]["with_latex"] == len(truth.formulas)
    assert not (d / ".prepare.lock").exists()

    # ビューアは準備済みの結果を読むだけ（検出・認識を呼ばない）
    calls = be.calls
    s = ViewerSession(src, d, backend="fixed", backend_obj=be)
    det2 = FakeDetector(truth, src)
    t0 = time.monotonic()
    s.start_auto(detector=det2)
    while s.auto_status()["state"] == "running":
        time.sleep(0.02)
    assert det2.calls == 0 and be.calls == calls
    assert time.monotonic() - t0 < 2
    assert len(s.list()["formulas"]) == len(truth.formulas)
    s.close()


def test_library_is_keyed_by_content(fx, tmp_path, lib):
    src, truth = _setup(fx, tmp_path)
    prepare.prepare_one(src, detector=FakeDetector(truth, src), backend=FixedBackend(["a"]), library=lib, log=lambda m: None)
    moved = tmp_path / "sub" / "renamed.pdf"
    moved.parent.mkdir()
    shutil.move(src, moved)
    det = FakeDetector(truth, moved)
    r = prepare.prepare_one(moved, detector=det, backend=FixedBackend(["a"]), library=lib, log=lambda m: None)
    assert det.calls == 0 and r["found"] == 0  # 同じ中身なので、検出し直さない
    items = prepare.list_library(lib)
    assert len(items) == 1 and items[0]["name"] == "renamed.pdf" and len(items[0]["source_paths"]) == 2


def test_interrupt_and_resume(fx, tmp_path, lib):
    src, truth = _setup(fx, tmp_path)
    n = {"k": 0}

    def stop():
        n["k"] += 1
        return n["k"] > 8  # 検出の途中で止める

    r = prepare.prepare_one(src, detector=FakeDetector(truth, src), backend=FixedBackend(["a"]), library=lib,
                            should_stop=stop, log=lambda m: None)
    assert r["meta_state"] == "interrupted"
    d, _ = prepare.entry_dir(src, lib)
    partial = FormulaSet.load(d / "formulas.json")
    done_pages = len(partial.meta["auto_pages"])
    assert 0 < done_pages <= 6
    det = FakeDetector(truth, src)
    det.calls = done_pages  # 残りのページから
    r2 = prepare.prepare_one(src, detector=det, backend=FixedBackend(["a"]), library=lib, log=lambda m: None)
    assert r2["meta_state"] == "done"
    assert det.calls - done_pages == 6 - done_pages  # 処理済みのページは検出しない
    fs = FormulaSet.load(d / "formulas.json")
    assert all(f.latex for f in fs.formulas)


def test_max_pages_is_partial(fx, tmp_path, lib):
    src, truth = _setup(fx, tmp_path)
    r = prepare.prepare_one(src, detector=FakeDetector(truth, src), backend=FixedBackend(["a"]), library=lib,
                            max_pages=2, log=lambda m: None)
    assert r["meta_state"] == "partial"


def test_skips_pdf_open_in_viewer_and_viewer_is_readonly_while_preparing(fx, tmp_path, lib):
    src, truth = _setup(fx, tmp_path)
    d, _ = prepare.entry_dir(src, lib)
    d.mkdir(parents=True)
    (d / ".viewer.lock").write_text(str(os.getpid()))
    r = prepare.prepare_one(src, detector=FakeDetector(truth, src), backend=FixedBackend(["a"]), library=lib, log=lambda m: None)
    assert r["state"] == "skipped" and "ビューア" in r["reason"]
    (d / ".viewer.lock").unlink()

    s = ViewerSession(src, d, backend="fixed", backend_obj=FixedBackend(["a"]))
    (d / ".prepare.lock").write_text(str(os.getpid()))  # 事前処理が実行中
    with pytest.raises(SessionError):
        s.create(0, [100, 450, 200, 480])
    assert s.auto_status()["state"] == "preparing"
    # 事前処理が書いた結果を読み直す
    fs = FormulaSet(input_sha256=s.input_sha, formulas=[f for f in truth.formulas[:2]])
    time.sleep(0.01)
    fs.save(d / "formulas.json")
    os.utime(d / "formulas.json", None)
    s.auto_status()
    assert len(s.list()["formulas"]) == 2
    (d / ".prepare.lock").unlink()
    s.create(0, [100, 450, 200, 480])  # 終われば編集できる
    s.close()


def test_stale_lock_is_cleared(tmp_path):
    p = tmp_path / ".prepare.lock"
    p.write_text("999999")  # 存在しない pid
    assert prepare.lock_holder(p) is None and not p.exists()


def test_collect_pdfs(tmp_path, fx):
    (tmp_path / "a").mkdir()
    shutil.copy(fx["scan"], tmp_path / "a" / "x.pdf")
    shutil.copy(fx["scan"], tmp_path / "y.pdf")
    (tmp_path / "a" / "note.txt").write_text("x")
    got = prepare.collect_pdfs([tmp_path])
    assert [p.name for p in got] == ["x.pdf", "y.pdf"]
    assert [p.name for p in prepare.collect_pdfs([tmp_path], recursive=False)] == ["y.pdf"]
