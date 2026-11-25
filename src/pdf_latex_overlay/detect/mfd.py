"""ページ全体から数式の枠を自動で見つける（PDF-Extract-Kit MFD、YOLOv8）。

- 検出器は別 venv のワーカー（detect/mfd_worker.py）で動かす。
- 検出枠は少し広げて（積分の上端・下端の取りこぼし対策）、大きい枠に含まれる小さい枠を除く。
- 複数行のまとまり（isolated）は、行ごとの枠に分ける。1行ずつクリックしてコピーできるようにするため。

既定の場所（プロジェクト直下、Git 管理外）: .venv-ml/bin/python、models/mfd_yolo_v8/yolo_v8_ft.pt
（環境変数 PLO_ML_PYTHON、PLO_MFD_WEIGHTS で変更）。
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path

from ..osutil import no_window, venv_python

import numpy as np
import pymupdf

from ..recognize.base import RecognizerUnavailable, crop_png
from ..recognize.postprocess import line_segments

ROOT = Path(__file__).resolve().parents[3]
WORKER = Path(__file__).resolve().parent / "mfd_worker.py"

# 実教材（スキャン2ページ、手作業の正解11式）で決めた値（MODELS.md）
DEFAULT_CONF = 0.1
DEFAULT_PAD = (3.0, 6.0)  # pt（横, 縦）
DEFAULT_IMGSZ = 1888
DETECT_DPI = 200


class MFDDetector:
    name = "mfd"

    def __init__(self, weights=None, python=None, device="cpu", timeout_s: float = 300.0):
        self.weights = Path(weights or os.environ.get("PLO_MFD_WEIGHTS") or ROOT / "models" / "mfd_yolo_v8" / "yolo_v8_ft.pt")
        self.python = Path(python or os.environ.get("PLO_ML_PYTHON") or venv_python(ROOT / ".venv-ml"))
        self.device = device
        self.timeout_s = timeout_s
        self.proc = None
        self.info: dict = {}
        self._lock = threading.Lock()

    def start(self):
        if self.proc and self.proc.poll() is None:
            return
        if not self.python.exists():
            raise RecognizerUnavailable(f"ML 用 Python がない: {self.python}（docs/MODELS.md の手順で導入）")
        if not self.weights.is_file():
            raise RecognizerUnavailable(f"数式検出の重みがない: {self.weights}（docs/MODELS.md の手順で取得）")
        self.proc = subprocess.Popen([str(self.python), "-I", "-X", "utf8", str(WORKER), "--weights", str(self.weights),
                                      "--device", self.device],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", bufsize=1, **no_window())
        line = self._readline(300)
        if not line:
            self.close()
            raise RecognizerUnavailable("数式検出ワーカーが起動しなかった")
        msg = json.loads(line)
        if not msg.get("ready"):
            self.close()
            raise RecognizerUnavailable(f"数式検出を読み込めない: {msg.get('error')}")
        self.info = msg

    def _readline(self, timeout):
        out: list[str] = []
        t = threading.Thread(target=lambda: out.append(self.proc.stdout.readline()), daemon=True)
        t.start()
        t.join(timeout)
        return None if t.is_alive() else (out[0] if out else None)

    def detect(self, png: bytes, conf=DEFAULT_CONF, imgsz=DEFAULT_IMGSZ, iou=0.45) -> list[list[float]]:
        with self._lock:
            self.start()
            with tempfile.TemporaryDirectory(prefix="plo-page-") as td:
                p = Path(td) / "page.png"
                p.write_bytes(png)
                self.proc.stdin.write(json.dumps({"id": "p", "image": str(p), "conf": conf, "imgsz": imgsz, "iou": iou}) + "\n")
                self.proc.stdin.flush()
                line = self._readline(self.timeout_s)
                if line is None:
                    self.close()
                    raise RecognizerUnavailable(f"数式検出がタイムアウト（{self.timeout_s}秒）")
                msg = json.loads(line)
                if msg.get("error"):
                    raise RecognizerUnavailable(f"数式検出に失敗: {msg['error']}")
                return msg["boxes"]

    def close(self):
        if self.proc:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
            self.proc = None


def _contained(inner, outer) -> float:
    ix = max(0.0, min(inner[2], outer[2]) - max(inner[0], outer[0]))
    iy = max(0.0, min(inner[3], outer[3]) - max(inner[1], outer[1]))
    a = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return ix * iy / a if a > 0 else 0.0


def split_into_lines(page: pymupdf.Page, view_rect, dpi: int = DETECT_DPI) -> list[tuple]:
    """見た目の矩形を、行ごとの見た目の矩形に分ける（1行ならそのまま）。横幅は各行のインクに詰める。"""
    stored = pymupdf.Rect(view_rect) * page.derotation_matrix
    pad = 2.0
    png = crop_png(page, stored, dpi=dpi, pad=pad)
    img, segs = line_segments(png, dpi)
    if len(segs) <= 1:
        return [tuple(view_rect)]
    crop_view = pymupdf.Rect(view_rect[0] - pad, view_rect[1] - pad, view_rect[2] + pad, view_rect[3] + pad) & page.rect
    k = 72.0 / dpi
    dark = np.asarray(img) < 170
    out = []
    for s0, e0 in segs:
        cols = np.nonzero(dark[s0:e0].any(axis=0))[0]
        x0 = crop_view.x0 + (cols[0] if len(cols) else 0) * k - 1.0
        x1 = crop_view.x0 + (cols[-1] + 1 if len(cols) else img.width) * k + 1.0
        r = pymupdf.Rect(x0, crop_view.y0 + s0 * k - 1.0, x1, crop_view.y0 + e0 * k + 1.0) & page.rect
        out.append((r.x0, r.y0, r.x1, r.y1))
    return out


def detect_page(detector, page: pymupdf.Page, conf=DEFAULT_CONF, pad=DEFAULT_PAD, split=True) -> list[dict]:
    """1ページの数式の枠（保存座標）を、上から下・左から右の順で返す。"""
    pix = page.get_pixmap(dpi=DETECT_DPI, alpha=False)  # 見た目どおり（回転後）
    boxes = detector.detect(pix.tobytes("png"), conf=conf)
    k = 72.0 / DETECT_DPI
    cands = []
    for x0, y0, x1, y1, cls, cf in boxes:
        r = pymupdf.Rect(x0 * k - pad[0], y0 * k - pad[1], x1 * k + pad[0], y1 * k + pad[1]) & page.rect
        if r.is_empty or r.width < 3 or r.height < 3:
            continue
        cands.append({"view": (r.x0, r.y0, r.x1, r.y1), "kind": "display" if int(cls) == 1 else "inline",
                      "conf": round(float(cf), 3)})
    # 大きい枠に8割以上含まれる小さい枠は除く（まとまりの中の行内数式など）
    cands.sort(key=lambda c: -(c["view"][2] - c["view"][0]) * (c["view"][3] - c["view"][1]))
    kept: list[dict] = []
    for c in cands:
        if any(_contained(c["view"], g["view"]) >= 0.8 for g in kept):
            continue
        kept.append(c)
    out = []
    for c in kept:
        parts = split_into_lines(page, c["view"]) if split and c["kind"] == "display" else [c["view"]]
        for v in parts:
            s = pymupdf.Rect(v) * page.derotation_matrix
            out.append({"rect": (s.x0, s.y0, s.x1, s.y1), "view": v, "kind": c["kind"], "conf": c["conf"],
                        "split": len(parts) > 1})
    out.sort(key=lambda d: (round(d["view"][1] / 6), d["view"][0]))
    return out
