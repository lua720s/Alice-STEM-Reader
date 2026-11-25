"""設定（数式の区切り記号）と、ビューアの中から PDF・フォルダを開く（2026-11-19 のユーザー指示）。"""

import json
import shutil
import sys
from pathlib import Path

import pymupdf
import pytest

from pdf_latex_overlay.delims import STYLES, convert_delims, wrap_single
from pdf_latex_overlay.viewer import hub as hubmod
from pdf_latex_overlay.viewer.hub import Hub
from pdf_latex_overlay.viewer.server import serve
from pdf_latex_overlay.viewer.session import SessionError, ViewerSession

STATIC = Path(__file__).resolve().parents[1] / "src" / "pdf_latex_overlay" / "viewer" / "static"


def _pdf(fx, d, name, title=None):
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    doc = pymupdf.open(fx["scan"])
    doc.set_metadata({"title": title or name})
    doc.save(p)
    doc.close()
    return p


MIXED = "(1) \\(21=1+20\\) として \\[a=b\\] のとき"


def test_convert_delims_styles():
    assert convert_delims(MIXED, "paren") == MIXED
    assert convert_delims(MIXED, "dollar") == "(1) $21=1+20$ として $$a=b$$ のとき"
    assert convert_delims(MIXED, "none") == "(1) 21=1+20 として a=b のとき"
    assert convert_delims("x^{2}", "none") == "x^{2}" and convert_delims("", "dollar") == ""
    with pytest.raises(ValueError):
        convert_delims("x", "latex")


def test_wrap_single_inline_and_display():
    assert wrap_single("x^{2}", "paren") == "\\(x^{2}\\)" and wrap_single("x^{2}", "dollar") == "$x^{2}$"
    assert wrap_single("a=b\nc=d", "paren") == "\\[a=b\nc=d\\]" and wrap_single("a=b\nc=d", "dollar") == "$$a=b\nc=d$$"
    assert wrap_single("x", "none") == "x" and wrap_single("  ", "paren") == "  "
    assert set(STYLES) == {"none", "paren", "dollar"}


def test_export_applies_the_delimiter_setting_to_embedded_text(fx, tmp_path):
    from pdf_latex_overlay.models import Formula

    p = _pdf(fx, tmp_path / "in", "a.pdf")
    s = ViewerSession(p, tmp_path / "work")
    f = s.create(0, (40, 60, 380, 90), latex="(1) \\(x+1\\) として")
    with s.lock:
        g = s._get(f["id"])
        g.kind, g.source, g.status = "text", "mixed", "confirmed"
    for style, expect in (("dollar", "(1) $x+1$ として"), ("none", "(1) x+1 として"), ("paren", "(1) \\(x+1\\) として")):
        r = s.export(delim=style)
        assert r["ok"] is True, r
        text = pymupdf.open(r["output"])[0].get_text("text", clip=pymupdf.Rect(40, 60, 380, 90)).strip()
        assert text == expect, (style, text)
    with pytest.raises(SessionError):
        s.export(delim="latex")
    s.close()


def test_hub_add_root_is_remembered_and_serve_reads_it(fx, tmp_path, monkeypatch):
    monkeypatch.setenv("PLO_LIBRARY", str(tmp_path / "lib"))
    a = _pdf(fx, tmp_path / "教材", "a.pdf")
    rf = tmp_path / "support" / "roots.json"
    h = Hub([], library=tmp_path / "lib", roots_file=rf)  # 何も指定しなくても起動できる
    assert h.tree()["roots"][0]["vroot"] and h.roots == []
    key = h.add_root(str(a.parent))
    assert key == "r0" and json.loads(rf.read_text()) == [str(a.parent.resolve())]
    assert h.add_root(str(a.parent)) == "r0"  # 重ねない
    sub = a.parent / "sub"
    sub.mkdir()
    assert h.add_root(str(sub)) == ""  # すでに並んでいるフォルダの中は、足さない
    for bad in ("/nonexistent", None, str(a)):
        with pytest.raises(SessionError):
            h.add_root(bad)
    t = h.tree()
    assert any(r["name"] == "教材" for r in t["roots"])
    h.close()


def test_pick_uses_osascript_and_handles_cancel(fx, tmp_path, monkeypatch):
    a = _pdf(fx, tmp_path / "教材", "a.pdf")
    h = Hub([], library=tmp_path / "lib")
    calls = []

    class R:
        def __init__(self, rc, out):
            self.returncode, self.stdout = rc, out

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return fake_run.result

    monkeypatch.setattr(hubmod.subprocess, "run", fake_run)
    monkeypatch.setattr(hubmod.sys, "platform", "darwin")
    fake_run.result = R(1, "")  # キャンセル（osascript は -128 で終わる）
    assert h.pick("pdf") == {"cancelled": True} and h.roots == []
    fake_run.result = R(0, str(a) + "\n")
    r = h.pick("pdf")
    assert r["added"] == 1 and h.current_id == r["opened"] and "choose file" in calls[-1][2] and "EPUB" in calls[-1][2]  # PDF 以外の資料も選べる
    fake_run.result = R(0, str(a.parent) + "/\n")
    assert h.pick("folder")["added"] == 1 and "choose folder" in calls[-1][2]
    with pytest.raises(SessionError):
        h.pick("other")
    # Windows・Linux: OS の選択画面（osutil.pick_paths）を使う
    from pdf_latex_overlay import osutil

    monkeypatch.setattr(hubmod.sys, "platform", "linux")
    asked = []
    monkeypatch.setattr(osutil, "pick_paths", lambda kind, prompt: asked.append(kind) or None)
    assert h.pick("pdf") == {"cancelled": True} and asked == ["files"]
    monkeypatch.setattr(osutil, "pick_paths", lambda kind, prompt: [str(a)])
    assert h.pick("pdf")["added"] == 1

    def broken(kind, prompt):
        raise osutil.OSFeatureError("tkinter がない")

    monkeypatch.setattr(osutil, "pick_paths", broken)
    with pytest.raises(SessionError) as e:
        h.pick("folder")
    assert e.value.status == 501
    h.close()


def test_static_ui_has_settings_and_open_buttons():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    for ident in ("settings-btn", "settings", "fmt-form", "exp-form", "set-sample", "keymap", "tree-open", "empty-open-pdf", "empty-open-folder"):
        assert f'id="{ident}"' in html, ident
    for tab in ("general", "output", "export", "keys"):  # 設定のタブ
        assert f'data-stab="{tab}"' in html and f'data-spane="{tab}"' in html
    assert 'name="open_mode"' in html and "format.js" in html
    for token in ("FORMAT_UI", "EXPORT_UI", "PF.copyText", "outText", "/api/settings", "/api/pick", "pickOpen"):
        assert token in js, token
