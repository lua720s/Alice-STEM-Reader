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
                 roots_file=a.roots_file, open_in=a.open_in)


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
            applaunch.open_ui(st["url"])
        return 0
    if a.hook:
        applaunch.run_hook("on-launch")  # 利用者が置いたフック（なければ何もしない）
        applaunch.ensure_agent()  # メニューバーの Snip（全体のショートカット）
    files = [p for p in a.paths if not Path(p).expanduser().is_dir()]
    dirs = [p for p in a.paths if Path(p).expanduser().is_dir()]
    try:
        opener = None
        if a.open_in == "browser":
            opener = applaunch.webbrowser.open
        elif a.open_in == "app":
            opener = lambda u: (applaunch.osutil.open_app_window(u, applaunch.support_dir() / "window-profile")  # noqa: E731
                                or applaunch.webbrowser.open(u))
        res = applaunch.app_open(files=files, folder=a.folder or (dirs[0] if dirs else None), backend=a.backend,
                                 view=a.view, new=a.new, opener=opener)
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
    from .appbuild import build_launchers

    try:
        made = build_launchers(a.dest, register=not a.no_register, snip=not a.no_snip, start_at_login=a.start_at_login)
    except (RuntimeError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 2
    except Exception as e:  # subprocess.CalledProcessError など
        print(f"作成に失敗: {e}\n{getattr(e, 'stderr', '')}", file=sys.stderr)
        return 2
    for m in made:
        print(f"created: {m}")
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


def cmd_window(a) -> int:
    """pywebview（pip install pywebview）で、画面をアプリのウィンドウとして開く。"""
    try:
        import webview  # type: ignore
    except ImportError:
        print("pywebview が入っていない（pip install pywebview）", file=sys.stderr)
        return 2
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True  # 書き出し（Markdown・TSV など）を保存できるように
    except Exception:
        pass
    webview.create_window("Alice STEM Reader", a.url, width=a.width, height=a.height, min_size=(900, 600))
    webview.start()
    return 0


def cmd_hotkey_agent(a) -> int:
    """（Windows・Linux）全体のショートカットを受け付けて常駐する。macOS はメニューバーの Snip アプリが受け持つ。"""
    from .hotkeys import run_agent

    return run_agent(once=a.check)


def cmd_setup_models(a) -> int:
    from .setup_models import main as setup_main

    return setup_main(tiny=a.tiny, paddle=not a.no_paddle)


def cmd_library(a) -> int:
    from .prepare import list_library

    _print(list_library(a.library))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="alice-stem-reader",
                                description="Alice STEM Reader: read documents and extract formulas and text as LaTeX / Markdown, entirely on your machine")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("embed", help="embed the LaTeX from a formula JSON and publish the derived PDF only if it passes verification")
    e.add_argument("input")
    e.add_argument("output")
    e.add_argument("--formulas", required=True)
    e.add_argument("--mode", choices=["invisible", "actualtext"], default="invisible")
    e.add_argument("--dpi", type=int, action="append", default=None, help="DPI for the appearance comparison (repeatable, default 72)")
    e.add_argument("--pdfkit", choices=["auto", "require", "off"], default="auto")
    e.add_argument("--require-confirmed", action="store_true", help="embed only formulas with status=confirmed")
    e.add_argument("--allow-empty", action="store_true")
    e.add_argument("--timeout", type=float, default=None, help="seconds")
    e.add_argument("--report", default=None, help="where to write the JSON report")
    e.set_defaults(func=cmd_embed)

    f = sub.add_parser("fixtures", help="generate the synthetic test fixtures")
    f.add_argument("out_dir")
    f.set_defaults(func=cmd_fixtures)

    c = sub.add_parser("copycheck", help="copy comparison table for a derived PDF (MuPDF / PDFKit)")
    c.add_argument("pdf")
    c.add_argument("--formulas", required=True)
    c.add_argument("--no-pdfkit", action="store_true")
    c.set_defaults(func=cmd_copycheck)

    v = sub.add_parser("viewer", help="start the local viewer (127.0.0.1 only); folders appear as a collection on the left")
    v.add_argument("paths", nargs="*", help="documents or folders (optional; you can also open them from the viewer)")
    v.add_argument("--formulas", default=None, help="formula JSON (created in the work area if omitted)")
    v.add_argument("--workdir", default=None)
    v.add_argument("--port", type=int, default=0)
    v.add_argument("--backend", default="none", help="recognition engine (none / unimernet[=base] / unimernet_tiny / ppformulanet / mathpix); can also be switched in the viewer")
    v.add_argument("--allow-external", action="store_true", help="consent to sending images to an external service such as Mathpix (required for --backend mathpix)")
    v.add_argument("--no-open", action="store_true")
    v.add_argument("--open-in", choices=["app", "browser"], default=None,
                   help="open the UI in an app window (Chrome/Edge --app or pywebview) or the default browser (default: the app setting)")
    v.add_argument("--roots-file", default=None, help="file that remembers folders added from the viewer (used by the app)")
    v.add_argument("--state-file", default=None, help="file to write connection info (port, token) to (used by the app)")
    v.add_argument("--idle-exit", type=float, default=None, metavar="MIN", help="exit after this many minutes without requests from the browser (used by the app)")
    v.add_argument("--auto", action="store_true", help="when opening a single document, detect and recognize every page immediately (off by default; use \"Recognize this page\")")
    v.add_argument("--no-auto", action="store_true", help=argparse.SUPPRESS)  # 以前の指定との互換（今は既定でしない）
    v.add_argument("--readonly", action="store_true", help="refuse rename, move, delete and folder creation")
    v.add_argument("--verify", action="store_true",
                   help="also read with a second model (default models/unimernet_tiny) and flag disagreements for review (about 3x slower)")
    v.set_defaults(func=cmd_viewer)

    ao = sub.add_parser("app-open", help="(used by the app) open documents or folders in the running viewer, starting it if needed")
    ao.add_argument("paths", nargs="*", help="documents (PDF, EPUB, images, ...) or folders")
    ao.add_argument("--folder", default=None)
    ao.add_argument("--backend", default="unimernet")
    ao.add_argument("--if-running", action="store_true", help="if running, open the browser and exit 0; otherwise exit 1")
    ao.add_argument("--print-last", action="store_true", help="print the folder opened last time")
    ao.add_argument("--open-in", choices=["app", "browser"], default=None, help="override the open_mode setting")
    ao.add_argument("--view", choices=["reader", "snips", "settings"], default=None, help="screen to show (reader, Snip dashboard, settings)")
    ao.add_argument("--new", choices=["latex", "markdown"], default=None, help="open the Snip dashboard with a new blank entry")
    ao.add_argument("--hook", action="store_true", help="run the launch hook (hooks/on-launch) if present, and start the menu bar Snip agent")
    ao.set_defaults(func=cmd_app_open)
    ast = sub.add_parser("app-stop", help="(used by the app) stop the running viewer")
    ast.set_defaults(func=cmd_app_stop)
    ba = sub.add_parser("build-app", help="create launchers: macOS apps (Launchpad/Spotlight), Windows Start menu + Send to, Linux .desktop entries")
    ba.add_argument("--dest", default=None, help="(macOS) destination folder (default: ~/Applications)")
    ba.add_argument("--start-at-login", action="store_true",
                    help="(Windows/Linux) start the global-shortcut agent at login (macOS: use the menu bar app's menu)")
    ba.add_argument("--no-register", action="store_true", help="do not register with LaunchServices")
    ba.add_argument("--no-snip", action="store_true", help="do not build Alice STEM Snip.app")
    ba.set_defaults(func=cmd_build_app)

    r = sub.add_parser("recognize", help="crop formula rectangles and recognize them as LaTeX")
    r.add_argument("input")
    r.add_argument("--formulas", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--backend", default="unimernet", help="unimernet / ppformulanet / mathpix")
    r.add_argument("--allow-external", action="store_true", help="consent to sending images to an external service such as Mathpix")
    r.add_argument("--workdir", default=None)
    r.add_argument("--dpi", type=int, default=200)
    r.add_argument("--all", action="store_true", help="re-recognize formulas that already have LaTeX")
    r.add_argument("--verify", action="store_true", help="also read with a second model and flag disagreements for review")
    r.set_defaults(func=cmd_recognize)

    m = sub.add_parser("import-mineru", help="build a formula JSON from MinerU output")
    m.add_argument("input", help="the PDF given to MinerU (to check its hash and page sizes)")
    m.add_argument("mineru_json", help="*_middle.json or *_content_list.json")
    m.add_argument("--out", required=True)
    m.add_argument("--frame", choices=["auto", "rendered", "unrotated"], default="auto",
                   help="reference frame of MinerU coordinates (default auto)")
    m.set_defaults(func=cmd_import_mineru)
    pr = sub.add_parser("prepare", help="detect and recognize formulas ahead of time (folders allowed; can be interrupted and resumed)")
    pr.add_argument("paths", nargs="+", help="documents or folders")
    pr.add_argument("--library", default=None, help="where results are stored (default: the project library/ or $PLO_LIBRARY)")
    pr.add_argument("--no-recursive", action="store_true")
    pr.add_argument("--max-pages", type=int, default=None, help="maximum pages to process per document")
    pr.add_argument("--time-limit", type=float, default=None, help="overall time limit in seconds; stops at a safe point when exceeded")
    pr.add_argument("--verify", action="store_true", help="also read with a second model and flag disagreements for review (about 3x slower)")
    pr.add_argument("--backend", default="unimernet", help="recognition engine (unimernet / ppformulanet / mathpix)")
    pr.add_argument("--allow-external", action="store_true", help="consent to sending images to an external service such as Mathpix (required for --backend mathpix)")
    pr.set_defaults(func=cmd_prepare)

    lb = sub.add_parser("library", help="list documents processed ahead of time")
    lb.add_argument("--library", default=None)
    lb.set_defaults(func=cmd_library)

    dt = sub.add_parser("detect", help="detect formulas on every page (MFD)")
    dt.add_argument("input")
    dt.add_argument("--out", required=True)
    dt.add_argument("--recognize", action="store_true", help="then recognize them with UniMERNet")
    dt.set_defaults(func=cmd_detect)

    mp = sub.add_parser("mathpix-plan", help="plan the four Mathpix A/B validation jobs (nothing is sent)")
    mp.add_argument("fixtures_dir")
    mp.add_argument("--out-dir", default="outputs/mathpix")
    mp.set_defaults(func=cmd_mathpix_plan)

    fm = sub.add_parser("formats", help="show supported formats (PDF, EPUB, DjVu, images, Markdown, LaTeX, ...) and missing tools")
    fm.set_defaults(func=cmd_formats)

    cv = sub.add_parser("convert", help="convert a non-PDF document to PDF (the original is never modified; the result is also cached)")
    cv.add_argument("input")
    cv.add_argument("output", nargs="?", default=None)
    cv.add_argument("--library", default=None)
    cv.add_argument("--force", action="store_true")
    cv.set_defaults(func=cmd_convert)

    sn = sub.add_parser("snip", help="read an image (handwriting photos too) or a screen region and print LaTeX / text")
    sn.add_argument("image", nargs="?", default=None)
    sn.add_argument("--screen", action="store_true", help="drag to select a screen region (macOS)")
    sn.add_argument("--mode", choices=["auto", "math", "text"], default="auto")
    sn.add_argument("--backend", default="unimernet", choices=["unimernet", "unimernet_tiny", "ppformulanet", "mathpix"])
    sn.add_argument("--allow-external", action="store_true")
    sn.add_argument("--delim", choices=["paren", "dollar", "none"], default="paren")
    sn.add_argument("--wrap", action="store_true", help="add delimiters to math-only results too")
    sn.add_argument("--copy", action="store_true", help="copy the result to the clipboard (macOS)")
    sn.add_argument("--json", action="store_true")
    sn.set_defaults(func=cmd_snip)

    asn = sub.add_parser("app-snip", help="(used by the Snip app) read a screen region and copy the result")
    asn.add_argument("--mode", choices=["auto", "math", "text"], default="auto")
    asn.add_argument("--backend", default="unimernet")
    asn.set_defaults(func=cmd_app_snip)

    wn = sub.add_parser("window", help="open the UI in a native window with pywebview (used by the app)")
    wn.add_argument("url")
    wn.add_argument("--width", type=int, default=1440)
    wn.add_argument("--height", type=int, default=920)
    wn.set_defaults(func=cmd_window)

    hk = sub.add_parser("hotkey-agent", help="(Windows/Linux) run in the background and handle the global shortcuts")
    hk.add_argument("--check", action="store_true", help="register the shortcuts, report the result and exit")
    hk.set_defaults(func=cmd_hotkey_agent)

    sm = sub.add_parser("setup-models", help="install the recognition models into this project (SHA-256 verified; all platforms)")
    sm.add_argument("--tiny", action="store_true", help="also install UniMERNet tiny")
    sm.add_argument("--no-paddle", action="store_true", help="skip PaddleOCR (text recognition)")
    sm.set_defaults(func=cmd_setup_models)

    vd = sub.add_parser("verify-derived", help="compare a derived PDF's appearance and page attributes with the original")
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
