"""2つのモデルの読み取りを突き合わせて、食い違った式に「要確認」の印を付ける。

実教材の11式で、tiny と base の読み取りが食い違った4式はすべて本当の誤りを含み、一致した7式はすべて
正解だった（MODELS.md。標本が小さいので傾向）。この性質を使う。

食い違いの判定は、「数学的に同じ」とみなせる違い（空白・書体・間隔・括弧の大きさ・同じ記号の別の書き方）を
無視する。違いを無視しすぎると、本当の誤りを見逃すので、無視するのは次のものに限る。
"""

from __future__ import annotations

import re

# 中身を残して、外側の書体指定だけを外す
_WRAPPERS = r"(?:textit|textrm|textbf|textsf|mathrm|mathit|mathbf|mathsf|mathtt|boldsymbol|pmb|bm|mbox|text)"
_WRAP_RE = re.compile(r"\\" + _WRAPPERS + r"\s*\{([^{}]*)\}")
# 間隔・大きさ・表示の指定（意味を変えない）
_NOOP_RE = re.compile(
    r"\\(?:displaystyle|textstyle|scriptstyle|scriptscriptstyle|limits|nolimits|displaylimits|quad|qquad|"
    r"left|right|bigg|Bigg|big|Big|bigl|bigr|biggl|biggr|Bigl|Bigr|Biggl|Biggr)(?![A-Za-z])"
)
_SPACE_RE = re.compile(r"\\[ ,;:!]|~")
# 同じ記号の別の書き方
_ALIASES = [
    (re.compile(r"\\[dt]frac(?![A-Za-z])"), r"\\frac"),
    (re.compile(r"\\rightarrow(?![A-Za-z])"), r"\\to"),
    (re.compile(r"\\operatorname\*?\{([A-Za-z]+)\}"), r"\\\1"),
    (re.compile(r"\\mathop\{([^{}]*)\}"), r"\1"),
    (re.compile(r"\\lbrace(?![A-Za-z])"), r"\\{"),
    (re.compile(r"\\rbrace(?![A-Za-z])"), r"\\}"),
    (re.compile(r"\\prime(?![A-Za-z])"), r"'"),
    (re.compile(r"\\(?:mid|vert|lvert|rvert)(?![A-Za-z])"), "|"),  # 縦線の別の書き方
    (re.compile(r"\\geq(?![A-Za-z])"), r"\\ge"),
    (re.compile(r"\\leq(?![A-Za-z])"), r"\\le"),
    (re.compile(r"\\neq(?![A-Za-z])"), r"\\ne"),
]


def normalize_math(s: str | None) -> str:
    """食い違いの判定用に、数学的に同じとみなせる違いを除いた文字列にする（表示用ではない）。"""
    if not s:
        return ""
    t = s
    for _ in range(3):  # 入れ子の書体指定
        t = _WRAP_RE.sub(r"\1", t)
    t = _NOOP_RE.sub("", t)
    t = _SPACE_RE.sub("", t)
    for pat, rep in _ALIASES:
        t = pat.sub(rep, t)
    t = re.sub(r"\s+", "", t)
    t = t.replace("{}", "").replace("^{'}", "'")  # f^{\prime} と f' は同じ
    return t


def lines_of(latex: str | None) -> list[str]:
    """行ごとに正規化する。句読点だけの行（`.` など）は、式の一部ではないので除く。"""
    out = [normalize_math(x) for x in (latex or "").split("\n") if x.strip()]
    return [x for x in out if not re.fullmatch(r"[.,;:]*", x)]


def same_math(a: str | None, b: str | None) -> bool:
    """2つの読み取りが、数学的に同じとみなせるか。行数が違えば、違う。"""
    la, lb = lines_of(a), lines_of(b)
    return bool(la) and la == lb


def review_flag(primary, alt) -> str | None:
    """主の読み取りと、もう一方の読み取りから、「要確認」の理由を決める。

    - disagree: どちらも読めたが、数学的に食い違う
    - failed: どちらかが読めなかった（崩れた出力・エラーを含む）
    - None: 一致している（確認の優先度が低い）
    """
    pl, al = getattr(primary, "latex", None), getattr(alt, "latex", None)
    if not pl or not al or getattr(primary, "error", None) or getattr(alt, "error", None):
        return "failed"
    return None if same_math(pl, al) else "disagree"
