"""開く前に認識しておく（事前処理）とライブラリ。

Mathpix Snip Web と同じく、変換（数式の座標と LaTeX の作成）を先に済ませておき、
ビューアは結果を読むだけにする。

- ライブラリ: PDF の中身の SHA-256 で引く作業領域 `library/<ハッシュ先頭16文字>/`。
  名前を変えたり移動したりした PDF でも、同じ中身なら同じ結果を使う。
  - formulas.json: 式の枠と LaTeX（ビューアと共通の形式）
  - meta.json: 元の名前・パス、ページ数、状態（pending / running / done / interrupted / error）、件数、所要時間
  - history.jsonl: 変更履歴（ビューアと共通）
- 再開: 処理済みのページ（meta の auto_pages）と、認識済みの式は飛ばす。中断はページ・式の単位。
- 排他: 事前処理中は `.prepare.lock`、ビューア表示中は `.viewer.lock` を置き、同じ結果を同時に書き換えない。
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path
from typing import Callable

import pymupdf

from . import coords, pdfcheck, safety
from .models import Formula, FormulaSet
from .recognize.base import RecognizerUnavailable, crop_png
from .recognize.region import recognize_png

ROOT = Path(__file__).resolve().parents[2]


class PrepareExit:
    OK = 0
    USAGE = 2
    INTERRUPTED = 6
    MODEL_MISSING = 8
    PARTIAL = 9


# ---------------------------------------------------------------- ライブラリ
def library_root(path=None) -> Path:
    return Path(path or os.environ.get("PLO_LIBRARY") or ROOT / "library")


def entry_dir(pdf_path, library=None, sha: str | None = None) -> tuple[Path, str]:
    sha = sha or safety.sha256_file(pdf_path)
    return library_root(library) / sha[:16], sha


def read_meta(d: Path) -> dict:
    p = d / "meta.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def write_meta(d: Path, meta: dict) -> None:
    tmp = d / "meta.json.tmp"
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(d / "meta.json")


def list_library(library=None) -> list[dict]:
    root = library_root(library)
    out = []
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        m = read_meta(d)
        if not m:
            continue
        fs_path = d / "formulas.json"
        n = n_latex = 0
        if fs_path.exists():
            fl = json.loads(fs_path.read_text(encoding="utf-8")).get("formulas", [])
            n = len(fl)
            n_latex = sum(1 for f in fl if f.get("latex"))
        out.append({"id": d.name, "name": m.get("name"), "pages": m.get("pages"), "state": m.get("state"),
                    "formulas": n, "with_latex": n_latex, "elapsed_s": m.get("elapsed_s"),
                    "source_paths": m.get("source_paths", []), "dir": str(d)})
    return out


# ---------------------------------------------------------------- 排他
def _pid_alive(pid: int) -> bool:
    from .osutil import pid_alive

    return pid_alive(pid)  # Windows では os.kill(pid, 0) が Ctrl+C を送るので、OS ごとに調べる


def lock_holder(path: Path) -> int | None:
    """ロックファイルの持ち主が生きていればその pid。死んでいれば古いロックとして消して None。"""
    if not path.exists():
        return None
    try:
        pid = int(path.read_text().strip() or "0")
    except (ValueError, OSError):
        pid = 0
    if pid and _pid_alive(pid):
        return pid
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    return None


@contextlib.contextmanager
def hold_lock(path: Path):
    lock_holder(path)  # 古いロックは片付ける
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)  # 既にあれば FileExistsError
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


# ---------------------------------------------------------------- 処理本体（ビューアと共通）
def _contained(inner, outer) -> float:
    ix = max(0.0, min(inner[2], outer[2]) - max(inner[0], outer[0]))
    iy = max(0.0, min(inner[3], outer[3]) - max(inner[1], outer[1]))
    a = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return ix * iy / a if a > 0 else 0.0


def process_document(pdf_path, fs: FormulaSet, *, detector, backend, lock=None,
                     save: Callable[[], None] = lambda: None, progress: Callable[..., None] = lambda **k: None,
                     log: Callable[..., None] = lambda *a, **k: None, should_stop: Callable[[], bool] = lambda: False,
                     max_pages: int | None = None, only_pages=None, force: bool = False, verifier=None) -> dict:
    """未処理のページを検出し、未認識の枠を認識する。fs をその場で更新し、こまめに save() する。

    only_pages を渡すと、そのページだけを処理する（0 始まり）。force なら、処理済みのページでも検出し直す
    （既にある枠と重なる検出は追加しないので、既存の結果は消えない）。
    lock を渡すと（ビューアの RLock など）、fs を触る間だけ取る。認識の待ち時間は lock の外。
    戻り値: {"state": done|interrupted, "found": n, "recognized": n, "failed": n}
    """
    from .detect.mfd import detect_page

    lk = lock or contextlib.nullcontext()
    doc = pymupdf.open(pdf_path)  # この処理専用（PyMuPDF の文書はスレッド間で共有しない）
    found = recognized = failed = 0
    try:
        if only_pages is not None:
            pages = sorted({int(p) for p in only_pages if 0 <= int(p) < doc.page_count})
        else:
            pages = range(doc.page_count if not max_pages else min(doc.page_count, max_pages))
        for pno in pages:
            with lk:
                done = pno in set(fs.meta.get("auto_pages", [])) and not force
            progress(phase="detect", page=pno + 1, pages=len(pages))
            if done:
                continue
            if should_stop():
                return {"state": "interrupted", "found": found, "recognized": recognized, "failed": failed}
            hits = detect_page(detector, doc[pno])
            with lk:
                n = 0
                for d in hits:
                    # 既にある枠（手で描いた枠を含む）と重なるものは追加しない
                    if any(g.page == pno and g.kind != "text" and (coords.iou(g.rect, d["rect"]) > 0.5 or _contained(d["rect"], g.rect) > 0.8)
                           for g in fs.formulas):
                        continue
                    n += 1
                    fid = f"auto_p{pno}_{n}"
                    while any(g.id == fid for g in fs.formulas):
                        n += 1
                        fid = f"auto_p{pno}_{n}"
                    fs.formulas.append(Formula(id=fid, page=pno, rect=tuple(d["rect"]), source="mfd",
                                               kind=d["kind"], confidence=d["conf"], status="pending"))
                    found += 1
                if pno not in fs.meta.setdefault("auto_pages", []):
                    fs.meta["auto_pages"].append(pno)
                fs.meta["detector"] = {"name": "PDF-Extract-Kit MFD yolo_v8_ft", **getattr(detector, "info", {}).get("versions", {})}
                log("detect", page=pno, added=n)
                save()
        with lk:
            todo = [f.id for f in fs.formulas
                    if f.latex is None and f.status != "excluded" and f.page in pages]
        progress(phase="recognize", total=len(todo), recognized=0)
        for k, fid in enumerate(todo):
            if should_stop():
                return {"state": "interrupted", "found": found, "recognized": recognized, "failed": failed}
            with lk:
                f = next((g for g in fs.formulas if g.id == fid), None)
                if f is None or f.latex is not None:
                    continue
                try:
                    png = crop_png(doc[f.page], f.rect, dpi=200)
                except ValueError as e:  # ページの外の枠など。この式だけ失敗にして、続ける
                    f.reason = f"recognition_failed: {e}"
                    failed += 1
                    save()
                    continue
                split = f.kind != "inline"
            res = recognize_png(backend, png, dpi=200, split=split, verifier=verifier)
            with lk:
                f = next((g for g in fs.formulas if g.id == fid), None)
                if f is not None and f.latex is None:
                    f.alt_latex, f.review = getattr(res, "alt", None), getattr(res, "review", None)
                    if res.latex:
                        f.ocr_latex = getattr(res, "raw", None) or res.latex
                        f.latex = res.latex
                        f.source = "mfd+" + getattr(backend, "name", "?") if f.source == "mfd" else f.source
                        f.reason = f"partial: {res.error}" if res.error else None
                        recognized += 1
                    else:
                        f.reason = f"recognition_failed: {res.error}"
                        failed += 1
                    save()
            progress(phase="recognize", recognized=k + 1)
        return {"state": "done", "found": found, "recognized": recognized, "failed": failed}
    finally:
        doc.close()


# ---------------------------------------------------------------- 事前処理（CLI）
def collect_pdfs(paths, recursive=True, all_formats=False) -> list[Path]:
    """PDF（all_formats なら、ビューアで開ける全形式: EPUB・DjVu・画像・Markdown・LaTeX など）を集める。"""
    from .docformats import is_supported

    ok = is_supported if all_formats else (lambda x: Path(x).suffix.lower() == ".pdf")
    out: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            out += sorted(x for x in it if x.is_file() and not x.name.startswith(".") and ok(x))
        elif p.is_file() and ok(p):
            out.append(p)
    seen, uniq = set(), []
    for p in out:
        r = p.resolve()
        if r not in seen:
            seen.add(r)
            uniq.append(p)
    return uniq


def prepare_one(pdf: Path, *, detector, backend, library=None, should_stop=lambda: False,
                max_pages=None, log=print, verifier=None, source: Path | None = None) -> dict:
    """1つの PDF を事前処理する。原本は読むだけ。

    PDF 以外（EPUB・画像・DjVu など）は、先に docformats.as_pdf で変換した PDF を渡し、元のファイルを source に渡す。
    """
    source = Path(source) if source else Path(pdf)
    pf = pdfcheck.preflight(pdf)
    if not pf.ok:
        return {"pdf": str(pdf), "state": "skipped", "reason": "; ".join(pf.reasons)}
    d, sha = entry_dir(pdf, library)
    d.mkdir(parents=True, exist_ok=True)
    viewer = lock_holder(d / ".viewer.lock")
    if viewer:
        return {"pdf": str(pdf), "state": "skipped", "reason": f"ビューアで開かれている（pid {viewer}）", "entry": d.name}
    try:
        with hold_lock(d / ".prepare.lock"):
            fs_path = d / "formulas.json"
            fs = FormulaSet.load(fs_path) if fs_path.exists() else FormulaSet(input_sha256=sha)
            if fs.input_sha256 and fs.input_sha256 != sha:
                return {"pdf": str(pdf), "state": "error", "reason": "ライブラリの結果と PDF のハッシュが一致しない"}
            fs.input_sha256 = sha
            doc = pymupdf.open(pdf)
            npages = doc.page_count
            doc.close()
            meta = read_meta(d)
            paths = list(dict.fromkeys([*meta.get("source_paths", []), str(source.resolve())]))
            meta.update({"name": source.name, "input_sha256": sha, "pages": npages, "source_paths": paths,
                         "state": "running", "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
            write_meta(d, meta)
            t0 = time.monotonic()

            def save():
                fs.save(fs_path)

            def hist(action, **kw):
                rec = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "action": action, "by": "prepare", **kw}
                with open(d / "history.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")

            def progress(**k):
                if k.get("phase") == "detect":
                    log(f"  {pdf.name}: 検出 {k['page']}/{k['pages']} ページ")
                elif k.get("phase") == "recognize" and "total" in k:
                    log(f"  {pdf.name}: 認識 {k['total']} 件")

            try:
                r = process_document(pdf, fs, detector=detector, backend=backend, save=save, progress=progress,
                                     log=hist, should_stop=should_stop, max_pages=max_pages, verifier=verifier)
            except RecognizerUnavailable as e:
                meta.update(state="error", error=str(e))
                write_meta(d, meta)
                raise
            except Exception as e:
                meta.update(state="error", error=f"{type(e).__name__}: {e}")
                write_meta(d, meta)
                return {"pdf": str(pdf), "state": "error", "reason": meta["error"], "entry": d.name}
            elapsed = round(time.monotonic() - t0, 1)
            complete = r["state"] == "done" and (not max_pages or max_pages >= npages)
            meta.update(state="done" if complete else ("interrupted" if r["state"] == "interrupted" else "partial"),
                        finished=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        elapsed_s=round(meta.get("elapsed_s", 0) + elapsed, 1),  # 処理時間の累計（再開分を含む）
                        counts={"formulas": len(fs.formulas), "with_latex": sum(1 for f in fs.formulas if f.latex),
                                "failed": sum(1 for f in fs.formulas if f.latex is None and f.status != "excluded"),
                                "review": sum(1 for f in fs.formulas if f.review and f.status != "excluded")})
            meta.pop("error", None)
            write_meta(d, meta)
            return {"pdf": str(pdf), "entry": d.name, "elapsed_s": elapsed, **r, "meta_state": meta["state"]}
    except FileExistsError:
        return {"pdf": str(pdf), "state": "skipped", "reason": "別の事前処理が実行中", "entry": d.name}


def process_text(pdf_path, fs: FormulaSet, *, text_backend, lock=None, save: Callable[[], None] = lambda: None,
                 progress: Callable[..., None] = lambda **k: None, log: Callable[..., None] = lambda *a, **k: None,
                 should_stop: Callable[[], bool] = lambda: False, only_pages=None, force: bool = False, detector=None) -> dict:
    """文章（日本語など）を行ごとに読んで、種類 text の枠として fs に加える。数式の枠は塗りつぶして読まない。

    force なら、処理済みのページも読み直す（そのページの、修正・確定していない文章の枠は置き換える）。
    戻り値: {"state": done|interrupted, "lines": n}
    """
    from .detect.mfd import detect_page
    from .recognize.textocr import ocr_page_text

    lk = lock or contextlib.nullcontext()
    doc = pymupdf.open(pdf_path)
    lines = 0
    try:
        pages = sorted({int(p) for p in only_pages if 0 <= int(p) < doc.page_count}) if only_pages is not None \
            else list(range(doc.page_count))
        for i, pno in enumerate(pages):
            progress(phase="text", page=i + 1, pages=len(pages))
            with lk:
                if pno in set(fs.meta.get("text_pages", [])) and not force:
                    continue
                avoid = [pymupdf.Rect(f.rect) * doc[pno].rotation_matrix for f in fs.formulas
                         if f.page == pno and f.kind != "text" and f.status != "excluded"]
                undetected = detector is not None and pno not in set(fs.meta.get("auto_pages", []))
            if should_stop():
                return {"state": "interrupted", "lines": lines}
            if undetected:  # 数式をまだ検出していないページは、塗りつぶすために検出だけしておく（枠は足さない）
                avoid += [pymupdf.Rect(d["view"]) for d in detect_page(detector, doc[pno])]
            hits = ocr_page_text(text_backend, doc[pno], avoid)
            with lk:
                keep = [f for f in fs.formulas
                        if not (f.page == pno and f.kind == "text" and f.source == "ppocr" and not f.edited and f.status == "pending")]
                fs.formulas[:] = keep
                n = 0
                for d in hits:
                    if any(g.page == pno and g.kind == "text" and coords.iou(g.rect, d["rect"]) > 0.5 for g in fs.formulas):
                        continue  # 修正・確定した文章の枠と重なるものは、足さない
                    n += 1
                    fid = f"text_p{pno}_{n}"
                    while any(g.id == fid for g in fs.formulas):
                        n += 1
                        fid = f"text_p{pno}_{n}"
                    fs.formulas.append(Formula(id=fid, page=pno, rect=tuple(d["rect"]), latex=d["text"], ocr_latex=d["text"],
                                               source="ppocr", kind="text", confidence=d["score"], status="pending"))
                    lines += 1
                if pno not in fs.meta.setdefault("text_pages", []):
                    fs.meta["text_pages"].append(pno)
                fs.meta["text_ocr"] = {"engine": getattr(text_backend, "version", "ppocr")}
                log("text", page=pno, added=n)
                save()
        return {"state": "done", "lines": lines}
    finally:
        doc.close()
