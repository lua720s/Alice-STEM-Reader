"""公開入口 `process_pdf`: 式の選別 → 埋め込み → 検証 → 公開。

検証に一つでも失敗したら派生PDFを公開せず、終了コードと失敗箇所をレポートに残す。
AliceArchive などの呼び出し側は、この関数と RunReport の JSON だけに依存する。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import pymupdf

from . import __version__, coords, copycheck, embed, pdfcheck, safety
from .models import Formula, FormulaSet

REPORT_SCHEMA = "pdf-latex-overlay/run-report"
REPORT_VERSION = "1.0"


class ExitCode:
    OK = 0
    USAGE = 2  # 設定不備・入力 JSON 不正・ハッシュ不一致
    SAFETY = 3  # 原本保護・出力衝突
    UNSUPPORTED = 4  # 暗号化・署名など未対応の PDF
    VERIFY_FAILED = 5  # 検証不合格
    INTERRUPTED = 6  # SIGINT/SIGTERM・タイムアウト
    NOTHING = 7  # 埋め込める式がない
    INTERNAL = 10


@dataclass
class Options:
    mode: str = "invisible"
    dpis: tuple[int, ...] = (72,)
    pdfkit: str = "auto"  # auto / require / off
    require_confirmed: bool = False
    allow_empty: bool = False
    duplicate_iou: float = 0.5
    overlap_ratio: float = 0.1  # 重なりが小さい方の面積のこの割合を超えたら、片方だけを埋め込む
    timeout_s: float | None = None
    report_path: str | None = None

    def digest(self) -> str:
        d = asdict(self)
        d.pop("report_path", None)
        return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]


@dataclass
class RunReport:
    ok: bool = False
    exit_code: int = ExitCode.INTERNAL
    input: str | None = None
    output: str | None = None
    input_sha256: str | None = None
    output_sha256: str | None = None
    options_digest: str | None = None
    mode: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    formulas: list[dict[str, Any]] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    started_at: str | None = None
    elapsed_s: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d = {"schema": REPORT_SCHEMA, "version": REPORT_VERSION, "tool_version": __version__, **d}
        return d


class _Stop(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


def _priority(f: Formula) -> tuple:
    """重なった枠のどれを埋め込むか: 確定 > 修正済み > 小さい枠 > 先に作った枠。"""
    x0, y0, x1, y1 = f.rect
    return (0 if f.status == "confirmed" else 1, 0 if f.edited else 1, (x1 - x0) * (y1 - y0))


def _overlap(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return ix * iy / small if small > 0 else 0.0


def classify(doc: pymupdf.Document, formulas: list[Formula], opts: Options) -> list[Formula]:
    """埋め込める式を選び、それ以外は status=excluded と理由を付ける（入力の式は書き換えない）。

    重なった枠は、優先順位（_priority）の高いものだけを埋め込み、残りは overlaps:<id> で除外する。
    重ねて埋め込むと、選択したときに両方の文字が混ざるため。
    """
    result = [Formula.from_dict(f0.to_dict()) for f0 in formulas]
    candidates: list[Formula] = []
    for f in result:
        reason = None
        if f.kind == "text":
            reason = "text_not_embedded"  # 文章（日本語など）は、派生PDFに埋め込まない。画面でのコピーに使う
        elif f.status in ("excluded", "error"):
            reason = f.reason or f"input_status:{f.status}"
        elif opts.require_confirmed and f.status != "confirmed":
            reason = "not_confirmed"
        elif not f.latex or not f.latex.strip():
            reason = "no_latex"
        elif not (0 <= f.page < doc.page_count):
            reason = "page_out_of_range"
        else:
            page = doc[f.page]
            text = embed.normalize_for_layer(f.latex)
            if coords.clamp_to_page(page, f.rect)[1]:
                reason = "clipped"
            elif opts.mode == "invisible" and not all(embed.is_winansi_ascii(x) for x in embed.layer_lines(f.latex)):
                reason = "non_ascii_latex"
            elif not text:
                reason = "no_latex"
            elif pdfcheck.chars_in_rect(page, f.rect):
                reason = "existing_text_conflict"
        if reason:
            f.status, f.reason = "excluded", reason
        else:
            candidates.append(f)
    order = {id(f): i for i, f in enumerate(result)}
    accepted: dict[int, list[Formula]] = {}
    for f in sorted(candidates, key=lambda x: (_priority(x), order[id(x)])):
        reason = None
        for g in accepted.get(f.page, []):
            if coords.iou(f.rect, g.rect) > opts.duplicate_iou:
                reason = f"duplicate_of:{g.id}"
                break
            if _overlap(f.rect, g.rect) > opts.overlap_ratio:
                reason = f"overlaps:{g.id}"
                break
        if reason:
            f.status, f.reason = "excluded", reason
        else:
            f.status, f.reason = "embedded", None
            accepted.setdefault(f.page, []).append(f)
    return result


def verify(input_path, tmp_path, embedded: list[Formula], opts: Options, input_sha: str) -> dict[str, Any]:
    """派生PDFを開き直して検査する。各項目の ok と詳細を返す。"""
    checks: dict[str, Any] = {}
    checks["input_unchanged"] = {"ok": safety.sha256_file(input_path) == input_sha}
    pa, pb = pdfcheck.page_attributes(input_path), pdfcheck.page_attributes(tmp_path)
    checks["page_attributes"] = {"ok": pa == pb}
    ia, ib = pdfcheck.structure_inventory(input_path), pdfcheck.structure_inventory(tmp_path)
    diff_keys = [k for k in ia if ia.get(k) != ib.get(k)]
    checks["structure"] = {"ok": not diff_keys, "differs": diff_keys}
    app = []
    for dpi in opts.dpis:
        r = pdfcheck.compare_appearance(input_path, tmp_path, dpi=dpi)
        app.append({"dpi": dpi, "identical": r.identical, "diffs": r.page_diffs})
    checks["appearance"] = {"ok": all(a["identical"] for a in app), "runs": app}

    da, db = pymupdf.open(input_path), pymupdf.open(tmp_path)
    try:
        rects: dict[int, list[pymupdf.Rect]] = {}
        for f in embedded:
            rects.setdefault(f.page, []).append(pymupdf.Rect(f.rect))
        missing, stray = [], []
        for pno in range(da.page_count):
            wa, wb = pdfcheck.words(da[pno]), pdfcheck.words(db[pno])
            missing += [(pno, w[4]) for w in wa if w not in wb]
            for w in wb:
                if w in wa:
                    continue
                c = pymupdf.Rect(w[:4])
                ctr = pymupdf.Point((c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2)
                if not any(ctr in r for r in rects.get(pno, [])):
                    stray.append((pno, w[4]))
        checks["body_text"] = {"ok": not missing and not stray, "missing": missing[:20], "stray": stray[:20]}
    finally:
        da.close()
        db.close()

    got = copycheck.mupdf_extract(tmp_path, embedded)
    bad = [f.id for f in embedded
           if [x.strip() for x in (got.get(f.id) or "").splitlines() if x.strip()] != embed.layer_lines(f.latex)]
    checks["mupdf_copy"] = {"ok": not bad, "failed": bad}

    if opts.pdfkit != "off":
        if copycheck.pdfkit_available():
            try:
                r = copycheck.pdfkit_select(tmp_path, embedded, page_text=sorted({f.page for f in embedded}))
                fails = []
                for f in embedded:
                    exp = " ".join(embed.layer_lines(f.latex))
                    s = r["selections"].get(f.id)
                    count = copycheck.normalize(r["page_text"].get(str(f.page)) or "").count(copycheck.normalize(exp))
                    if copycheck.normalize(s) != copycheck.normalize(exp) or count != 1:
                        fails.append({"id": f.id, "got": s, "page_count": count})
                checks["pdfkit_copy"] = {"ok": not fails, "failed": fails, "os": r.get("os")}
            except Exception as e:  # 選択範囲の作成失敗も検証不合格
                checks["pdfkit_copy"] = {"ok": False, "error": str(e)}
        elif opts.pdfkit == "require":
            checks["pdfkit_copy"] = {"ok": False, "error": "PDFKit（swift）が使えない"}
        else:
            checks["pdfkit_copy"] = {"ok": None, "skipped": "PDFKit なし"}
    return checks


def process_pdf(input_path, output_path, formulas: FormulaSet | str | Path, options: Options | None = None,
                should_stop: Callable[[], bool] = lambda: False) -> RunReport:
    opts = options or Options()
    rep = RunReport(input=str(input_path), output=str(output_path), mode=opts.mode,
                    options_digest=opts.digest(), started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    t0 = time.monotonic()
    tmp: Path | None = None
    lock: Path | None = None

    def stop_check() -> bool:
        if opts.timeout_s is not None and time.monotonic() - t0 > opts.timeout_s:
            return True
        return should_stop()

    try:
        try:
            inp, out = safety.check_paths(input_path, output_path)
        except safety.SafetyError as e:
            raise _Stop(ExitCode.SAFETY, str(e))
        lock = out.with_name(f".{out.name}.lock")
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
        except FileExistsError:
            lock = None
            raise _Stop(ExitCode.SAFETY, f"同じ出力先を別のジョブが処理中: {out}")
        rep.input_sha256 = safety.sha256_file(inp)

        try:
            fs = formulas if isinstance(formulas, FormulaSet) else FormulaSet.load(formulas)
        except (ValueError, KeyError, OSError, json.JSONDecodeError) as e:
            raise _Stop(ExitCode.USAGE, f"式 JSON を読めない: {e}")
        if fs.input_sha256 and fs.input_sha256 != rep.input_sha256:
            raise _Stop(ExitCode.USAGE, "式 JSON の input_sha256 が入力PDFと一致しない")
        if opts.mode not in ("invisible", "actualtext"):
            raise _Stop(ExitCode.USAGE, f"未知の埋め込み方式: {opts.mode}")

        pf = pdfcheck.preflight(inp)
        rep.checks["preflight"] = {"ok": pf.ok, "reasons": pf.reasons, "info": pf.info}
        if not pf.ok:
            raise _Stop(ExitCode.UNSUPPORTED, "未対応のPDF: " + "; ".join(pf.reasons))

        doc = pymupdf.open(inp)
        try:
            classified = classify(doc, fs.formulas, opts)
        finally:
            doc.close()
        rep.formulas = [f.to_dict() for f in classified]
        embedded = [f for f in classified if f.status == "embedded"]
        rep.counts = {
            "total": len(classified),
            "embedded": len(embedded),
            "excluded": sum(1 for f in classified if f.status == "excluded"),
        }
        if not embedded and not opts.allow_empty:
            raise _Stop(ExitCode.NOTHING, "埋め込める式がない")

        tmp = safety.temp_path_for(out)
        try:
            embed.embed_formulas(inp, tmp, embedded, mode=opts.mode, should_stop=stop_check)
        except InterruptedError as e:
            raise _Stop(ExitCode.INTERRUPTED, str(e))
        if stop_check():
            raise _Stop(ExitCode.INTERRUPTED, "検証前に中断")

        checks = verify(inp, tmp, embedded, opts, rep.input_sha256)
        rep.checks.update(checks)
        failed = [k for k, v in checks.items() if v.get("ok") is False]
        if failed:
            raise _Stop(ExitCode.VERIFY_FAILED, "検証不合格: " + ", ".join(failed))
        if stop_check():
            raise _Stop(ExitCode.INTERRUPTED, "公開前に中断")

        rep.output_sha256 = safety.sha256_file(tmp)
        try:
            safety.publish(tmp, out)
        except safety.SafetyError as e:
            raise _Stop(ExitCode.SAFETY, str(e))
        tmp = None
        rep.ok, rep.exit_code = True, ExitCode.OK
    except _Stop as s:
        rep.ok, rep.exit_code = False, s.code
        rep.errors.append(str(s))
    except Exception as e:  # 想定外でも公開しない
        rep.ok, rep.exit_code = False, ExitCode.INTERNAL
        rep.errors.append(f"{type(e).__name__}: {e}")
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()  # この実行が作った未公開の一時ファイルだけを消す
        if lock is not None:
            try:
                lock.unlink()
            except FileNotFoundError:
                pass
        rep.elapsed_s = round(time.monotonic() - t0, 3)
        if opts.report_path:
            Path(opts.report_path).write_text(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return rep
