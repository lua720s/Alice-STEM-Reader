"""段階1: Mathpix 検証の計画（送信なし）と、派生PDFの比較コマンド。"""

import json
import shutil

import pymupdf

from pdf_latex_overlay import embed
from pdf_latex_overlay.cli import main
from pdf_latex_overlay.mathpix import options_json, plan
from pdf_latex_overlay.models import FormulaSet


def test_options_json_matches_spec():
    on = options_json(True)
    assert on["conversion_formats"] == {"mmd.overlay.pdf": True}
    assert on["conversion_options"]["mmd.overlay.pdf"]["math_latex_layer"] is True
    assert options_json(False)["conversion_options"]["mmd.overlay.pdf"]["math_latex_layer"] is False


def test_plan_is_dry_run(fx, tmp_path, monkeypatch):
    import socket

    def no_network(*a, **k):
        raise AssertionError("ネットワークを使ってはいけない")

    monkeypatch.setattr(socket, "create_connection", no_network)
    p = plan(fx["vector"].parent, tmp_path / "mp")
    assert p["dry_run"] and p["sent"] == 0 and len(p["jobs"]) == 4
    names = {j["name"] for j in p["jobs"]}
    assert names == {"vector-latexlayer-on", "vector-latexlayer-off", "scan-latexlayer-on", "scan-latexlayer-off"}
    for j in p["jobs"]:
        assert j["command"].startswith("mpx scs convert ") and "--async" not in j["command"]
        dumped = json.dumps(j).lower()
        assert "app_key" not in dumped and "app_id" not in dumped  # 認証情報を含めない
    assert not (tmp_path / "mp").exists()  # 何も作らない


def test_verify_derived(fx, tmp_path, capsys):
    out = tmp_path / "d.pdf"
    fs = FormulaSet.load(fx["scan_json"])
    embed.embed_formulas(fx["scan"], out, [f for f in fs.formulas if fs.meta["expect"][f.id] == "embed"])
    assert main(["verify-derived", str(fx["scan"]), str(out)]) == 0
    doc = pymupdf.open(out)
    doc[0].draw_rect(pymupdf.Rect(5, 5, 30, 30), color=(1, 0, 0))
    bad = tmp_path / "bad.pdf"
    doc.save(bad)
    assert main(["verify-derived", str(fx["scan"]), str(bad)]) == 5
