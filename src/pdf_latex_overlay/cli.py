"""コマンドライン。各サブコマンドは公開入口を呼ぶだけにする。"""

from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

from . import __version__
from .pipeline import ExitCode, Options, process_pdf


class StopFlag:
    """SIGINT/SIGTERM で立つフラグ。新規ページの処理だけを止める。"""

    def __init__(self):
        self.stopped = False
        self.signal = None

    def install(self):
        def handler(signum, frame):
            self.stopped = True
            self.signal = signum

        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
        return self

    def __call__(self) -> bool:
        return self.stopped


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_embed(a) -> int:
    if a.report and Path(a.report).resolve() == Path(a.input).resolve():
        print("レポートの出力先が入力と同じ", file=sys.stderr)
        return ExitCode.USAGE
    opts = Options(
        mode=a.mode, dpis=tuple(a.dpi), pdfkit=a.pdfkit, require_confirmed=a.require_confirmed,
        allow_empty=a.allow_empty, timeout_s=a.timeout, report_path=a.report,
    )
    flag = StopFlag().install()
    rep = process_pdf(a.input, a.output, a.formulas, opts, should_stop=flag)
    summary = {
        "ok": rep.ok, "exit_code": rep.exit_code, "output": rep.output if rep.ok else None,
        "counts": rep.counts, "errors": rep.errors,
        "excluded": [{"id": f["id"], "reason": f["reason"]} for f in rep.formulas if f["status"] == "excluded"],
        "failed_checks": [k for k, v in rep.checks.items() if isinstance(v, dict) and v.get("ok") is False],
    }
    _print(summary)
    return rep.exit_code


def cmd_fixtures(a) -> int:
    from . import fixtures

    _print({k: str(v) for k, v in fixtures.build(a.out_dir).items()})
    return 0


def cmd_copycheck(a) -> int:
    from . import copycheck
    from .models import FormulaSet

    fs = FormulaSet.load(a.formulas)
    targets = [f for f in fs.formulas if f.latex and f.status not in ("excluded", "error")]
    rows = copycheck.copy_table(a.pdf, targets, use_pdfkit=not a.no_pdfkit)
    _print(rows)
    return 0


def cmd_viewer(a) -> int:
    from .viewer.server import serve

    return serve(a.input, formulas_path=a.formulas, port=a.port, open_browser=not a.no_open,
                 backend=a.backend, workdir=a.workdir)


def cmd_recognize(a) -> int:
    from .recognize import run_recognition

    flag = StopFlag().install()
    return run_recognition(a.input, a.formulas, a.out, backend=a.backend, workdir=a.workdir,
                           dpi=a.dpi, only_missing=not a.all, should_stop=flag)


def cmd_import_mineru(a) -> int:
    from .detect.mineru import import_mineru

    fs = import_mineru(a.mineru_json, a.input, frame=a.frame)
    if Path(a.out).exists():
        print(f"出力先が既に存在する: {a.out}", file=sys.stderr)
        return ExitCode.SAFETY
    fs.save(a.out)
    _print({"formulas": len(fs.formulas), "out": a.out, "skipped": fs.meta.get("skipped", []),
            "warnings": fs.meta.get("warnings", [])})
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pdf-latex-overlay", description="数式をLaTeXとしてコピーできる派生PDFを作る")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("embed", help="式 JSON の LaTeX を埋め込み、検証に通ったら派生PDFを公開する")
    e.add_argument("input")
    e.add_argument("output")
    e.add_argument("--formulas", required=True)
    e.add_argument("--mode", choices=["invisible", "actualtext"], default="invisible")
    e.add_argument("--dpi", type=int, action="append", default=None, help="外観比較の DPI（複数可、既定 72）")
    e.add_argument("--pdfkit", choices=["auto", "require", "off"], default="auto")
    e.add_argument("--require-confirmed", action="store_true", help="status=confirmed の式だけを埋め込む")
    e.add_argument("--allow-empty", action="store_true")
    e.add_argument("--timeout", type=float, default=None, help="秒")
    e.add_argument("--report", default=None, help="レポート JSON の保存先")
    e.set_defaults(func=cmd_embed)

    f = sub.add_parser("fixtures", help="合成フィクスチャを生成する")
    f.add_argument("out_dir")
    f.set_defaults(func=cmd_fixtures)

    c = sub.add_parser("copycheck", help="派生PDFのコピー比較表（MuPDF / PDFKit）")
    c.add_argument("pdf")
    c.add_argument("--formulas", required=True)
    c.add_argument("--no-pdfkit", action="store_true")
    c.set_defaults(func=cmd_copycheck)

    v = sub.add_parser("viewer", help="ローカルビューアを起動する（127.0.0.1 のみ）")
    v.add_argument("input")
    v.add_argument("--formulas", default=None, help="式 JSON（なければ作業領域に新規作成）")
    v.add_argument("--workdir", default=None)
    v.add_argument("--port", type=int, default=0)
    v.add_argument("--backend", default="none", help="再認識のバックエンド（none / unimernet）")
    v.add_argument("--no-open", action="store_true")
    v.set_defaults(func=cmd_viewer)

    r = sub.add_parser("recognize", help="式の矩形を切り出して LaTeX を認識する")
    r.add_argument("input")
    r.add_argument("--formulas", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--backend", default="unimernet")
    r.add_argument("--workdir", default=None)
    r.add_argument("--dpi", type=int, default=200)
    r.add_argument("--all", action="store_true", help="LaTeX が既にある式も再認識する")
    r.set_defaults(func=cmd_recognize)

    m = sub.add_parser("import-mineru", help="MinerU の結果 JSON から式 JSON を作る")
    m.add_argument("input", help="MinerU に渡した元PDF（ハッシュとページ寸法の確認用）")
    m.add_argument("mineru_json", help="*_middle.json または *_content_list.json")
    m.add_argument("--out", required=True)
    m.add_argument("--frame", choices=["auto", "rendered", "unrotated"], default="auto",
                   help="MinerU の座標の基準ページ（既定 auto: 表示どおり）")
    m.set_defaults(func=cmd_import_mineru)
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    if getattr(a, "dpi", None) is None and a.cmd == "embed":
        a.dpi = [72]
    return a.func(a)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
