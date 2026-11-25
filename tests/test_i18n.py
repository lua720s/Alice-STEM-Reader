"""画面の多言語化（日本語・英語・中国語・スペイン語・ロシア語）。2026-11-19 のユーザー指示。"""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "pdf_latex_overlay" / "viewer" / "static"
I18N = (STATIC / "i18n.js").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
JS = "\n".join((STATIC / f).read_text(encoding="utf-8") for f in ("app.js", "dashboard.js"))
KEYS = (STATIC / "keys.js").read_text(encoding="utf-8")


def _dict():
    out = {}
    for m in re.finditer(r'^    "((?:[^"\\]|\\.)*)": \[(.*)\],\s*$', I18N, flags=re.M):
        key = bytes(m.group(1), "utf-8").decode("unicode_escape").encode("latin1").decode("utf-8") if "\\" in m.group(1) else m.group(1)
        vals = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(2))
        out[key] = [v.replace("\\n", "\n") for v in vals]
    return out


def test_every_entry_has_four_translations_with_same_placeholders():
    d = _dict()
    assert len(d) > 100
    for k, vals in d.items():
        assert len(vals) == 4 and all(v.strip() for v in vals), k
        ph = sorted(re.findall(r"\{\w+\}", k))
        for v in vals:
            assert sorted(re.findall(r"\{\w+\}", v)) == ph, (k, v)


def test_all_ui_keys_are_translated():
    d = _dict()
    keys = set(re.findall(r'data-i18n(?:-title|-label|-ph|-alt)?="([^"]+)"', HTML))
    keys |= set(re.findall(r'\bT\("((?:[^"\\]|\\.)*)"', JS))
    keys |= set(re.findall(r'"(準備済み|一部のみ処理済み|処理中|未処理|確認中|テーマ: [^"]+)"', JS))
    keys |= set(re.findall(r'label: "([^"]*[^\x00-\x7f][^"]*)"', KEYS))  # ショートカットの動作の名前
    keys |= {"UniMERNet base（手元で動く・既定）", "UniMERNet tiny（手元で動く・軽い）", "PP-FormulaNet（手元で動く）", "Mathpix（画像を外部に送る）"}
    norm = {k.replace("\\n", "\n") for k in d}
    missing = [k for k in keys if k.replace("\\n", "\n") not in norm]
    assert not missing, missing


def test_five_languages_and_served():
    for code in ("ja", "en", "zh", "es", "ru"):
        assert f'["{code}",' in I18N
    assert 'id="lang"' in HTML and "i18n.js" in HTML
    from pdf_latex_overlay.viewer.server import STATIC_FILES

    assert "i18n.js" in STATIC_FILES
