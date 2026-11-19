"""文章と数式が混ざった領域を、Mathpix Snip のように読む（日本語の文章 + 行内数式 `\\( … \\)`）。

流れ
1. 領域内の数式の枠（数式検出 MFD）を集める。
2. 領域を PaddleOCR で読む（文字ごとの位置つき）。日本語の文字が数式の枠の中にあれば、その枠は誤検出（文章）として捨てる。
3. 残った数式の枠は UniMERNet などで LaTeX にする。文章は、数式の枠の外の文字だけを使う。
4. 文章と数式を、上から下・左から右の行にまとめる。

数式だけの領域（枠がほぼ領域全体を占める、または文章がない）は、None を返す（呼び出し側が従来どおり数式として読む）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf

from .base import crop_png
from .region import recognize_png
from .textocr import _CJK, looks_like_text

_CJK_RE = re.compile(f"[{_CJK}]")
MIN_WORD_SCORE = 0.5
_PUNCT = "()（）[]［］「」『』【】〈〉《》，、。．.,;:；：！？!?・〜~―ー-−+=/×÷"  # 文章に付く記号（括弧・句読点）は残す
_LABEL_RE = re.compile(r"^[（(]?\d{1,3}[)）]?$|^[①-⑳]$")  # 「(1)」「②」などの番号
_MATHY_OP_RE = re.compile(r"[=+\-−×÷^²³√∫∑∏πθ<>≤≥/]")
_MATHY_ONLY_RE = re.compile(r"[A-Za-z0-9\s().,^'′]+")
PURE_FORMULA_COVER = 0.6  # 数式の枠が領域のこの割合以上を占めるなら、数式だけの領域とみなす（OCR は行わない）


@dataclass
class MixedResult:
    latex: str
    items: list[dict] = field(default_factory=list)  # 行ごとの内訳（確認用）
    dropped_boxes: int = 0  # 日本語の文字を含んでいたので捨てた、数式の誤検出の枠の数


def _inside(c, r) -> bool:
    return r[0] <= c[0] <= r[2] and r[1] <= c[1] <= r[3]


def _center(r):
    return ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)


def _area(r):
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def _inter(a, b):
    return _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def group_rows(items: list[dict]) -> list[list[dict]]:
    """縦に重なる要素を同じ行にまとめる（行内数式の上付き・下付きで高さが違っても同じ行）。"""
    rows: list[dict] = []
    for it in sorted(items, key=lambda i: (i["view"][1] + i["view"][3]) / 2):
        v = it["view"]
        placed = False
        for r in rows:
            ov = min(v[3], r["y1"]) - max(v[1], r["y0"])
            if ov > 0.5 * min(v[3] - v[1], r["y1"] - r["y0"]):
                r["items"].append(it)
                r["y0"], r["y1"] = min(r["y0"], v[1]), max(r["y1"], v[3])
                placed = True
                break
        if not placed:
            rows.append({"y0": v[1], "y1": v[3], "items": [it]})
    return [sorted(r["items"], key=lambda i: i["view"][0]) for r in rows]


def join_row(row: list[dict]) -> str:
    out = ""
    prev = None
    for it in row:
        t = it["text"]
        if prev is not None:
            gap = it["view"][0] - prev["view"][2]
            h = max(it["view"][3] - it["view"][1], 1.0)
            # 数式と文章のあいだは空白を入れる。単語どうしは、文字の種類ごとの空きの大きさで決める
            pc, nc = bool(_CJK_RE.search(prev["text"][-1:])), bool(_CJK_RE.search(t[:1]))
            limit = 0.8 if (pc and nc) else 0.5 if (pc or nc) else 0.3
            if it["kind"] == "formula" or prev["kind"] == "formula" or gap > limit * h:
                out += " "
        out += t
        prev = it
    return out.strip()


def math_runs(words: list[dict]) -> tuple[list[list[dict]], list[dict]]:
    """日本語の文字を含まない単語の連なりのうち、数式らしいもの（と、残りの単語）に分ける。"""
    runs: list[list[dict]] = []
    loose: list[dict] = []
    for row in group_rows([{"kind": "text", "text": w["text"], "view": w["view"], "w": w} for w in words]):
        cur: list[dict] = []

        def flush():
            nonlocal cur
            if cur:
                s = "".join(w["text"] for w in cur)
                label = bool(_LABEL_RE.match(s))
                mathy = (not label and len(s) >= 3 and (
                    _MATHY_OP_RE.search(s) or (re.search(r"\d", s) and re.search(r"[A-Za-z]", s))
                    or (len(cur) >= 2 and _MATHY_ONLY_RE.fullmatch(s) and re.search(r"[A-Za-z]", s))))
                if mathy:
                    runs.append(cur)
                else:
                    loose.extend(cur)
            cur = []

        for it in row:
            w = it["w"]
            if _CJK_RE.search(w["text"]):
                flush()
                loose.append(w)
                continue
            if cur:
                gap = w["view"][0] - cur[-1]["view"][2]
                if gap > 1.2 * max(w["view"][3] - w["view"][1], 1.0):
                    flush()
            cur.append(w)
        flush()
    return runs, loose


def recognize_mixed(page: pymupdf.Page, view_rect, page_boxes: list[dict], backend, text_backend, dpi: int = 200,
                    verifier=None) -> MixedResult | None:
    """view_rect（見た目の座標 pt）の領域を、文章 + 数式として読む。数式だけの領域なら None。

    page_boxes: そのページの数式検出の結果（detect_page の戻り値）。
    """
    reg = tuple(pymupdf.Rect(view_rect) & page.rect)
    if _area(reg) <= 0:
        return None
    boxes = [b for b in page_boxes if _inside(_center(b["view"]), reg)]
    if any(_inter(b["view"], reg) / _area(reg) >= PURE_FORMULA_COVER for b in boxes) or (
            sum(_inter(b["view"], reg) for b in boxes) / _area(reg) >= PURE_FORMULA_COVER):
        return None  # 数式だけの領域
    clip = pymupdf.Rect(reg)
    pix = page.get_pixmap(dpi=dpi, clip=clip, alpha=False)
    k = dpi / 72.0
    words = []
    for ln in text_backend.read(pix.tobytes("png"), words=True):
        if float(ln.get("score", 0)) < MIN_WORD_SCORE:
            continue
        for w in ln.get("words") or [{"text": ln["text"], "box": ln["box"]}]:
            t = (w.get("text") or "").strip()
            if not t:
                continue
            b = w["box"]
            words.append({"text": t, "score": ln["score"],
                          "view": (clip.x0 + b[0] / k, clip.y0 + b[1] / k, clip.x0 + b[2] / k, clip.y0 + b[3] / k)})
    # 日本語の文字が中にある数式の枠は、文章の誤検出として捨てる
    keep, dropped = [], 0
    for b in boxes:
        inner = [w for w in words if _inside(_center(w["view"]), b["view"])]
        if any(_CJK_RE.search(w["text"]) for w in inner):
            dropped += 1
        else:
            keep.append(b)
    text_words = [w for w in words if not any(_inside(_center(w["view"]), b["view"]) for b in keep)
                  and re.search(r"[\w" + _CJK + re.escape(_PUNCT) + "]", w["text"])]
    if not any(looks_like_text(w["text"]) for w in text_words):
        return None  # 文章がない（数式だけ）
    # 数式検出が見落とした数式（文章の文字を含まない、数式らしい連なり）は、その範囲を数式として読み直す
    runs, loose = math_runs(text_words)
    items = [{"kind": "text", "text": w["text"], "view": w["view"]} for w in loose]
    for run in runs:
        r = pymupdf.Rect(min(w["view"][0] for w in run) - 1, min(w["view"][1] for w in run) - 1,
                         max(w["view"][2] for w in run) + 1, max(w["view"][3] for w in run) + 1) & page.rect
        try:
            res = recognize_png(backend, crop_png(page, r, dpi=dpi), dpi=dpi, split=False, verifier=verifier)
        except ValueError:
            res = None
        if res is not None and res.latex and len(res.latex.strip()) >= 2:
            items.append({"kind": "formula", "text": f"\\({res.latex.strip()}\\)", "view": tuple(r)})
        else:
            items += [{"kind": "text", "text": w["text"], "view": w["view"]} for w in run]
    for b in keep:
        r = pymupdf.Rect(b["view"]) & page.rect
        try:
            png = crop_png(page, r, dpi=dpi)
        except ValueError:
            continue
        res = recognize_png(backend, png, dpi=dpi, split=(b["kind"] != "inline"), verifier=verifier)
        if res.latex:
            latex = res.latex.strip()
            wrap = ("\\(", "\\)") if b["kind"] == "inline" else ("\\[", "\\]")
            if "\n" in latex:
                wrap = ("\\[", "\\]")
            items.append({"kind": "formula", "text": f"{wrap[0]}{latex}{wrap[1]}", "view": tuple(r), "display": wrap[0] == "\\["})
        else:  # 読めなかった枠は、中の文字（数字・記号）を文章として残す
            items += [{"kind": "text", "text": w["text"], "view": w["view"]}
                      for w in words if _inside(_center(w["view"]), b["view"])]
    rows = group_rows(items)
    for r in rows:  # 同じ行に文章もあるなら、表示数式ではなく行内数式にする（「(1) \\(…\\) として」）
        if len(r) > 1:
            for it in r:
                if it.get("display") and "\n" not in it["text"]:
                    it["text"] = "\\(" + it["text"][2:-2] + "\\)"
    lines = [join_row(r) for r in rows]
    lines = [x for x in lines if x]
    return MixedResult(latex="\n".join(lines), items=[{"text": x} for x in lines], dropped_boxes=dropped)
