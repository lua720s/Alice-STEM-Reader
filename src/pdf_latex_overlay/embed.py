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


def normalize_for_layer(latex: str) -> str:
    """文字層に入れる文字列。改行・タブを空白へ（中身のコマンドは変えない）。"""
    return " ".join(latex.replace("\r", " ").replace("\n", " ").replace("\t", " ").split(" ")).strip()


@dataclass
class Placement:
    tm: tuple[float, float, float, float, float, float]
    text: str


def placement(page: pymupdf.Page, rect, text: str) -> Placement:
    """保存座標の矩形に、見た目上で横書きに収まる文字行列を作る（フォントサイズ 1 基準）。"""
    to_user = ~page.transformation_matrix  # 保存座標 → PDF ユーザー空間
    derot = page.derotation_matrix  # 描画座標 → 保存座標
    vis = pymupdf.Rect(rect) * page.rotation_matrix  # 見た目上の矩形
    h = vis.height
    fs = h  # 文字の高さ ≒ 矩形の高さ
    base_y = vis.y1 - 0.2 * h  # Helvetica の descender 分だけ上げる

    def user(x, y):
        return pymupdf.Point(x, y) * derot * to_user

    o = user(vis.x0, base_y)
    ex = user(vis.x0 + 1, base_y) - o  # 見た目の右方向（単位長）
    ey = user(vis.x0, base_y - 1) - o  # 見た目の上方向（単位長）
    natural = _helv().text_length(text, fontsize=1.0)
    hs = vis.width / natural if natural > 0 else 1.0
    tm = (ex.x * hs, ex.y * hs, ey.x * fs, ey.y * fs, o.x, o.y)
    return Placement(tuple(round(v, 4) for v in tm), text)


def build_ops(font_name: pikepdf.Name, pl: Placement, mode: str, actual: str | None, inner: str | None):
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
        pikepdf.ContentStreamInstruction([pikepdf.String(text.encode("ascii"))], pikepdf.Operator("Tj")),
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
            for pno in sorted(by_page):
                if should_stop():
                    raise InterruptedError(f"ページ{pno}の前で中断")
                ppage = pdf.pages[pno]
                mpage = mdoc[pno]
                name = ppage.add_resource(font, pikepdf.Name.Font, prefix=FONT_KEY_PREFIX)
                ops = []
                for f in by_page[pno]:
                    text = normalize_for_layer(f.latex)
                    if mode == "invisible" or actualtext_inner is not None:
                        inner_ok = is_winansi_ascii(text if actualtext_inner is None else actualtext_inner)
                    else:
                        inner_ok = True
                    layer_text = text if is_winansi_ascii(text) else "?" * max(1, len(text))
                    if not inner_ok:
                        raise ValueError(f"{f.id}: 不可視テキストは ASCII のみ対応")
                    pl = placement(mpage, f.rect, layer_text)
                    ops += build_ops(name, pl, mode, actual=text, inner=actualtext_inner)
                    done.append(f.id)
                ppage.contents_add(b"q\n", prepend=True)
                ppage.contents_add(b"Q\n" + pikepdf.unparse_content_stream(ops) + b"\n", prepend=False)
            pdf.save(dst_path, object_stream_mode=pikepdf.ObjectStreamMode.preserve)
    finally:
        mdoc.close()
    return done
