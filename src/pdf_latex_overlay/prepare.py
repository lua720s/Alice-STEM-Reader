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
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


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
                     max_pages: int | None = None) -> dict:
    """未処理のページを検出し、未認識の枠を認識する。fs をその場で更新し、こまめに save() する。

    lock を渡すと（ビューアの RLock など）、fs を触る間だけ取る。認識の待ち時間は lock の外。
    戻り値: {"state": done|interrupted, "found": n, "recognized": n, "failed": n}
    """
    from .detect.mfd import detect_page

    lk = lock or contextlib.nullcontext()
    doc = pymupdf.open(pdf_path)  # この処理専用（PyMuPDF の文書はスレッド間で共有しない）
    found = recognized = failed = 0
    try:
        pages = range(doc.page_count if not max_pages else min(doc.page_count, max_pages))
        for pno in pages:
            with lk:
                done = pno in set(fs.meta.get("auto_pages", []))
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
                    if any(g.page == pno and (coords.iou(g.rect, d["rect"]) > 0.5 or _contained(d["rect"], g.rect) > 0.8)
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
                fs.meta.setdefault("auto_pages", []).append(pno)
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
                png = crop_png(doc[f.page], f.rect, dpi=200)
                split = f.kind != "inline"
            res = recognize_png(backend, png, dpi=200, split=split)
            with lk:
                f = next((g for g in fs.formulas if g.id == fid), None)
                if f is not None and f.latex is None:
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
def collect_pdfs(paths, recursive=True) -> list[Path]:
    out: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            it = p.rglob("*.pdf") if recursive else p.glob("*.pdf")
            out += sorted(x for x in it if x.is_file() and not x.name.startswith("."))
        elif p.is_file() and p.suffix.lower() == ".pdf":
            out.append(p)
    seen, uniq = set(), []
    for p in out:
        r = p.resolve()
        if r not in seen:
            seen.add(r)
            uniq.append(p)
    return uniq


def prepare_one(pdf: Path, *, detector, backend, library=None, should_stop=lambda: False,
                max_pages=None, log=print) -> dict:
    """1つの PDF を事前処理する。原本は読むだけ。"""
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
            paths = list(dict.fromkeys([*meta.get("source_paths", []), str(pdf.resolve())]))
            meta.update({"name": pdf.name, "input_sha256": sha, "pages": npages, "source_paths": paths,
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
                                     log=hist, should_stop=should_stop, max_pages=max_pages)
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
                                "failed": sum(1 for f in fs.formulas if f.latex is None and f.status != "excluded")})
            meta.pop("error", None)
            write_meta(d, meta)
            return {"pdf": str(pdf), "entry": d.name, "elapsed_s": elapsed, **r, "meta_state": meta["state"]}
    except FileExistsError:
        return {"pdf": str(pdf), "state": "skipped", "reason": "別の事前処理が実行中", "entry": d.name}
