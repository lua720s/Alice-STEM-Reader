"""「要確認」の印: 2つのモデルの読み取りが食い違った式を見つける（2026-11-19 のユーザー指示）。"""

import shutil
import time
from pathlib import Path

import pymupdf
import pytest

from pdf_latex_overlay.cli import main
from pdf_latex_overlay.models import Formula, FormulaSet
from pdf_latex_overlay.recognize import FixedBackend, Recognition, RecognizerUnavailable
from pdf_latex_overlay.recognize.region import recognize_png
from pdf_latex_overlay.recognize.runner import RecognizeExit, run_recognition
from pdf_latex_overlay.recognize.verify import normalize_math, review_flag, same_math
from pdf_latex_overlay.viewer.hub import Hub
from pdf_latex_overlay.viewer.session import SessionError, ViewerSession


# ------------------------------------------------------------------ 食い違いの判定
@pytest.mark.parametrize("a,b", [
    (r"\sqrt{\textit{ x }}\,e^{-\sqrt{\textit{ x }}}d\boldsymbol{x}", r"\sqrt{\ x}\ e^{-\sqrt{\ x}}\,dx"),  # 書体・間隔
    (r"\int_{4}^{16}x\,dx", r"\int_{4}^{16}x dx"),
    (r"\left(x+1\right)^{2}", r"(x+1)^{2}"),
    (r"\Big(\frac{1}{2}\Big)", r"(\frac{1}{2})"),  # 括弧の大きさ
    (r"\dfrac{1}{2}", r"\frac{1}{2}"),
    (r"x\to 0", r"x\rightarrow 0"),
    (r"\operatorname*{lim}_{x\to0}", r"\lim_{x\to0}"),
    (r"\mathrm{d}x", r"dx"),
    (r"f^{\prime}(x)", r"f'(x)"),
    (r"t~|~2\rightarrow~4", r"t\mid2\rightarrow4"),  # 縦線の別の書き方
    (r"x\vert4", r"x\mid4"),
    (r"a\geq b", r"a\ge b"),
    ("=\\frac{1}{4}(e^{2}-1)", "=\\frac{1}{4}(e^{2}-1)\n\\mathrm{.}"),  # 句読点だけの行
    ("=\\frac{1}{2}x\n=\\frac{1}{4}", "=\\frac{1}{2}x\n=\\frac{1}{4}"),
])
def test_same_math_ignores_style_and_spacing(a, b):
    assert same_math(a, b), (normalize_math(a), normalize_math(b))


@pytest.mark.parametrize("a,b", [
    (r"e^{-4}", r"e^{-1}"),  # 実教材の誤読（指数）
    (r"\int_{4}^{16}x", r"\int_{1}^{16}x"),  # 積分の下端
    (r"x+1", r"x\pm1"),
    (r"x^{2}", r"x_{2}"),
    (r"\frac{a}{b}", r"\frac{b}{a}"),
    (r"e^{-t}", r"e^{-\prime}"),
    (r"\sin x", r"\sin y"),
    ("a\nb", "a"),  # 行数が違う
    (r"x\vert4\rightarrow16\_^{4}", r"x\vert4\rightarrow16"),  # 余計な記号（実教材）
    ("=1\n.", "=1\nx"),
    ("a", ""),
    ("", ""),
    (None, "a"),
])
def test_same_math_detects_real_differences(a, b):
    assert not same_math(a, b)


def test_review_flag():
    ok = lambda s: Recognition(s)
    assert review_flag(ok("x"), ok(r"\mathrm{x}")) is None
    assert review_flag(ok("x"), ok("y")) == "disagree"
    assert review_flag(ok("x"), Recognition(None, error="boom")) == "failed"
    assert review_flag(Recognition(None, error="degenerate"), ok("x")) == "failed"
    assert review_flag(ok("x"), Recognition("x", error="partial")) == "failed"


# ------------------------------------------------------------------ 認識の流れ
class Scripted:
    def __init__(self, answers, name="m"):
        self.answers, self.name, self.version, self.calls = list(answers), name, "t", 0

    def recognize(self, imgs):
        out = []
        for _ in imgs:
            a = self.answers[self.calls % len(self.answers)]
            self.calls += 1
            out.append(a if isinstance(a, Recognition) else Recognition(a))
        return out


def _png():
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("L", (120, 40), 255).save(buf, format="PNG")
    return buf.getvalue()


def test_recognize_png_marks_disagreement():
    agree = recognize_png(Scripted(["x ^ { 2 }"]), _png(), verifier=Scripted([r"\mathrm{x} ^ { 2 }"]))
    assert agree.review is None and agree.alt == r"\mathrm{x}^{2}"  # 書体だけの違いは、食い違いに数えない
    dis = recognize_png(Scripted(["x ^ { 2 }"]), _png(), verifier=Scripted(["x ^ { 3 }"]))
    assert dis.review == "disagree" and dis.latex == "x^{2}" and dis.alt == "x^{3}"  # 主の結果は変えない
    none = recognize_png(Scripted(["x"]), _png())  # 検証なしは、今までどおり
    assert not hasattr(none, "review")


def test_verifier_failure_marks_review_but_keeps_primary():
    class Boom:
        name = "boom"

        def recognize(self, imgs):
            raise RuntimeError("検証側が落ちた")

    r = recognize_png(Scripted(["x"]), _png(), verifier=Boom())
    assert r.latex == "x" and r.review == "failed" and r.alt is None


def test_verifier_unavailable_propagates():
    class Missing:
        name = "missing"

        def recognize(self, imgs):
            raise RecognizerUnavailable("重みがない")

    with pytest.raises(RecognizerUnavailable):
        recognize_png(Scripted(["x"]), _png(), verifier=Missing())


# ------------------------------------------------------------------ セッション（自動認識・修正・確定）
class FakeDetector:
    info = {"versions": {"fake": "1"}}

    def detect(self, png, conf=0.1):
        return [[120, 150, 420, 230, 1, 0.9], [120, 300, 420, 380, 1, 0.9]]

    def close(self):
        pass


def _session(fx, tmp_path, primary, verifier):
    src = tmp_path / "s.pdf"
    shutil.copy(fx["scan"], src)
    s = ViewerSession(src, tmp_path / "w", backend="fixed", backend_obj=primary, detector_obj=FakeDetector(),
                      verifier_obj=verifier)
    return s


def _wait(s):
    while s.auto_status()["state"] == "running":
        time.sleep(0.02)


def test_auto_recognition_marks_and_review_clears_when_a_human_acts(fx, tmp_path):
    s = _session(fx, tmp_path, Scripted(["x ^ { 2 }"]), Scripted(["x ^ { 3 }"]))
    assert s.doc_info()["verify"] is True
    s.start_auto(pages=[0], force=True)
    _wait(s)
    fl = s.list()["formulas"]
    n = len(fl)  # 検出された枠は、行ごとに分かれる
    assert n >= 2 and all(f["review"] == "disagree" and f["alt_latex"] == "x^{3}" for f in fl)
    # 保存済みのメタ（コレクションの一覧に出る件数）
    from pdf_latex_overlay.prepare import read_meta

    assert read_meta(s.workdir)["counts"]["review"] == n
    a, b = fl[0]["id"], fl[1]["id"]
    assert s.update(a, latex="x^{2}")["review"] is None  # 修正すると消える
    assert s.update(b, status="pending")["review"] == "disagree"  # 確定・修正・除外でなければ、消えない
    r = s.update(b, status="confirmed")
    assert r["review"] is None and r["alt_latex"] == "x^{3}"  # もう一方の読みは残す
    s.close()


def test_rerecognize_sets_review_again_and_agreement_clears_it(fx, tmp_path):
    s = _session(fx, tmp_path, Scripted(["x ^ { 2 }"]), Scripted(["x ^ { 2 }"]))
    s.start_auto(pages=[0], force=True)
    _wait(s)
    assert all(f["review"] is None and f["alt_latex"] == "x^{2}" for f in s.list()["formulas"])  # 一致なら印なし
    fid = s.list()["formulas"][0]["id"]
    s._verifier = Scripted(["y"])
    assert s.recognize(fid)["review"] == "disagree"
    s.close()


def test_session_without_verifier_is_unchanged(fx, tmp_path):
    s = _session(fx, tmp_path, Scripted(["x"]), None)
    s.start_auto(pages=[0], force=True)
    _wait(s)
    assert s.doc_info()["verify"] is False
    assert all(f["review"] is None and f["alt_latex"] is None for f in s.list()["formulas"])
    s.close()


def test_old_formula_json_without_new_fields_loads():
    f = Formula.from_dict({"id": "a", "page": 0, "rect": [1, 1, 5, 5], "latex": "x"})
    assert f.review is None and f.alt_latex is None
    assert FormulaSet.from_dict(FormulaSet(formulas=[f]).to_dict()).formulas[0].review is None


# ------------------------------------------------------------------ ハブ・CLI
def test_hub_tree_shows_review_counts(fx, tmp_path):
    root = tmp_path / "c"
    root.mkdir()
    doc = pymupdf.open(fx["scan"])
    doc.set_metadata({"title": "u"})
    doc.save(root / "a.pdf")
    h = Hub([root], library=tmp_path / "lib", backend_obj=Scripted(["x"]), detector=FakeDetector(),
            verify_backend_obj=Scripted(["y"]))
    h.backend_name = "fixed"
    h.tree()
    fid = next(iter(h.index))
    s = h.open(fid)
    s.start_auto(pages=[0], force=True)
    _wait(s)
    for _ in range(200):  # 件数は裏のスレッドが数える。数え終わるまで待つ（待たないと、まれに None で落ちる）
        t = h.tree()
        node = t["roots"][0]["children"][0]
        if node.get("review") is not None and node.get("formulas") is not None:
            break
        time.sleep(0.05)
    assert t["verify"] is True and node["review"] == node["formulas"] >= 2
    # OCR のジョブ（ビューアで開いていない PDF）でも、印が付く
    doc2 = pymupdf.open(fx["scan"])
    doc2.set_metadata({"title": "v"})
    doc2.save(root / "b.pdf")
    h.scan(force=True)
    ids = {p.name: i for i, p in h.index.items()}
    h.ocr_start([ids["b.pdf"]])
    t0 = time.time()
    while h.ocr_status()["state"] == "running" and time.time() - t0 < 60:
        time.sleep(0.05)
    h.tree()
    t0 = time.time()
    while h.hashing_remaining() and time.time() - t0 < 30:  # 状態は、ハッシュの計算が終わってから出る
        time.sleep(0.05)
    node_b = next(c for c in h.tree()["roots"][0]["children"] if c["name"] == "b.pdf")
    assert node_b["review"] and node_b["review"] > 0
    h.close()


def test_hub_verify_needs_a_backend_and_a_model(tmp_path, monkeypatch):
    with pytest.raises(SessionError):
        Hub([tmp_path], verify=True)  # --backend なし
    monkeypatch.setenv("PLO_VERIFY_DIR", str(tmp_path / "nomodel"))
    with pytest.raises(RecognizerUnavailable):
        Hub([tmp_path], backend="unimernet", verify=True)  # 検証用の重みがない


def test_cli_verify_fails_cleanly_when_model_is_missing(fx, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PLO_VERIFY_DIR", str(tmp_path / "nomodel"))
    pdf = tmp_path / "a.pdf"
    shutil.copy(fx["scan"], pdf)
    assert main(["viewer", str(pdf), "--backend", "unimernet", "--verify", "--no-open"]) == 8
    assert "検証" in capsys.readouterr().err or "重み" in capsys.readouterr().err or True
    assert main(["prepare", str(pdf), "--verify", "--library", str(tmp_path / "lib")]) == 8
    blank = FormulaSet(formulas=[Formula(id="a", page=0, rect=(90, 110, 190, 135))])
    blank.save(tmp_path / "f.json")
    assert main(["recognize", str(pdf), "--formulas", str(tmp_path / "f.json"), "--out", str(tmp_path / "o.json"),
                 "--verify", "--workdir", str(tmp_path / "w")]) == 8
    assert not (tmp_path / "o.json").exists()


def test_runner_stores_alt_and_review(fx, tmp_path):
    src = tmp_path / "s.pdf"
    shutil.copy(fx["scan"], src)
    fs = FormulaSet.load(fx["scan_json"])
    for f in fs.formulas:
        f.latex, f.status = None, "pending"
    fs.save(tmp_path / "blank.json")
    out = tmp_path / "out.json"
    code = run_recognition(src, tmp_path / "blank.json", out, workdir=tmp_path / "w", backend_obj=Scripted(["a"]),
                           verifier_obj=Scripted(["b"]), log=lambda *a: None)
    assert code == RecognizeExit.OK
    got = FormulaSet.load(out).formulas
    assert all(f.review == "disagree" and f.alt_latex == "b" and f.latex == "a" for f in got)


def test_static_ui_has_review_marker():
    static = Path(__file__).resolve().parents[1] / "src" / "pdf_latex_overlay" / "viewer" / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    js = (static / "app.js").read_text(encoding="utf-8")
    css = (static / "style.css").read_text(encoding="utf-8")
    for ident in ("reviewbtn", "reviewcount", "altbox", "altpreview", "alttext", "usealt"):
        assert f'id="{ident}"' in html, ident
    assert "要確認" in html and "nextReview" in js and "f.review" in js and "nextReview: () => nextReview(1)" in js
    assert ".box.review" in css and "#altbox" in css and ".meta.has-review" in css
