"""認識・検出結果を期待値と照合する（レポート用、合否判定ではない）。"""

from __future__ import annotations

import difflib
import re

from .coords import iou
from .models import FormulaSet


def squash(s: str | None) -> str:
    """空白をすべて除いた比較用文字列（コマンドや中身は変えない）。"""
    return re.sub(r"\s+", "", s or "")


_LENIENT = [
    (re.compile(r"\\(textstyle|displaystyle|scriptstyle)"), ""),
    (re.compile(r"\\[!,;:]"), ""),  # 間隔コマンド
    (re.compile(r"\\mathop\{(.)\}"), r"\1"),
    (re.compile(r"\{([=+\-])\}"), r"\1"),
    (re.compile(r"\\begin\{array\}\{[lcr]\}\{(.*)\}\\end\{array\}"), r"\1"),
]


def lenient(s: str | None) -> str:
    """表示が同じになる書き換え（間隔・スタイル指定、1行の array 包み）を除いた比較用文字列。
    意味の一致を保証しない。レポートでは exact と分けて示す。"""
    t = squash(s)
    for _ in range(3):
        for pat, rep in _LENIENT:
            t = pat.sub(rep, t)
    # 1文字だけを囲む余分な波括弧 {x} → x（^ _ の直後とコマンド引数は除く）
    t = re.sub(r"(?<![\^_}a-zA-Z])\{(\\frac\{[^{}]*\}\{[^{}]*\})\}", r"\1", t)
    return t


def compare_recognition(expected: FormulaSet, got: FormulaSet) -> dict:
    gm = {f.id: f for f in got.formulas}
    rows = []
    for e in expected.formulas:
        g = gm.get(e.id)
        lat = g.latex if g else None
        rows.append({
            "id": e.id,
            "expected": e.latex,
            "got": lat,
            "exact_nospace": squash(lat) == squash(e.latex),
            "lenient_equal": lenient(lat) == lenient(e.latex),
            "char_ratio": round(difflib.SequenceMatcher(None, squash(e.latex), squash(lat)).ratio(), 3),
            "status": g.status if g else "missing",
        })
    n = len(rows)
    return {
        "count": n,
        "exact_nospace": sum(r["exact_nospace"] for r in rows),
        "lenient_equal": sum(r["lenient_equal"] for r in rows),
        "mean_char_ratio": round(sum(r["char_ratio"] for r in rows) / n, 3) if n else None,
        "rows": rows,
    }


def compare_regions(expected: FormulaSet, got: FormulaSet, threshold: float = 0.5) -> dict:
    """期待矩形と検出矩形を IoU で一対一に対応付け、検出漏れ・誤検出を数える。"""
    matched, misses = [], []
    used: set[str] = set()
    for e in expected.formulas:
        best, best_iou = None, 0.0
        for g in got.formulas:
            if g.page != e.page or g.id in used:
                continue
            v = iou(e.rect, g.rect)
            if v > best_iou:
                best, best_iou = g, v
        if best is not None and best_iou >= threshold:
            used.add(best.id)
            matched.append({"expected": e.id, "got": best.id, "iou": round(best_iou, 3)})
        else:
            misses.append({"expected": e.id, "best_iou": round(best_iou, 3)})
    false_pos = [g.id for g in got.formulas if g.id not in used]
    return {"matched": matched, "missed": misses, "false_positives": false_pos,
            "mean_iou": round(sum(m["iou"] for m in matched) / len(matched), 3) if matched else None}
