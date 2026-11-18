"""段階2a: 切り出し認識（バックエンド共通部、再開、失敗時、UniMERNet 実モデル）。"""

import json

import pytest

from pdf_latex_overlay.evaluate import compare_recognition, lenient, squash
from pdf_latex_overlay.models import FormulaSet
from pdf_latex_overlay.recognize import FixedBackend, Recognition
from pdf_latex_overlay.recognize.runner import RecognizeExit, run_recognition
from pdf_latex_overlay.recognize.unimernet import ROOT, UniMERNetBackend, tidy_latex


@pytest.mark.parametrize("raw,want", [
    (r"\begin{array} { r } { x = \frac { a } { b } } \end{array}", r"\begin{array}{r}{x=\frac{a}{b}}\end{array}"),
    (r"\ln x + \alpha b", r"\ln x+\alpha b"),  # 制御語の直後の英字の前は残す
    (r"a \ b", r"a\ b"),  # 制御空白
    (r"\text { if } x", r"\text{ if }x"),  # \text の中は残す
    (r"a \\ b", r"a\\b"),
    (r"x ^ { 2 }", r"x^{2}"),
])
def test_tidy_latex(raw, want):
    assert tidy_latex(raw) == want


def test_lenient_metric():
    assert lenient(r"\textstyle\int_{0}^{1}x^{2}\,dx={\frac{1}{3}}") == lenient(r"\int_{0}^{1}x^{2}\,dx=\frac{1}{3}")
    assert squash("a + b") == "a+b"
    assert lenient("a+b") != lenient("a-b")


def _blank(fx, variant, tmp_path):
    fs = FormulaSet.load(fx[f"{variant}_json"])
    for f in fs.formulas:
        f.latex = None
        f.status = "pending"
    p = tmp_path / "blank.json"
    fs.save(p)
    return p


def test_runner_fixed_backend(fx, tmp_path):
    blank = _blank(fx, "scan", tmp_path)
    out = tmp_path / "out.json"
    be = FixedBackend([r"x ^ { 2 }"])
    code = run_recognition(fx["scan"], blank, out, backend="fixed", workdir=tmp_path / "w", backend_obj=be, log=lambda *a: None)
    assert code == RecognizeExit.OK and be.calls == 11
    fs = FormulaSet.load(out)
    assert all(f.latex == r"x ^ { 2 }" and f.ocr_latex == r"x ^ { 2 }" and not f.edited for f in fs.formulas)
    assert fs.meta["recognition"][-1]["count"] == 11
    # 既存の出力は上書きしない
    assert run_recognition(fx["scan"], blank, out, backend_obj=be, workdir=tmp_path / "w", log=lambda *a: None) == RecognizeExit.SAFETY


def test_runner_interrupt_and_resume(fx, tmp_path):
    blank = _blank(fx, "scan", tmp_path)
    out = tmp_path / "out.json"
    be = FixedBackend(["a"])
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return calls["n"] > 4  # 4件処理したら止める

    code = run_recognition(fx["scan"], blank, out, workdir=tmp_path / "w", backend_obj=be, should_stop=stop, log=lambda *a: None)
    assert code == RecognizeExit.INTERRUPTED and not out.exists()
    state = json.loads((tmp_path / "w" / "state.json").read_text())
    assert len(state["done"]) == 4
    be2 = FixedBackend(["a"])
    code = run_recognition(fx["scan"], blank, out, workdir=tmp_path / "w", backend_obj=be2, log=lambda *a: None)
    assert code == RecognizeExit.OK and be2.calls == 7  # 残りだけ


def test_runner_partial_failure(fx, tmp_path):
    blank = _blank(fx, "scan", tmp_path)

    class Flaky:
        name, version = "flaky", "1"
        n = 0

        def recognize(self, imgs):
            self.n += 1
            return [Recognition(None, None, "boom") if self.n == 2 else Recognition("y")]

    out = tmp_path / "out.json"
    code = run_recognition(fx["scan"], blank, out, workdir=tmp_path / "w", backend_obj=Flaky(), log=lambda *a: None)
    assert code == RecognizeExit.PARTIAL
    errs = [f for f in FormulaSet.load(out).formulas if f.status == "error"]
    assert len(errs) == 1 and "boom" in errs[0].reason


def test_model_missing(fx, tmp_path):
    blank = _blank(fx, "scan", tmp_path)
    be = UniMERNetBackend(model_dir=tmp_path / "nomodel")
    code = run_recognition(fx["scan"], blank, tmp_path / "o.json", backend_obj=be, workdir=tmp_path / "w", log=lambda *a: None)
    assert code == RecognizeExit.MODEL_MISSING and not (tmp_path / "o.json").exists()


HAVE_MODEL = (ROOT / "models" / "unimernet_tiny").is_dir() and (ROOT / ".venv-ml" / "bin" / "python").exists()


@pytest.mark.model
@pytest.mark.skipif(not HAVE_MODEL, reason="UniMERNet（.venv-ml と models/unimernet_tiny）が未導入")
def test_unimernet_real_on_fixture(fx, tmp_path):
    """実モデル: 合成画像化PDFの式を CPU で認識し、全体の傾向を確認する（記録は MODELS.md）。"""
    blank = _blank(fx, "scan", tmp_path)
    out = tmp_path / "out.json"
    code = run_recognition(fx["scan"], blank, out, backend="unimernet", workdir=tmp_path / "w", log=lambda *a: None)
    assert code == RecognizeExit.OK
    r = compare_recognition(FormulaSet.load(fx["scan_json"]), FormulaSet.load(out))
    by = {x["id"]: x for x in r["rows"]}
    assert by["p0_inline"]["exact_nospace"]  # 単純な式は完全一致
    assert r["lenient_equal"] >= 6, r
    assert r["mean_char_ratio"] >= 0.7, r
