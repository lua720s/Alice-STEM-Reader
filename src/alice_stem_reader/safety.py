"""原本保護と出力の公開。

- 入力は読み取り専用で開き、SHA-256 を前後で比較する。
- 出力先が既存・入力と同一実体・シンボリックリンク経由のときは拒否する。
- 一時ファイルは出力先と同じディレクトリに作り、検証後に os.link で公開する
  （既存ファイルがあれば失敗し、上書きしない）。
"""

from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path


class SafetyError(Exception):
    """原本保護・出力衝突による停止。"""


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_paths(input_path: str | Path, output_path: str | Path) -> tuple[Path, Path]:
    inp = Path(input_path)
    out = Path(output_path)
    if not inp.is_file():
        raise SafetyError(f"入力PDFが見つからない: {inp}")
    if out.is_symlink():
        raise SafetyError(f"出力先がシンボリックリンク: {out}")
    if out.exists():
        raise SafetyError(f"出力先が既に存在する（上書きしない）: {out}")
    parent = out.parent if str(out.parent) else Path(".")
    if not parent.is_dir():
        raise SafetyError(f"出力先ディレクトリがない: {parent}")
    # 親ディレクトリがリンクで入力と同じ場所を指す場合も含め、実体で比較する
    inp_real = inp.resolve()
    out_real = parent.resolve() / out.name
    if inp_real == out_real:
        raise SafetyError("出力先が入力と同一の実体")
    if out.suffix.lower() != ".pdf":
        raise SafetyError(f"出力先の拡張子が .pdf ではない: {out}")
    return inp, out


def temp_path_for(output_path: Path) -> Path:
    return output_path.with_name(f".{output_path.name}.{secrets.token_hex(6)}.partial")


def publish(tmp_path: Path, output_path: Path) -> None:
    """一時ファイルを上書きなしで公開する。"""
    try:
        os.link(tmp_path, output_path)  # 既存なら FileExistsError
    except FileExistsError as e:
        raise SafetyError(f"公開直前に出力先が作られた（衝突）: {output_path}") from e
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
