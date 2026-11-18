"""式 JSON の各矩形を切り出して認識し、新しい式 JSON を書く（再開可能）。

作業領域の state.json に、入力ハッシュ・設定ハッシュ・モデル版・処理済みの式を記録する。
再開はこれらが一致した場合に限る。SIGINT/SIGTERM では次の式に進まず、状態を残して止める。
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import pymupdf

from .. import safety
from ..models import FormulaSet
from .base import RecognizerUnavailable, crop_png, get_backend


class RecognizeExit:
    OK = 0
    USAGE = 2
    SAFETY = 3
    INTERRUPTED = 6
    MODEL_MISSING = 8
    PARTIAL = 9  # 一部の式で認識に失敗（出力はするが成功件数に含めない）


def _digest(d) -> str:
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]


def run_recognition(input_path, formulas_path, out_path, backend="unimernet", workdir=None, dpi=200,
                    only_missing=True, should_stop=lambda: False, backend_obj=None, log=None) -> int:
    log = log or (lambda *a: print(*a, file=sys.stderr))
    inp, out = Path(input_path), Path(out_path)
    if not inp.is_file():
        log(f"入力PDFがない: {inp}")
        return RecognizeExit.USAGE
    if out.exists() or out.is_symlink():
        log(f"出力先が既に存在する（上書きしない）: {out}")
        return RecognizeExit.SAFETY
    if out.resolve() in (inp.resolve(), Path(formulas_path).resolve()):
        log("出力先が入力と同じ")
        return RecognizeExit.SAFETY
    sha = safety.sha256_file(inp)
    fs = FormulaSet.load(formulas_path)
    if fs.input_sha256 and fs.input_sha256 != sha:
        log("式 JSON の input_sha256 が入力PDFと一致しない")
        return RecognizeExit.USAGE

    try:
        be = backend_obj or get_backend(backend)
        if hasattr(be, "start"):
            be.start()
    except RecognizerUnavailable as e:
        log(str(e))
        return RecognizeExit.MODEL_MISSING

    config = {"dpi": dpi, "only_missing": only_missing, "backend": backend}
    wd = Path(workdir) if workdir else Path.cwd() / "runs" / f"recognize-{sha[:12]}-{_digest(config)}"
    wd.mkdir(parents=True, exist_ok=True)
    state_path = wd / "state.json"
    key = {"input_sha256": sha, "config": _digest(config), "model": getattr(be, "version", "?"),
           "formulas_sha256": safety.sha256_file(formulas_path)}
    state = {"key": key, "done": {}}
    if state_path.exists():
        old = json.loads(state_path.read_text())
        if old.get("key") == key:
            state = old
            log(f"再開: 処理済み {len(state['done'])} 件")
        else:
            log("作業領域の条件が一致しないため、最初から処理する")

    def save_state():
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1))
        tmp.replace(state_path)

    doc = pymupdf.open(inp)
    interrupted = False
    try:
        for f in fs.formulas:
            if f.id in state["done"]:
                continue
            if only_missing and f.latex:
                continue
            if f.status == "excluded" or not (0 <= f.page < doc.page_count):
                continue
            if should_stop():
                interrupted = True
                break
            t0 = time.monotonic()
            res = be.recognize([crop_png(doc[f.page], f.rect, dpi=dpi)])[0]
            state["done"][f.id] = {
                "latex": res.latex, "raw": getattr(res, "raw", res.latex), "error": res.error,
                "elapsed_s": round(time.monotonic() - t0, 3),
            }
            save_state()
            log(f"{f.id}: {res.latex if res.latex else 'ERROR ' + str(res.error)}")
    finally:
        doc.close()
        if hasattr(be, "close") and backend_obj is None:
            be.close()

    failed = 0
    for f in fs.formulas:
        d = state["done"].get(f.id)
        if not d:
            continue
        if d["latex"] is None:
            f.status, f.reason = "error", f"recognition_failed: {d['error']}"
            failed += 1
        else:
            f.ocr_latex, f.latex = d["raw"], d["latex"]
            f.source, f.confidence, f.edited = backend, None, False
            f.status, f.reason = "pending", None
    fs.input_sha256 = sha
    fs.meta.setdefault("recognition", []).append({"backend": backend, "model": key["model"], "dpi": dpi,
                                                  "count": len(state["done"]), "failed": failed,
                                                  "interrupted": interrupted})
    if interrupted:
        log(f"中断: 状態を {state_path} に保存。同じ引数で再実行すると続きから処理する")
        return RecognizeExit.INTERRUPTED
    fs.save(out)
    return RecognizeExit.PARTIAL if failed else RecognizeExit.OK
