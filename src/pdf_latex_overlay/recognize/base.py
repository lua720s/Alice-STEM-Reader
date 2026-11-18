"""認識バックエンドの共通部分と、式領域の切り出し。

OCR 結果はデータとして扱い、LaTeX を実行・評価しない。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pymupdf


class RecognizerUnavailable(RuntimeError):
    """モデルや依存が揃っていない（モデル不足は失敗として報告する）。"""


@dataclass
class Recognition:
    latex: str | None
    confidence: float | None = None  # 未提供は None
    error: str | None = None


class Backend(Protocol):
    name: str
    version: str

    def recognize(self, images_png: list[bytes]) -> list[Recognition]: ...


def crop_png(page: pymupdf.Page, rect, dpi: int = 200, pad: float = 2.0) -> bytes:
    """保存座標の矩形を、見た目どおり（回転後・正立）の PNG として切り出す。"""
    view = pymupdf.Rect(rect) * page.rotation_matrix
    view = pymupdf.Rect(view.x0 - pad, view.y0 - pad, view.x1 + pad, view.y1 + pad) & page.rect
    pix = page.get_pixmap(dpi=dpi, clip=view, colorspace=pymupdf.csRGB, alpha=False)
    return pix.tobytes("png")


class NoneBackend:
    name = "none"
    version = "-"

    def recognize(self, images_png):
        raise RecognizerUnavailable("認識バックエンドが設定されていない（--backend unimernet を指定）")


class FixedBackend:
    """試験用: 決まった答えを順に返す。"""

    name = "fixed"
    version = "test"

    def __init__(self, answers: list[str | None]):
        self.answers = list(answers)
        self.calls = 0

    def recognize(self, images_png):
        out = []
        for _ in images_png:
            a = self.answers[self.calls % len(self.answers)] if self.answers else None
            self.calls += 1
            out.append(Recognition(latex=a, confidence=None))
        return out


def get_backend(name: str, **kw) -> Backend:
    if name in (None, "", "none"):
        return NoneBackend()
    if name == "unimernet":
        from .unimernet import UniMERNetBackend

        return UniMERNetBackend(**kw)
    raise ValueError(f"未知の認識バックエンド: {name}")
