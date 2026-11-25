"""ほかのアプリ（文献管理・学習用アプリ・デジタルアーカイブなど）から呼び出す例。

元の資料は原本として保持し、派生PDF（数式をLaTeXとしてコピーできるPDF）は、いつでも作り直せる派生物として扱う。
呼び出し側は `process_pdf` と、その結果（RunReport の JSON）だけに依存すればよい。
"""

from pathlib import Path

from alice_stem_reader import ExitCode, Options, process_pdf


def make_latex_copyable_asset(source_pdf: Path, formulas_json: Path, asset_dir: Path) -> dict:
    out = asset_dir / f"{source_pdf.stem}.latex-overlay.pdf"
    report_path = asset_dir / f"{source_pdf.stem}.latex-overlay.report.json"
    rep = process_pdf(source_pdf, out, formulas_json, Options(report_path=str(report_path)))
    if rep.exit_code != ExitCode.OK:
        # 派生PDFは公開されていない。レポートの errors と checks を利用者に表示する
        return {"status": "failed", "exit_code": rep.exit_code, "errors": rep.errors, "report": str(report_path)}
    return {"status": "ok", "derived_pdf": str(out), "derived_sha256": rep.output_sha256,
            "source_sha256": rep.input_sha256, "counts": rep.counts, "report": str(report_path)}
