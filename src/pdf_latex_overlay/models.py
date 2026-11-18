"""共通データ構造。

座標は DESIGN.md に従い、回転前の CropBox 左上原点、単位 pt（PyMuPDF のページ座標と同じ）。
ページ番号は 0 始まり。ビューア・CLI・AliceArchive アダプターはこの JSON を共有する。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"


@dataclass
class Formula:
    """1 つの数式領域と、その LaTeX。"""

    id: str
    page: int
    rect: tuple[float, float, float, float]  # x0, y0, x1, y1（回転前 CropBox 左上原点、pt）
    latex: str | None = None  # 確定済み（埋め込みに使う）LaTeX
    ocr_latex: str | None = None  # 認識器の原文。修正しても保持する
    source: str = "manual"  # manual / fixture / unimernet / mineru / mathpix ...
    confidence: float | None = None  # 未提供なら None（欠損）
    kind: str | None = None  # inline / display / None
    status: str = "pending"  # pending / confirmed / excluded / error / embedded
    reason: str | None = None  # 除外・エラー理由
    edited: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["rect"] = [round(float(v), 3) for v in self.rect]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Formula":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        known["rect"] = tuple(float(v) for v in d["rect"])
        if len(known["rect"]) != 4:
            raise ValueError(f"rect は4要素が必要: {d.get('id')}")
        x0, y0, x1, y1 = known["rect"]
        if not (x1 > x0 and y1 > y0):
            raise ValueError(f"rect が空または反転: {d.get('id')} {known['rect']}")
        known["page"] = int(known["page"])
        return cls(**known)


@dataclass
class FormulaSet:
    """式の集合。入力 PDF のハッシュに結び付ける。"""

    formulas: list[Formula] = field(default_factory=list)
    input_sha256: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "pdf-latex-overlay/formulas",
            "version": SCHEMA_VERSION,
            "input_sha256": self.input_sha256,
            "meta": self.meta,
            "formulas": [f.to_dict() for f in self.formulas],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FormulaSet":
        if d.get("schema") not in (None, "pdf-latex-overlay/formulas"):
            raise ValueError(f"未対応のスキーマ: {d.get('schema')}")
        ver = str(d.get("version", SCHEMA_VERSION))
        if ver.split(".")[0] != SCHEMA_VERSION.split(".")[0]:
            raise ValueError(f"未対応のスキーマ版: {ver}")
        fs = [Formula.from_dict(x) for x in d.get("formulas", [])]
        ids = [f.id for f in fs]
        if len(ids) != len(set(ids)):
            raise ValueError("式 ID が重複している")
        return cls(formulas=fs, input_sha256=d.get("input_sha256"), meta=d.get("meta", {}))

    @classmethod
    def load(cls, path: str | Path) -> "FormulaSet":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def save(self, path: str | Path) -> None:
        p = Path(path)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
