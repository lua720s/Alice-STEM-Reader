"""PP-FormulaNet バックエンド: 別 venv（.venv-pp）のワーカーを子プロセスとして使う。

既定の場所（プロジェクト直下、いずれも Git 管理外）
- Python: .venv-pp/bin/python（環境変数 PLO_PP_PYTHON で変更）
- モデル: models/pp_cache/official_models/<モデル名>（環境変数 PLO_PP_DIR でモデルの親フォルダを変更）
- モデル名: 環境変数 PLO_PP_MODEL（既定 PP-FormulaNet_plus-M）
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

from ..osutil import no_window, venv_python

from .base import Recognition, RecognizerUnavailable
from .unimernet import tidy_latex

ROOT = Path(__file__).resolve().parents[3]
WORKER = Path(__file__).resolve().parent / "ppformulanet_worker.py"
DEFAULT_MODEL = "PP-FormulaNet_plus-M"


def tidy_pp(s: str) -> str:
    """PP-FormulaNet の出力の整形: `\\operatorname{l o g}` → `\\log`、`d x` → `dx` のような字句間の空白を詰める。"""
    s = re.sub(r"\\operatorname\s*\{([^{}]*)\}", lambda m: "\\operatorname{" + m.group(1).replace(" ", "") + "}", s)
    s = re.sub(r"\\operatorname\{(log|ln|sin|cos|tan|exp|lim|max|min|sup|inf|det|dim|ker|arg|sec|csc|cot|sinh|cosh|tanh)\}", r"\\\1 ", s)  # 後ろに英字が続くときの空白は、tidy_latex が残す
    return tidy_latex(s)


class PPFormulaNetBackend:
    name = "ppformulanet"

    def __init__(self, model_name=None, model_dir=None, python=None, timeout_s: float = 120.0, threads: int = 0):
        self.model_name = model_name or os.environ.get("PLO_PP_MODEL") or DEFAULT_MODEL
        base = Path(os.environ.get("PLO_PP_DIR") or ROOT / "models" / "pp_cache" / "official_models")
        self.model_dir = Path(model_dir or base / self.model_name)
        self.python = Path(python or os.environ.get("PLO_PP_PYTHON") or venv_python(ROOT / ".venv-pp"))
        self.timeout_s = timeout_s
        self.threads = threads
        self.proc: subprocess.Popen | None = None
        self.info: dict = {}
        self._lock = threading.Lock()
        self.version = f"ppformulanet:{self.model_name}"

    def _check(self):
        if not self.python.exists():
            raise RecognizerUnavailable(f"PP-FormulaNet 用 Python がない: {self.python}（docs/MODELS.md の手順で導入）")
        if not (self.model_dir / "inference.pdiparams").is_file():
            raise RecognizerUnavailable(f"PP-FormulaNet の重みがない: {self.model_dir}（docs/MODELS.md の手順で取得）")

    def start(self):
        if self.proc and self.proc.poll() is None:
            return
        self._check()
        cmd = [str(self.python), "-I", "-X", "utf8", str(WORKER), "--model-dir", str(self.model_dir), "--model-name", self.model_name]
        if self.threads:
            cmd += ["--threads", str(self.threads)]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", bufsize=1, **no_window())
        line = self._readline(timeout=max(self.timeout_s, 300))
        if not line:
            self.close()
            raise RecognizerUnavailable("PP-FormulaNet ワーカーが起動しなかった")
        msg = json.loads(line)
        if not msg.get("ready"):
            self.close()
            raise RecognizerUnavailable(f"PP-FormulaNet を読み込めない: {msg.get('error')}")
        self.info = msg
        self.version = f"ppformulanet:{self.model_name}:{json.dumps(msg.get('versions'), sort_keys=True)}"

    def _readline(self, timeout: float) -> str | None:
        result: list[str] = []
        t = threading.Thread(target=lambda: result.append(self.proc.stdout.readline()), daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            return None
        return result[0] if result else None

    def recognize(self, images_png: list[bytes]) -> list[Recognition]:
        with self._lock:
            self.start()
            out = []
            with tempfile.TemporaryDirectory(prefix="plo-crops-") as td:
                for i, png in enumerate(images_png):
                    p = Path(td) / f"{i}.png"
                    p.write_bytes(png)
                    self.proc.stdin.write(json.dumps({"id": str(i), "image": str(p)}) + "\n")
                    self.proc.stdin.flush()
                    line = self._readline(self.timeout_s)
                    if line is None:
                        self.close()
                        out.append(Recognition(None, None, f"timeout>{self.timeout_s}s"))
                        self.start()
                        continue
                    msg = json.loads(line)
                    raw = msg.get("latex")
                    r = Recognition(latex=tidy_pp(raw) if raw else None, confidence=None, error=msg.get("error"))
                    r.raw = raw  # type: ignore[attr-defined]
                    r.elapsed_s = msg.get("elapsed_s")  # type: ignore[attr-defined]
                    out.append(r)
            return out

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
