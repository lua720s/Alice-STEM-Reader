"""コピー結果の測定: MuPDF の領域抽出と、macOS PDFKit の矩形選択。

LaTeX の比較は生文字列と正規化後（前後空白・改行・外側の $ のみ除去）の両方で行う。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pymupdf

from . import coords
from .models import Formula

SWIFT_SRC = Path(__file__).resolve().parents[2] / "tools" / "pdfkit_copy.swift"


def normalize(s: str | None) -> str | None:
    if s is None:
        return None
    t = " ".join(s.replace("\r", "\n").split())
    for a, b in (("$$", "$$"), ("$", "$"), ("\\[", "\\]"), ("\\(", "\\)")):
        if t.startswith(a) and t.endswith(b) and len(t) > len(a) + len(b):
            t = t[len(a):-len(b)].strip()
            break
    return t


def mupdf_extract(pdf_path, formulas: list[Formula]) -> dict[str, str]:
    doc = pymupdf.open(pdf_path)
    try:
        return {f.id: doc[f.page].get_text("text", clip=pymupdf.Rect(f.rect)).strip() for f in formulas}
    finally:
        doc.close()


def pdfkit_available() -> bool:
    return shutil.which("swift") is not None and SWIFT_SRC.exists()


def _binary(cache_dir: Path) -> Path:
    """swift スクリプトを一度だけコンパイルする（起動を速くするため）。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    exe = cache_dir / "pdfkit_copy"
    if not exe.exists() or exe.stat().st_mtime < SWIFT_SRC.stat().st_mtime:
        subprocess.run(["swiftc", "-O", "-o", str(exe), str(SWIFT_SRC)], check=True, capture_output=True, timeout=300)
    return exe


def pdfkit_select(pdf_path, formulas: list[Formula], page_text: list[int] | None = None,
                  cache_dir: Path | None = None, shrink: float = 0.0) -> dict:
    """PDFKit で各式の矩形を選択した文字列。shrink は矩形を内側に縮める割合（境界の取りこぼし確認用）。"""
    doc = pymupdf.open(pdf_path)
    queries = []
    try:
        for f in formulas:
            x0, y0, x1, y1 = f.rect
            dx, dy = (x1 - x0) * shrink, (y1 - y0) * shrink
            r = coords.stored_to_pdf_user(doc[f.page], (x0 + dx, y0 + dy, x1 - dx, y1 - dy))
            queries.append({"id": f.id, "page": f.page, "rect": list(r)})
    finally:
        doc.close()
    exe = _binary(cache_dir or Path(__file__).resolve().parents[2] / "build")
    payload = json.dumps({"pdf": str(Path(pdf_path).resolve()), "queries": queries, "page_text": page_text or []})
    res = subprocess.run([str(exe)], input=payload.encode(), capture_output=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(f"PDFKit 確認に失敗: {res.stderr.decode(errors='replace')}")
    return json.loads(res.stdout)


def copy_table(pdf_path, formulas: list[Formula], use_pdfkit: bool = True) -> list[dict]:
    """式ごとのコピー比較表。"""
    mu = mupdf_extract(pdf_path, formulas)
    pk = {}
    pk_text = {}
    if use_pdfkit and pdfkit_available():
        r = pdfkit_select(pdf_path, formulas, page_text=sorted({f.page for f in formulas}))
        pk = r["selections"]
        pk_text = r["page_text"]
    rows = []
    for f in formulas:
        exp = f.latex
        row = {
            "id": f.id, "page": f.page, "expected": exp,
            "mupdf": mu.get(f.id), "mupdf_exact": mu.get(f.id) == exp,
            "mupdf_norm": normalize(mu.get(f.id)) == normalize(exp),
        }
        if pk:
            got = pk.get(f.id)
            row.update({
                "pdfkit": got, "pdfkit_exact": got == exp,
                "pdfkit_norm": normalize(got) == normalize(exp),
                "pdfkit_page_count": (pk_text.get(str(f.page)) or "").count(exp) if exp else None,
            })
        rows.append(row)
    return rows
