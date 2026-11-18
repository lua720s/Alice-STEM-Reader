"""ビューアのセッション（HTTP から独立させ、試験できるようにする）。

- 元PDFは読み取り専用。式 JSON と派生PDFは作業領域（workdir）に保存する。
- 修正しても OCR 原文（ocr_latex）は保持し、変更履歴を history.jsonl に追記する。
- 画面には回転後の見た目座標（view_rect）で渡し、保存は回転前の保存座標で行う。
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import pymupdf

from .. import safety
from ..models import Formula, FormulaSet
from ..pipeline import Options, process_pdf
from ..recognize import RecognizerUnavailable, crop_png, get_backend
from ..recognize.unimernet import tidy_latex

_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
EDITABLE_STATUS = {"pending", "confirmed", "excluded"}


class SessionError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


class ViewerSession:
    def __init__(self, input_path, workdir, formulas_path=None, backend="none", backend_obj=None):
        self.input = Path(input_path).resolve()
        if not self.input.is_file():
            raise SessionError(f"入力PDFがない: {self.input}", 404)
        self.input_sha = safety.sha256_file(self.input)
        self.workdir = Path(workdir).resolve()
        (self.workdir / "exports").mkdir(parents=True, exist_ok=True)
        self.formulas_path = self.workdir / "formulas.json"
        self.history_path = self.workdir / "history.jsonl"
        self.lock = threading.RLock()
        self.doc = pymupdf.open(self.input)  # 読み取りのみ
        self.backend_name = backend
        self._backend = backend_obj
        if self.formulas_path.exists():
            self.fs = FormulaSet.load(self.formulas_path)
        elif formulas_path:
            self.fs = FormulaSet.load(formulas_path)  # 指定ファイルは読むだけ。保存は作業領域へ
        else:
            self.fs = FormulaSet()
        if self.fs.input_sha256 and self.fs.input_sha256 != self.input_sha:
            raise SessionError("式 JSON の input_sha256 が入力PDFと一致しない", 409)
        self.fs.input_sha256 = self.input_sha
        self._save()

    # ---- 内部 ----
    @property
    def backend(self):
        if self._backend is None:
            self._backend = get_backend(self.backend_name)
        return self._backend

    def _save(self):
        self.fs.save(self.formulas_path)

    def _log(self, action: str, **kw):
        rec = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "action": action, **kw}
        with open(self.history_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _get(self, fid: str) -> Formula:
        for f in self.fs.formulas:
            if f.id == fid:
                return f
        raise SessionError(f"式がない: {fid}", 404)

    def _page(self, pno: int) -> pymupdf.Page:
        if not isinstance(pno, int) or not (0 <= pno < self.doc.page_count):
            raise SessionError(f"ページ番号が範囲外: {pno}")
        return self.doc[pno]

    def _to_stored(self, pno: int, view_rect) -> tuple:
        page = self._page(pno)
        try:
            r = pymupdf.Rect([float(v) for v in view_rect])
        except (TypeError, ValueError):
            raise SessionError("view_rect が不正")
        r.normalize()
        r = r & page.rect
        if r.is_empty or r.width < 2 or r.height < 2:
            raise SessionError("矩形が小さすぎるかページ外")
        s = r * page.derotation_matrix
        return (s.x0, s.y0, s.x1, s.y1)

    def _view(self, f: Formula) -> dict:
        d = f.to_dict()
        if 0 <= f.page < self.doc.page_count:
            v = pymupdf.Rect(f.rect) * self.doc[f.page].rotation_matrix
            d["view_rect"] = [round(x, 3) for x in v]
        return d

    # ---- API ----
    def doc_info(self) -> dict:
        return {
            "name": self.input.name,
            "sha256": self.input_sha,
            "pages": [{"width": p.rect.width, "height": p.rect.height, "rotation": p.rotation} for p in self.doc],
            "backend": self.backend_name,
            "workdir": str(self.workdir),
        }

    def page_png(self, pno: int, dpi: int = 110) -> bytes:
        dpi = max(36, min(int(dpi), 300))
        with self.lock:
            return self._page(pno).get_pixmap(dpi=dpi, alpha=False).tobytes("png")

    def list(self) -> dict:
        with self.lock:
            return {"input_sha256": self.input_sha, "formulas": [self._view(f) for f in self.fs.formulas]}

    def create(self, page: int, view_rect, latex: str | None = None, recognize: bool = False) -> dict:
        with self.lock:
            rect = self._to_stored(page, view_rect)
            n = 1
            ids = {f.id for f in self.fs.formulas}
            while f"m{page}_{n}" in ids:
                n += 1
            f = Formula(id=f"m{page}_{n}", page=page, rect=rect, latex=latex, source="manual",
                        status="pending", edited=latex is not None)
            self.fs.formulas.append(f)
            self._log("create", id=f.id, page=page, rect=list(rect), latex=latex)
            self._save()
        if recognize:
            return self.recognize(f.id)
        return self._view(f)

    def update(self, fid: str, latex=None, status=None, view_rect=None, reason=None) -> dict:
        with self.lock:
            f = self._get(fid)
            before = f.to_dict()
            if latex is not None:
                if not isinstance(latex, str) or len(latex) > 20000:
                    raise SessionError("latex が不正")
                f.latex = latex
                f.edited = f.ocr_latex is None or latex not in (f.ocr_latex, tidy_latex(f.ocr_latex))
            if status is not None:
                if status not in EDITABLE_STATUS:
                    raise SessionError(f"status は {sorted(EDITABLE_STATUS)} のいずれか")
                f.status = status
                f.reason = (reason or "user") if status == "excluded" else None
            if view_rect is not None:
                f.rect = self._to_stored(f.page, view_rect)
            self._log("update", id=fid, before=before, after=f.to_dict())
            self._save()
            return self._view(f)

    def delete(self, fid: str) -> dict:
        with self.lock:
            f = self._get(fid)
            self.fs.formulas.remove(f)
            self._log("delete", id=fid, before=f.to_dict())
            self._save()
            return {"deleted": fid}

    def crop(self, fid: str, dpi: int = 200) -> bytes:
        with self.lock:
            f = self._get(fid)
            return crop_png(self._page(f.page), f.rect, dpi=dpi)

    def recognize(self, fid: str) -> dict:
        png = self.crop(fid)
        try:
            res = self.backend.recognize([png])[0]
        except RecognizerUnavailable as e:
            raise SessionError(str(e), 503)
        with self.lock:
            f = self._get(fid)
            before = f.to_dict()
            if res.latex is None:
                f.reason = f"recognition_failed: {res.error or 'no output'}"
            else:
                f.ocr_latex = getattr(res, "raw", None) or res.latex  # 認識器の原文
                f.latex = res.latex  # 空白を詰めた版（意味は変えない）
                f.edited = False
                f.confidence = res.confidence
                f.source = getattr(self.backend, "name", "unknown")
                if f.status == "excluded":
                    f.status, f.reason = "pending", None
            self._log("recognize", id=fid, backend=getattr(self.backend, "name", "?"),
                      version=getattr(self.backend, "version", "?"), before=before, after=f.to_dict())
            self._save()
            return self._view(f)

    def export(self, mode: str = "invisible", only_confirmed: bool = False) -> dict:
        with self.lock:
            snapshot = FormulaSet.from_dict(self.fs.to_dict())
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = self.workdir / "exports" / f"{self.input.stem}.latex-{stamp}.pdf"
        n = 1
        while out.exists():
            out = self.workdir / "exports" / f"{self.input.stem}.latex-{stamp}-{n}.pdf"
            n += 1
        rep = process_pdf(self.input, out, snapshot,
                          Options(mode=mode, require_confirmed=only_confirmed,
                                  report_path=str(out.with_suffix(".report.json"))))
        self._log("export", ok=rep.ok, exit_code=rep.exit_code, output=str(out) if rep.ok else None,
                  counts=rep.counts, errors=rep.errors)
        return {
            "ok": rep.ok, "exit_code": rep.exit_code, "output": str(out) if rep.ok else None,
            "report": str(out.with_suffix(".report.json")), "counts": rep.counts, "errors": rep.errors,
            "excluded": [{"id": f["id"], "reason": f["reason"]} for f in rep.formulas if f["status"] == "excluded"],
            "failed_checks": [k for k, v in rep.checks.items() if isinstance(v, dict) and v.get("ok") is False],
        }

    def close(self):
        self.doc.close()


def valid_id(s: str) -> bool:
    return bool(_ID_RE.match(s))
