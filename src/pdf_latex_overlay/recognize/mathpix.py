"""Mathpix バックエンド（外部サービス）。切り出した式の画像を Mathpix に送って LaTeX を得る。

- 画像が Mathpix のサーバーに送られる。呼び出す側が、利用者の同意（allow_external=True）を得てから使う。
  同意がなければ、送信せずに RecognizerUnavailable を出す。
- 認証情報は `mpx configure`（またはMATHPIX_APP_ID / MATHPIX_APP_KEY）に任せる。このプログラムは認証情報を読まない・保存しない。
- `mpx scs convert 画像 出力.mmd` が /v3/text に送る。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from .base import Recognition, RecognizerUnavailable

_DELIMS = [(r"\\\(", r"\\\)"), (r"\\\[", r"\\\]"), (r"\$\$", r"\$\$"), (r"\$", r"\$")]


def strip_delimiters(s: str) -> str:
    """Mathpix の出力から、数式の区切り（\\( \\) \\[ \\] $$ $）を除く。複数の数式があれば改行でつなぐ。"""
    s = (s or "").strip()
    parts = []
    for a, b in _DELIMS:
        found = re.findall(a + r"(.*?)" + b, s, flags=re.S)
        if found:
            parts = [x.strip() for x in found if x.strip()]
            break
    if parts:
        return "\n".join(parts)
    # 実際の出力で、区切りが残った事例があった（対になる形で見つからない場合）。両端の区切りだけを外す
    s = re.sub(r"^(?:\\+[(\[]|\$\$?)\s*", "", s)
    s = re.sub(r"\s*(?:\\+[)\]]|\$\$?)$", "", s)
    return s.strip()


def _run_mpx(png_path: Path, out_path: Path, timeout: float) -> tuple[int, str]:
    cmd = ["mpx", "-q", "scs", "convert", str(png_path), str(out_path),
           "--options-json", json.dumps({"math_inline_delimiters": ["\\(", "\\)"], "math_display_delimiters": ["\\[", "\\]"]})]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stderr or p.stdout or "").strip()[:300]


class MathpixBackend:
    name = "mathpix"
    version = "mathpix:v3/text"

    def __init__(self, allow_external: bool = False, runner=None, timeout_s: float = 60.0):
        self.allow_external = allow_external
        self._runner = runner or _run_mpx
        self.timeout_s = timeout_s
        self._lock = threading.Lock()
        self.sent = 0  # 送った画像の枚数（利用量の目安）

    def _check(self):
        if not self.allow_external:
            raise RecognizerUnavailable("Mathpix は画像を外部に送る。同意（--allow-external、または画面の確認）がないので送らない")
        if self._runner is _run_mpx and shutil.which("mpx") is None:
            raise RecognizerUnavailable("mpx（Mathpix CLI）がない。導入して `mpx configure` で認証情報を設定する")

    def start(self):
        self._check()

    def recognize(self, images_png: list[bytes]) -> list[Recognition]:
        self._check()
        out = []
        with self._lock, tempfile.TemporaryDirectory(prefix="plo-mpx-") as td:
            for i, png in enumerate(images_png):
                src, dst = Path(td) / f"{i}.png", Path(td) / f"{i}.mmd"
                src.write_bytes(png)
                try:
                    rc, msg = self._runner(src, dst, self.timeout_s)
                except Exception as e:  # noqa: BLE001  タイムアウト等
                    out.append(Recognition(None, None, f"{type(e).__name__}: {e}"))
                    continue
                self.sent += 1
                if rc != 0 or not dst.exists():
                    out.append(Recognition(None, None, f"mpx 失敗 (code {rc}): {msg}"))
                    continue
                raw = dst.read_text(encoding="utf-8", errors="replace")
                latex = strip_delimiters(raw)
                r = Recognition(latex=latex or None, confidence=None, error=None if latex else "empty")
                r.raw = raw  # type: ignore[attr-defined]
                out.append(r)
        return out

    def close(self):
        pass
