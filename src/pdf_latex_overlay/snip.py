"""Snip: 画面・画像・資料の一部を切り取って、その部分だけを LaTeX / 文章にする（Mathpix Snip に相当）。

入力
- 画面の一部（macOS の `screencapture -i`、Windows の切り取り領域、Linux の grim+slurp など。範囲をドラッグで選ぶ）
- 画像（ファイル・貼り付け・ドロップ）。手書きの式やノートの写真も可（EXIF の向きを補正する）
- 開いている資料（PDF・EPUB・DjVu・Markdown・LaTeX など）の範囲。文字層がある資料では、
  OCR の結果に加えて、文字層の文字（Markdown・LaTeX の原稿なら原文そのもの）も返す

読み方（mode）
- auto: 文章が含まれていれば「文章 + 行内数式 `\\( … \\)`」、数式だけなら LaTeX（Mathpix Snip と同じ）
- math: 数式として読む（複数行は行ごと）
- text: 文章として読む（PaddleOCR）

結果は履歴（ライブラリの `_snips/`）に、画像・読み取り・出所（どの資料の何ページのどこか、いつ、どのエンジンか）とともに残す。
Markdown・LaTeX・TSV（フラッシュカードのアプリに読み込める）・JSON で書き出せる。
"""

from __future__ import annotations

import base64
import binascii
import csv
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pymupdf

from .delims import STYLES, convert_delims, wrap_single
from .recognize.base import Recognition, RecognizerUnavailable
from .recognize.region import recognize_png

SNIP_DPI = 200  # 画像の 1 画素を 1/200 インチとみなす（ビューアの切り出しと同じ）
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_PIXELS = 40_000_000
MODES = ("auto", "math", "text")
HISTORY_MAX = 5000
_ID_RE = re.compile(r"^s\d{8}-\d{6}-[0-9a-f]{6}$")


class SnipError(RuntimeError):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


# ---------------------------------------------------------------- 画像の受け取り
def decode_image(data) -> bytes:
    """data URL・base64 の文字列、またはバイト列から、正規化した PNG を作る（向きを補正し、RGB にする）。"""
    from PIL import Image, ImageOps

    if isinstance(data, str):
        s = data.split(",", 1)[1] if data.startswith("data:") else data
        try:
            raw = base64.b64decode(s, validate=False)
        except (binascii.Error, ValueError):
            raise SnipError("画像のデータが不正（base64）")
    elif isinstance(data, (bytes, bytearray)):
        raw = bytes(data)
    else:
        raise SnipError("画像がない")
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise SnipError(f"画像が空か、大きすぎる（{MAX_IMAGE_BYTES // (1024 * 1024)}MB まで）", 413)
    try:
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        im = _open_image(raw)
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):  # 透明な部分は白にする（画面の切り取りなど）
            rgba = im.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
    except SnipError:
        raise
    except Exception as e:
        raise SnipError(f"画像として読めなかった（{type(e).__name__}）")
    if im.width < 4 or im.height < 4:
        raise SnipError("画像が小さすぎる")
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def _open_image(raw: bytes):
    """画像を開く。HEIC（iPhone の写真）は pillow-heif、なければ macOS の sips で読む。"""
    from PIL import Image, UnidentifiedImageError

    try:
        return Image.open(io.BytesIO(raw))
    except UnidentifiedImageError:
        if raw[4:12] not in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftypmsf1", b"ftyphevc"):
            raise
    try:
        import pillow_heif  # type: ignore

        pillow_heif.register_heif_opener()
        return Image.open(io.BytesIO(raw))
    except ImportError:
        pass
    if sys.platform != "darwin" or not shutil.which("sips"):
        raise SnipError("HEIC を読むには pillow-heif が必要（pip install pillow-heif）", 415)
    with tempfile.TemporaryDirectory() as td:
        src, out = Path(td) / "in.heic", Path(td) / "out.png"
        src.write_bytes(raw)
        subprocess.run(["sips", "-s", "format", "png", str(src), "--out", str(out)], capture_output=True, timeout=120)
        if not out.is_file():
            raise SnipError("HEIC を変換できなかった", 415)
        im = Image.open(out)
        im.load()
        return im


def image_page(png: bytes) -> tuple[pymupdf.Document, pymupdf.Page]:
    """画像を 1 ページの PDF にする（1 画素 = 1/200 インチ。200dpi で描くと元の画素どおり）。"""
    pix = pymupdf.Pixmap(png)
    w, h = pix.width * 72.0 / SNIP_DPI, pix.height * 72.0 / SNIP_DPI
    doc = pymupdf.open()
    page = doc.new_page(width=w, height=h)
    page.insert_image(page.rect, stream=png)
    return doc, page


# ---------------------------------------------------------------- 読み取り
def _text_backend(text_backend):
    if text_backend is None:
        from .recognize.textocr import shared_backend

        text_backend = shared_backend()
    try:
        if hasattr(text_backend, "_check"):
            text_backend._check()
        return text_backend
    except RecognizerUnavailable:
        return None


def recognize_snip(png: bytes, *, backend, detector=None, text_backend=None, verifier=None, mode: str = "auto",
                   page=None, view_rect=None, boxes=None) -> dict:
    """切り取った画像を読む。page と view_rect（見た目の座標 pt）を渡すと、資料のページの上で読む（数式検出を使い回せる）。

    戻り値: {"kind": math|mixed|text, "latex": 結果, "raw": 認識器の原文, "review", "alt", "error"}
    """
    if mode not in MODES:
        raise SnipError(f"mode は {MODES} のいずれか")
    own = None
    if page is None:
        own, page = image_page(png)
        view_rect = tuple(page.rect)
    try:
        tb = _text_backend(text_backend) if mode in ("auto", "text") else None
        if mode == "text":
            if tb is None:
                raise SnipError("文章の認識（PaddleOCR）が使えない（docs/MODELS.md の手順で導入）", 503)
            return _read_text(tb, page, view_rect)
        if mode == "auto" and tb is not None and detector is not None:
            from .detect.mfd import detect_page
            from .recognize.mixed import recognize_mixed

            try:
                if boxes is None:
                    boxes = detect_page(detector, page)
                mixed = recognize_mixed(page, view_rect, boxes, backend, tb, verifier=verifier)
            except RecognizerUnavailable:
                mixed = None
            if mixed is not None:
                return {"kind": "mixed", "latex": mixed.latex, "raw": mixed.latex, "review": None, "alt": None, "error": None}
        res: Recognition = recognize_png(backend, png, dpi=SNIP_DPI, verifier=verifier)
        if res.latex is None and mode == "auto" and tb is not None:  # 数式として読めなければ、文章として読む
            t = _read_text(tb, page, view_rect)
            if t["latex"]:
                return t
        return {"kind": "math", "latex": res.latex, "raw": getattr(res, "raw", None) or res.latex,
                "review": getattr(res, "review", None), "alt": getattr(res, "alt", None), "error": res.error}
    finally:
        if own is not None:
            own.close()


def _read_text(tb, page, view_rect) -> dict:
    from .recognize.textocr import ocr_page_text

    clip = pymupdf.Rect(view_rect)
    lines = [d for d in ocr_page_text(tb, page, text_only=False) if pymupdf.Rect(d["view"]).intersects(clip)]
    text = "\n".join(d["text"] for d in lines)
    return {"kind": "text", "latex": text or None, "raw": text or None, "review": None, "alt": None,
            "error": None if text else "文字が見つからなかった"}


def text_layer(page: pymupdf.Page, view_rect) -> str:
    """ページの文字層のうち、範囲に入る文字（見た目の座標）。スキャン・画像なら空。"""
    clip = pymupdf.Rect(view_rect) * page.derotation_matrix
    try:
        return page.get_text("text", clip=clip, sort=True).strip()
    except Exception:
        return ""


# ---------------------------------------------------------------- 画面の切り取り（macOS）
def capture_screen(timeout_s: float = 300.0) -> bytes | None:
    """画面の範囲をドラッグで選んで切り取る（macOS: screencapture、Windows: 切り取り領域、Linux: grim+slurp など）。Esc で取り消すと None。"""
    from .osutil import OSFeatureError, capture_screen as _cap

    try:
        return _cap(timeout_s)
    except OSFeatureError as e:
        raise SnipError(str(e), 501) from e


def copy_to_clipboard(text: str) -> bool:
    """結果をクリップボードに入れる（macOS・Windows・Linux）。できなければ False。"""
    from .osutil import copy_to_clipboard as _copy

    return _copy(text)


def notify(title: str, message: str):
    """OS の通知（失敗しても無視）。"""
    from .osutil import notify as _notify

    _notify(title, message)


def format_for_copy(rec: dict, fmt="paren", single: bool = False) -> str:
    """コピーする文字列。fmt は出力の形式の設定（dict）。以前の形（"paren" などの文字列と single）も受け付ける。"""
    from .outfmt import copy_text, from_legacy

    if not isinstance(fmt, dict):
        fmt = from_legacy(fmt if fmt in STYLES else "paren", single)
    return copy_text(rec.get("kind"), rec.get("latex") or "", fmt)


# ---------------------------------------------------------------- 履歴
class SnipStore:
    """Snip の履歴。`<library>/_snips/` に、画像（<id>.png）と記録（snips.json）を置く。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / "snips.json"
        self.lock = threading.RLock()

    def _load(self) -> list[dict]:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return d if isinstance(d, list) else []
        except (OSError, ValueError):
            return []

    def _save(self, items: list[dict]):
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def new_entry(self, fmt: str = "latex") -> dict:
        """画像のない、書くための記録（Mathpix の Snip エディタに相当）。fmt: latex（数式）/ markdown（文章＋数式）。"""
        if fmt not in ("latex", "markdown"):
            raise SnipError("fmt は latex か markdown")
        kind = "math" if fmt == "latex" else "mixed"
        return self.add(None, {"kind": kind, "latex": ""}, {"type": "editor", "format": fmt}, {"name": "editor", "version": "-"})

    def add(self, png: bytes | None, result: dict, source: dict, engine: dict, extra: dict | None = None) -> dict:
        sid = time.strftime("s%Y%m%d-%H%M%S-") + secrets.token_hex(3)
        rec = {"id": sid, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "source": source, "engine": engine,
               "kind": result.get("kind"), "latex": result.get("latex"), "ocr_raw": result.get("raw"),
               "review": result.get("review"), "alt_latex": result.get("alt"), "error": result.get("error"),
               "edited": False, **(extra or {})}
        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)
            if png is not None:
                (self.root / f"{sid}.png").write_bytes(png)
            rec["has_image"] = png is not None
            items = self._load()
            items.append(rec)
            drop = items[:-HISTORY_MAX] if len(items) > HISTORY_MAX else []
            items = items[-HISTORY_MAX:]
            self._save(items)
            for old in drop:
                try:
                    (self.root / f"{old['id']}.png").unlink()
                except OSError:
                    pass
        return rec

    def list(self, limit: int = 200) -> list[dict]:
        with self.lock:
            return list(reversed(self._load()))[: max(1, min(int(limit), HISTORY_MAX))]

    def get(self, sid: str) -> dict:
        if not isinstance(sid, str) or not _ID_RE.match(sid):
            raise SnipError("ID が不正")
        with self.lock:
            for r in self._load():
                if r.get("id") == sid:
                    return r
        raise SnipError("その Snip はない", 404)

    def image(self, sid: str) -> bytes:
        self.get(sid)
        try:
            return (self.root / f"{sid}.png").read_bytes()
        except OSError:
            raise SnipError("画像がない", 404)

    def update(self, sid: str, latex=None, note=None) -> dict:
        self.get(sid)
        with self.lock:
            items = self._load()
            for r in items:
                if r.get("id") == sid:
                    if latex is not None:
                        if not isinstance(latex, str) or len(latex) > 50000:
                            raise SnipError("latex が不正")
                        r["latex"], r["edited"] = latex, latex != r.get("ocr_raw")
                    if note is not None:
                        if not isinstance(note, str) or len(note) > 20000:
                            raise SnipError("note が不正")
                        r["note"] = note
                    r["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                    self._save(items)
                    return r
        raise SnipError("その Snip はない", 404)

    def delete(self, sid: str) -> dict:
        rec = self.get(sid)
        with self.lock:
            self._save([r for r in self._load() if r.get("id") != sid])
            try:
                (self.root / f"{sid}.png").unlink()
            except OSError:
                pass
        return {"deleted": sid, "snip": rec}

    def export(self, kind: str, ids=None, fmt: dict | None = None, exp: dict | None = None, delim: str | None = None,
               **legacy) -> tuple[bytes, str, str]:
        """書き出す。(中身, MIME, ファイル名)。kind: md / tex / tsv / csv / json。ids を省くと全部（古い順）。

        fmt: 出力の形式の設定、exp: 書き出しの設定（outfmt.FORMAT_DEFAULTS・EXPORT_DEFAULTS）。
        exp["include_images"] が真なら、Markdown・LaTeX は画像と一緒に ZIP にする。
        """
        from . import outfmt

        fmt = outfmt.clean("format", fmt) if fmt is not None else outfmt.from_legacy(delim or "paren", False)
        exp = outfmt.clean("export", exp)
        if kind not in ("md", "tex", "tsv", "csv", "json"):
            raise SnipError("fmt は md / tex / tsv / csv / json のいずれか")
        with self.lock:
            items = self._load()
        if ids is not None:
            if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                raise SnipError("ids は文字列のリスト")
            want = set(ids)
            items = [r for r in items if r.get("id") in want]
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if kind == "json":
            data = items if exp["include_source"] else [{k: v for k, v in r.items() if k not in ("source", "engine")} for r in items]
            return json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"), "application/json", f"snips-{stamp}.json"
        if kind in ("tsv", "csv"):
            buf = io.StringIO()
            w = csv.writer(buf, delimiter="\t" if kind == "tsv" else ",", lineterminator="\n")
            for r in items:  # 1 列目: 表（メモか出所）、2 列目: 裏（読み取った内容）、3 列目: 出所
                src = source_label(r.get("source") or {})
                front = (r.get("note") if exp["card_front"] == "note" and exp["include_notes"] else None) or src
                back = outfmt.copy_text(r.get("kind"), r.get("latex") or "", {**fmt, "math_copy": fmt["math_copy"] if fmt["math_copy"] != "raw" else "auto"})
                row = [outfmt.card_cell(front, exp), outfmt.card_cell(back, exp)]
                if exp["include_source"]:
                    row.append(outfmt.card_cell(f"{src} / {r.get('created', '')}", exp))
                w.writerow(row)
            mime = "text/tab-separated-values" if kind == "tsv" else "text/csv"
            return buf.getvalue().encode("utf-8"), mime, f"snips-{stamp}.{kind}"
        md = kind == "md"
        f = outfmt.md_format(fmt, exp) if md else outfmt.tex_format(fmt)
        images = exp["include_images"]
        parts, pics = [], []
        for r in items:
            body = r.get("latex") or ""
            src = source_label(r.get("source") or {})
            block = []
            if exp["include_source"]:
                block.append(f"<!-- {src} / {r.get('created', '')} -->" if md else f"% {src} / {r.get('created', '')}")
            if exp["include_notes"] and r.get("note"):
                block.append(r["note"] if md else outfmt.escape_tex_text(r["note"]))
            if images and (self.root / f"{r['id']}.png").is_file():
                pics.append(r["id"])
                block.append(f"![{src}](images/{r['id']}.png)" if md
                             else f"\\includegraphics[width=0.8\\linewidth]{{images/{r['id']}.png}}")
            if body:
                if r.get("kind") in ("mixed", "text"):
                    block.append(outfmt.convert_text(body, f) if md else outfmt.tex_text(body, fmt))
                else:
                    block.append(outfmt.format_math(body, f, "auto" if md else "display"))
            parts.append("\n\n".join(block))
        text = "\n\n".join(parts) + "\n"
        if not md and exp["tex_document"]:
            text = outfmt.tex_document(text, "Snips", images=bool(pics))
        name = f"snips-{stamp}.{kind}"
        if not pics:
            return text.encode("utf-8"), ("text/markdown" if md else "application/x-tex"), name
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(name, text)
            for sid in pics:
                z.write(self.root / f"{sid}.png", f"images/{sid}.png")
        return buf.getvalue(), "application/zip", f"snips-{stamp}.zip"


def source_label(src: dict) -> str:
    t = src.get("type")
    if t == "document":
        return f"{src.get('name', '?')} p.{(src.get('page') or 0) + 1}"
    if t == "screen":
        return "画面"
    if t == "editor":
        return "エディタ"
    return src.get("name") or {"paste": "貼り付けた画像", "image": "画像"}.get(t, "画像")


__all__ = ["SnipError", "SnipStore", "capture_screen", "copy_to_clipboard", "decode_image", "format_for_copy",
           "image_page", "notify", "recognize_snip", "text_layer", "source_label", "MODES"]
