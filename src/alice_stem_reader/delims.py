"""数式の区切り記号（`\\( \\)` / `\\[ \\]` / `$ $` / `$$ $$` / なし）の変換。

混在した文章の中の数式は、内部では `\\( … \\)`（行内）と `\\[ … \\]`（表示）で持つ。コピーや埋め込みのときに、
利用者が選んだ書き方に変える（画面の設定。viewer/static/app.js の convertDelims と同じ規則）。
"""

from __future__ import annotations

import re

STYLES = ("none", "paren", "dollar")
_TOKEN = re.compile(r"\\\(|\\\)|\\\[|\\\]")


def convert_delims(text: str, style: str) -> str:
    """内部の記法（`\\( \\)`・`\\[ \\]`）を、style の書き方に変える。none は、区切りを除く。"""
    if style not in STYLES:
        raise ValueError(f"未知の区切り: {style}")
    if style == "paren" or not text:
        return text
    if style == "none":
        return _TOKEN.sub("", text)
    return _TOKEN.sub(lambda m: "$$" if m.group(0) in ("\\[", "\\]") else "$", text)


def wrap_single(latex: str, style: str) -> str:
    """単独の数式（文章を含まない）に区切りを付ける。複数行は表示数式、1行は行内。none は、そのまま。"""
    if style not in STYLES:
        raise ValueError(f"未知の区切り: {style}")
    t = latex.strip("\n")
    if style == "none" or not t.strip():
        return latex
    multi = "\n" in t.strip()
    if style == "paren":
        return f"\\[{t}\\]" if multi else f"\\({t}\\)"
    return f"$${t}$$" if multi else f"${t}$"
