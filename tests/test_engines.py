"""認識エンジンの選択（PP-FormulaNet / UniMERNet / Mathpix）。2026-11-19 のユーザー指示。

Mathpix には実際には送らない（mpx の代わりに偽の実行関数を使う）。重みや別 venv が要るエンジンは、実物がなくても動く範囲を調べる。
"""

import json
import threading
from pathlib import Path

import pytest

from pdf_latex_overlay import cli
from pdf_latex_overlay.recognize import ENGINES, RecognizerUnavailable, get_backend
from pdf_latex_overlay.recognize.mathpix import MathpixBackend, strip_delimiters
from pdf_latex_overlay.recognize.ppformulanet import PPFormulaNetBackend, tidy_pp
from pdf_latex_overlay.viewer.hub import Hub
from pdf_latex_overlay.viewer.server import make_server
from pdf_latex_overlay.viewer.session import SessionError



def _req(url, token=None, method="GET", body=None):
    import urllib.error
    import urllib.request

    headers = {"X-Token": token} if token else {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def test_engine_names_and_external_flag():
    assert set(ENGINES) == {"unimernet", "ppformulanet", "mathpix"}
    assert ENGINES["mathpix"]["external"] and not ENGINES["ppformulanet"]["external"]
    assert get_backend("ppformulanet").name == "ppformulanet"
    assert get_backend("mathpix").name == "mathpix"


def test_strip_delimiters():
    assert strip_delimiters(r"\( x^{2} \)") == "x^{2}"
    assert strip_delimiters("\\[\n\\int_0^1 x\\,dx\n\\]\n") == r"\int_0^1 x\,dx"
    assert strip_delimiters("$a$ and $b$") == "a\nb"
    assert strip_delimiters("plain") == "plain"
    assert strip_delimiters("\\(\\int_{4}^{16}x\\)") == r"\int_{4}^{16}x"
    assert strip_delimiters("\\(a+b") == "a+b"  # 閉じが欠けた出力


def test_tidy_pp():
    assert tidy_pp(r"\int x(\operatorname{l o g}x)^{2}d x") == r"\int x(\log x)^{2}dx"
    assert tidy_pp(r"\operatorname{s i n}x") == r"\sin x"


def test_mathpix_refuses_without_consent_and_never_sends():
    calls = []
    b = MathpixBackend(allow_external=False, runner=lambda *a: calls.append(a) or (0, ""))
    with pytest.raises(RecognizerUnavailable):
        b.recognize([b"png"])
    assert calls == [] and b.sent == 0


def test_mathpix_with_consent_uses_runner_and_strips():
    def fake(src, dst, timeout):
        assert Path(src).read_bytes() == b"png"
        Path(dst).write_text("\\( x^{2}+1 \\)\n", encoding="utf-8")
        return 0, ""

    b = MathpixBackend(allow_external=True, runner=fake)
    (r,) = b.recognize([b"png"])
    assert r.latex == "x^{2}+1" and r.error is None and b.sent == 1


def test_mathpix_reports_failure_without_crashing():
    b = MathpixBackend(allow_external=True, runner=lambda s, d, t: (1, "auth error"))
    (r,) = b.recognize([b"png"])
    assert r.latex is None and "auth error" in r.error

    def boom(*a):
        raise TimeoutError("slow")

    (r,) = MathpixBackend(allow_external=True, runner=boom).recognize([b"png"])
    assert r.latex is None and "TimeoutError" in r.error


def test_ppformulanet_missing_weights_is_reported(tmp_path):
    b = PPFormulaNetBackend(model_dir=tmp_path / "none", python=Path(__file__))
    with pytest.raises(RecognizerUnavailable):
        b.start()


def test_cli_refuses_mathpix_without_allow_external(tmp_path, capsys):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF")
    rc = cli.main(["prepare", str(pdf), "--backend", "mathpix", "--library", str(tmp_path / "lib")])
    assert rc == 7 or rc == 2  # 同意がないので、PDF の中身を見る前に止まる（送らない）
    assert "allow-external" in capsys.readouterr().err or rc == 7


@pytest.fixture
def hub(fx, tmp_path):
    import shutil

    shutil.copy(fx["scan"], tmp_path / "a.pdf")
    h = Hub([tmp_path / "a.pdf"], library=tmp_path / "lib")
    yield h
    h.close()


def test_hub_set_engine_requires_consent_for_external(hub, monkeypatch):
    with pytest.raises(SessionError) as e:
        hub.set_engine("mathpix")
    assert e.value.status == 409 and hub.backend_name == "none"
    with pytest.raises(SessionError):
        hub.set_engine("nonexistent")
    monkeypatch.setattr(MathpixBackend, "_check", lambda self: None)
    out = hub.set_engine("mathpix", confirm_external=True)
    assert out["backend"] == "mathpix" and hub.backend_name == "mathpix" and hub.detector is not None
    assert hub.tree()["backend"] == "mathpix" and {e["name"] for e in hub.tree()["engines"]} == set(ENGINES)


def test_hub_set_engine_refused_while_ocr_running(hub, monkeypatch):
    monkeypatch.setattr(MathpixBackend, "_check", lambda self: None)
    hub._ocr = {"state": "running"}
    with pytest.raises(SessionError) as e:
        hub.set_engine("mathpix", confirm_external=True)
    assert e.value.status == 409
    hub._ocr = None


def test_hub_set_engine_missing_model_is_409(hub, monkeypatch):
    monkeypatch.setenv("PLO_PP_PYTHON", "/nonexistent/python")
    with pytest.raises(SessionError) as e:
        hub.set_engine("ppformulanet")
    assert e.value.status == 409 and hub.backend_name == "none"


def test_http_engine_requires_literal_true_consent(hub, monkeypatch):
    monkeypatch.setattr(MathpixBackend, "_check", lambda self: None)
    httpd, token = make_server(hub, 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        st, body, _ = _req(base + "/api/engine", token, "POST", {"name": "mathpix"})
        assert st == 409
        st, _, _ = _req(base + "/api/engine", token, "POST", {"name": "mathpix", "confirm_external": "yes"})
        assert st == 409  # 真偽値の true だけを同意とみなす
        st, body, _ = _req(base + "/api/engine", token, "POST", {"name": "mathpix", "confirm_external": True})
        assert st == 200 and json.loads(body)["backend"] == "mathpix"
        st, body, _ = _req(base + "/api/tree", token)
        assert json.loads(body)["backend"] == "mathpix"
    finally:
        httpd.shutdown()
        httpd.server_close()
