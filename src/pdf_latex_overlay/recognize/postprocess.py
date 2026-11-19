"""認識の前後処理。

- split_lines: 複数行を囲んだ切り出し画像を、行ごとの画像に分ける（横方向の投影で空白行を探す）。
- clean_latex: 表示だけを調整する記法を取り除き、1行ずつの LaTeX にする。数式の意味は変えない範囲に限る。
"""

from __future__ import annotations

import io
import re

import numpy as np
from PIL import Image

from .unimernet import tidy_latex


# ---------------------------------------------------------------- 行分割
def erase_edge_rules(a: np.ndarray, dpi: int, ink: int = 170, edge: float = 0.08,
                     min_cover: float = 0.6, max_w_pt: float = 1.2) -> np.ndarray:
    """枠の左右の端にある細い縦線（囲み枠・点線の罫線）を白で消す。

    高さの min_cover 以上を覆い、幅が max_w_pt 以下で、左右 edge の範囲にある線だけを対象にする。
    1行分の高さしかない積分記号などは、複数行の枠では高さの割合が足りないので消えない。
    1行だけの枠では、積分記号を消さないよう、高さが 2 行分（約 24pt）未満なら何もしない。
    """
    h, w = a.shape
    px = dpi / 72.0
    if h < 24 * px:
        return a
    dark = a < ink
    cover = dark.sum(axis=0) / h
    max_w = max(1, int(round(max_w_pt * px)))
    # 点線: 短い点の繰り返し（8個以上）で、高さの1割以上を覆う列
    def dash_runs(col):
        d = np.diff(np.concatenate([[0], col.astype(np.int8), [0]]))
        return int((d == 1).sum())

    dotted = np.array([cover[x] >= 0.1 and dash_runs(dark[:, x]) >= 8 for x in range(w)])
    cand = np.nonzero((cover >= min_cover) | dotted)[0]
    if not len(cand):
        return a
    out = a.copy()
    for r in np.split(cand, np.nonzero(np.diff(cand) > 1)[0] + 1):
        near_edge = r[-1] < w * edge or r[0] > w * (1 - edge)
        if near_edge and len(r) <= max_w:
            out[:, max(0, r[0] - 1):min(w, r[-1] + 2)] = 255
    return out



def line_segments(png: bytes, dpi: int, min_gap_pt: float = 2.5, min_line_pt: float = 6.0,
                  ink: int = 170):
    """行の上下位置（ピクセル、[start, end)）と、罫線を消した画像を返す。行がなければ空のリスト。

    - min_gap_pt 以上の空白行（インクのない横帯）で区切る。分数の線と分子・分母の間は通常これより狭い。
    - 高さが min_line_pt 未満の断片（積分の上端・下端、波線など）は、近い方の行に戻す。
    """
    img = Image.open(io.BytesIO(png)).convert("L")
    a = erase_edge_rules(np.array(img), dpi, ink)
    img = Image.fromarray(a)
    rows = (a < ink).sum(axis=1)
    noise = max(1, int(a.shape[1] * 0.002))  # ごみ点を無視
    has_ink = rows > noise
    px = dpi / 72.0
    min_gap = max(2, int(round(min_gap_pt * px)))
    min_line = max(3, int(round(min_line_pt * px)))

    segs: list[list[int]] = []  # [start, end)
    y = 0
    n = len(has_ink)
    while y < n:
        if not has_ink[y]:
            y += 1
            continue
        start = y
        gap = 0
        while y < n:
            if has_ink[y]:
                gap = 0
            else:
                gap += 1
                if gap >= min_gap:
                    break
            y += 1
        end = y - gap if gap else y
        segs.append([start, end])
    # 枠の上下の端に触れている低い断片は、隣の行の一部が枠で切れたものなので捨てる
    if not segs:  # インクのない（白紙の）枠
        return img, []
    dark_all = a < ink

    def ink_width(sg):
        cols = np.nonzero(dark_all[sg[0]:sg[1]].any(axis=0))[0]
        return int(cols[-1] - cols[0] + 1) if len(cols) else 0

    widest = max(ink_width(sg) for sg in segs)
    tallest = max(e - s for s, e in segs)

    def is_fragment(sg):
        # 低い断片、または幅の狭い断片（∑ や ∫ の上端・下端など）は行と数えない
        return (sg[1] - sg[0]) < min_line or ink_width(sg) < 0.35 * widest

    # 切り出しの境界そのものに触れている低い断片は、枠で切れた隣の行なので捨てる
    segs = [sg for sg in segs
            if not ((is_fragment(sg) or (sg[1] - sg[0]) < 0.6 * tallest) and (sg[0] == 0 or sg[1] >= n))] or segs
    if len(segs) == 1:
        return img, segs
    # 低い断片を近い行へ併合
    changed = True
    while changed and len(segs) > 1:
        changed = False
        for i, (s, e) in enumerate(segs):
            if not is_fragment([s, e]):
                continue
            if i == 0:
                j = 1
            elif i == len(segs) - 1:
                j = i - 1
            else:
                j = i - 1 if s - segs[i - 1][1] <= segs[i + 1][0] - e else i + 1
            lo, hi = min(i, j), max(i, j)
            segs[lo] = [min(segs[lo][0], segs[hi][0]), max(segs[lo][1], segs[hi][1])]
            del segs[hi]
            changed = True
            break
    return img, segs


def split_lines(png: bytes, dpi: int, **kw) -> list[bytes]:
    """行間の空白で画像を分ける。分けられなければ元の画像1枚を返す。

    - 2.5pt 以上の空白行（インクのない横帯）で区切る。分数の線と分子・分母の間は通常これより狭い。
    - 低い断片・幅の狭い断片（積分の上端・下端、波線など）は、近い方の行に戻す。
    - 枠の端で切れた隣の行の断片は捨てる。
    """
    img, segs = line_segments(png, dpi, **kw)
    if not segs:
        return [png]
    px = dpi / 72.0
    pad = max(2, int(round(1.5 * px)))
    if len(segs) == 1 and segs[0][0] == 0 and segs[0][1] >= img.height:
        return [png]
    out = []
    for s0, e0 in segs:
        crop = img.crop((0, max(0, s0 - pad), img.width, min(img.height, e0 + pad)))
        buf = io.BytesIO()
        crop.convert("RGB").save(buf, format="PNG")
        out.append(buf.getvalue())
    return out


# ---------------------------------------------------------------- LaTeX 整形
_STYLE = re.compile(r"\\(displaystyle|textstyle|scriptstyle|scriptscriptstyle)(?![A-Za-z])")
_NEG_SPACE = re.compile(r"\\!")
_ENV = re.compile(r"\\begin\{(array|aligned|gathered|split|matrix)\}(\{[^{}]*\})?(.*)\\end\{\1\}", re.S)


def _match_brace(s: str, i: int) -> int:
    """s[i] == '{' に対応する '}' の位置。逆斜線で始まる \\{ \\} は数えない。"""
    depth = 0
    j = i
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1


# 引数を取るコマンド（1引数・2引数）。これ以外の直後の { } は引数ではない（例: \\pm{\\sqrt{x}}）。
ARG1 = {"sqrt", "mathrm", "mathbf", "mathit", "mathsf", "mathtt", "mathbb", "mathcal", "mathfrak", "boldsymbol",
        "text", "textrm", "textbf", "textit", "mbox", "operatorname", "overline", "underline", "hat", "widehat",
        "bar", "vec", "tilde", "widetilde", "dot", "ddot", "check", "breve", "acute", "grave", "overbrace",
        "underbrace", "boxed", "pmb", "bm", "begin", "end",
        "label", "tag", "color", "mathop", "mathbin", "mathrel", "mathord", "mathopen", "mathclose", "mathpunct", "mathinner", "phantom", "hphantom", "vphantom", "cancel", "not"}
ARG2 = {"frac", "dfrac", "tfrac", "cfrac", "binom", "dbinom", "tbinom", "stackrel", "overset", "underset", "textcolor"}


def balance_braces(s: str) -> str:
    """対応しない } を捨て、閉じていない { を末尾で閉じる（認識器の出力の崩れ対策）。"""
    out = []
    depth = 0
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\":
            out.append(s[i:i + 2])
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            if depth == 0:
                i += 1
                continue
            depth -= 1
        out.append(c)
        i += 1
    return "".join(out) + "}" * depth


def unwrap_braces(s: str) -> str:
    """意味のない波括弧を外す: 引数でも上付き・下付きでもない { } と、{{x}} の外側。

    コマンドごとに残りの引数の数を数えながら読み進める（入れ子に対応）。
    """
    out: list[str] = []
    pending = 0  # 次に来る { } のうち、引数として残す数
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+)", s[i:])
            if m:
                name = m.group(1)
                out.append(m.group(0))
                i += len(m.group(0))
                pending = 2 if (name in ARG2 or name == "begin") else (1 if name in ARG1 else 0)
                continue
            out.append(s[i:i + 2])
            i += 2
            pending = 0
            continue
        if c in "^_":
            out.append(c)
            pending = 1
            i += 1
            continue
        if c == "[" and pending:  # \sqrt[3]{x} の省略可能な引数
            j = s.find("]", i)
            if j > 0:
                out.append(s[i:j + 1])
                i = j + 1
                continue
        if c == " ":
            out.append(c)
            i += 1
            continue
        if c == "{":
            j = _match_brace(s, i)
            if j < 0:
                out.append(s[i:])
                break
            inner = s[i + 1:j]
            # {{x}} → {x}（内側が全体を包む1つの群なら外側を外す）
            while inner.startswith("{") and _match_brace(inner, 0) == len(inner) - 1:
                inner = inner[1:-1]
            body = unwrap_braces(inner)
            if pending:
                out.append("{" + body + "}")
                pending -= 1
            elif body.strip():
                # \int{x} → \int x（制御語と英字がくっつかないように空白を入れる）
                if re.search(r"\\[A-Za-z]+$", "".join(out[-3:]) if out else "") and body[:1].isalpha():
                    out.append(" ")
                out.append(body)
            i = j + 1
            continue
        out.append(c)
        pending = 0
        i += 1
    return "".join(out)


def clean_latex(s: str | None) -> str | None:
    """表示用の記法を除き、複数行は改行で区切った行の並びにする。"""
    if s is None:
        return None
    t = tidy_latex(s)
    t = _STYLE.sub(" ", t)
    t = re.sub(r"\\(bf|it|rm)\\([A-Za-z]+)", r"\\\2", t)  # \\bf\\Pi のような古い書体指定は外す
    t = _NEG_SPACE.sub(" ", t)
    t = tidy_latex(t)
    m = _ENV.fullmatch(t.strip())
    if m:
        rows = [r for r in re.split(r"\\\\(?:\[[^\]]*\])?", m.group(3))]
        rows = [r.replace("&", "") for r in rows]
        return "\n".join(x for x in (clean_latex(r) for r in rows) if x)
    t = unwrap_braces(balance_braces(t))
    t = tidy_latex(t)
    t = tidy_latex(re.sub(r"(?<!\\)[_^]\{\}", " ", t))  # 空の上付き・下付き
    t = re.sub(r"(\\(qquad|quad|,|;|:| )|~|\s)+$", "", t)  # 末尾の空白命令
    t = re.sub(r"^(\\(qquad|quad|,|;|:| )|~|\s)+", "", t)
    return t


def looks_degenerate(raw: str | None) -> bool:
    """認識器が同じ字句を繰り返して崩れた出力か（例: \\! が数百回、同じ項の反復）。"""
    if not raw:
        return True
    toks = raw.split()
    if len(toks) >= 40:
        run = best = 1
        for a, b in zip(toks, toks[1:]):
            run = run + 1 if a == b else 1
            best = max(best, run)
        if best >= 15:
            return True
        # 長い出力で、ほとんどが同じ数種類の字句
        if len(set(toks)) <= len(toks) * 0.08:
            return True
    m = re.search(r"(.{12,}?)\1{4,}", raw.replace(" ", ""))
    return bool(m)


def clean_lines(raw: str | None) -> str | None:
    """行ごとの認識原文（改行区切り）を、行ごとに整形してつなぐ。"""
    if raw is None:
        return None
    out = [clean_latex(x) for x in raw.split("\n")]
    return "\n".join(x for x in out if x)
