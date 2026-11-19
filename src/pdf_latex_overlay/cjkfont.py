"""日本語などの文字を不可視テキストにするための、サブセット埋め込みフォント。

- PyMuPDF に同梱の CJK フォント（Droid Sans Fallback Full、Apache-2.0）から、使う文字だけを fontTools で切り出して埋め込む。
- 文字は Identity-H（2バイトのグリフ番号）で書き、/W に実際の字幅、/ToUnicode に Unicode を持たせる。
  字幅が正確なので、矩形選択（PDFKit）でも文字が欠けない。
- 文字は不可視（描画モード 3）で使うので、見た目には影響しない。
"""

from __future__ import annotations

import io

import pikepdf
import pymupdf
from fontTools import subset, ttLib

_FONT: pymupdf.Font | None = None


def base_font() -> pymupdf.Font:
    global _FONT
    if _FONT is None:
        _FONT = pymupdf.Font("cjk")
    return _FONT


def missing_chars(text: str) -> list[str]:
    """同梱フォントにない文字（埋め込めない）。"""
    f = base_font()
    return sorted({c for c in text if c != "\n" and not f.has_glyph(ord(c))})


def _utf16_hex(c: str) -> str:
    b = c.encode("utf-16-be")
    return b.hex().upper()


class CJKFont:
    """chars（使う文字）だけを含むサブセットを作り、PDF に埋め込む。"""

    def __init__(self, pdf: pikepdf.Pdf, chars):
        chars = sorted({c for c in chars if c not in "\n\r"})
        if not chars:
            raise ValueError("埋め込む文字がない")
        base = base_font()
        absent = [c for c in chars if not base.has_glyph(ord(c))]
        if absent:
            raise ValueError(f"フォントにない文字: {''.join(absent[:10])}")
        opts = subset.Options()
        opts.layout_features = []
        opts.notdef_outline = True
        opts.glyph_names = False
        opts.hinting = False
        opts.name_IDs = [1, 2]
        opts.drop_tables += ["GSUB", "GPOS", "GDEF", "DSIG", "kern", "vhea", "vmtx", "VDMX", "LTSH", "hdmx"]
        tt = ttLib.TTFont(io.BytesIO(base.buffer))
        sb = subset.Subsetter(opts)
        sb.populate(unicodes=[ord(c) for c in chars])
        sb.subset(tt)
        buf = io.BytesIO()
        tt.save(buf)
        data = buf.getvalue()
        tt = ttLib.TTFont(io.BytesIO(data))
        cmap = tt.getBestCmap()
        upem = tt["head"].unitsPerEm
        hmtx = tt["hmtx"]
        self.gid: dict[str, int] = {}
        self.w1000: dict[str, float] = {}
        for c in chars:
            name = cmap[ord(c)]
            g = tt.getGlyphID(name)
            self.gid[c] = g
            self.w1000[c] = hmtx[name][0] * 1000.0 / upem
        self.ascent = abs(base.ascender)
        self.descent = abs(base.descender)
        hh = tt["head"]
        bbox = [round(v * 1000.0 / upem) for v in (hh.xMin, hh.yMin, hh.xMax, hh.yMax)]
        ff = pdf.make_indirect(pikepdf.Stream(pdf, data))
        ff.Length1 = len(data)
        desc = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.FontDescriptor, FontName=pikepdf.Name("/PLOSUBS+DroidSansFallback"), Flags=4,
            FontBBox=bbox, ItalicAngle=0, Ascent=round(self.ascent * 1000), Descent=-round(self.descent * 1000),
            CapHeight=700, StemV=80, FontFile2=ff))
        wids = []
        for c in sorted(chars, key=lambda x: self.gid[x]):
            wids += [self.gid[c], [round(self.w1000[c], 1)]]
        cid = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.CIDFontType2, BaseFont=pikepdf.Name("/PLOSUBS+DroidSansFallback"),
            CIDSystemInfo=pikepdf.Dictionary(Registry=pikepdf.String("Adobe"), Ordering=pikepdf.String("Identity"), Supplement=0),
            FontDescriptor=desc, CIDToGIDMap=pikepdf.Name.Identity, DW=1000, W=pikepdf.Array(wids)))
        pairs = sorted((self.gid[c], c) for c in chars)
        blocks = []
        for i in range(0, len(pairs), 100):
            chunk = pairs[i:i + 100]
            blocks.append(f"{len(chunk)} beginbfchar\n" + "\n".join(f"<{g:04X}> <{_utf16_hex(c)}>" for g, c in chunk) + "\nendbfchar")
        tounicode = ("/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
                     "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
                     "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
                     + "\n".join(blocks) + "\nendcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n")
        tu = pdf.make_indirect(pikepdf.Stream(pdf, tounicode.encode("ascii")))
        self.font = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type0, BaseFont=pikepdf.Name("/PLOSUBS+DroidSansFallback"),
            Encoding=pikepdf.Name("/Identity-H"), DescendantFonts=pikepdf.Array([cid]), ToUnicode=tu))

    def width(self, text: str) -> float:
        """フォントサイズ 1 のときの横幅（em）。"""
        return sum(self.w1000[c] for c in text) / 1000.0

    def encode(self, text: str) -> bytes:
        return b"".join(self.gid[c].to_bytes(2, "big") for c in text)
