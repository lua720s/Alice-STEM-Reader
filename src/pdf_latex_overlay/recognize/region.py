"""1つの枠（矩形）を認識する共通の手順。ビューアと CLI の両方から使う。

1. 見た目どおりに切り出す → 2. 複数行なら行ごとに分ける（囲み枠の罫線は消す）
3. 行ごとに認識 → 崩れた出力（同じ字句の反復）は、拡大縮小した画像で読み直す
4. 表示だけの記法を取り除き、行を改行でつなぐ
"""

from __future__ import annotations

import io

import pymupdf
from PIL import Image

from .base import Recognition, crop_png
from .postprocess import clean_latex, looks_degenerate, split_lines

RETRY_SCALES = (0.75, 1.3)


def _scaled(png: bytes, f: float) -> bytes:
    img = Image.open(io.BytesIO(png)).convert("RGB")
    img = img.resize((max(8, int(img.width * f)), max(8, int(img.height * f))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _raw(r: Recognition) -> str | None:
    return getattr(r, "raw", None) or r.latex


def recognize_region(backend, page: pymupdf.Page, rect, dpi: int = 200, split: bool = True) -> Recognition:
    return recognize_png(backend, crop_png(page, rect, dpi=dpi), dpi=dpi, split=split)


def recognize_png(backend, png: bytes, dpi: int = 200, split: bool = True) -> Recognition:
    """切り出し済みの画像を認識する（ページ文書に触れないので、別スレッドから呼べる）。"""
    lines = split_lines(png, dpi) if split else [png]
    results = backend.recognize(lines)
    raws, cleans, problems = [], [], []
    for k, (img, r) in enumerate(zip(lines, results)):
        raw = _raw(r)
        if not r.error and looks_degenerate(raw):
            for f in RETRY_SCALES:
                r2 = backend.recognize([_scaled(img, f)])[0]
                if not r2.error and not looks_degenerate(_raw(r2)):
                    r, raw = r2, _raw(r2)
                    break
        if looks_degenerate(raw) or r.error:
            problems.append(f"line{k + 1}:{r.error or 'degenerate'}")
            raws.append(raw or "")
            continue
        raws.append(raw)
        c = clean_latex(raw)
        if c:
            cleans.append(c)
    out = Recognition(latex="\n".join(cleans) if cleans else None, confidence=None,
                      error="; ".join(problems) if problems else None)
    out.raw = "\n".join(raws)  # type: ignore[attr-defined]
    out.lines = len(lines)  # type: ignore[attr-defined]
    return out
