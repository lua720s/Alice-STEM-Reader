"""UniMERNet バックエンド: 別 venv のワーカーを子プロセスとして使う。

既定の場所（プロジェクト直下、いずれも Git 管理外）
- Python: .venv-ml/bin/python（環境変数 PLO_ML_PYTHON で変更）
- モデル: models/unimernet_tiny（環境変数 PLO_UNIMERNET_DIR で変更）
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

from .base import Recognition, RecognizerUnavailable

ROOT = Path(__file__).resolve().parents[3]
WORKER = Path(__file__).resolve().parent / "unimernet_worker.py"


TEXT_CMDS = ("\\text", "\\mbox", "\\textrm", "\\textit", "\\textbf", "\\operatorname")


def tidy_latex(s: str) -> str:
    """UniMERNet の字句ごとの空白を詰める（意味を変えない範囲に限る）。

    残す空白: 制御語の直後で英字が続く箇所（\\ln x）、制御空白（\\ の直後）、\\text{...} 等の中。
    """
    s = " ".join(s.split())
    out: list[str] = []
    text_depth = []  # \\text{ の開始時の波括弧の深さ
    depth = 0
    pending_text = False
    i = 0
    while i < len(s):
        c = s[i]
        prev = "".join(out[-40:])
        if c == "{":
            depth += 1
            if pending_text:
                text_depth.append(depth)
                pending_text = False
        elif c == "}":
            if text_depth and text_depth[-1] == depth:
                text_depth.pop()
            depth -= 1
        elif c == " ":
            nxt = s[i + 1] if i + 1 < len(s) else ""
            trailing_bs = len(prev) - len(prev.rstrip("\\"))
            keep = (
                bool(text_depth)
                or trailing_bs % 2 == 1
                or (re.search(r"\\[A-Za-z]+$", prev) and nxt.isalpha())
            )
            if keep:
                out.append(" ")
            i += 1
            continue
        out.append(c)
        if c.isalpha() or c == "\\":
            tail = "".join(out[-14:])
            nxt = s[i + 1] if i + 1 < len(s) else ""
            if not nxt.isalpha() and any(tail.endswith(t) for t in TEXT_CMDS):
                pending_text = True
        elif c not in " {":
            pending_text = False if c != "}" else pending_text
        i += 1
    return "".join(out)


class UniMERNetBackend:
    name = "unimernet"

    def __init__(self, model_dir=None, python=None, device="cpu", timeout_s: float = 120.0, threads: int = 0):
        self.model_dir = Path(model_dir or os.environ.get("PLO_UNIMERNET_DIR") or ROOT / "models" / "unimernet_tiny")
        self.python = Path(python or os.environ.get("PLO_ML_PYTHON") or ROOT / ".venv-ml" / "bin" / "python")
        self.device = device
        self.timeout_s = timeout_s
        self.threads = threads
        self.proc: subprocess.Popen | None = None
        self.info: dict = {}
        self._lock = threading.Lock()
        self.version = f"unimernet:{self.model_dir.name}"

    def _check(self):
        if not self.python.exists():
            raise RecognizerUnavailable(f"ML 用 Python がない: {self.python}（MODELS.md の手順で導入）")
        if not self.model_dir.is_dir() or not list(self.model_dir.glob("*.pth")):
            raise RecognizerUnavailable(f"UniMERNet の重みがない: {self.model_dir}（MODELS.md の手順で取得）")

    def start(self):
        if self.proc and self.proc.poll() is None:
            return
        self._check()
        cmd = [str(self.python), "-I", str(WORKER), "--model-dir", str(self.model_dir), "--device", self.device]
        if self.threads:
            cmd += ["--threads", str(self.threads)]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, bufsize=1)
        line = self._readline(timeout=max(self.timeout_s, 300))
        if not line:
            self.close()
            raise RecognizerUnavailable("UniMERNet ワーカーが起動しなかった")
        msg = json.loads(line)
        if not msg.get("ready"):
            self.close()
            raise RecognizerUnavailable(f"UniMERNet を読み込めない: {msg.get('error')}")
        self.info = msg
        self.version = f"unimernet:{self.model_dir.name}:{json.dumps(msg.get('versions'), sort_keys=True)}"

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
                    if line is None:  # タイムアウト: ワーカーを止め、以降は再起動で続ける
                        self.close()
                        out.append(Recognition(None, None, f"timeout>{self.timeout_s}s"))
                        self.start()
                        continue
                    msg = json.loads(line)
                    raw = msg.get("latex")
                    r = Recognition(latex=tidy_latex(raw) if raw else None, confidence=None, error=msg.get("error"))
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
