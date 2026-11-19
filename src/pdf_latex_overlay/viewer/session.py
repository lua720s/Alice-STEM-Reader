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
from ..coords import iou
from ..recognize.postprocess import clean_lines
from ..recognize.region import recognize_png

_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
EDITABLE_STATUS = {"pending", "confirmed", "excluded"}
REPLACE_IOU = 0.6


class SessionError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


class ViewerSession:
    def __init__(self, input_path, workdir, formulas_path=None, backend="none", backend_obj=None, detector_obj=None, verifier_obj=None):
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
        self._detector = detector_obj  # Hub が持つ検出器を使い回す（PDF を切り替えても読み込み直さない）
        self._verifier = verifier_obj  # 2つ目のモデル。あれば、食い違った式に「要確認」の印を付ける
        self._stop = threading.Event()
        self._auto_thread: threading.Thread | None = None
        self.auto: dict = {"state": "idle"}
        self.text_state: dict = {"state": "idle"}  # 文章（日本語など）の認識
        self._page_boxes: dict[int, list] = {}  # ページごとの数式検出の結果（混在領域の認識で使い回す）
        self._text_backend_override = None  # 試験用
        self._text_thread: threading.Thread | None = None
        self._disk_mtime = 0.0
        self.version = 0  # 式の集合が変わるたびに増やす（画面の再読み込み判定）
        if self.formulas_path.exists():
            self.fs = FormulaSet.load(self.formulas_path)
        elif formulas_path:
            self.fs = FormulaSet.load(formulas_path)  # 指定ファイルは読むだけ。保存は作業領域へ
        else:
            self.fs = FormulaSet()
        if self.fs.input_sha256 and self.fs.input_sha256 != self.input_sha:
            raise SessionError("式 JSON の input_sha256 が入力PDFと一致しない", 409)
        if self.formulas_path.exists() and self.fs.input_sha256 == self.input_sha:
            self._disk_mtime = self.formulas_path.stat().st_mtime  # 既存の結果はそのまま使う（書き直さない）
        else:
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
        self._disk_mtime = self.formulas_path.stat().st_mtime

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
            "verify": self._verifier is not None,
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
        self._check_writable()
        with self.lock:
            rect = self._to_stored(page, view_rect)
            n = 1
            ids = {f.id for f in self.fs.formulas}
            while f"m{page}_{n}" in ids:
                n += 1
            f = Formula(id=f"m{page}_{n}", page=page, rect=rect, latex=latex, source="manual",
                        status="pending", edited=latex is not None)
            # ほぼ同じ位置に描き直した枠は、未確定・未修正の古い枠と置き換える（履歴には残る）
            replaced = [g for g in self.fs.formulas
                        if g.page == page and iou(g.rect, rect) > REPLACE_IOU
                        and g.status != "confirmed" and not g.edited]
            for g in replaced:
                self.fs.formulas.remove(g)
                self._log("replace", id=g.id, by=f.id, before=g.to_dict())
            self.fs.formulas.append(f)
            self._log("create", id=f.id, page=page, rect=list(rect), latex=latex,
                      replaced=[g.id for g in replaced])
            self._save()
            self.version += 1
        if recognize:
            return self.recognize(f.id)
        return self._view(f)

    def update(self, fid: str, latex=None, status=None, view_rect=None, reason=None) -> dict:
        self._check_writable()
        with self.lock:
            f = self._get(fid)
            before = f.to_dict()
            if latex is not None:
                if not isinstance(latex, str) or len(latex) > 20000:
                    raise SessionError("latex が不正")
                f.latex = latex
                f.edited = f.ocr_latex is None or latex not in (f.ocr_latex, clean_lines(f.ocr_latex))
            if status is not None:
                if status not in EDITABLE_STATUS:
                    raise SessionError(f"status は {sorted(EDITABLE_STATUS)} のいずれか")
                f.status = status
                f.reason = (reason or "user") if status == "excluded" else None
            if view_rect is not None:
                f.rect = self._to_stored(f.page, view_rect)
            if latex is not None or status in ("confirmed", "excluded"):
                f.review = None  # 人が確認・修正・除外したので、要確認の印は外す（もう一方の読みは残す）
            self._log("update", id=fid, before=before, after=f.to_dict())
            self._save()
            self.version += 1
            return self._view(f)

    # ---- メモ（PDFの外、ライブラリの `notes.json` に保存する。PDF 自体は変えない） ----
    NOTES_MAX = 100_000

    def notes_get(self) -> dict:
        p = self.workdir / "notes.json"
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        pages = d.get("pages") if isinstance(d.get("pages"), dict) else {}
        return {"doc": d.get("doc") if isinstance(d.get("doc"), str) else "",
                "pages": {k: v for k, v in pages.items() if isinstance(v, str)}, "updated": d.get("updated")}

    def notes_set(self, scope, text, page=None) -> dict:
        """scope: doc（PDF全体のメモ）/ page（そのページのメモ）。空の文字列で、そのメモを消す。"""
        if scope not in ("doc", "page"):
            raise SessionError("scope が不正（doc か page）")
        if not isinstance(text, str) or len(text) > self.NOTES_MAX:
            raise SessionError(f"メモが不正（文字列、{self.NOTES_MAX} 文字まで）")
        if scope == "page" and (not isinstance(page, int) or isinstance(page, bool) or not (0 <= page < self.doc.page_count)):
            raise SessionError("page が不正")
        with self.lock:
            d = self.notes_get()
            if scope == "doc":
                d["doc"] = text
            elif text.strip():
                d["pages"][str(page)] = text
            else:
                d["pages"].pop(str(page), None)
            d["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            p = self.workdir / "notes.json"
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(p)
            self._log("notes", scope=scope, page=page, chars=len(text))
            return d

    def delete(self, fid: str) -> dict:
        self._check_writable()
        with self.lock:
            f = self._get(fid)
            self.fs.formulas.remove(f)
            self._log("delete", id=fid, before=f.to_dict())
            self._save()
            self.version += 1
            return {"deleted": fid, "formula": f.to_dict()}  # 元に戻す（restore）ために、枠の中身も返す

    def delete_many(self, page: int, scope: str) -> dict:
        """そのページの枠をまとめて削除する。scope: text（文章の枠だけ）/ all（すべて）。履歴には残り、restore で戻せる。"""
        self._check_writable()
        if scope not in ("text", "all"):
            raise SessionError("scope が不正（text か all）")
        if not isinstance(page, int) or isinstance(page, bool) or not (0 <= page < self.doc.page_count):
            raise SessionError("page が不正")
        with self.lock:
            gone = [f for f in self.fs.formulas if f.page == page and (scope == "all" or f.kind == "text")]
            for f in gone:
                self.fs.formulas.remove(f)
            if gone:
                self._log("delete_many", page=page, scope=scope, ids=[f.id for f in gone], before=[f.to_dict() for f in gone])
                self._save()
                self.version += 1
            return {"deleted": [f.id for f in gone], "formulas": [f.to_dict() for f in gone]}

    def restore(self, formulas) -> dict:
        """削除した枠を戻す（元に戻す）。同じ ID の枠がすでにあるものは戻さない。"""
        self._check_writable()
        if not isinstance(formulas, list) or not formulas or len(formulas) > 5000:
            raise SessionError("formulas が不正")
        try:
            items = [Formula.from_dict(d) for d in formulas]
        except (ValueError, KeyError, TypeError) as e:
            raise SessionError(f"枠の形式が不正: {e}")
        with self.lock:
            have = {f.id for f in self.fs.formulas}
            added = []
            for f in items:
                if f.id in have or not (0 <= f.page < self.doc.page_count):
                    continue
                self.fs.formulas.append(f)
                have.add(f.id)
                added.append(f)
            if added:
                self._log("restore", ids=[f.id for f in added])
                self._save()
                self.version += 1
            return {"restored": [f.id for f in added]}

    def crop(self, fid: str, dpi: int = 200) -> bytes:
        with self.lock:
            f = self._get(fid)
            return crop_png(self._page(f.page), f.rect, dpi=dpi)

    def recognize(self, fid: str) -> dict:
        self._check_writable()
        with self.lock:  # 文書への描画だけを排他し、認識中も画面の操作を止めない
            f = self._get(fid)
            if f.kind == "text" and f.source != "mixed":
                raise SessionError("文章の枠は、数式として再認識しない（このページの文章を認識、で読み直す）", 409)
            png = crop_png(self._page(f.page), f.rect, dpi=200)
            page_no, rect = f.page, f.rect
        mixed = self._try_mixed(page_no, rect)  # 文章と数式が混ざった領域は、Mathpix Snip のように読む
        if mixed is not None:
            return self._store_mixed(fid, mixed)
        if self._get(fid).source == "mixed":  # 混在ではなくなった（数式だけ）。通常の数式として読み直す
            with self.lock:
                g = self._get(fid)
                g.kind, g.source = None, "manual"
        try:
            res = recognize_png(self.backend, png, dpi=200, verifier=self._verifier)
        except RecognizerUnavailable as e:
            raise SessionError(str(e), 503)
        with self.lock:
            f = self._get(fid)
            before = f.to_dict()
            f.alt_latex, f.review = getattr(res, "alt", None), getattr(res, "review", None)
            if res.latex is None:
                f.reason = f"recognition_failed: {res.error or 'no output'}"
            else:
                f.ocr_latex = getattr(res, "raw", None) or res.latex  # 認識器の原文（行ごと）
                f.latex = res.latex  # 表示用の記法を除いた版
                f.edited = False
                f.confidence = res.confidence
                f.source = getattr(self.backend, "name", "unknown") if f.source != "mfd" else "mfd+" + getattr(self.backend, "name", "?")
                f.reason = f"partial: {res.error}" if res.error else None
                if f.status == "excluded":
                    f.status = "pending"
            self._log("recognize", id=fid, backend=getattr(self.backend, "name", "?"),
                      version=getattr(self.backend, "version", "?"), lines=getattr(res, "lines", 1),
                      before=before, after=f.to_dict())
            self._save()
            self.version += 1
            return self._view(f)

    def _try_mixed(self, pno: int, rect):
        """領域に文章（日本語など）が含まれていれば、文章 + 数式（`\\( … \\)`）として読んだ結果。なければ None（数式として読む）。"""
        from ..detect.mfd import detect_page
        from ..recognize.mixed import recognize_mixed
        from ..recognize.textocr import shared_backend

        det = self._detector
        if det is None or self.backend_name in (None, "", "none") and self._backend is None:
            return None
        tb = self._text_backend_override or shared_backend()
        try:
            if hasattr(tb, "_check"):
                tb._check()
        except RecognizerUnavailable:
            return None  # 文字認識の環境がない: 従来どおり、数式として読む
        try:
            with self.lock:
                page = self._page(pno)
                view = tuple(pymupdf.Rect(rect) * page.rotation_matrix)
                boxes = self._page_boxes.get(pno)
                if boxes is None:
                    boxes = detect_page(det, page)  # ページ全体の数式検出（結果は使い回す）
                    self._page_boxes[pno] = boxes
            doc = pymupdf.open(self.input)  # この処理専用（PyMuPDF の文書はスレッド間で共有しない）
            try:
                return recognize_mixed(doc[pno], view, boxes, self.backend, tb, verifier=self._verifier)
            finally:
                doc.close()
        except RecognizerUnavailable:
            return None
        except Exception as e:  # 混在の認識に失敗しても、数式として読む道は残す
            self._log("mixed_failed", error=f"{type(e).__name__}: {e}")
            return None

    def _store_mixed(self, fid: str, mixed) -> dict:
        with self.lock:
            f = self._get(fid)
            before = f.to_dict()
            f.latex = mixed.latex
            f.ocr_latex = mixed.latex
            f.kind, f.source, f.edited, f.confidence, f.reason = "text", "mixed", False, None, None
            f.alt_latex = f.review = None
            if f.status == "excluded":
                f.status = "pending"
            self._log("recognize_mixed", id=fid, lines=len(mixed.items), dropped_boxes=mixed.dropped_boxes,
                      before=before, after=f.to_dict())
            self._save()
            self.version += 1
            return self._view(f)

    # ---- 自動認識（開いた時点で全ページを検出・認識する） ----
    def auto_status(self) -> dict:
        pid = self.preparing()
        if pid or self.auto.get("state") != "running":
            self.refresh_from_disk()
        with self.lock:
            st = dict(self.auto, version=self.version, text=dict(self.text_state))
        if pid:
            st.update(state="preparing", phase="prepare", pid=pid)
        return st

    def start_text(self, pages=None, force: bool = False, backend=None) -> dict:
        """文章（日本語など）を、PaddleOCR で行ごとに読む。pages は 0 始まり（省略で全ページ）。数式の枠は読まない。"""
        if pages is not None:
            if not isinstance(pages, list) or not pages or any(
                    not isinstance(p, int) or isinstance(p, bool) or not (0 <= p < self.doc.page_count) for p in pages):
                raise SessionError("pages が不正（ページ番号は 0 から始まる整数のリスト）")
        if self.preparing():
            raise SessionError("事前処理中のため、終わってから文章を認識する", 409)
        self._check_writable()
        with self.lock:
            if self.text_state.get("state") == "running":
                return dict(self.text_state)
            self.text_state = {"state": "running", "page": 0, "pages": len(pages) if pages else self.doc.page_count,
                               "lines": 0, "error": None}
        t = threading.Thread(target=self._text_run, args=(pages, force, backend), daemon=True)
        self._text_thread = t
        t.start()
        return dict(self.text_state)

    def _text_run(self, pages, force, backend):
        from ..prepare import process_text
        from ..recognize.textocr import shared_backend

        def save():
            self._save()
            self.version += 1

        def progress(**k):
            self.text_state.update({kk: v for kk, v in k.items() if kk != "phase"})

        try:
            r = process_text(self.input, self.fs, text_backend=backend or shared_backend(), lock=self.lock, save=save,
                             progress=progress, log=self._log, should_stop=self._stop.is_set, only_pages=pages, force=force, detector=self._detector)
            with self.lock:
                self.text_state.update(state="done" if r["state"] == "done" else "idle", lines=r["lines"])
        except Exception as e:  # 失敗は画面に出し、途中までの結果は残す
            with self.lock:
                self.text_state.update(state="error", error=f"{type(e).__name__}: {e}")

    def start_auto(self, detector=None, pages=None, force: bool = False) -> dict:
        """全ページ（既定）または pages（0 始まり）だけを、検出・認識する。force は処理済みのページも検出し直す。"""
        if pages is not None:
            if not isinstance(pages, list) or not pages or any(
                    not isinstance(p, int) or isinstance(p, bool) or not (0 <= p < self.doc.page_count) for p in pages):
                raise SessionError("pages が不正（ページ番号は 0 から始まる整数のリスト）")
        if self.preparing():
            return self.auto_status()  # 事前処理が進めているので、ビューアでは処理しない
        with self.lock:
            if self.auto.get("state") == "running":
                return dict(self.auto)
            if self.backend_name in (None, "", "none") and self._backend is None:
                raise SessionError("認識バックエンドがない（--backend unimernet で起動）", 409)
            self.auto = {"state": "running", "phase": "detect", "page": 0, "pages": self.doc.page_count,
                         "found": 0, "recognized": 0, "error": None}
        t = threading.Thread(target=self._auto_run, args=(detector, pages, force), daemon=True)
        self._auto_thread = t
        t.start()
        return dict(self.auto)

    def _auto_run(self, detector, pages=None, force=False):
        from ..detect.mfd import MFDDetector
        from ..prepare import process_document

        det = detector or self._detector
        own = det is None
        if own:
            det = MFDDetector()

        def save():
            self._save()
            self.version += 1

        def progress(**k):
            self.auto.update({kk: v for kk, v in k.items() if kk != "pages"})

        try:
            r = process_document(self.input, self.fs, detector=det, backend=self.backend, lock=self.lock,
                                 save=save, progress=progress, log=self._log, should_stop=self._stop.is_set,
                                 only_pages=pages, force=force, verifier=self._verifier)
            with self.lock:
                self.auto.update(state="done" if r["state"] == "done" else "idle", phase="done", found=r["found"])
            self._update_meta("done" if r["state"] == "done" and pages is None else "partial")
        except Exception as e:  # 失敗は画面に表示し、途中までの結果は残す
            with self.lock:
                self.auto.update(state="error", error=f"{type(e).__name__}: {e}")
        finally:
            if own:
                det.close()

    def _update_meta(self, state: str):
        """ライブラリの一覧（コレクションの状態表示）に出る状態と件数を更新する。"""
        from ..prepare import read_meta, write_meta

        try:
            with self.lock:
                counts = {"formulas": len(self.fs.formulas), "with_latex": sum(1 for f in self.fs.formulas if f.latex),
                          "failed": sum(1 for f in self.fs.formulas if f.latex is None and f.status != "excluded"),
                          "review": sum(1 for f in self.fs.formulas if f.review and f.status != "excluded")}
            m = read_meta(self.workdir)
            if m.get("state") == "running":  # 事前処理が進めている間は、その状態を壊さない
                return
            m.update(state=state, counts=counts, pages=self.doc.page_count)
            write_meta(self.workdir, m)
        except (OSError, ValueError):
            pass

    # ---- 事前処理（prepare）との排他 ----
    def preparing(self) -> int | None:
        from ..prepare import lock_holder

        return lock_holder(self.workdir / ".prepare.lock")

    def refresh_from_disk(self) -> bool:
        """事前処理が書いた結果を読み直す（事前処理の実行中・直後）。"""
        with self.lock:
            try:
                m = self.formulas_path.stat().st_mtime
            except FileNotFoundError:
                return False
            if m <= self._disk_mtime:
                return False
            self.fs = FormulaSet.load(self.formulas_path)
            self._disk_mtime = m
            self.version += 1
            return True

    def _check_writable(self):
        pid = self.preparing()
        if pid:
            raise SessionError(f"事前処理（pid {pid}）がこの PDF を処理中のため、終わるまで編集できない", 409)

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
        self._stop.set()  # 裏の自動認識を、区切りのよいところで止める
        for t in (self._auto_thread, self._text_thread):
            if t is not None and t.is_alive() and t is not threading.current_thread():
                t.join(timeout=20)
        self.doc.close()


def _contained(inner, outer) -> float:
    ix = max(0.0, min(inner[2], outer[2]) - max(inner[0], outer[0]))
    iy = max(0.0, min(inner[3], outer[3]) - max(inner[1], outer[1]))
    a = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return ix * iy / a if a > 0 else 0.0


def valid_id(s: str) -> bool:
    return bool(_ID_RE.match(s))
