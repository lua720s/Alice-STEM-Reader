"""PDF 以外の資料（EPUB・画像・Markdown・LaTeX・DjVu など）と、Snip（画面・画像・資料の一部を読む）。"""

import base64
import io
import json
import urllib.error
import urllib.request
import zipfile

import pymupdf
import pytest
from PIL import Image, ImageDraw

from alice_stem_reader import docformats, snip
from alice_stem_reader.recognize import FixedBackend
from alice_stem_reader.viewer.hub import Hub
from alice_stem_reader.viewer.server import make_server
from alice_stem_reader.viewer.session import SessionError


def _png(w=600, h=200, text="x^2+1", rotate_exif=False) -> bytes:
    im = Image.new("RGB", (w, h), "white")
    ImageDraw.Draw(im).text((20, h // 2), text, fill="black")
    buf = io.BytesIO()
    if rotate_exif:
        exif = Image.Exif()
        exif[0x0112] = 6  # 90° 回転して表示すべき写真
        im.save(buf, format="JPEG", exif=exif)
    else:
        im.save(buf, format="PNG")
    return buf.getvalue()


def _epub(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="c.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("c.opf", '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="i">'
                   '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="i">x</dc:identifier><dc:title>T</dc:title>'
                   '<dc:language>ja</dc:language></metadata><manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
                   '</manifest><spine><itemref idref="c1"/></spine></package>')
        z.writestr("c1.xhtml", '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body><h1>第1章</h1><p>運動方程式 F = ma を考える。</p></body></html>')


class FakeText:
    version = "fake-ocr"

    def __init__(self, lines):
        self.lines = lines

    def read(self, png, words=False):
        return self.lines


# ---------------------------------------------------------------- 形式の判定と変換
def test_supported_kinds():
    assert docformats.kind_of("a.PDF") == "pdf" and docformats.kind_of("b.epub") == "ebook"
    assert docformats.kind_of("c.djvu") == "djvu" and docformats.kind_of("d.md") == "markdown"
    assert docformats.kind_of("e.tex") == "latex" and docformats.kind_of("f.HEIC") == "image"
    assert docformats.kind_of("g.docx") is None and not docformats.is_supported("g.docx")


def test_markdown_keeps_math_source_in_text_layer(tmp_path):
    md = tmp_path / "note.md"
    md.write_text("# 解の公式\n\n解は $x=\\frac{-b\\pm\\sqrt{b^2-4ac}}{2a}$ で、a_1 は係数。\n\n$$\n\\int_0^1 x\\,dx\n$$\n", encoding="utf-8")
    out = docformats.as_pdf(md, tmp_path / "lib")
    assert out.parent.name == "_converted" and out.suffix == ".pdf"
    text = pymupdf.open(out)[0].get_text()
    assert "$x=\\frac{-b\\pm\\sqrt{b^2-4ac}}{2a}$" in text  # 数式の原文が壊れない（_ や \\ が Markdown として解釈されない）
    assert "\\int_0^1 x\\,dx" in text and "解の公式" in text
    assert docformats.conversion_info(out)["method"] == "markdown:source"
    assert docformats.as_pdf(md, tmp_path / "lib") == out  # 2 回目はキャッシュ
    assert md.read_text(encoding="utf-8").startswith("# 解の公式")  # 原本は変えない


def test_image_exif_orientation_and_multipage_tiff(tmp_path):
    jpg = tmp_path / "hand.jpg"
    jpg.write_bytes(_png(800, 300, rotate_exif=True))
    p = pymupdf.open(docformats.as_pdf(jpg, tmp_path / "lib"))[0]
    assert p.rect.height > p.rect.width  # 向きを補正して縦長に
    tif = tmp_path / "scan.tiff"
    a = Image.open(io.BytesIO(_png()))
    a.save(tif, save_all=True, append_images=[a.copy(), a.copy()])
    assert pymupdf.open(docformats.as_pdf(tif, tmp_path / "lib")).page_count == 3


def test_epub_is_laid_out_with_text(tmp_path):
    e = tmp_path / "book.epub"
    _epub(e)
    d = pymupdf.open(docformats.as_pdf(e, tmp_path / "lib"))
    assert d.page_count >= 1 and "運動方程式" in d[0].get_text()


def test_latex_falls_back_to_source_without_tex(tmp_path, monkeypatch):
    monkeypatch.setattr(docformats.shutil, "which", lambda x: None)
    t = tmp_path / "paper.tex"
    t.write_text("\\documentclass{article}\\begin{document}$E=mc^2$\\end{document}", encoding="utf-8")
    out = docformats.as_pdf(t, tmp_path / "lib")
    info = docformats.conversion_info(out)
    assert info["method"] == "latex:source" and "TeX" in info["compile_error"]
    assert "$E=mc^2$" in pymupdf.open(out)[0].get_text()


def test_djvu_without_ddjvu_explains(tmp_path, monkeypatch):
    monkeypatch.setattr(docformats.shutil, "which", lambda x: None)
    d = tmp_path / "old.djvu"
    d.write_bytes(b"AT&TFORM")
    with pytest.raises(docformats.ConversionError, match="djvulibre"):
        docformats.as_pdf(d, tmp_path / "lib")


# ---------------------------------------------------------------- コレクションに並び、開ける
def test_hub_lists_and_opens_other_formats(tmp_path):
    root = tmp_path / "資料"
    root.mkdir()
    (root / "note.md").write_text("本文 $a^2$", encoding="utf-8")
    (root / "photo.png").write_bytes(_png())
    (root / "x.docx").write_text("x")
    h = Hub([root], library=tmp_path / "lib")
    names = {c["name"]: c for c in h.tree()["roots"][0]["children"]}
    assert set(names) == {"note.md", "photo.png"} and names["note.md"]["format"] == "markdown"
    s = h.open(names["note.md"]["id"])
    info = s.doc_info()
    assert info["name"] == "note.md" and info["format"] == "markdown" and info["conversion"]["method"] == "markdown:source"
    assert s.source.name == "note.md" and s.input.parent.name == "_converted"
    h.close()


# ---------------------------------------------------------------- Snip
def test_decode_image_normalizes():
    png = snip.decode_image("data:image/png;base64," + base64.b64encode(_png()).decode())
    assert png.startswith(b"\x89PNG")
    with pytest.raises(snip.SnipError):
        snip.decode_image("not-an-image")
    rot = Image.open(io.BytesIO(snip.decode_image(_png(800, 300, rotate_exif=True))))
    assert rot.height > rot.width


def test_recognize_snip_math_and_mixed_fallbacks():
    png = snip.decode_image(_png())
    r = snip.recognize_snip(png, backend=FixedBackend(["x^{2}+1"]), mode="math")
    assert r["kind"] == "math" and r["latex"] == "x^{2}+1"
    tb = FakeText([{"text": "次の式を解け", "score": 0.95, "box": [10, 10, 300, 60]}])
    r = snip.recognize_snip(png, backend=FixedBackend([None]), text_backend=tb, mode="auto")
    assert r["kind"] == "text" and r["latex"] == "次の式を解け"  # 数式として読めなければ、文章として
    r = snip.recognize_snip(png, backend=FixedBackend(["a"]), text_backend=tb, mode="text")
    assert r["kind"] == "text"
    with pytest.raises(snip.SnipError):
        snip.recognize_snip(png, backend=FixedBackend(["a"]), mode="nope")


def test_format_for_copy_follows_delimiter_settings():
    assert snip.format_for_copy({"kind": "math", "latex": "x"}, "dollar", True) == "$x$"
    assert snip.format_for_copy({"kind": "math", "latex": "x"}, "dollar", False) == "x"
    assert snip.format_for_copy({"kind": "mixed", "latex": "式 \\(x\\)"}, "dollar") == "式 $x$"


def test_snip_store_history_and_exports(tmp_path):
    import zipfile

    from alice_stem_reader import outfmt

    st = snip.SnipStore(tmp_path / "_snips")
    a = st.add(b"png1", {"kind": "math", "latex": "a^2"}, {"type": "screen"}, {"name": "fixed"})
    b = st.add(b"png2", {"kind": "mixed", "latex": "式 \\(b\\) と 50%"}, {"type": "document", "name": "力学.pdf", "page": 2},
               {"name": "fixed"})
    assert [r["id"] for r in st.list()] == [b["id"], a["id"]]  # 新しい順
    assert st.image(a["id"]) == b"png1"
    st.update(a["id"], latex="a^{2}", note="面積")
    md, ctype, name = st.export("md")
    md = md.decode()
    assert "$a^{2}$" in md and "式 $b$" in md and "力学.pdf p.3" in md and name.endswith(".md") and "面積" in md
    tsv = st.export("tsv")[0].decode().splitlines()
    assert tsv[0].split("\t")[:2] == ["面積", "\\(a^{2}\\)"]
    csvt = st.export("csv", exp={"card_front": "source", "include_source": False})[0].decode().splitlines()
    assert csvt[0] == "画面,\\(a^{2}\\)"
    tex = st.export("tex", ids=[b["id"]], exp={"tex_document": True})[0].decode()
    assert "式 \\(b\\) と 50\\%" in tex and "a^{2}" not in tex and "\\documentclass" in tex and tex.rstrip().endswith("\\end{document}")
    data, ctype, name = st.export("md", exp={"include_images": True, "include_source": False})
    assert ctype == "application/zip" and name.endswith(".zip")
    z = zipfile.ZipFile(io.BytesIO(data))
    assert sorted(n.split("/")[0] for n in z.namelist()).count("images") == 2
    assert f"](images/{a['id']}.png)" in z.read([n for n in z.namelist() if n.endswith(".md")][0]).decode()
    js = json.loads(st.export("json", exp={"include_source": False})[0])
    assert js[0]["latex"] == "a^{2}" and "source" not in js[0]
    md2 = st.export("md", fmt={"inline": "paren"}, exp={"md_math": "output"})[0].decode()  # Markdown でも出力の形式に従う
    assert "式 \\(b\\)" in md2
    st.delete(a["id"])
    assert len(st.list()) == 1 and not (tmp_path / "_snips" / f"{a['id']}.png").exists()
    with pytest.raises(snip.SnipError):
        st.get("../../etc")
    with pytest.raises(outfmt.FormatError):
        st.export("md", exp={"include_images": "yes"})


def _hub_with_models(tmp_path, answers):
    root = tmp_path / "in"
    root.mkdir(exist_ok=True)
    h = Hub([root], library=tmp_path / "lib")
    h.backend_name, h.backend_obj = "fixed", FixedBackend(answers)
    return h, root


def test_hub_snip_image_screen_and_region(tmp_path, fx):
    import shutil

    h, root = _hub_with_models(tmp_path, ["y=mx+b"])
    rec = h.snip_image("data:image/png;base64," + base64.b64encode(_png()).decode(), mode="math", source="paste")
    assert rec["latex"] == "y=mx+b" and rec["source"]["type"] == "paste" and rec["engine"]["name"] == "fixed"
    assert h.snip_screen(capture=lambda: None) == {"cancelled": True}
    r2 = h.snip_screen(mode="math", copy=False, capture=lambda: _png())
    assert r2["source"] == {"type": "screen"} and r2["copied"] is False
    # 資料の範囲: Markdown は、OCR を使わず原文を返す
    (root / "n.md").write_text("面積は $\\pi r^2$ である。", encoding="utf-8")
    h.scan(force=True)
    h.open(h.id_of(root / "n.md"))
    r3 = h.snip_region(0, [0, 0, 595, 300])
    assert "$\\pi r^2$" in r3["latex"] and r3["mode"] == "source" and r3["source"]["name"] == "n.md"
    # PDF（スキャン）の範囲は OCR
    shutil.copy(fx["scan"], root / "s.pdf")
    h.scan(force=True)
    h.open(h.id_of(root / "s.pdf"))
    r4 = h.snip_region(0, [50, 50, 300, 150], mode="math")
    assert r4["latex"].split("\n")[0] == "y=mx+b" and r4["source"]["page"] == 0
    assert len(h.snips().list()) == 4
    h.close()


def test_snip_requires_engine(tmp_path):
    h = Hub([tmp_path], library=tmp_path / "lib")
    with pytest.raises(SessionError, match="エンジン"):
        h.snip_image(base64.b64encode(_png()).decode())
    h.close()


def test_snip_http_endpoints(tmp_path):
    h, _ = _hub_with_models(tmp_path, ["z"])
    httpd, token = make_server(h)
    import threading

    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def call(method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                     headers={"X-Token": token, **({"Content-Type": "application/json"} if data else {})})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.headers.get("Content-Type"), r.read()

    try:
        big = _png(2400, 1600)  # 1MB を超えうる大きさでも受け付ける
        ct, body = call("POST", "/api/snip/image", {"image": base64.b64encode(big).decode(), "mode": "math", "name": "手書き.png"})
        rec = json.loads(body)
        assert rec["latex"] == "z" and rec["source"]["name"] == "手書き.png"
        ct, body = call("GET", "/api/snips")
        assert json.loads(body)["items"][0]["id"] == rec["id"]
        ct, body = call("GET", f"/api/snips/{rec['id']}.png")
        assert ct == "image/png" and body.startswith(b"\x89PNG")
        call("POST", f"/api/snips/{rec['id']}", {"latex": "z^2"})
        ct, body = call("POST", "/api/snips/export", {"fmt": "md"})
        assert ct.startswith("text/markdown") and b"$z^2$" in body
        ct, body = call("GET", "/api/formats")
        assert ".epub" in json.loads(body)["extensions"]
        with pytest.raises(urllib.error.HTTPError) as ei:
            call("POST", "/api/snip/region", {"page": 0, "view_rect": [0, 0, 10, 10]})
        assert ei.value.code == 409  # 資料を開いていない
        call("DELETE", f"/api/snips/{rec['id']}")
        assert json.loads(call("GET", "/api/snips")[1])["items"] == []
    finally:
        httpd.shutdown()
        h.close()


def test_rename_keeps_original_extension(tmp_path):
    root = tmp_path / "資料"
    root.mkdir()
    (root / "a.epub").write_bytes(b"x")
    h = Hub([root], library=tmp_path / "lib")
    h.scan(force=True)
    h.rename(h.id_of(root / "a.epub"), "教科書")
    assert (root / "教科書.epub").exists()  # .pdf を付けない
    h.close()


def test_export_text_reading_order_and_notes(tmp_path, fx):
    import shutil

    from alice_stem_reader.models import Formula
    from alice_stem_reader.viewer.session import ViewerSession

    src = tmp_path / "演習.pdf"
    shutil.copy(fx["scan"], src)
    s = ViewerSession(src, tmp_path / "wd")
    s.fs.formulas += [Formula(id="b", page=0, rect=(50, 300, 200, 320), latex="x^{2}", status="pending"),
                      Formula(id="a", page=0, rect=(50, 100, 300, 120), latex="次の式 \\(y\\) を見よ", kind="text", status="pending"),
                      Formula(id="c", page=0, rect=(50, 400, 200, 440), latex="a\nb", status="excluded")]
    s.notes_set("doc", "第2章の演習")
    md, ctype, name = s.export_text("md")
    assert md.index("次の式 $y$ を見よ") < md.index("$x^{2}$") and "第2章の演習" in md and "a\nb" not in md
    assert name.startswith("演習-") and name.endswith(".md")
    tex = s.export_text("tex")[0]
    assert "\\(x^{2}\\)" in tex and "次の式 \\(y\\) を見よ" in tex
    s.close()


def test_bundled_katex_is_served_and_paths_are_locked(tmp_path):
    import threading

    h = Hub([tmp_path], library=tmp_path / "lib")
    httpd, _ = make_server(h)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/vendor/katex/katex.min.js") as r:
            assert r.status == 200 and b"katex" in r.read(2000)
        with urllib.request.urlopen(base + "/vendor/katex/fonts/KaTeX_Main-Regular.woff2") as r:
            assert r.headers["Content-Type"] == "font/woff2"
        for bad in ("/vendor/katex/fonts/../../server.py", "/vendor/katex/LICENSE", "/../hub.py"):
            with pytest.raises(urllib.error.HTTPError) as ei:
                urllib.request.urlopen(base + bad)
            assert ei.value.code in (403, 404)
    finally:
        httpd.shutdown()
        h.close()


def test_blank_editor_entries_and_ui_queue_and_settings(tmp_path, monkeypatch):
    import threading

    from alice_stem_reader import applaunch

    monkeypatch.setenv("ASR_APP_DIR", str(tmp_path / "support"))
    h = Hub([tmp_path], library=tmp_path / "lib")
    httpd, token = make_server(h)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def call(method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                     headers={"X-Token": token, **({"Content-Type": "application/json"} if data else {})})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    try:
        rec = call("POST", "/api/snips/new", {"format": "markdown"})
        assert rec["kind"] == "mixed" and rec["has_image"] is False and rec["source"] == {"type": "editor", "format": "markdown"}
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/api/snips/{rec['id']}.png", headers={"X-Token": token}))
        assert ei.value.code == 404
        call("POST", "/api/ui/push", {"view": "snips", "new": "latex"})
        assert call("GET", "/api/ui/pull")["commands"] == [{"view": "snips", "new": "latex"}]
        assert call("GET", "/api/ui/pull")["commands"] == []
        s = call("GET", "/api/settings")
        assert s["global_hotkeys"]["snip"] == applaunch.GLOBAL_HOTKEYS["snip"] and s["global_hotkeys_custom"] == {}  # Mathpix と同じ既定
        s = call("POST", "/api/settings", {"global_hotkeys": {"snip": "ctrl+alt+k"}, "delim": "dollar", "single": True})
        assert s["global_hotkeys"]["snip"] == "ctrl+alt+k" and s["global_hotkeys"]["open"] == applaunch.GLOBAL_HOTKEYS["open"] and s["delim"] == "dollar"
        cfg = applaunch.read_config()
        assert cfg["global_hotkeys"] == {"snip": "ctrl+alt+k"} and cfg["format"]["math_copy"] == "auto"  # 以前の delim・single から
        s = call("POST", "/api/settings", {"format": {"inline": "dollar", "display": "equation*", "multiline": "aligned"},
                                           "export": {"snip_format": "tsv", "card_linebreak": "space"}, "open_mode": "browser"})
        assert s["format"]["display"] == "equation*" and s["format"]["math_copy"] == "raw" and s["export"]["snip_format"] == "tsv"
        assert s["open_mode"] == "browser" and s["export"]["include_notes"] is True
        for bad in ({"format": {"inline": "x"}}, {"export": {"nope": 1}}, {"open_mode": "tv"}):
            with pytest.raises(urllib.error.HTTPError) as ei:
                call("POST", "/api/settings", bad)
            assert ei.value.code == 400
        for bad in ({"snip": "m"}, {"snip": "ctrl+cmd+m", "open": "ctrl+cmd+m"}, {"evil": "ctrl+x"}):  # 1文字だけ・重複・未知
            with pytest.raises(urllib.error.HTTPError) as ei:
                call("POST", "/api/settings", {"global_hotkeys": bad})
            assert ei.value.code == 400
    finally:
        httpd.shutdown()
        h.close()


def test_app_open_queues_view_when_tab_is_active(fx, tmp_path, monkeypatch):
    from alice_stem_reader import applaunch

    monkeypatch.setenv("ASR_APP_DIR", str(tmp_path / "support"))
    monkeypatch.setenv("ASR_APP_LOG", str(tmp_path / "app.log"))
    monkeypatch.setenv("ASR_LIBRARY", str(tmp_path / "lib"))
    opened = []
    try:
        applaunch.app_open(backend="none", opener=opened.append)
        st = applaunch.running_state()
        applaunch._call(st, "GET", "/api/ping")  # 画面が開いている（直前に通信があった）
        applaunch.app_open(backend="none", opener=opened.append, new="latex")
        assert len(opened) == 1  # 新しいタブは開かず、開いている画面に送る
        assert applaunch._call(st, "GET", "/api/ui/pull")["commands"] == [{"view": "snips", "new": "latex"}]
        with pytest.raises(ValueError):
            applaunch.app_open(backend="none", opener=opened.append, view="evil")
    finally:
        applaunch.app_stop()


def test_global_hotkey_defaults_agree_everywhere():
    """メニューバーのアプリ（Swift）・サーバー（Python）・画面（keys.js）で、既定の全体ショートカットが同じ（Mathpix と同じ）。"""
    import re
    from pathlib import Path

    from alice_stem_reader import applaunch

    root = Path(applaunch.__file__).resolve().parent
    swift = (root / "agent" / "snip_agent.swift").read_text(encoding="utf-8")
    keys = (root / "viewer" / "static" / "keys.js").read_text(encoding="utf-8")
    assert applaunch.MAC_HOTKEYS == {"snip": "ctrl+cmd+m", "open": "ctrl+cmd+o", "latex": "ctrl+cmd+l", "markdown": "ctrl+cmd+n"}
    assert applaunch.PC_HOTKEYS == {k: v.replace("cmd", "alt") for k, v in applaunch.MAC_HOTKEYS.items()}  # Windows・Linux は Ctrl+Alt
    assert 'IS_MAC ? "Ctrl+Cmd+" : "Ctrl+Alt+"' in keys
    for k, v in applaunch.MAC_HOTKEYS.items():
        assert f'"{k}": "{v}"' in swift
        letter = v.rsplit("+", 1)[1].upper()
        assert re.search(rf'keys: \[G \+ "{letter}"\][^\n]*hotkey: "{k}"', keys), k
    assert "RegisterEventHotKey" in swift and "--background" in swift and "LSUIElement" not in swift
