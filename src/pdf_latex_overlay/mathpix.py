"""Mathpix の A/B 検証（MATHPIX_VALIDATION.md）を、外部送信なしで計画・評価する。

- plan: 合成PDF（ベクター・画像化）× math_latex_layer（false / true）の計4ジョブの依頼内容と、
  実行用コマンドを作る。送信はしない。認証情報は読まない・書かない。
- 評価は既存の機能で行う: 取得したPDFに対して `verify-derived`（原本ハッシュ・ページ属性・画素差）と
  `copycheck`（MuPDF・PDFKit のコピー比較）を実行する。

実際の送信は、利用者が個別に承認したうえで、出力されたコマンドを手で実行する。
応答を失った POST は、請求やジョブの重複を避けるため自動で再送しない。
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from . import safety

PDF_API = "POST https://api.mathpix.com/v3/pdf"


def options_json(math_latex_layer: bool, accessible: bool = True) -> dict:
    opts = {"math_latex_layer": math_latex_layer}
    if accessible:
        opts["accessible"] = True
    return {"conversion_formats": {"mmd.overlay.pdf": True}, "conversion_options": {"mmd.overlay.pdf": opts}}


def plan(fixtures_dir, out_dir) -> dict:
    fx = Path(fixtures_dir)
    out = Path(out_dir)
    jobs = []
    for variant in ("vector", "scan"):
        pdf = fx / f"{variant}.pdf"
        if not pdf.exists():
            raise FileNotFoundError(f"フィクスチャがない: {pdf}（`fixtures` で生成）")
        for layer in (False, True):
            name = f"{variant}-latexlayer-{'on' if layer else 'off'}"
            result = out / f"{name}.mmd.overlay.pdf"
            opts = options_json(layer)
            cmd = ["mpx", "scs", "convert", str(pdf), str(result), "--options-json", json.dumps(opts, separators=(",", ":"))]
            jobs.append({
                "name": name,
                "input": str(pdf),
                "input_sha256": safety.sha256_file(pdf),
                "expected_formulas": str(fx / f"{variant}.formulas.json"),
                "request": {"endpoint": PDF_API, "multipart": {"file": pdf.name, "options_json": opts}},
                "command": " ".join(shlex.quote(c) for c in cmd),
                "result": str(result),
                "after": [
                    f"pdf-latex-overlay verify-derived {shlex.quote(str(pdf))} {shlex.quote(str(result))}",
                    f"pdf-latex-overlay copycheck {shlex.quote(str(result))} --formulas {shlex.quote(str(fx / f'{variant}.formulas.json'))}",
                ],
            })
    return {
        "dry_run": True,
        "sent": 0,
        "note": "外部送信はしていない。実行は個別承認後に command を手で実行する。応答を失った送信は再送しない。",
        "limits": {"new_jobs_max": 4},
        "jobs": jobs,
    }
