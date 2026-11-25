"""合成フィクスチャの生成。

著作物を使わず、テストに必要な条件（ベクター・画像化、回転 0/90/180/270、非ゼロ CropBox、
数式なし、インライン・表示数式、本文との重なり、境界クリップ、既存文字層との競合、
長い LaTeX、逆斜線・括弧）を含む PDF と、期待式・矩形の JSON を作る。

手順: まず「見た目のページ」（回転なし）を作り、それを目標ページへ回転付きで配置する。
期待矩形は見た目の座標を derotation で保存座標（回転前 CropBox 左上原点）に変換して得る。
数式画像は matplotlib の mathtext で描く（TeX 不要）。
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .models import Formula, FormulaSet

A5 = (420.0, 595.0)

# mathtext で描ける LaTeX。期待値そのもの。
FORMULAS: dict[str, tuple[str, str]] = {
    "quad": (r"x=\frac{-b\pm\sqrt{b^{2}-4ac}}{2a}", "display"),
    "integral": (r"\int_{0}^{1}x^{2}\,dx=\frac{1}{3}", "display"),
    "sum": (r"\sum_{k=1}^{n}k=\frac{n(n+1)}{2}", "display"),
    "inline_a": (r"a^{2}+b^{2}=c^{2}", "inline"),
    "euler": (r"e^{i\pi}+1=0", "display"),
    "long": (r"\left(\sum_{i=1}^{n}a_{i}b_{i}\right)^{2}\leq\left(\sum_{i=1}^{n}a_{i}^{2}\right)\left(\sum_{i=1}^{n}b_{i}^{2}\right)", "display"),
    "limit": (r"\lim_{x\to 0}\frac{\sin x}{x}=1", "display"),
    "deriv": (r"\frac{d}{dx}\{\ln x\}=\frac{1}{x}", "display"),
    "clip": (r"f(x)=\sqrt{1-x^{2}}", "display"),
    "overlap": (r"\alpha+\beta=\gamma", "inline"),
}


def render_formula_png(latex: str, fontsize: float = 12, dpi: int = 300) -> tuple[bytes, float, float]:
    """mathtext で数式 PNG を作る。戻り値は (png, 幅pt, 高さpt)。"""
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import mathtext
    from matplotlib.font_manager import FontProperties

    matplotlib.rcParams["mathtext.fontset"] = "cm"  # 教科書に近い Computer Modern
    buf = io.BytesIO()
    mathtext.math_to_image(f"${latex}$", buf, prop=FontProperties(size=fontsize), dpi=dpi, format="png")
    png = buf.getvalue()
    pix = pymupdf.Pixmap(png)
    return png, pix.width * 72.0 / dpi, pix.height * 72.0 / dpi


@dataclass
class VisualPage:
    """回転なしの見た目ページ。formulas は見た目座標の矩形。"""

    width: float
    height: float
    rotation: int = 0
    crop_offset: tuple[float, float] | None = None  # 非ゼロ CropBox（MediaBox を広げて切り出す）
    items: list[dict] = field(default_factory=list)


BODY = (
    "This synthetic page tests copyable LaTeX overlays. The body text is ordinary "
    "selectable text and must stay unchanged after processing."
)


def _page_specs() -> list[VisualPage]:
    W, H = A5
    pages: list[VisualPage] = []
    # p0: 表示数式・インライン・既存文字層との競合・本文との重なり・長い式
    p = VisualPage(W, H)
    p.items += [
        {"t": "text", "pos": (40, 50), "s": "1. Synthetic fixture: formulas", "size": 14},
        {"t": "text", "pos": (40, 75), "s": BODY[:70], "size": 9},
        {"t": "text", "pos": (40, 88), "s": BODY[70:], "size": 9},
        {"t": "formula", "id": "p0_quad", "key": "quad", "pos": (90, 110), "size": 13},
        {"t": "text", "pos": (40, 180), "s": "Pythagoras says", "size": 9},
        {"t": "formula", "id": "p0_inline", "key": "inline_a", "pos": (112, 171), "size": 9},
        {"t": "text", "pos": (175, 180), "s": "for right triangles.", "size": 9},
        {"t": "formula", "id": "p0_long", "key": "long", "pos": (40, 210), "size": 11},
        {"t": "text", "pos": (40, 290), "s": "Text-layer formula: E=mc^2 (already selectable)", "size": 9},
        {"t": "textformula", "id": "p0_textlayer", "latex": "E=mc^2", "line_pos": (40, 290),
         "prefix": "Text-layer formula: ", "s": "E=mc^2", "size": 9},
        {"t": "text", "pos": (40, 330), "s": "Overlapping text line runs into the formula here", "size": 9},
        {"t": "formula", "id": "p0_overlap", "key": "overlap", "pos": (200, 320), "size": 9},
        {"t": "text", "pos": (40, 380), "s": "日本語の本文行：数式の前後でコピーが混ざらないこと。", "size": 9, "font": "japan"},
        {"t": "formula", "id": "p0_deriv", "key": "deriv", "pos": (120, 400), "size": 12},
    ]
    pages.append(p)
    # p1: 数式なし
    p = VisualPage(W, H)
    p.items += [{"t": "text", "pos": (40, 60 + 14 * i), "s": f"Line {i}: no formulas on this page.", "size": 9} for i in range(10)]
    pages.append(p)
    # p2-p4: 回転 90/180/270（見た目は正立）
    for rot, key in ((90, "integral"), (180, "sum"), (270, "euler")):
        p = VisualPage(W, H, rotation=rot)
        p.items += [
            {"t": "text", "pos": (40, 50), "s": f"Rotated page /Rotate {rot}", "size": 12},
            {"t": "formula", "id": f"p_rot{rot}", "key": key, "pos": (100, 90), "size": 13},
        ]
        pages.append(p)
    # p5: 非ゼロ CropBox と境界クリップ
    p = VisualPage(W, H, crop_offset=(37.0, 23.0))
    p.items += [
        {"t": "text", "pos": (40, 50), "s": "Non-zero CropBox origin", "size": 12},
        {"t": "formula", "id": "p5_limit", "key": "limit", "pos": (100, 90), "size": 13},
        {"t": "formula", "id": "p5_clip", "key": "clip", "pos": (W - 45, 200), "size": 13},
    ]
    pages.append(p)
    return pages


def _draw_visual(page: pymupdf.Page, vp: VisualPage, cache: dict) -> list[dict]:
    """見た目ページに描画し、数式の見た目矩形を返す。"""
    placed = []
    for it in vp.items:
        if it["t"] == "text":
            page.insert_text(it["pos"], it["s"], fontsize=it["size"], fontname=it.get("font", "helv"))
        elif it["t"] == "textformula":
            font = pymupdf.Font("helv")
            x = it["line_pos"][0] + font.text_length(it["prefix"], fontsize=it["size"])
            y = it["line_pos"][1]
            w = font.text_length(it["s"], fontsize=it["size"])
            # 本文行の一部としてすでに描かれている（同じ位置の文字列）
            r = pymupdf.Rect(x - 0.5, y - it["size"] * 0.9, x + w + 0.5, y + it["size"] * 0.25)
            placed.append({"id": it["id"], "latex": it["latex"], "rect": r, "kind": "inline", "textlayer": True})
        else:
            latex, kind = FORMULAS[it["key"]]
            if (latex, it["size"]) not in cache:
                cache[(latex, it["size"])] = render_formula_png(latex, it["size"])
            png, w, h = cache[(latex, it["size"])]
            r = pymupdf.Rect(it["pos"][0], it["pos"][1], it["pos"][0] + w, it["pos"][1] + h)
            page.insert_image(r, stream=png)
            placed.append({"id": it["id"], "latex": latex, "rect": r, "kind": kind, "textlayer": False})
    return placed


def build(out_dir: str | Path, scan_dpi: int = 150) -> dict[str, Path]:
    """vector.pdf / scan.pdf と期待 JSON を作る。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    specs = _page_specs()
    visual = pymupdf.open()
    placed_all = []
    cache: dict = {}
    for vp in specs:
        pg = visual.new_page(width=vp.width, height=vp.height)
        placed_all.append(_draw_visual(pg, vp, cache))

    # 画像化した見た目ページ
    visual_scan = pymupdf.open()
    for i, vp in enumerate(specs):
        pix = visual[i].get_pixmap(dpi=scan_dpi, colorspace=pymupdf.csGRAY, alpha=False)
        pg = visual_scan.new_page(width=vp.width, height=vp.height)
        pg.insert_image(pg.rect, stream=pix.tobytes("png"))

    results = {}
    for variant, src in (("vector", visual), ("scan", visual_scan)):
        doc = pymupdf.open()
        fs = FormulaSet(meta={"fixture": variant, "generator": "alice_stem_reader.fixtures"})
        expect: dict[str, str] = {}
        for i, vp in enumerate(specs):
            rot = vp.rotation
            uw, uh = (vp.width, vp.height) if rot in (0, 180) else (vp.height, vp.width)
            if vp.crop_offset:
                ox, oy = vp.crop_offset
                pg = doc.new_page(width=uw + 2 * ox, height=uh + 2 * oy)
                pg.set_cropbox(pymupdf.Rect(ox, oy, ox + uw, oy + uh))
            else:
                pg = doc.new_page(width=uw, height=uh)
            pg.set_rotation(rot)
            pg.show_pdf_page(pymupdf.Rect(0, 0, uw, uh), src, i, rotate=rot)
            for f in placed_all[i]:
                stored = f["rect"] * pg.derotation_matrix
                formula = Formula(
                    id=f["id"], page=i, rect=tuple(stored), latex=f["latex"], ocr_latex=None,
                    source="fixture", kind=f["kind"], status="confirmed",
                )
                fs.formulas.append(formula)
                if f["id"] == "p5_clip":
                    expect[f["id"]] = "exclude:clipped"
                elif variant == "vector" and (f["textlayer"] or f["id"] == "p0_overlap"):
                    expect[f["id"]] = "exclude:existing_text_conflict"
                else:
                    expect[f["id"]] = "embed"
        pdf_path = out / f"{variant}.pdf"
        doc.save(pdf_path, garbage=3, deflate=True)
        doc.close()
        fs.meta["expect"] = expect
        fs.meta["visual_rects"] = {f["id"]: list(f["rect"]) for pl in placed_all for f in pl}
        fs.save(out / f"{variant}.formulas.json")
        results[variant] = pdf_path
        results[f"{variant}_json"] = out / f"{variant}.formulas.json"
    visual.save(out / "visual.pdf")
    results["visual"] = out / "visual.pdf"
    return results


if __name__ == "__main__":  # pragma: no cover
    import sys

    print(json.dumps({k: str(v) for k, v in build(sys.argv[1] if len(sys.argv) > 1 else "outputs/fixtures").items()}, indent=2))
