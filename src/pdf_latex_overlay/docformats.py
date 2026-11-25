"""PDF 以外の資料を、ビューアで開ける PDF に変換する（変換結果はライブラリにキャッシュする）。

対応する形式
- PDF: そのまま使う（変換しない）。
- 電子書籍など（EPUB・MOBI・FB2・XPS/OXPS・CBZ・SVG）: MuPDF でレイアウトして PDF にする。文字は選択・抽出できる。
- 画像（PNG・JPEG・TIFF〔複数ページ可〕・BMP・GIF・WebP・HEIC など）: 1 枚 1 ページの PDF にする。
  スマートフォンで撮った手書きノートなどのため、EXIF の向きを補正する。
- DjVu: DjVuLibre の `ddjvu` で PDF にする（`brew install djvulibre`）。
- Markdown・テキスト: 原文をそのまま組版する（数式は `$…$`・`\\(…\\)` の原文のまま）。
  文字が PDF の文字層に入るので、範囲を Snip すると、OCR を使わずに原文（LaTeX）がそのまま取れる。
- LaTeX（.tex）: TeX が入っていればコンパイルして、組版済みの PDF にする（数式は OCR で LaTeX に戻せる）。
  TeX がない・コンパイルに失敗したときは、原文をそのまま組版する（理由は変換記録に残す）。

変換結果は `library/_converted/<元ファイルのハッシュ先頭16文字>.pdf` に置き、同じ中身なら作り直さない。
元のファイルは読むだけで、変えない。変換の記録（使った道具・版・日時）を同じ名前の `.json` に残す。
"""

from __future__ import annotations

import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pymupdf

from . import safety

PDF = {".pdf"}
EBOOK = {".epub", ".mobi", ".fb2", ".xps", ".oxps", ".cbz", ".svg"}
IMAGE = {".png", ".jpg", ".jpeg", ".jpe", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".jp2", ".jpx",
         ".pnm", ".pgm", ".ppm", ".pbm", ".heic", ".heif"}
DJVU = {".djvu", ".djv"}
MARKDOWN = {".md", ".markdown", ".mdown", ".mkd"}
LATEX = {".tex", ".latex", ".ltx"}
TEXT = {".txt"}
SUPPORTED = PDF | EBOOK | IMAGE | DJVU | MARKDOWN | LATEX | TEXT

KINDS = {**{e: "pdf" for e in PDF}, **{e: "ebook" for e in EBOOK}, **{e: "image" for e in IMAGE},
         **{e: "djvu" for e in DJVU}, **{e: "markdown" for e in MARKDOWN}, **{e: "latex" for e in LATEX},
         **{e: "text" for e in TEXT}}

PAGE = pymupdf.paper_rect("a4")
MARGIN = 56  # pt（約 2cm）
TEX_TIMEOUT_S = 180
DJVU_TIMEOUT_S = 900
CONVERTER_VERSION = 1  # 変換の手順を変えたら上げる（古いキャッシュを使わない）


class ConversionError(RuntimeError):
    """変換できなかった（必要な道具がない、壊れたファイルなど）。メッセージは画面にそのまま出す。"""


def kind_of(path) -> str | None:
    return KINDS.get(Path(path).suffix.lower())


def is_supported(path) -> bool:
    return kind_of(path) is not None


def needs_conversion(path) -> bool:
    k = kind_of(path)
    return k is not None and k != "pdf"


def converted_dir(library=None) -> Path:
    from .prepare import library_root

    return library_root(library) / "_converted"


def converted_path(src_sha: str, library=None) -> Path:
    return converted_dir(library) / f"{src_sha[:16]}.v{CONVERTER_VERSION}.pdf"


def cached_pdf(path, src_sha: str | None, library=None) -> Path | None:
    """変換済みの PDF があれば、その場所。PDF ならそのもの。なければ None（変換はしない）。"""
    p = Path(path)
    if not needs_conversion(p):
        return p
    if not src_sha:
        return None
    out = converted_path(src_sha, library)
    return out if out.is_file() else None


def as_pdf(path, library=None, src_sha: str | None = None) -> Path:
    """ビューアで開ける PDF の場所を返す。PDF 以外は変換して、キャッシュに置く。"""
    src = Path(path)
    kind = kind_of(src)
    if kind is None:
        raise ConversionError(f"対応していない形式: {src.suffix or src.name}")
    if kind == "pdf":
        return src
    if not src.is_file():
        raise ConversionError(f"ファイルが見つからない: {src.name}")
    sha = src_sha or safety.sha256_file(src)
    out = converted_path(sha, library)
    if out.is_file():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    data, info = CONVERTERS[kind](src)
    doc = pymupdf.open("pdf", data)
    try:
        if doc.page_count == 0:
            raise ConversionError(f"ページがない: {src.name}")
        doc.set_metadata({"title": src.stem, "producer": "Alice STEM Reader (docformats)", "creator": info.get("method", kind)})
        tmp = out.with_name(out.name + f".{os.getpid()}.tmp")
        doc.save(tmp, garbage=3, deflate=True, no_new_id=True)
        pages = doc.page_count
    finally:
        doc.close()
    os.replace(tmp, out)
    record = {"source_name": src.name, "source_sha256": sha, "kind": kind, "pages": pages,
              "converted": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "elapsed_s": round(time.monotonic() - t0, 2),
              "converter_version": CONVERTER_VERSION, "pymupdf": pymupdf.VersionBind, **info}
    out.with_suffix(".json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def conversion_info(pdf_path) -> dict:
    """変換の記録（変換した PDF なら）。なければ空。"""
    p = Path(pdf_path).with_suffix(".json")
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() and p.parent.name == "_converted" else {}
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- 変換の実装
def _ebook(src: Path) -> tuple[bytes, dict]:
    try:
        doc = pymupdf.open(src)
    except Exception as e:  # 壊れたファイル・未対応の中身
        raise ConversionError(f"開けなかった（{src.suffix}）: {e}") from e
    try:
        if doc.is_reflowable:  # 電子書籍は、紙の大きさを決めてレイアウトする
            doc.layout(rect=pymupdf.paper_rect("a5"), fontsize=11)
        return doc.convert_to_pdf(), {"method": f"mupdf:{src.suffix.lower().lstrip('.')}"}
    finally:
        doc.close()


def _load_images(src: Path) -> list:
    from PIL import Image, ImageOps

    try:
        if src.suffix.lower() in (".heic", ".heif"):
            try:
                import pillow_heif  # type: ignore

                pillow_heif.register_heif_opener()
            except ImportError:
                return _heic_via_sips(src)
        im = Image.open(src)
    except Exception as e:
        raise ConversionError(f"画像を開けなかった: {src.name}（{e}）") from e
    frames = []
    for i in range(getattr(im, "n_frames", 1)):
        im.seek(i)
        fr = ImageOps.exif_transpose(im.copy())  # 撮影したときの向きに直す
        frames.append(fr.convert("RGB") if fr.mode not in ("RGB", "L") else fr)
        if src.suffix.lower() == ".gif":  # GIF は最初の 1 枚だけ（アニメーションは対象外）
            break
    return frames


def _heic_via_sips(src: Path) -> list:
    from PIL import Image

    if sys.platform != "darwin" or not shutil.which("sips"):
        raise ConversionError("HEIC を開くには pillow-heif が必要（pip install pillow-heif）。macOS では sips でも開ける")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "x.png"
        r = subprocess.run(["sips", "-s", "format", "png", str(src), "--out", str(out)], capture_output=True, timeout=120)
        if r.returncode != 0 or not out.is_file():
            raise ConversionError(f"HEIC を変換できなかった: {src.name}")
        return [Image.open(out).convert("RGB")]


def images_to_pdf(frames: list) -> bytes:
    """画像を 1 枚 1 ページの PDF にする。ページの大きさは、長い辺が A4 の長辺（842pt）程度になるようにする。"""
    doc = pymupdf.open()
    for im in frames:
        w, h = im.size
        scale = 842.0 / max(w, h) if max(w, h) > 842 else 1.0  # 小さい画像は 1px = 1pt
        buf = io.BytesIO()
        if im.mode == "L" or im.mode == "1":
            im.convert("L").save(buf, format="PNG", optimize=True)
        else:
            im.save(buf, format="JPEG", quality=92, subsampling=0)
        page = doc.new_page(width=w * scale, height=h * scale)
        page.insert_image(page.rect, stream=buf.getvalue())
    try:
        return doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()


def _image(src: Path) -> tuple[bytes, dict]:
    frames = _load_images(src)
    if not frames:
        raise ConversionError(f"画像がない: {src.name}")
    return images_to_pdf(frames), {"method": "image", "frames": len(frames)}


def _djvu(src: Path) -> tuple[bytes, dict]:
    exe = shutil.which("ddjvu")
    if not exe:
        raise ConversionError("DjVu を開くには DjVuLibre が必要（macOS: brew install djvulibre、Debian/Ubuntu: apt install djvulibre-bin）")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out.pdf"
        try:
            r = subprocess.run([exe, "-format=pdf", "-quality=85", str(src), str(out)], capture_output=True, text=True,
                               timeout=DJVU_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise ConversionError(f"DjVu の変換が {DJVU_TIMEOUT_S} 秒で終わらなかった")
        if r.returncode != 0 or not out.is_file():
            raise ConversionError(f"DjVu を変換できなかった: {(r.stderr or '').strip()[:300]}")
        return out.read_bytes(), {"method": "ddjvu", "tool": exe}


# ---- Markdown・テキスト（原文をそのまま組版する）
_MATH_RE = re.compile(r"\$\$.+?\$\$|\\\[.+?\\\]|\\\(.+?\\\)|(?<![\\$])\$(?!\s)[^$\n]+?(?<!\s)\$", re.S)
CSS = """
* { font-family: sans-serif; }
body { font-size: 10.5pt; line-height: 1.55; }
h1 { font-size: 17pt; margin: 4pt 0 8pt 0; } h2 { font-size: 14pt; margin: 10pt 0 6pt 0; }
h3 { font-size: 12pt; margin: 8pt 0 4pt 0; } h4, h5, h6 { font-size: 11pt; }
p { margin: 0 0 6pt 0; }
pre, code { font-family: monospace; font-size: 9pt; }
pre { white-space: pre-wrap; background-color: #f4f4f4; padding: 4pt; margin: 0 0 6pt 0; }
blockquote { margin: 0 0 6pt 12pt; color: #444; }
table { border-collapse: collapse; } td, th { border: 1px solid #999; padding: 2pt 4pt; }
.math { font-family: monospace; color: #0b3d91; }
.note { font-size: 8pt; color: #777; }
"""


def _protect_math(text: str) -> tuple[str, list[str]]:
    keep: list[str] = []

    def sub(m):
        keep.append(m.group(0))
        return f"\x00M{len(keep) - 1}\x00"

    return _MATH_RE.sub(sub, text), keep


def _restore_math(s: str, keep: list[str]) -> str:
    def one(m):
        src = keep[int(m.group(1))]
        if src.startswith(("$$", "\\[")):  # 表示数式は、改行を保って段落として置く
            return f'<pre class="math">{html.escape(src, quote=False)}</pre>'
        return f'<span class="math">{html.escape(src, quote=False)}</span>'

    return re.sub(r"\x00M(\d+)\x00", one, s)


def markdown_to_html(text: str) -> str:
    """Markdown を HTML にする。数式（`$…$`・`$$…$$`・`\\(…\\)`・`\\[…\\]`）は原文のまま残す。"""
    body, keep = _protect_math(text)
    try:
        import markdown  # type: ignore

        out = markdown.markdown(body, extensions=["extra", "sane_lists"], output_format="html")
    except ImportError:
        out = _mini_markdown(body)
    return _restore_math(out, keep)


def _mini_markdown(text: str) -> str:
    """markdown パッケージがないときの、最小限の変換（見出し・段落・コード・箇条書き）。"""
    out, para, code, lst = [], [], None, None

    def flush():
        nonlocal para
        if para:
            out.append("<p>" + "<br>".join(html.escape(x, quote=False) for x in para) + "</p>")
            para = []

    for line in text.splitlines():
        if code is not None:
            if line.strip().startswith("```"):
                out.append("<pre>" + html.escape("\n".join(code), quote=False) + "</pre>")
                code = None
            else:
                code.append(line)
            continue
        if line.strip().startswith("```"):
            flush()
            code = []
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush()
            n = len(m.group(1))
            out.append(f"<h{n}>{html.escape(m.group(2), quote=False)}</h{n}>")
            continue
        m = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$", line)
        if m:
            flush()
            out.append(f"<p>• {html.escape(m.group(1), quote=False)}</p>")
            continue
        if not line.strip():
            flush()
            continue
        para.append(line)
    if code is not None:
        out.append("<pre>" + html.escape("\n".join(code), quote=False) + "</pre>")
    flush()
    return "\n".join(out)


def html_to_pdf(body_html: str, title: str | None = None) -> bytes:
    story = pymupdf.Story(html=f"<body>{body_html}</body>", user_css=CSS)
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    where = PAGE + (MARGIN, MARGIN, -MARGIN, -MARGIN)
    more, guard = 1, 0
    while more:
        dev = writer.begin_page(PAGE)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
        guard += 1
        if guard > 5000:
            break
    writer.close()
    return buf.getvalue()


def _read_text(src: Path) -> str:
    raw = src.read_bytes()
    for enc in ("utf-8-sig", "cp932", "euc_jp", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _markdown(src: Path) -> tuple[bytes, dict]:
    return html_to_pdf(markdown_to_html(_read_text(src)), src.stem), {"method": "markdown:source"}


def _text(src: Path) -> tuple[bytes, dict]:
    return html_to_pdf("<pre>" + html.escape(_read_text(src), quote=False) + "</pre>"), {"method": "text:source"}


# ---- LaTeX（TeX があればコンパイル、なければ原文を組版）
_JCLASS_RE = re.compile(r"\\documentclass(\[[^\]]*\])?\{(j[a-z]*article|j[a-z]*book|j[a-z]*report|jarticle|jbook|jreport|tarticle)\}")


def tex_engines() -> list[str]:
    return [e for e in ("tectonic", "lualatex", "xelatex", "pdflatex") if shutil.which(e)]


def _compile_tex(src: Path) -> tuple[bytes, dict] | tuple[None, str]:
    """コンパイルできれば (PDF, 記録)。できなければ (None, 理由)。シェルの実行（\\write18）は許可しない。"""
    text = _read_text(src)
    if "\\documentclass" not in text and "\\begin{document}" not in text:
        return None, "完全な文書ではない（\\documentclass がない）"
    plans = []
    if _JCLASS_RE.search(text) and shutil.which("uplatex") and shutil.which("dvipdfmx"):
        plans.append("uplatex")
    plans += tex_engines()
    if not plans:
        return None, "TeX（lualatex・xelatex・pdflatex・tectonic）が見つからない"
    errors = []
    for eng in plans:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            try:
                if eng == "tectonic":
                    cmds = [["tectonic", "--outdir", td, "--untrusted", str(src)]]
                elif eng == "uplatex":
                    base = ["uplatex", "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape", f"-output-directory={td}", src.name]
                    cmds = [base, base, ["dvipdfmx", "-o", str(out / (src.stem + ".pdf")), str(out / (src.stem + ".dvi"))]]
                else:
                    base = [eng, "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape", f"-output-directory={td}", src.name]
                    cmds = [base, base]  # 相互参照のため 2 回
                ok = True
                for c in cmds:
                    r = subprocess.run(c, cwd=src.parent, capture_output=True, text=True, timeout=TEX_TIMEOUT_S,
                                       env={**os.environ, "openout_any": "p", "openin_any": "a"})
                    if r.returncode != 0:
                        tail = [ln for ln in (r.stdout + r.stderr).splitlines() if ln.startswith("!")][:3]
                        errors.append(f"{eng}: " + (" / ".join(tail) or f"終了コード {r.returncode}"))
                        ok = False
                        break
                pdf = out / (src.stem + ".pdf")
                if ok and pdf.is_file():
                    return pdf.read_bytes(), {"method": f"tex:{eng}", "tool": shutil.which(eng if eng != "uplatex" else "uplatex")}
            except subprocess.TimeoutExpired:
                errors.append(f"{eng}: {TEX_TIMEOUT_S} 秒で終わらなかった")
            except OSError as e:
                errors.append(f"{eng}: {e}")
    return None, "; ".join(errors)[:600]


def _latex(src: Path) -> tuple[bytes, dict]:
    data, info = _compile_tex(src)
    if data is not None:
        return data, info
    reason = info
    note = f'<p class="note">LaTeX の原文を表示しています（コンパイルしなかった理由: {html.escape(reason)}）。</p>'
    body = note + "<pre>" + html.escape(_read_text(src), quote=False) + "</pre>"
    return html_to_pdf(body), {"method": "latex:source", "compile_error": reason}


def _screen_capture():
    from .osutil import screen_capture_available

    return screen_capture_available()


CONVERTERS = {"ebook": _ebook, "image": _image, "djvu": _djvu, "markdown": _markdown, "latex": _latex, "text": _text}


def describe_support() -> dict:
    """この環境で使える形式と、足りない道具（画面と `formats` コマンドで表示する）。"""
    try:
        import pillow_heif  # type: ignore  # noqa: F401

        heic = True
    except ImportError:
        heic = sys.platform == "darwin" and bool(shutil.which("sips"))
    return {
        "extensions": sorted(SUPPORTED),
        "djvu": bool(shutil.which("ddjvu")),
        "tex_engines": tex_engines() + (["uplatex"] if shutil.which("uplatex") and shutil.which("dvipdfmx") else []),
        "heic": heic,
        "screen_capture": _screen_capture(),
        "platform": sys.platform,
    }


__all__ = ["SUPPORTED", "ConversionError", "as_pdf", "cached_pdf", "conversion_info", "describe_support",
           "is_supported", "kind_of", "needs_conversion", "markdown_to_html", "images_to_pdf"]
