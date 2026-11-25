"""出力の形式（コピー・Snip・書き出し・派生PDFの文章）と、書き出しの設定。

内部のデータは変えない: 数式だけの結果は区切りのない LaTeX（複数行は改行区切り）、文章＋数式は `\\( … \\)`（行内）・
`\\[ … \\]`（表示）を含む文字列で持つ。出力するときに、利用者の設定でこの形に変える。
画面（viewer/static/format.js）も同じ規則で変える（試験で一致を確かめる）。

設定は app.json の "format" と "export"（メニューバー・常駐のショートカットからの Snip も、同じ設定を使う）。
"""

from __future__ import annotations

import re

FORMAT_DEFAULTS = {
    "inline": "paren",      # 行内数式: paren \( … \) / dollar $ … $ / none
    "display": "bracket",   # 表示数式: bracket \[ … \] / dollars $$ … $$ / equation / equation* / none
    "math_copy": "raw",     # 数式だけの結果のコピー: raw（区切りなし）/ auto（1行は行内・複数行は表示）/ inline / display
    "multiline": "lines",   # 複数行の数式: lines（改行で並べる）/ aligned / gathered
    "text_lines": "keep",   # 文章の改行: keep / join（段落ごとに 1 行にする）
}
FORMAT_CHOICES = {
    "inline": ("paren", "dollar", "none"),
    "display": ("bracket", "dollars", "equation", "equation*", "none"),
    "math_copy": ("raw", "auto", "inline", "display"),
    "multiline": ("lines", "aligned", "gathered"),
    "text_lines": ("keep", "join"),
}
EXPORT_DEFAULTS = {
    "snip_format": "md",        # Snip の書き出しの既定: md / tex / tsv / csv / json
    "md_math": "dollar",        # Markdown に書き出すときの数式: dollar（$ … $・$$ … $$）/ output（出力の形式の設定に従う）
    "include_source": True,     # 出所（資料・ページ・日時）を入れる
    "include_notes": True,      # メモを入れる
    "include_images": False,    # 切り取った画像も入れる（Markdown・LaTeX は ZIP にまとめる）
    "tex_document": False,      # LaTeX を、そのままコンパイルできる文書（\documentclass …）にする
    "card_front": "note",       # TSV・CSV の 1 列目（カードの表面）: note（メモ、なければ出所）/ source
    "card_linebreak": "<br>",   # TSV・CSV のセルの中の改行: <br> / \n（文字の \ と n）/ space
    "doc_page_markers": True,   # 資料の書き出し: ページの区切り（<!-- p.3 --> など）を入れる
    "doc_include_text": True,   # 資料の書き出し: 文章の枠も入れる（外すと数式だけ）
}
EXPORT_CHOICES = {
    "snip_format": ("md", "tex", "tsv", "csv", "json"),
    "md_math": ("dollar", "output"),
    "card_front": ("note", "source"),
    "card_linebreak": ("<br>", "\\n", "space"),
}

_MATH_RE = re.compile(r"\\\((.+?)\\\)|\\\[(.+?)\\\]", re.S)


class FormatError(ValueError):
    pass


def clean(section: str, d) -> dict:
    """設定を検査して、既定で埋める。不正な値は FormatError。"""
    defaults, choices = (FORMAT_DEFAULTS, FORMAT_CHOICES) if section == "format" else (EXPORT_DEFAULTS, EXPORT_CHOICES)
    out = dict(defaults)
    if d is None:
        return out
    if not isinstance(d, dict):
        raise FormatError(f"{section} はオブジェクト")
    for k, v in d.items():
        if k not in defaults:
            raise FormatError(f"未知の設定: {section}.{k}")
        if isinstance(defaults[k], bool):
            if not isinstance(v, bool):
                raise FormatError(f"{section}.{k} は真偽値")
        elif v not in choices[k]:
            raise FormatError(f"{section}.{k} は {choices[k]} のいずれか")
        out[k] = v
    return out


def from_legacy(delim: str | None, single: bool = False) -> dict:
    """以前の設定（delim: paren / dollar / none、single）から、形式の設定を作る。"""
    f = dict(FORMAT_DEFAULTS)
    if delim == "dollar":
        f.update(inline="dollar", display="dollars")
    elif delim == "none":
        f.update(inline="none", display="none")
    if single:
        f["math_copy"] = "auto"
    return f


def settings_from_config(cfg: dict) -> tuple[dict, dict]:
    """app.json の内容から (format, export)。format がなければ、以前の delim・single から作る。"""
    try:
        fmt = clean("format", cfg.get("format")) if cfg.get("format") is not None else from_legacy(cfg.get("delim"), bool(cfg.get("single")))
    except FormatError:
        fmt = dict(FORMAT_DEFAULTS)
    try:
        exp = clean("export", cfg.get("export"))
    except FormatError:
        exp = dict(EXPORT_DEFAULTS)
    return fmt, exp


# ---------------------------------------------------------------- 数式
def _lines(latex: str) -> list[str]:
    return [ln for ln in (latex or "").split("\n") if ln.strip()]


def join_multiline(latex: str, mode: str) -> str:
    ls = _lines(latex)
    if len(ls) <= 1 or mode == "lines":
        return "\n".join(ls) if ls else (latex or "")
    env = "aligned" if mode == "aligned" else "gathered"
    return f"\\begin{{{env}}}\n" + " \\\\\n".join(ls) + f"\n\\end{{{env}}}"


def wrap_inline(t: str, style: str) -> str:
    if style == "paren":
        return f"\\({t}\\)"
    if style == "dollar":
        return f"${t}$"
    return t


def wrap_display(t: str, style: str) -> str:
    multi = "\n" in t.strip()
    if style == "bracket":
        return f"\\[\n{t}\n\\]" if multi else f"\\[{t}\\]"
    if style == "dollars":
        return f"$$\n{t}\n$$" if multi else f"$${t}$$"
    if style in ("equation", "equation*"):
        return f"\\begin{{{style}}}\n{t}\n\\end{{{style}}}"
    return t


def format_math(latex: str, fmt: dict, how: str | None = None) -> str:
    """数式だけの結果を、設定に従って書く。how を省くと fmt["math_copy"]。"""
    how = how or fmt["math_copy"]
    t = join_multiline((latex or "").strip("\n"), fmt["multiline"])
    if not t.strip() or how == "raw":
        return t
    multi = "\n" in t.strip()
    if how == "inline" or (how == "auto" and not multi):
        return wrap_inline(t, fmt["inline"]) if not multi else wrap_display(t, fmt["display"])
    return wrap_display(t, fmt["display"])


def convert_text(text: str, fmt: dict) -> str:
    """文章＋数式（内部は \\( … \\)・\\[ … \\]）を、設定の区切りに変える。"""
    if not text:
        return text or ""
    if fmt["text_lines"] == "join":
        paras = re.split(r"\n\s*\n", text)
        text = "\n\n".join(re.sub(r"\s*\n\s*", " ", p).strip() for p in paras)

    def sub(m):
        if m.group(1) is not None:
            return wrap_inline(m.group(1), fmt["inline"])
        return wrap_display(join_multiline(m.group(2), fmt["multiline"]), fmt["display"])

    return _MATH_RE.sub(sub, text)


def copy_text(kind: str | None, latex: str, fmt: dict) -> str:
    """コピー・クリップボードに入れる文字列。"""
    if kind in ("mixed", "text"):
        return convert_text(latex or "", fmt)
    return format_math(latex or "", fmt)


# ---------------------------------------------------------------- 書き出しの補助
MD_FORMAT = {"inline": "dollar", "display": "dollars", "math_copy": "auto", "multiline": "lines", "text_lines": "keep"}
TEX_FORMAT = {"inline": "paren", "display": "bracket", "math_copy": "auto", "multiline": "aligned", "text_lines": "keep"}


def md_format(fmt: dict, exp: dict) -> dict:
    """Markdown に書き出すときの形式（既定は $ … $・$$ … $$。設定で「出力の形式に従う」も選べる）。"""
    if exp.get("md_math") == "output":
        return {**fmt, "math_copy": "auto" if fmt["math_copy"] == "raw" else fmt["math_copy"]}
    return {**MD_FORMAT, "multiline": fmt["multiline"], "text_lines": fmt["text_lines"]}


def tex_format(fmt: dict) -> dict:
    """LaTeX に書き出すときの形式（行内は \\( … \\)。表示は、設定が equation 系ならそれ、ほかは \\[ … \\]）。"""
    disp = fmt["display"] if fmt["display"] in ("equation", "equation*") else "bracket"
    return {**TEX_FORMAT, "display": disp, "multiline": fmt["multiline"] if fmt["multiline"] != "lines" else "aligned",
            "text_lines": fmt["text_lines"]}


_TEX_SPECIAL = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$", "&": r"\&", "#": r"\#", "%": r"\%",
                "_": r"\_", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def escape_tex_text(s: str) -> str:
    """文章（数式の外）を LaTeX の本文として安全にする（% や & で壊れないように）。"""
    return "".join(_TEX_SPECIAL.get(c, c) for c in s)


def tex_text(text: str, fmt: dict) -> str:
    """文章＋数式を LaTeX の本文にする: 数式の外だけをエスケープし、数式は tex_format の区切りで書く。

    改行を保つ設定（keep）では、文章の中の 1 つの改行を `\\` にする（LaTeX では、1 つの改行は段落にならないため）。
    表示数式の直前・直後の改行は、そのままにする。
    """
    tf = tex_format(fmt)
    text = text or ""
    if tf["text_lines"] == "join":
        text = "\n\n".join(re.sub(r"\s*\n\s*", " ", p).strip() for p in re.split(r"\n\s*\n", text))
    keep = tf["text_lines"] == "keep"
    out, last, prev_display = [], 0, False
    matches = list(_MATH_RE.finditer(text))
    for k in range(len(matches) + 1):
        m = matches[k] if k < len(matches) else None
        seg = escape_tex_text(text[last:m.start() if m else len(text)])
        if keep:
            next_display = m is not None and m.group(2) is not None
            body = seg.rstrip("\n") if next_display else seg
            tail = seg[len(body):]
            lead = len(body) - len(body.lstrip("\n")) if prev_display else 0
            body = body[:lead] + re.sub(r"(?<!\n)\n(?!\n)", lambda _: "\\\\\n", body[lead:])
            seg = body + tail
        out.append(seg)
        if m is None:
            break
        out.append(wrap_inline(m.group(1), "paren") if m.group(1) is not None
                   else wrap_display(join_multiline(m.group(2), tf["multiline"]), tf["display"]))
        prev_display = m.group(2) is not None
        last = m.end()
    return "".join(out)


def tex_document(body: str, title: str = "", images: bool = False) -> str:
    """そのままコンパイルできる LaTeX 文書にする（日本語は lualatex で）。"""
    pre = ["% Generated by Alice STEM Reader. Compile with lualatex (Japanese text) or pdflatex.",
           "\\documentclass{article}", "\\usepackage{iftex}", "\\ifLuaTeX\\usepackage{luatexja}\\fi",
           "\\usepackage{amsmath,amssymb}"]
    if images:
        pre.append("\\usepackage{graphicx}")
    head = [*pre, "\\begin{document}"]
    if title:
        head.append(f"\\section*{{{escape_tex_text(title)}}}")
    return "\n".join(head) + "\n\n" + body.strip() + "\n\n\\end{document}\n"


def card_cell(s: str, exp: dict) -> str:
    lb = exp["card_linebreak"]
    return s.replace("\r", "").replace("\n", "<br>" if lb == "<br>" else "\\n" if lb == "\\n" else " ")


__all__ = ["FORMAT_DEFAULTS", "EXPORT_DEFAULTS", "FORMAT_CHOICES", "EXPORT_CHOICES", "FormatError", "clean", "from_legacy",
           "settings_from_config", "format_math", "convert_text", "copy_text", "md_format", "tex_format", "tex_text",
           "tex_document", "escape_tex_text", "card_cell", "join_multiline"]
