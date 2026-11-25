"""出力の形式（outfmt.py）と、画面の同じ規則（viewer/static/format.js）。2 つは同じ結果を返す。"""

import itertools
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from pdf_latex_overlay import outfmt

STATIC = Path(__file__).resolve().parents[1] / "src" / "pdf_latex_overlay" / "viewer" / "static"

CASES_MATH = ["x^{2}+1", "a=b\nc=d", "\n\\int_0^1 x\\,dx\n", ""]
CASES_TEXT = ["次の式 \\(x^2\\) を見よ。\n次の行 \\[a=b\\]", "段落1の\n続き\n\n段落2 \\(y\\)", "数式なし"]


def all_formats():
    keys = list(outfmt.FORMAT_CHOICES)
    for combo in itertools.product(*(outfmt.FORMAT_CHOICES[k] for k in keys)):
        yield dict(zip(keys, combo))


def test_examples():
    f = dict(outfmt.FORMAT_DEFAULTS)
    assert outfmt.format_math("x", f) == "x"  # 既定は区切りなし（以前と同じ）
    assert outfmt.format_math("x", {**f, "math_copy": "auto"}) == "\\(x\\)"
    assert outfmt.format_math("a\nb", {**f, "math_copy": "auto"}) == "\\[\na\nb\n\\]"
    assert outfmt.format_math("a\nb", {**f, "math_copy": "display", "multiline": "aligned", "display": "dollars"}) == \
        "$$\n\\begin{aligned}\na \\\\\nb\n\\end{aligned}\n$$"
    assert outfmt.format_math("x", {**f, "math_copy": "display", "display": "equation"}) == "\\begin{equation}\nx\n\\end{equation}"
    assert outfmt.convert_text("式 \\(x\\) と \\[y\\]", {**f, "inline": "dollar", "display": "dollars"}) == "式 $x$ と $$y$$"
    assert outfmt.convert_text("a\nb\n\nc", {**f, "text_lines": "join"}) == "a b\n\nc"
    assert outfmt.from_legacy("dollar", True) == {**f, "inline": "dollar", "display": "dollars", "math_copy": "auto"}


def test_tex_text_escapes_only_outside_math():
    f = dict(outfmt.FORMAT_DEFAULTS)
    out = outfmt.tex_text("50% & a_b \\(x_1^{2}\\)\n次\n\\[a\nb\\]\n終", f)
    assert out.startswith("50\\% \\& a\\_b \\(x_1^{2}\\)\\\\\n次")  # 本文はエスケープ、数式はそのまま、改行は \\
    assert "\\begin{aligned}\na \\\\\nb\n\\end{aligned}" in out and "\\\\\n\\[" not in out  # 表示数式の前に \\ を付けない
    doc = outfmt.tex_document(out, "演習_1")
    assert doc.count("\\begin{document}") == 1 and "\\section*{演習\\_1}" in doc and "luatexja" in doc


def test_clean_validates():
    assert outfmt.clean("export", {"include_images": True})["include_images"] is True
    for bad in ({"inline": "x"}, {"nope": 1}, "str"):
        with pytest.raises(outfmt.FormatError):
            outfmt.clean("format", bad)
    with pytest.raises(outfmt.FormatError):
        outfmt.clean("export", {"tex_document": "yes"})
    assert outfmt.settings_from_config({"delim": "none"})[0]["inline"] == "none"  # 以前の設定を引き継ぐ


@pytest.mark.skipif(not shutil.which("node"), reason="node がない")
def test_python_and_browser_rules_agree():
    fmts = list(all_formats())
    js = f"""
const F = require({json.dumps(str(STATIC / "format.js"))});
const fmts = {json.dumps(fmts)}, maths = {json.dumps(CASES_MATH)}, texts = {json.dumps(CASES_TEXT)};
const out = [];
for (const f of fmts) {{
  for (const m of maths) for (const how of ["raw", "auto", "inline", "display"]) out.push(F.formatMath(m, f, how));
  for (const t of texts) out.push(F.convertText(t, f));
  out.push(F.copyText("math", maths[1], f), F.copyText("mixed", texts[0], f));
}}
console.log(JSON.stringify(out));
"""
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    want = []
    for f in fmts:
        for m in CASES_MATH:
            for how in ("raw", "auto", "inline", "display"):
                want.append(outfmt.format_math(m, f, how))
        for t in CASES_TEXT:
            want.append(outfmt.convert_text(t, f))
        want += [outfmt.copy_text("math", CASES_MATH[1], f), outfmt.copy_text("mixed", CASES_TEXT[0], f)]
    assert len(got) == len(want) and got == want
