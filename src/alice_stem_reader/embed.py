"""確定済みの式を、見た目を変えずに選択・コピーできる文字層として追記する。

方式
- invisible: 文字描画モード 3（不可視）で LaTeX を Helvetica（WinAnsi）として描く。
- actualtext: `/Span <</ActualText (LaTeX)>> BDC ... EMC` で不可視テキストを包む。
  内側の文字は既定で LaTeX と同じ。比較用に別の ASCII 代替文字も指定できる。

既存のコンテンツストリームは書き換えず、`q` を前置・`Q` と追記分を後置する。
文字の向きはページの /Rotate を打ち消して、見た目上で左から右へ並ぶようにする。
"""

from __future__ import annotations

from dataclasses import dataclass

import pikepdf
import pymupdf

from .cjkfont import CJKFont
from .models import Formula

FONT_KEY_PREFIX = "PLOF"
_HELV = None


def _helv() -> pymupdf.Font:
    global _HELV
    if _HELV is None:
        _HELV = pymupdf.Font("helv")
    return _HELV


def is_winansi_ascii(s: str) -> bool:
    return all(0x20 <= ord(c) < 0x7F for c in s)


def layer_lines(latex: str) -> list[str]:
    """文字層に入れる行。改行で区切り、行内のタブ・連続空白は1つの空白にする（コマンドは変えない）。"""
    out = []
    for line in latex.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        t = " ".join(line.replace("\t", " ").split())
        if t:
            out.append(t)
    return out


def normalize_for_layer(latex: str) -> str:
    """比較・判定用: 行を改行でつないだ文字層の文字列。"""
    return "\n".join(layer_lines(latex))


def line_rects(page: pymupdf.Page, rect, k: int) -> list[tuple]:
    """見た目の上下方向に k 等分した帯（保存座標）。複数行の LaTeX を行ごとに置くため。"""
    if k <= 1:
        return [tuple(rect)]
    vis = pymupdf.Rect(rect) * page.rotation_matrix
    h = vis.height / k
    out = []
    for i in range(k):
        band = pymupdf.Rect(vis.x0, vis.y0 + i * h, vis.x1, vis.y0 + (i + 1) * h) * page.derotation_matrix
        out.append((band.x0, band.y0, band.x1, band.y1))
    return out


@dataclass
class Placement:
    tm: tuple[float, float, float, float, float, float]
    text: str


# 字形の高さ（Helvetica の ascent 0.718 + descent 0.207）を矩形高さのこの割合に収め、上下に余白を残す。
# 縦に近い式どうし（例: 積分の上下端で背の高い式の直下の式）が PDFKit で同じ行にまとめられ、
# 矩形選択が空になるのを防ぐ（実教材で確認、2026-11-19）。
BAND = 0.65
# 横方向の最小倍率。長い LaTeX を狭い矩形に詰めると、隣り合う同じ文字（例: "}}"）がほぼ重なり、
# MuPDF が重複字形として片方を捨てる。足りないときは文字を小さくして倍率を保つ。
MIN_HSCALE = 0.35
_ASC, _DESC = 0.718, 0.207


def placement(page: pymupdf.Page, rect, text: str, natural: float | None = None, asc: float = _ASC,
              desc: float = _DESC) -> Placement:
    """保存座標の矩形に、見た目上で横書きに収まる文字行列を作る（フォントサイズ 1 基準）。

    natural は、フォントサイズ 1 のときの文字列の横幅（省略時は Helvetica）。asc・desc はそのフォントの字形の高さ。
    """
    to_user = ~page.transformation_matrix  # 保存座標 → PDF ユーザー空間
    derot = page.derotation_matrix  # 描画座標 → 保存座標
    vis = pymupdf.Rect(rect) * page.rotation_matrix  # 見た目上の矩形
    if natural is None:
        natural = _helv().text_length(text, fontsize=1.0)
    fs = vis.height * BAND / (asc + desc)
    if natural > 0:
        fs = min(fs, vis.width / (natural * MIN_HSCALE))
    hs = vis.width / (natural * fs) if natural > 0 else 1.0
    cy = (vis.y0 + vis.y1) / 2
    base_y = cy + fs * (asc - desc) / 2  # 字形の帯を矩形の上下中央に置く

    def user(x, y):
        return pymupdf.Point(x, y) * derot * to_user

    o = user(vis.x0, base_y)
    ex = user(vis.x0 + 1, base_y) - o  # 見た目の右方向（単位長）
    ey = user(vis.x0, base_y - 1) - o  # 見た目の上方向（単位長）
    tm = (ex.x * hs * fs, ex.y * hs * fs, ey.x * fs, ey.y * fs, o.x, o.y)
    return Placement(tuple(round(v, 4) for v in tm), text)


def build_ops(font_name: pikepdf.Name, pl: Placement, mode: str, actual: str | None, inner: str | None,
              data: bytes | None = None):
    ops = []
    if mode == "actualtext":
        props = pikepdf.Dictionary(ActualText=pikepdf.String(actual if actual is not None else pl.text))
        ops.append(pikepdf.ContentStreamInstruction([pikepdf.Name.Span, props], pikepdf.Operator("BDC")))
    text = inner if (mode == "actualtext" and inner is not None) else pl.text
    ops += [
        pikepdf.ContentStreamInstruction([], pikepdf.Operator("BT")),
        pikepdf.ContentStreamInstruction([3], pikepdf.Operator("Tr")),
        pikepdf.ContentStreamInstruction([font_name, 1], pikepdf.Operator("Tf")),
        pikepdf.ContentStreamInstruction(list(pl.tm), pikepdf.Operator("Tm")),
        pikepdf.ContentStreamInstruction([pikepdf.String(data if data is not None else text.encode("ascii"))], pikepdf.Operator("Tj")),
        pikepdf.ContentStreamInstruction([], pikepdf.Operator("ET")),
    ]
    if mode == "actualtext":
        ops.append(pikepdf.ContentStreamInstruction([], pikepdf.Operator("EMC")))
    return ops


def embed_formulas(
    src_path,
    dst_path,
    formulas: list[Formula],
    mode: str = "invisible",
    actualtext_inner: str | None = None,
    should_stop=lambda: False,
) -> list[str]:
    """formulas（全て埋め込み可能と判定済み）を dst_path に書く。埋め込んだ式 ID を返す。

    src_path は読み取りのみ。should_stop が True を返したらページ単位で中断（InterruptedError）。
    """
    if mode not in ("invisible", "actualtext"):
        raise ValueError(f"未知の埋め込み方式: {mode}")
    mdoc = pymupdf.open(src_path)
    done: list[str] = []
    try:
        with pikepdf.open(src_path) as pdf:
            by_page: dict[int, list[Formula]] = {}
            for f in formulas:
                by_page.setdefault(f.page, []).append(f)
            font = pdf.make_indirect(pikepdf.Dictionary(
                Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                BaseFont=pikepdf.Name.Helvetica, Encoding=pikepdf.Name.WinAnsiEncoding,
            ))
            # 文章（日本語など）の枠: 使う文字だけのサブセットフォントを埋め込む
            chars = {c for f in formulas if f.kind == "text" for ln in layer_lines(f.latex) for c in ln}
            cj = CJKFont(pdf, chars) if chars else None
            for pno in sorted(by_page):
                if should_stop():
                    raise InterruptedError(f"ページ{pno}の前で中断")
                ppage = pdf.pages[pno]
                mpage = mdoc[pno]
                name = ppage.add_resource(font, pikepdf.Name.Font, prefix=FONT_KEY_PREFIX)
                cname = ppage.add_resource(cj.font, pikepdf.Name.Font, prefix="PLOJ") if cj and any(
                    f.kind == "text" for f in by_page[pno]) else None
                ops = []
                for f in by_page[pno]:
                    lines = layer_lines(f.latex)
                    if f.kind == "text":  # 文章: 不可視テキスト（描画モード 3）。実際の字幅で収める
                        for line, r in zip(lines, line_rects(mpage, f.rect, len(lines))):
                            pl = placement(mpage, r, line, natural=cj.width(line), asc=cj.ascent, desc=cj.descent)
                            ops += build_ops(cname, pl, "invisible", actual=line, inner=None, data=cj.encode(line))
                        done.append(f.id)
                        continue
                    for line, r in zip(lines, line_rects(mpage, f.rect, len(lines))):
                        inner_text = line if actualtext_inner is None else actualtext_inner
                        if mode == "invisible" and not is_winansi_ascii(line):
                            raise ValueError(f"{f.id}: 不可視テキストは ASCII のみ対応")
                        if actualtext_inner is not None and not is_winansi_ascii(actualtext_inner):
                            raise ValueError("代替文字は ASCII のみ")
                        layer_text = line if is_winansi_ascii(line) else "?" * max(1, len(line))
                        pl = placement(mpage, r, layer_text)
                        ops += build_ops(name, pl, mode, actual=line, inner=inner_text if mode == "actualtext" else None)
                    done.append(f.id)
                ppage.contents_add(b"q\n", prepend=True)
                ppage.contents_add(b"Q\n" + pikepdf.unparse_content_stream(ops) + b"\n", prepend=False)
            pdf.save(dst_path, object_stream_mode=pikepdf.ObjectStreamMode.preserve)
    finally:
        mdoc.close()
    return done
