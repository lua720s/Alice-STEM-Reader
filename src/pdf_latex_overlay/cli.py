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

    return serve(a.paths, formulas_path=a.formulas, port=a.port, open_browser=not a.no_open,
                 backend=a.backend, workdir=a.workdir, auto=a.auto, readonly=a.readonly, verify=a.verify,
                 allow_external=a.allow_external, state_file=a.state_file, idle_exit_min=a.idle_exit,
                 roots_file=a.roots_file)


def cmd_app_open(a) -> int:
    """Finder の「このアプリケーションで開く」や .app から呼ばれる。動いているビューアがあれば再利用する。"""
    from . import applaunch

    if a.print_last:
        print(applaunch.last_folder() or "")
        return 0
    if a.if_running:
        st = applaunch.running_state()
        if not st:
            return 1
        if st.get("idle_s", 999) > 8:
            applaunch.webbrowser.open(st["url"])
        return 0
    if a.hook:
        applaunch.run_hook("on-launch")  # 利用者が置いたフック（なければ何もしない）
    files = [p for p in a.paths if not Path(p).expanduser().is_dir()]
    dirs = [p for p in a.paths if Path(p).expanduser().is_dir()]
    try:
        res = applaunch.app_open(files=files, folder=a.folder or (dirs[0] if dirs else None), backend=a.backend)
    except (ValueError, RuntimeError) as e:
        print(str(e), file=sys.stderr)
        return 2
    _print(res)
    return 0


def cmd_app_stop(a) -> int:
    from . import applaunch

    print("止めた" if applaunch.app_stop() else "動いていない")
    return 0


def cmd_build_app(a) -> int:
    from .appbuild import build_app, build_snip_app

    try:
        app = build_app(a.dest, register=not a.no_register)
        if not a.no_snip:
            print(f"作成した: {build_snip_app(a.dest, register=not a.no_register)}")
    except (RuntimeError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 2
    except Exception as e:  # subprocess.CalledProcessError など
        print(f"作成に失敗: {e}\n{getattr(e, 'stderr', '')}", file=sys.stderr)
        return 2
    print(f"作成した: {app}")
    return 0


def cmd_recognize(a) -> int:
    from .recognize import run_recognition

    flag = StopFlag().install()
    ver = None
    if a.verify:
        from .recognize import RecognizerUnavailable
        from .recognize.unimernet import make_verifier

        try:
            ver = make_verifier()
        except RecognizerUnavailable as e:
            print(str(e), file=sys.stderr)
            return 8
    if a.backend == "mathpix" and not a.allow_external:
        print("Mathpix は式の画像を外部に送る。同意するなら --allow-external を付ける", file=sys.stderr)
        return 2
    try:
        return run_recognition(a.input, a.formulas, a.out, backend=a.backend, allow_external=a.allow_external, workdir=a.workdir,
                               dpi=a.dpi, only_missing=not a.all, should_stop=flag, verifier_obj=ver)
    finally:
        if ver is not None:
            ver.close()


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


def cmd_mathpix_plan(a) -> int:
    from .mathpix import plan

    _print(plan(a.fixtures_dir, a.out_dir))
    return 0


def cmd_verify_derived(a) -> int:
    """外部ツール（Mathpix 等）が作った派生PDFを、原本と比べる（原本ハッシュは記録のみ）。"""
    from . import pdfcheck

    res = {
        "original_sha256": __import__("pdf_latex_overlay.safety", fromlist=["x"]).sha256_file(a.original),
        "page_attributes_equal": pdfcheck.page_attributes(a.original) == pdfcheck.page_attributes(a.derived),
        "appearance": [],
    }
    for dpi in a.dpi or [72]:
        r = pdfcheck.compare_appearance(a.original, a.derived, dpi=dpi)
        res["appearance"].append({"dpi": dpi, "identical": r.identical, "diffs": r.page_diffs[:20]})
    ok = res["page_attributes_equal"] and all(x["identical"] for x in res["appearance"])
    res["ok"] = ok
    _print(res)
    return 0 if ok else ExitCode.VERIFY_FAILED


def cmd_detect(a) -> int:
    """全ページの数式を自動検出し（必要なら認識し）、式 JSON に書く。"""
    import pymupdf

    from . import safety
    from .detect.mfd import MFDDetector, detect_page
    from .models import Formula, FormulaSet
    from .recognize import RecognizerUnavailable

    if Path(a.out).exists():
        print(f"出力先が既に存在する: {a.out}", file=sys.stderr)
        return ExitCode.SAFETY
    flag = StopFlag().install()
    det = MFDDetector()
    fs = FormulaSet(input_sha256=safety.sha256_file(a.input), meta={"detector": "PDF-Extract-Kit MFD yolo_v8_ft"})
    doc = pymupdf.open(a.input)
    try:
        for pno in range(doc.page_count):
            if flag():
                return ExitCode.INTERRUPTED
            for n, d in enumerate(detect_page(det, doc[pno]), 1):
                fs.formulas.append(Formula(id=f"auto_p{pno}_{n}", page=pno, rect=tuple(d["rect"]), source="mfd",
                                           kind=d["kind"], confidence=d["conf"]))
    except RecognizerUnavailable as e:
        print(str(e), file=sys.stderr)
        return 8
    finally:
        det.close()
        doc.close()
    fs.save(a.out)
    _print({"formulas": len(fs.formulas), "out": a.out})
    if a.recognize:
        from .recognize import run_recognition

        out2 = str(Path(a.out).with_suffix(".recognized.json"))
        return run_recognition(a.input, a.out, out2, backend="unimernet", should_stop=flag)
    return 0


def cmd_prepare(a) -> int:
    """開く前に、PDF（フォルダも可）の数式をまとめて検出・認識しておく。"""
    import time

    from .detect.mfd import MFDDetector
    from .prepare import PrepareExit, collect_pdfs, prepare_one
    from .recognize import RecognizerUnavailable, get_backend

    from .docformats import ConversionError, as_pdf

    pdfs = collect_pdfs(a.paths, recursive=not a.no_recursive, all_formats=True)
    if not pdfs:
        print("PDF などの資料が見つからない", file=sys.stderr)
        return PrepareExit.USAGE
    flag = StopFlag().install()
    t0 = time.monotonic()

    def stop():
        return flag() or (a.time_limit is not None and time.monotonic() - t0 > a.time_limit)

    if a.backend == "mathpix" and not a.allow_external:
        print("Mathpix は式の画像を外部に送る。同意するなら --allow-external を付ける", file=sys.stderr)
        return PrepareExit.USAGE
    det, be, ver = MFDDetector(), get_backend(a.backend, **({"allow_external": True} if a.backend == "mathpix" else {})), None
    if a.verify:
        from .recognize.unimernet import make_verifier

        try:
            ver = make_verifier()
        except RecognizerUnavailable as e:
            print(str(e), file=sys.stderr)
            return PrepareExit.MODEL_MISSING
    results = []
    try:
        for i, pdf in enumerate(pdfs, 1):
            if stop():
                results.append({"pdf": str(pdf), "state": "not_started"})
                continue
            print(f"[{i}/{len(pdfs)}] {pdf.name}", file=sys.stderr, flush=True)
            try:
                conv = as_pdf(pdf, a.library)  # EPUB・DjVu・画像などは PDF に変換してから
            except ConversionError as e:
                results.append({"pdf": str(pdf), "state": "skipped", "reason": str(e)})
                continue
            results.append(prepare_one(conv, detector=det, backend=be, library=a.library, should_stop=stop,
                                       max_pages=a.max_pages, log=lambda m: print(m, file=sys.stderr, flush=True),
                                       verifier=ver, source=pdf))
    except RecognizerUnavailable as e:
        print(str(e), file=sys.stderr)
        return PrepareExit.MODEL_MISSING
    finally:
        det.close()
        if hasattr(be, "close"):
            be.close()
        if ver is not None:
            ver.close()
    _print({"elapsed_s": round(time.monotonic() - t0, 1), "results": results})
    states = {r.get("meta_state", r["state"]) for r in results}
    if stop() or "interrupted" in states or "not_started" in states:
        return PrepareExit.INTERRUPTED
    if states & {"error", "partial"} or any(r.get("failed") for r in results):
        return PrepareExit.PARTIAL
    return PrepareExit.OK


def cmd_formats(a) -> int:
    """この環境で開ける形式と、足りない道具。"""
    from .docformats import describe_support

    _print(describe_support())
    return 0


def cmd_convert(a) -> int:
    """PDF 以外の資料（EPUB・DjVu・画像・Markdown・LaTeX など）を PDF に変換する（原本は変えない）。"""
    import shutil as _sh

    from .docformats import ConversionError, as_pdf, conversion_info

    try:
        out = as_pdf(Path(a.input), a.library)
    except ConversionError as e:
        print(str(e), file=sys.stderr)
        return 4
    info = conversion_info(out)
    if a.output:
        dest = Path(a.output)
        if dest.exists() and not a.force:
            print(f"出力先がすでにある（上書きは --force）: {dest}", file=sys.stderr)
            return 3
        _sh.copyfile(out, dest)
        out = dest
    _print({"output": str(out), **info})
    return 0


def cmd_snip(a) -> int:
    """画像ファイル（または --screen で画面の範囲）を読んで、LaTeX・文章を出力する。"""
    from .detect.mfd import MFDDetector
    from .recognize import RecognizerUnavailable, get_backend
    from .snip import SnipError, capture_screen, copy_to_clipboard, decode_image, format_for_copy, recognize_snip

    try:
        if a.screen:
            raw = capture_screen()
            if raw is None:
                return 6
        elif a.image:
            raw = Path(a.image).read_bytes()
        else:
            print("画像ファイルか --screen を指定する", file=sys.stderr)
            return 2
        png = decode_image(raw)
    except (SnipError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 2
    if a.backend == "mathpix" and not a.allow_external:
        print("Mathpix は画像を外部に送る。同意するなら --allow-external を付ける", file=sys.stderr)
        return 2
    be = get_backend(a.backend, **({"allow_external": True} if a.backend == "mathpix" else {}))
    det = MFDDetector() if a.mode == "auto" else None
    try:
        res = recognize_snip(png, backend=be, detector=det, mode=a.mode)
    except RecognizerUnavailable as e:
        print(str(e), file=sys.stderr)
        return 8
    except SnipError as e:
        print(str(e), file=sys.stderr)
        return 2
    finally:
        for m in (det, be):
            if m is not None and hasattr(m, "close"):
                m.close()
    text = format_for_copy(res, a.delim, a.wrap)
    if a.json:
        _print(res)
    else:
        print(text)
    if a.copy:
        copy_to_clipboard(text)
    return 0 if res.get("latex") else 5


def cmd_app_snip(a) -> int:
    """（Snip アプリ用）画面の範囲を選んで読み、結果をクリップボードへ。常駐するビューアを使う（なければ起動する）。"""
    from .applaunch import app_snip

    try:
        r = app_snip(mode=a.mode, backend=a.backend)
    except (RuntimeError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 1
    _print({k: r.get(k) for k in ("id", "kind", "latex", "copied", "cancelled", "error") if k in r})
    return 0 if r.get("cancelled") or r.get("latex") else 5


def cmd_library(a) -> int:
    from .prepare import list_library

    _print(list_library(a.library))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="alice-stem-reader",
                                description="Alice STEM Reader: 資料を読みながら、数式・文章を LaTeX / Markdown として取り出す（すべて手元で動く）")
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

    v = sub.add_parser("viewer", help="ローカルビューアを起動する（127.0.0.1 のみ）。フォルダを渡すと左にコレクションが並ぶ")
    v.add_argument("paths", nargs="*", help="PDF またはフォルダ（複数可。なければ、画面の「開く」から選ぶ）")
    v.add_argument("--formulas", default=None, help="式 JSON（なければ作業領域に新規作成）")
    v.add_argument("--workdir", default=None)
    v.add_argument("--port", type=int, default=0)
    v.add_argument("--backend", default="none", help="認識エンジン（none / unimernet[=base] / unimernet_tiny / ppformulanet / mathpix）。画面の「エンジン」でも切り替えられる")
    v.add_argument("--allow-external", action="store_true", help="Mathpix など外部サービスへ画像を送ることに同意する（--backend mathpix に必要）")
    v.add_argument("--no-open", action="store_true")
    v.add_argument("--roots-file", default=None, help="画面から追加したフォルダを覚えておく場所（.app 用）")
    v.add_argument("--state-file", default=None, help="接続情報（ポート・トークン）を書き出す場所（.app 用）")
    v.add_argument("--idle-exit", type=float, default=None, metavar="分", help="画面からの通信がこの分数なければ終了する（.app 用）")
    v.add_argument("--auto", action="store_true", help="PDF を1つだけ指定して開いたとき、全ページを自動で検出・認識する（既定ではしない。「このページを認識」で行う）")
    v.add_argument("--no-auto", action="store_true", help=argparse.SUPPRESS)  # 以前の指定との互換（今は既定でしない）
    v.add_argument("--readonly", action="store_true", help="名前の変更・移動・削除・フォルダ作成を受け付けない")
    v.add_argument("--verify", action="store_true",
                   help="2つ目のモデル（既定 models/unimernet_tiny）でも読み、食い違った式に「要確認」の印を付ける（約3倍遅い）")
    v.set_defaults(func=cmd_viewer)

    ao = sub.add_parser("app-open", help="（.app 用）動いているビューアで資料やフォルダを開く。なければ起動する")
    ao.add_argument("paths", nargs="*", help="資料（PDF・EPUB・画像など）またはフォルダ")
    ao.add_argument("--folder", default=None)
    ao.add_argument("--backend", default="unimernet")
    ao.add_argument("--if-running", action="store_true", help="動いていれば画面を開いて終了コード0、動いていなければ1")
    ao.add_argument("--print-last", action="store_true", help="前回開いたフォルダを表示する")
    ao.add_argument("--hook", action="store_true", help="起動時のフック（hooks/on-launch）があれば実行する")
    ao.set_defaults(func=cmd_app_open)
    ast = sub.add_parser("app-stop", help="（.app 用）常駐しているビューアを止める")
    ast.set_defaults(func=cmd_app_stop)
    ba = sub.add_parser("build-app", help="macOS のアプリを作る（Launchpad・Spotlight から開ける。Reader と Snip の2つ）")
    ba.add_argument("--dest", default="~/Applications", help="作る場所（既定: ~/Applications）")
    ba.add_argument("--no-register", action="store_true", help="LaunchServices に登録しない")
    ba.add_argument("--no-snip", action="store_true", help="画面 Snip 用のアプリ（Alice STEM Snip.app）は作らない")
    ba.set_defaults(func=cmd_build_app)

    r = sub.add_parser("recognize", help="式の矩形を切り出して LaTeX を認識する")
    r.add_argument("input")
    r.add_argument("--formulas", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--backend", default="unimernet", help="unimernet / ppformulanet / mathpix")
    r.add_argument("--allow-external", action="store_true", help="Mathpix など外部サービスへ画像を送ることに同意する")
    r.add_argument("--workdir", default=None)
    r.add_argument("--dpi", type=int, default=200)
    r.add_argument("--all", action="store_true", help="LaTeX が既にある式も再認識する")
    r.add_argument("--verify", action="store_true", help="2つ目のモデルでも読み、食い違った式に「要確認」の印を付ける")
    r.set_defaults(func=cmd_recognize)

    m = sub.add_parser("import-mineru", help="MinerU の結果 JSON から式 JSON を作る")
    m.add_argument("input", help="MinerU に渡した元PDF（ハッシュとページ寸法の確認用）")
    m.add_argument("mineru_json", help="*_middle.json または *_content_list.json")
    m.add_argument("--out", required=True)
    m.add_argument("--frame", choices=["auto", "rendered", "unrotated"], default="auto",
                   help="MinerU の座標の基準ページ（既定 auto: 表示どおり）")
    m.set_defaults(func=cmd_import_mineru)
    pr = sub.add_parser("prepare", help="開く前に数式をまとめて検出・認識しておく（フォルダ可、中断・再開可）")
    pr.add_argument("paths", nargs="+", help="PDF またはフォルダ")
    pr.add_argument("--library", default=None, help="結果の保存先（既定: プロジェクトの library/、環境変数 PLO_LIBRARY）")
    pr.add_argument("--no-recursive", action="store_true")
    pr.add_argument("--max-pages", type=int, default=None, help="1つの PDF で処理する最大ページ数")
    pr.add_argument("--time-limit", type=float, default=None, help="全体の上限（秒）。超えたら区切りのよいところで止める")
    pr.add_argument("--verify", action="store_true", help="2つ目のモデルでも読み、食い違った式に「要確認」の印を付ける（約3倍遅い）")
    pr.add_argument("--backend", default="unimernet", help="認識エンジン（unimernet / ppformulanet / mathpix）")
    pr.add_argument("--allow-external", action="store_true", help="Mathpix など外部サービスへ画像を送ることに同意する（--backend mathpix に必要）")
    pr.set_defaults(func=cmd_prepare)

    lb = sub.add_parser("library", help="事前処理した PDF の一覧")
    lb.add_argument("--library", default=None)
    lb.set_defaults(func=cmd_library)

    dt = sub.add_parser("detect", help="全ページの数式を自動検出する（MFD）")
    dt.add_argument("input")
    dt.add_argument("--out", required=True)
    dt.add_argument("--recognize", action="store_true", help="続けて UniMERNet で認識する")
    dt.set_defaults(func=cmd_detect)

    mp = sub.add_parser("mathpix-plan", help="Mathpix の A/B 検証4ジョブを計画する（送信しない）")
    mp.add_argument("fixtures_dir")
    mp.add_argument("--out-dir", default="outputs/mathpix")
    mp.set_defaults(func=cmd_mathpix_plan)

    fm = sub.add_parser("formats", help="この環境で開ける形式（PDF・EPUB・DjVu・画像・Markdown・LaTeX など）と、足りない道具")
    fm.set_defaults(func=cmd_formats)

    cv = sub.add_parser("convert", help="PDF 以外の資料を PDF に変換する（原本は変えない。結果はライブラリにも残る）")
    cv.add_argument("input")
    cv.add_argument("output", nargs="?", default=None)
    cv.add_argument("--library", default=None)
    cv.add_argument("--force", action="store_true")
    cv.set_defaults(func=cmd_convert)

    sn = sub.add_parser("snip", help="画像（手書きの写真も可）や画面の範囲を読んで、LaTeX・文章を出力する")
    sn.add_argument("image", nargs="?", default=None)
    sn.add_argument("--screen", action="store_true", help="画面の範囲をドラッグで選ぶ（macOS）")
    sn.add_argument("--mode", choices=["auto", "math", "text"], default="auto")
    sn.add_argument("--backend", default="unimernet", choices=["unimernet", "unimernet_tiny", "ppformulanet", "mathpix"])
    sn.add_argument("--allow-external", action="store_true")
    sn.add_argument("--delim", choices=["paren", "dollar", "none"], default="paren")
    sn.add_argument("--wrap", action="store_true", help="数式だけの結果にも区切りを付ける")
    sn.add_argument("--copy", action="store_true", help="結果をクリップボードへ（macOS）")
    sn.add_argument("--json", action="store_true")
    sn.set_defaults(func=cmd_snip)

    asn = sub.add_parser("app-snip", help="（Snip アプリ用）画面の範囲を読み、結果をクリップボードへ")
    asn.add_argument("--mode", choices=["auto", "math", "text"], default="auto")
    asn.add_argument("--backend", default="unimernet")
    asn.set_defaults(func=cmd_app_snip)

    vd = sub.add_parser("verify-derived", help="派生PDFの外観・ページ属性を原本と比べる")
    vd.add_argument("original")
    vd.add_argument("derived")
    vd.add_argument("--dpi", type=int, action="append", default=None)
    vd.set_defaults(func=cmd_verify_derived)
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    if getattr(a, "dpi", None) is None and a.cmd == "embed":
        a.dpi = [72]
    return a.func(a)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
