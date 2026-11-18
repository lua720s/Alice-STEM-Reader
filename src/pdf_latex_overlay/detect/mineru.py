"""MinerU の結果 JSON を、このプロジェクトの式 JSON に変換する（段階2b）。

MinerU 本体はこのプロジェクトに入れない（依存が大きく、ライセンスも独自のため）。利用者が別途
実行した結果ファイルを読むだけにする。対応する形式は MinerU 4.2.3 と docvortex 0.6 のソースで確認した。

1. MinerU 4 の middle.json（`"schema": "docvortex.middle"`）
   - 表示数式は `type: "equation"` のブロック。`bbox` はページ画像に対する 0〜1 の正規化座標
   - 行内数式（`equation_inline` の span）には bbox がないので、個別の矩形を作れない → 除外として記録
2. content_list.json（MinerU 4 の V1 形式）: `type: "equation"`、`bbox` は 0〜1000 の整数、`page_idx`、`text`
3. 旧形式の middle.json（MinerU 1〜3 の `pdf_info`）: span の `bbox` は pt、`page_size` が基準
   - `interline_equation` と `inline_equation` の span に bbox がある

MinerU の座標は、表示どおりのページ画像（回転後、左上原点）を基準とする前提で扱う。
旧形式では page_size と回転前後の寸法を照合し、どちらの基準かを判定する。
OCR の LaTeX はデータとして扱う（外側の $ / $$ / \\[ \\] だけを外す）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import pymupdf

from .. import coords, safety
from ..models import Formula, FormulaSet

EQUATION_BLOCK_TYPES = {"equation", "interline_equation", "equation_interline"}
INLINE_TYPES = {"equation_inline", "inline_equation"}


def strip_delimiters(s: str) -> str:
    t = s.strip()
    for a, b in (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"), ("$", "$")):
        if t.startswith(a) and t.endswith(b) and len(t) > len(a) + len(b):
            return t[len(a):-len(b)].strip()
    return t


def _walk_blocks(blocks: Iterable[Any]):
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        yield b
        content = b.get("content")
        if isinstance(content, list):
            yield from _walk_blocks([c for c in content if isinstance(c, dict) and "type" in c and ("bbox" in c or "content" in c)])
        for key in ("blocks", "lines", "spans"):
            if isinstance(b.get(key), list):
                yield from _walk_blocks(b[key])


def _inline_spans(obj) -> list[str]:
    """bbox のない行内数式（MinerU 4）の LaTeX を集める。"""
    found = []
    if isinstance(obj, dict):
        if obj.get("type") in INLINE_TYPES and isinstance(obj.get("content"), str) and "bbox" not in obj:
            found.append(obj["content"])
        for v in obj.values():
            found += _inline_spans(v)
    elif isinstance(obj, list):
        for v in obj:
            found += _inline_spans(v)
    return found


def detect_format(data) -> str:
    if isinstance(data, dict) and data.get("schema") == "docvortex.middle":
        return "mineru4_middle"
    if isinstance(data, dict) and "pdf_info" in data:
        return "legacy_middle"
    if isinstance(data, list) and all(isinstance(x, dict) for x in data) and any("page_idx" in x for x in data):
        return "content_list_v1"
    raise ValueError("MinerU の middle.json / content_list.json として認識できない")


def _candidates(data, fmt: str) -> tuple[list[dict], list[dict]]:
    """(候補, 除外) を返す。候補は {page, frame, rect, latex, kind}。frame は norm1 / norm1000 / pt。"""
    cands, skipped = [], []
    if fmt == "mineru4_middle":
        for page in data.get("pages", []):
            pidx = page.get("page_idx")
            for b in _walk_blocks(page.get("blocks", [])):
                if b.get("type") in EQUATION_BLOCK_TYPES:
                    latex = b.get("content") if isinstance(b.get("content"), str) else None
                    if b.get("bbox") and latex and latex.strip():
                        cands.append({"page": pidx, "frame": "norm1", "rect": b["bbox"], "latex": latex, "kind": "display"})
                    else:
                        skipped.append({"page": pidx, "reason": "no_bbox_or_latex", "latex": latex})
            for latex in _inline_spans(page.get("blocks", [])):
                skipped.append({"page": pidx, "reason": "inline_no_bbox", "latex": latex})
    elif fmt == "content_list_v1":
        for item in data:
            if item.get("type") != "equation":
                continue
            latex = item.get("text")
            if item.get("bbox") and latex and item.get("text_format", "latex") == "latex":
                cands.append({"page": item.get("page_idx"), "frame": "norm1000", "rect": item["bbox"],
                              "latex": latex, "kind": "display"})
            else:
                skipped.append({"page": item.get("page_idx"), "reason": "no_bbox_or_latex", "latex": latex})
    elif fmt == "legacy_middle":
        for i, page in enumerate(data["pdf_info"]):
            pidx = page.get("page_idx", i)
            size = page.get("page_size")
            seen = set()
            for key in ("para_blocks", "preproc_blocks"):
                for b in _walk_blocks(page.get(key, [])):
                    t = b.get("type")
                    if t in ("interline_equation", "inline_equation") and "content" in b and b.get("bbox"):
                        sig = (t, tuple(round(v, 1) for v in b["bbox"]))
                        if sig in seen:
                            continue
                        seen.add(sig)
                        cands.append({"page": pidx, "frame": "pt", "page_size": size, "rect": b["bbox"],
                                      "latex": b["content"],
                                      "kind": "display" if t == "interline_equation" else "inline"})
    return cands, skipped


def _to_rendered_pt(page: pymupdf.Page, c: dict, frame: str = "auto") -> tuple[float, float, float, float]:
    W, H = page.rect.width, page.rect.height  # 表示どおりのページ寸法（回転後）
    x0, y0, x1, y1 = (float(v) for v in c["rect"])
    if frame == "unrotated":  # 基準が回転前のページ（利用者が明示）
        uw, uh = page.cropbox.width, page.cropbox.height
        if c["frame"] == "norm1":
            x0, y0, x1, y1 = x0 * uw, y0 * uh, x1 * uw, y1 * uh
        elif c["frame"] == "norm1000":
            x0, y0, x1, y1 = x0 * uw / 1000, y0 * uh / 1000, x1 * uw / 1000, y1 * uh / 1000
        r = pymupdf.Rect(x0, y0, x1, y1) * page.rotation_matrix
        return r.x0, r.y0, r.x1, r.y1
    if c["frame"] == "norm1":
        return x0 * W, y0 * H, x1 * W, y1 * H
    if c["frame"] == "norm1000":
        return x0 * W / 1000, y0 * H / 1000, x1 * W / 1000, y1 * H / 1000
    size = c.get("page_size")
    if frame == "rendered":
        size = None
    if size and len(size) == 2 and size[0] > 0 and size[1] > 0:
        sx, sy = W / float(size[0]), H / float(size[1])
        if abs(sx - 1) > 0.02 or abs(sy - 1) > 0.02:
            # 回転前の寸法が基準なら、回転前の保存座標として扱う
            uw, uh = page.cropbox.width, page.cropbox.height
            if abs(size[0] - uw) < 1 and abs(size[1] - uh) < 1 and page.rotation:
                r = pymupdf.Rect(x0, y0, x1, y1) * page.rotation_matrix
                return r.x0, r.y0, r.x1, r.y1
            return x0 * sx, y0 * sy, x1 * sx, y1 * sy
    return x0, y0, x1, y1


def import_mineru(mineru_json, input_pdf, duplicate_iou: float = 0.5, frame: str = "auto") -> FormulaSet:
    """frame: auto（既定。表示どおりのページ基準。旧形式は page_size で判定）/ rendered / unrotated。"""
    if frame not in ("auto", "rendered", "unrotated"):
        raise ValueError(f"frame は auto / rendered / unrotated: {frame}")
    data = json.loads(Path(mineru_json).read_text(encoding="utf-8"))
    fmt = detect_format(data)
    cands, skipped = _candidates(data, fmt)
    doc = pymupdf.open(input_pdf)
    fs = FormulaSet(input_sha256=safety.sha256_file(input_pdf))
    counters: dict[int, int] = {}
    warnings: list[dict] = []
    warned: set[int] = set()
    try:
        for c in cands:
            p = c["page"]
            if not isinstance(p, int) or not (0 <= p < doc.page_count):
                skipped.append({"page": p, "reason": "page_out_of_range", "latex": c["latex"]})
                continue
            page = doc[p]
            if frame == "auto" and page.rotation == 180 and p not in warned:
                warned.add(p)
                warnings.append({"page": p, "warning": "rotate180_frame_ambiguous",
                                 "detail": "回転180度のページは基準（回転前後）を寸法で判別できない。表示どおりと仮定した"})
            vr = pymupdf.Rect(_to_rendered_pt(page, c, frame)).normalize()
            if vr.is_empty:
                skipped.append({"page": p, "reason": "empty_bbox", "latex": c["latex"]})
                continue
            stored = vr * page.derotation_matrix
            rect = (stored.x0, stored.y0, stored.x1, stored.y1)
            dup = next((f for f in fs.formulas if f.page == p and coords.iou(f.rect, rect) > duplicate_iou), None)
            if dup is not None:
                skipped.append({"page": p, "reason": f"duplicate_of:{dup.id}", "latex": c["latex"]})
                continue
            counters[p] = counters.get(p, 0) + 1
            raw = c["latex"]
            fs.formulas.append(Formula(
                id=f"mineru_p{p}_{counters[p]}", page=p, rect=rect, latex=strip_delimiters(raw), ocr_latex=raw,
                source="mineru", kind=c["kind"], status="pending",
            ))
    finally:
        doc.close()
    fs.meta = {"importer": "mineru", "format": fmt, "source_file": Path(mineru_json).name,
               "skipped": skipped, "warnings": warnings,
               "frame": frame if frame != "auto" else "auto(rendered_page_top_left)"}
    return fs
