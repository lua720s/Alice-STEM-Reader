"""PDF の検査: 処理可否、ページ属性、構造、外観（画素差）、本文テキスト。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pikepdf
import pymupdf


@dataclass
class Preflight:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    info: dict[str, Any] = field(default_factory=dict)


def preflight(path) -> Preflight:
    """初期版で扱えない PDF を理由付きで止める。"""
    reasons: list[str] = []
    info: dict[str, Any] = {}
    try:
        with pikepdf.open(path) as pdf:
            if pdf.is_encrypted:
                reasons.append("encrypted: 暗号化PDFは未対応")
            root = pdf.Root
            acro = root.get("/AcroForm")
            if acro is not None:
                info["has_acroform"] = True
                for f in acro.get("/Fields", []):
                    if f.get("/FT") == "/Sig" and f.get("/V") is not None:
                        reasons.append("signed: 署名済みPDFは未対応（追記で署名が無効になる）")
                        break
            if root.get("/Perms") is not None:
                reasons.append("perms: 文書の権限辞書（DocMDP 等）があるため未対応")
            info["tagged"] = root.get("/StructTreeRoot") is not None
            for i, page in enumerate(pdf.pages):
                if page.obj.get("/UserUnit") is not None:
                    reasons.append(f"userunit: ページ{i}に /UserUnit がある")
    except pikepdf.PasswordError:
        reasons.append("encrypted: パスワード付きPDFは未対応")
    except pikepdf.PdfError as e:
        reasons.append(f"broken: PDFを開けない ({e})")
    return Preflight(ok=not reasons, reasons=reasons, info=info)


def page_attributes(path) -> list[dict[str, Any]]:
    out = []
    doc = pymupdf.open(path)
    rots = [pg.rotation for pg in doc]  # /Rotate は継承されうるので MuPDF の解決結果を使う
    doc.close()
    with pikepdf.open(path) as pdf:
        for i, page in enumerate(pdf.pages):
            # MediaBox/CropBox は継承されうるので pikepdf の解決結果を使う
            mb = [float(v) for v in page.mediabox]
            cb = [float(v) for v in page.cropbox]
            out.append({"mediabox": mb, "cropbox": cb, "rotate": rots[i]})
    return out


def structure_inventory(path) -> dict[str, Any]:
    """維持を確認する構造の一覧（件数と主要値）。"""
    doc = pymupdf.open(path)
    try:
        links = []
        annots = []
        for pno, page in enumerate(doc):
            for ln in page.get_links():
                links.append((pno, ln.get("kind"), ln.get("uri"), ln.get("page"), tuple(round(v, 2) for v in ln["from"])))
            for a in page.annots() or []:
                annots.append((pno, a.type[1], tuple(round(v, 2) for v in a.rect)))
        widgets = []
        for pno, page in enumerate(doc):
            for w in page.widgets() or []:
                widgets.append((pno, w.field_name, w.field_type))
        inv = {
            "page_count": doc.page_count,
            "toc": doc.get_toc(simple=True),
            "links": links,
            "annots": annots,
            "widgets": widgets,
            "metadata": {k: v for k, v in (doc.metadata or {}).items() if k not in ("modDate", "producer")},
        }
    finally:
        doc.close()
    with pikepdf.open(path) as pdf:
        st = pdf.Root.get("/StructTreeRoot")
        inv["struct_tree"] = None if st is None else _struct_signature(st)
    return inv


def _struct_signature(st) -> dict[str, Any]:
    """構造ツリーの粗い署名: 要素型ごとの件数。"""
    counts: dict[str, int] = {}
    stack = [st]
    seen = set()
    while stack:
        node = stack.pop()
        if isinstance(node, pikepdf.Array):
            stack.extend(list(node))
            continue
        if not isinstance(node, pikepdf.Dictionary):
            continue
        og = node.objgen
        if og != (0, 0):
            if og in seen:
                continue
            seen.add(og)
        s = node.get("/S")
        if s is not None:
            counts[str(s)] = counts.get(str(s), 0) + 1
        k = node.get("/K")
        if k is not None:
            stack.append(k)
    return {"element_counts": dict(sorted(counts.items()))}


@dataclass
class AppearanceResult:
    identical: bool
    dpi: int
    page_diffs: list[dict[str, Any]]


def compare_appearance(path_a, path_b, dpi: int = 72, pages: list[int] | None = None) -> AppearanceResult:
    """同じレンダラ・DPI・色空間で全ページを描画し、画素差を数える。"""
    da, db = pymupdf.open(path_a), pymupdf.open(path_b)
    diffs = []
    try:
        if da.page_count != db.page_count:
            return AppearanceResult(False, dpi, [{"page": None, "reason": "page_count_mismatch"}])
        for pno in pages if pages is not None else range(da.page_count):
            pa = da[pno].get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False, annots=True)
            pb = db[pno].get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False, annots=True)
            if (pa.width, pa.height) != (pb.width, pb.height):
                diffs.append({"page": pno, "reason": "size_mismatch", "a": [pa.width, pa.height], "b": [pb.width, pb.height]})
                continue
            if pa.samples == pb.samples:
                continue
            a = np.frombuffer(pa.samples, dtype=np.uint8).reshape(pa.height, pa.width, 3)
            b = np.frombuffer(pb.samples, dtype=np.uint8).reshape(pb.height, pb.width, 3)
            mask = np.any(a != b, axis=2)
            ys, xs = np.nonzero(mask)
            diffs.append({
                "page": pno,
                "reason": "pixel_diff",
                "count": int(mask.sum()),
                "bbox_px": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
            })
    finally:
        da.close()
        db.close()
    return AppearanceResult(not diffs, dpi, diffs)


def words(page: pymupdf.Page) -> list[tuple]:
    return [tuple(w[:5]) for w in page.get_text("words")]


def chars_in_rect(page: pymupdf.Page, rect, min_overlap: float = 0.5) -> list[str]:
    """既存文字層のうち、矩形に中心が入る文字を返す（既存文字層との競合判定）。"""
    r = pymupdf.Rect(rect)
    found = []
    raw = page.get_text("rawdict", flags=pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES)
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    if ch["c"].isspace():
                        continue
                    b = pymupdf.Rect(ch["bbox"])
                    if b.is_empty:
                        continue
                    inter = b & r
                    if not inter.is_empty and inter.get_area() >= min_overlap * b.get_area():
                        found.append(ch["c"])
    return found
