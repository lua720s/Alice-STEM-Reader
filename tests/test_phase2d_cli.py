"""段階2d: 検証付きの公開入口と CLI。失敗時は公開しないこと。"""

import json
import os
import shutil
import subprocess
import sys

import pymupdf
import pytest

from pdf_latex_overlay import ExitCode, Options, embed, process_pdf, safety
from pdf_latex_overlay.models import Formula, FormulaSet

FAST = dict(pdfkit="off")


def _copy(fx, variant, tmp_path):
    src = tmp_path / f"{variant}.pdf"
    shutil.copy(fx[variant], src)
    return src


def _no_partials(d):
    return not [p for p in os.listdir(d) if p.endswith(".partial") or p.endswith(".lock")]


@pytest.mark.parametrize("variant", ["vector", "scan"])
def test_success_matches_expectations(fx, tmp_path, variant):
    src = _copy(fx, variant, tmp_path)
    before = safety.sha256_file(src)
    out = tmp_path / "out.pdf"
    rep = process_pdf(src, out, fx[f"{variant}_json"], Options(report_path=str(tmp_path / "r.json")))
    assert rep.ok and rep.exit_code == 0, rep.errors
    assert out.exists() and safety.sha256_file(src) == before
    exp = FormulaSet.load(fx[f"{variant}_json"]).meta["expect"]
    for f in rep.formulas:
        if exp[f["id"]] == "embed":
            assert f["status"] == "embedded", f
        else:
            assert f["status"] == "excluded" and f["reason"] == exp[f["id"]].split(":", 1)[1], f
    report = json.loads((tmp_path / "r.json").read_text())
    assert report["schema"] == "pdf-latex-overlay/run-report" and report["output_sha256"] == safety.sha256_file(out)
    assert all(v.get("ok") is not False for v in report["checks"].values())
    assert _no_partials(tmp_path)


def test_existing_output_refused(fx, tmp_path):
    src = _copy(fx, "scan", tmp_path)
    out = tmp_path / "out.pdf"
    out.write_bytes(b"keep")
    rep = process_pdf(src, out, fx["scan_json"], Options(**FAST))
    assert rep.exit_code == ExitCode.SAFETY and out.read_bytes() == b"keep"


def test_same_file_refused(fx, tmp_path):
    src = _copy(fx, "scan", tmp_path)
    rep = process_pdf(src, src, fx["scan_json"], Options(**FAST))
    assert rep.exit_code == ExitCode.SAFETY


def test_encrypted_refused(fx, tmp_path):
    enc = tmp_path / "enc.pdf"
    pymupdf.open(fx["scan"]).save(enc, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="")
    out = tmp_path / "out.pdf"
    rep = process_pdf(enc, out, fx["scan_json"], Options(**FAST))
    assert rep.exit_code == ExitCode.UNSUPPORTED and not out.exists()


def test_hash_mismatch(fx, tmp_path):
    src = _copy(fx, "scan", tmp_path)
    fs = FormulaSet.load(fx["scan_json"])
    fs.input_sha256 = "0" * 64
    rep = process_pdf(src, tmp_path / "o.pdf", fs, Options(**FAST))
    assert rep.exit_code == ExitCode.USAGE


def test_bad_json(fx, tmp_path):
    src = _copy(fx, "scan", tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text('{"formulas": [{"id": "a", "page": 0, "rect": [10, 10, 5, 5]}]}')
    rep = process_pdf(src, tmp_path / "o.pdf", bad, Options(**FAST))
    assert rep.exit_code == ExitCode.USAGE and not (tmp_path / "o.pdf").exists()


def test_nothing_to_embed(fx, tmp_path):
    src = _copy(fx, "scan", tmp_path)
    fs = FormulaSet(formulas=[Formula(id="a", page=1, rect=(10, 10, 50, 30), latex=None)])
    rep = process_pdf(src, tmp_path / "o.pdf", fs, Options(**FAST))
    assert rep.exit_code == ExitCode.NOTHING and rep.formulas[0]["reason"] == "no_latex"


def test_exclusion_reasons(fx, tmp_path):
    src = _copy(fx, "scan", tmp_path)
    fs = FormulaSet(formulas=[
        Formula(id="ok", page=0, rect=(90, 110, 186, 131), latex="x"),
        Formula(id="dup", page=0, rect=(91, 110, 186, 131), latex="x"),
        Formula(id="jp", page=0, rect=(40, 210, 197, 241), latex=r"\text{面積}"),
        Formula(id="page", page=99, rect=(1, 1, 2, 2), latex="x"),
        Formula(id="ex", page=0, rect=(1, 1, 2, 2), latex="x", status="excluded", reason="user"),
        Formula(id="unconf", page=0, rect=(120, 400, 188, 415), latex="y", status="pending"),
    ])
    rep = process_pdf(src, tmp_path / "o.pdf", fs, Options(**FAST))
    reasons = {f["id"]: f["reason"] for f in rep.formulas}
    assert rep.ok
    assert reasons["ok"] is None and reasons["dup"] == "duplicate_of:ok"
    assert reasons["jp"] == "non_ascii_latex" and reasons["page"] == "page_out_of_range"
    assert reasons["ex"] == "user" and reasons["unconf"] is None
    rep2 = process_pdf(src, tmp_path / "o2.pdf", fs, Options(require_confirmed=True, **FAST))
    assert {f["id"]: f["reason"] for f in rep2.formulas}["unconf"] == "not_confirmed"


def test_interrupt_does_not_publish(fx, tmp_path):
    src = _copy(fx, "scan", tmp_path)
    out = tmp_path / "o.pdf"
    rep = process_pdf(src, out, fx["scan_json"], Options(**FAST), should_stop=lambda: True)
    assert rep.exit_code == ExitCode.INTERRUPTED and not out.exists() and _no_partials(tmp_path)


def test_timeout(fx, tmp_path):
    src = _copy(fx, "scan", tmp_path)
    rep = process_pdf(src, tmp_path / "o.pdf", fx["scan_json"], Options(timeout_s=0.0, **FAST))
    assert rep.exit_code == ExitCode.INTERRUPTED


def test_verify_failure_blocks_publish(fx, tmp_path, monkeypatch):
    """描画が変わる不具合を仕込むと、外観検証で止まり公開されない。"""
    orig = embed.build_ops

    def visible_ops(*a, **k):
        import pikepdf
        ops = orig(*a, **k)
        return [o if str(o.operator) != "Tr" else pikepdf.ContentStreamInstruction([0], pikepdf.Operator("Tr")) for o in ops]

    monkeypatch.setattr(embed, "build_ops", visible_ops)
    src = _copy(fx, "scan", tmp_path)
    out = tmp_path / "o.pdf"
    rep = process_pdf(src, out, fx["scan_json"], Options(**FAST))
    assert rep.exit_code == ExitCode.VERIFY_FAILED and not out.exists() and _no_partials(tmp_path)
    assert rep.checks["appearance"]["ok"] is False
    assert rep.checks["appearance"]["runs"][0]["diffs"][0]["count"] > 0


def test_concurrent_lock(fx, tmp_path):
    src = _copy(fx, "scan", tmp_path)
    out = tmp_path / "o.pdf"
    (tmp_path / ".o.pdf.lock").write_text("123")
    rep = process_pdf(src, out, fx["scan_json"], Options(**FAST))
    assert rep.exit_code == ExitCode.SAFETY and not out.exists()
    assert (tmp_path / ".o.pdf.lock").exists()  # 他のジョブのロックは消さない


def test_cli_subprocess(fx, tmp_path):
    src = _copy(fx, "scan", tmp_path)
    out = tmp_path / "o.pdf"
    cmd = [sys.executable, "-m", "pdf_latex_overlay", "embed", str(src), str(out),
           "--formulas", str(fx["scan_json"]), "--pdfkit", "off", "--report", str(tmp_path / "r.json")]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["counts"]["embedded"] == 10
    r2 = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    assert r2.returncode == ExitCode.SAFETY  # 2回目は既存出力で拒否
