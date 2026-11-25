"""認識モデルを導入する（macOS・Windows・Linux 共通。scripts/setup_models.sh はこれを呼ぶ）。

プロジェクト直下に .venv-ml（PyTorch・UniMERNet・数式検出）、.venv-pp（PaddleOCR）、models/ を作る（どれも Git 管理外）。
重みは配布元（Hugging Face）から取り、記録した SHA-256 と照合する。一致しなければ止める。
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from .osutil import venv_python

ROOT = Path(__file__).resolve().parents[2]
HF = "https://huggingface.co"
CONFIG_FILES = ["config.json", "preprocessor_config.json", "tokenizer.json", "tokenizer_config.json", "README.md"]
TINY_REV = "3f09ac4b1cd583be47ea20a7d7daef839473028a"
MFD_REV = "ed6b654c018d742e65a17671e379c5e6ecc87ec9"
SHA = {
    "unimernet_base": "16cd0891233cfee3c11215a7b87306f160f7e7f3f52091a6253751c149a8c180",
    "unimernet_tiny": "6f7608624e2d7549c7f0f05fcfbe073ae521328cf70f1d46374d96f9881d7371",
    "mfd": "41029d5abb9b0b6df825ecaf8adf9151762ea0688ac0fc4ea0ea34ab9a5808fc",
}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url: str, out: Path, want: str | None = None) -> None:
    if out.is_file() and want and sha256(out) == want:
        print(f"  already present: {out.relative_to(ROOT)}")
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + ".partial")
    print(f"  downloading {url}", flush=True)
    with urllib.request.urlopen(url, timeout=60) as r, open(part, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    if want and sha256(part) != want:
        raise SystemExit(f"SHA-256 mismatch: {out} (the download was kept as {part})")
    part.replace(out)


def ensure_venv(name: str) -> Path:
    venv = ROOT / name
    py = venv_python(venv)
    if not py.exists():
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    return py


def main(tiny: bool = False, paddle: bool = True) -> int:
    print("== 1/4 ML environment (.venv-ml: PyTorch, UniMERNet, formula detection)", flush=True)
    py = ensure_venv(".venv-ml")
    subprocess.run([str(py), "-m", "pip", "install", "--no-cache-dir", "-q", "-r", str(ROOT / "requirements-ml.txt")], check=True)

    print("== 2/4 Formula recognition: UniMERNet base (default engine)", flush=True)
    for f in CONFIG_FILES:
        fetch(f"{HF}/wanderkid/unimernet_base/resolve/main/{f}", ROOT / "models" / "unimernet_base" / f)
    fetch(f"{HF}/wanderkid/unimernet_base/resolve/main/pytorch_model.pth", ROOT / "models" / "unimernet_base" / "pytorch_model.pth",
          SHA["unimernet_base"])
    if tiny:
        print("== 2b UniMERNet tiny", flush=True)
        for f in CONFIG_FILES:
            fetch(f"{HF}/wanderkid/unimernet_tiny/resolve/{TINY_REV}/{f}", ROOT / "models" / "unimernet_tiny" / f)
        fetch(f"{HF}/wanderkid/unimernet_tiny/resolve/{TINY_REV}/unimernet_tiny.pth",
              ROOT / "models" / "unimernet_tiny" / "unimernet_tiny.pth", SHA["unimernet_tiny"])

    print("== 3/4 Formula detection: PDF-Extract-Kit MFD (YOLOv8)", flush=True)
    fetch(f"{HF}/opendatalab/PDF-Extract-Kit-1.0/resolve/{MFD_REV}/models/MFD/YOLO/yolo_v8_ft.pt",
          ROOT / "models" / "mfd_yolo_v8" / "yolo_v8_ft.pt", SHA["mfd"])

    if paddle:
        print("== 4/4 Text recognition: PaddleOCR (.venv-pp)", flush=True)
        pp = ensure_venv(".venv-pp")
        subprocess.run([str(pp), "-m", "pip", "install", "--no-cache-dir", "-q", "paddlepaddle==3.3.1", "paddleocr==3.7.0",
                        "tokenizers", "ftfy"], check=True)
        print("  (PaddleOCR models are downloaded automatically the first time text is read)")
    print("Done. Licenses: UniMERNet Apache-2.0, MFD/ultralytics AGPL-3.0, PaddleOCR Apache-2.0 (see docs/MODELS.md).")
    return 0
