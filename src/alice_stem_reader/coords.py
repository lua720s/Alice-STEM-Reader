"""座標変換。

保存座標: 回転前 CropBox 左上原点、pt（= PyMuPDF のページ座標）。
- レンダリング画像（回転後、拡大あり、切り出し原点あり）との相互変換
- PDF ユーザー空間（左下原点、MediaBox 基準）への変換（埋め込み用）
変換は矩形の四隅を写して外接矩形を取る。
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf

Rect = tuple[float, float, float, float]


def _corners(r: Rect) -> list[tuple[float, float]]:
    x0, y0, x1, y1 = r
    return [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]


def _apply(m: pymupdf.Matrix, r: Rect) -> Rect:
    pts = [pymupdf.Point(x, y) * m for x, y in _corners(r)]
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


@dataclass(frozen=True)
class RenderTransform:
    """保存座標 → レンダリング画像のピクセル座標。

    zoom = dpi / 72。clip_origin はレンダリング画像（回転後 pt 座標）での切り出し原点。
    """

    rotation_matrix: tuple[float, ...]
    zoom: float
    clip_origin: tuple[float, float] = (0.0, 0.0)

    @classmethod
    def for_page(cls, page: pymupdf.Page, dpi: float, clip_origin=(0.0, 0.0)) -> "RenderTransform":
        return cls(tuple(page.rotation_matrix), dpi / 72.0, tuple(clip_origin))

    def _matrix(self) -> pymupdf.Matrix:
        m = pymupdf.Matrix(*self.rotation_matrix)
        m = m * pymupdf.Matrix(1, 0, 0, 1, -self.clip_origin[0], -self.clip_origin[1])
        return m * pymupdf.Matrix(self.zoom, self.zoom)

    def to_pixels(self, r: Rect) -> Rect:
        return _apply(self._matrix(), r)

    def from_pixels(self, r: Rect) -> Rect:
        return _apply(~self._matrix(), r)


def stored_to_pdf_user(page: pymupdf.Page, r: Rect) -> Rect:
    """保存座標 → PDF ユーザー空間（左下原点）。埋め込み用。"""
    return _apply(~page.transformation_matrix, r)


def clamp_to_page(page: pymupdf.Page, r: Rect) -> tuple[Rect, bool]:
    """ページ（回転前 CropBox）境界へクリップする。クリップしたら True。"""
    w, h = page.cropbox.width, page.cropbox.height
    x0, y0, x1, y1 = r
    c = (max(0.0, x0), max(0.0, y0), min(w, x1), min(h, y1))
    return c, c != tuple(r)


def unrotated_size(page: pymupdf.Page) -> tuple[float, float]:
    return page.cropbox.width, page.cropbox.height


def iou(a: Rect, b: Rect) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0
