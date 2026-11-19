"""日本語などの文章を読む（PaddleOCR / PP-OCR）。数式は読まず、数式の枠は塗りつぶして除く。

- 数式は、これまでどおり UniMERNet などで LaTeX にする。文章だけをここで読む。
- 結果は、種類 `kind="text"` の枠（Formula）に、読んだ文字を `latex` 欄に入れて保存する（画面でコピー・修正できる）。
- 派生PDFには、文章は埋め込まない（今の埋め込みは ASCII のみ。理由 `text_not_embedded` で除外する）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

import pymupdf

from .base import RecognizerUnavailable

ROOT = Path(__file__).resolve().parents[3]
WORKER = Path(__file__).resolve().parent / "ppocr_worker.py"
TEXT_DPI = 150
MIN_SCORE = 0.5
_CJK = r"\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff66-\uff9f"


def looks_like_text(s: str) -> bool:
    """文章らしいか。日本語の文字か、英単語（英字3つ以上の連なり）を含む行だけ。数字・記号・数式の断片は文章としない。"""
    return bool(re.search(f"[{_CJK}]", s) or re.search(r"[A-Za-z]{3,}", s))


class PPOCRBackend:
    name = "ppocr"

    def __init__(self, cache_dir=None, python=None, lang=None, timeout_s: float = 300.0):
        self.cache_dir = Path(cache_dir or os.environ.get("PLO_PP_CACHE") or ROOT / "models" / "pp_cache")
        self.python = Path(python or os.environ.get("PLO_PP_PYTHON") or ROOT / ".venv-pp" / "bin" / "python")
        self.lang = lang or os.environ.get("PLO_OCR_LANG") or "japan"
        self.timeout_s = timeout_s
        self.proc: subprocess.Popen | None = None
        self.info: dict = {}
        self._lock = threading.Lock()
        self.version = f"ppocr:{self.lang}"

    def _check(self):
        if not self.python.exists():
            raise RecognizerUnavailable(f"PaddleOCR 用 Python がない: {self.python}（MODELS.md の手順で導入）")

    def start(self):
        if self.proc and self.proc.poll() is None:
            return
        self._check()
        cmd = [str(self.python), "-I", str(WORKER), "--cache-dir", str(self.cache_dir), "--lang", self.lang]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, bufsize=1)
        line = self._readline(max(self.timeout_s, 600))  # 初回はモデルの取得が入る
        if not line:
            self.close()
            raise RecognizerUnavailable("PaddleOCR ワーカーが起動しなかった")
        msg = json.loads(line)
        if not msg.get("ready"):
            self.close()
            raise RecognizerUnavailable(f"PaddleOCR を読み込めない: {msg.get('error')}")
        self.info = msg
        self.version = f"ppocr:{self.lang}:{json.dumps(msg.get('versions'), sort_keys=True)}"

    def _readline(self, timeout: float) -> str | None:
        result: list[str] = []
        t = threading.Thread(target=lambda: result.append(self.proc.stdout.readline()), daemon=True)
        t.start()
        t.join(timeout)
        return None if t.is_alive() or not result else result[0]

    def read(self, png: bytes, words: bool = False) -> list[dict]:
        """画像から、行ごとの {text, score, box:[x0,y0,x1,y1]（画素）} を返す。words=True なら、行ごとの words（単語・文字の位置）も付く。"""
        with self._lock:
            self.start()
            with tempfile.TemporaryDirectory(prefix="plo-ocr-") as td:
                p = Path(td) / "page.png"
                p.write_bytes(png)
                self.proc.stdin.write(json.dumps({"id": "0", "image": str(p), "words": words}) + "\n")
                self.proc.stdin.flush()
                line = self._readline(self.timeout_s)
                if line is None:
                    self.close()
                    raise RuntimeError(f"文字認識がタイムアウトした（{self.timeout_s}秒）")
                msg = json.loads(line)
            if msg.get("error"):
                raise RuntimeError(msg["error"])
            return msg["lines"]

    def close(self):
        if self.proc:
            try:
                self.proc.stdin.close()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None

    def __del__(self):  # pragma: no cover
        try:
            self.close()
        except Exception:
            pass


_shared: PPOCRBackend | None = None
_shared_lock = threading.Lock()


def shared_backend() -> PPOCRBackend:
    """ビューアなどで共有する1つのワーカー（メモリを重ねて使わない）。"""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = PPOCRBackend()
        return _shared


def close_shared():
    global _shared
    with _shared_lock:
        if _shared is not None:
            _shared.close()
            _shared = None


def ocr_page_text(backend, page: pymupdf.Page, avoid_view_rects=(), dpi: int = TEXT_DPI, min_score: float = MIN_SCORE,
                  text_only: bool = True) -> list[dict]:
    """1ページの文章を行ごとに読む。avoid_view_rects（見た目の座標 pt、数式の枠）は塗りつぶして読まない。

    戻り値: [{"rect": 保存座標, "view": 見た目の座標(pt), "text": 文字, "score": 信頼度}]（上から下の順）
    """
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    k = dpi / 72.0
    for r in avoid_view_rects:
        ir = pymupdf.IRect(int(r[0] * k) - 2, int(r[1] * k) - 2, int(r[2] * k) + 2, int(r[3] * k) + 2) & pymupdf.IRect(0, 0, pix.width, pix.height)
        if not ir.is_empty:
            pix.set_rect(ir, (255, 255, 255))
    lines = backend.read(pix.tobytes("png"))
    out = []
    for ln in lines:
        text = (ln.get("text") or "").strip()
        if not text or float(ln.get("score", 0)) < min_score or (text_only and not looks_like_text(text)):
            continue
        x0, y0, x1, y1 = ln["box"]
        v = pymupdf.Rect(x0 / k, y0 / k, x1 / k, y1 / k) & page.rect
        if v.is_empty or v.width < 2 or v.height < 2:
            continue
        s = v * page.derotation_matrix
        out.append({"rect": (s.x0, s.y0, s.x1, s.y1), "view": (v.x0, v.y0, v.x1, v.y1), "text": text,
                    "score": round(float(ln["score"]), 3)})
    out.sort(key=lambda d: (round(d["view"][1] / 6), d["view"][0]))
    return out
