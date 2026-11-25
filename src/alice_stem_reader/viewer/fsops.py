"""コレクションのファイル操作（名前の変更・移動・削除・フォルダ作成）の部品。

これらは利用者の実際のファイルを動かす。そのため次の安全策を守る（呼び出し側の Hub が強制する）。
- 一覧にあるフォルダの外には出さない。上書きしない（同じ名前があれば失敗）。
- 削除は、永久に消さず、ゴミ箱へ移す（ゴミ箱から元に戻せる）。
- 名前は、区切り文字・隠しファイル・長すぎる名前を拒否する。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

from .session import SessionError

MAX_NAME_BYTES = 240


def clean_name(name, is_file: bool, ext: str = ".pdf") -> str:
    """利用者が入力した名前を検査する。ファイルは、元の拡張子（既定 .pdf）がなければ付ける。"""
    if not isinstance(name, str):
        raise SessionError("名前が不正")
    n = name.strip()
    if not n or n in (".", "..") or "/" in n or "\\" in n or "\x00" in n or ":" in n:
        raise SessionError("名前に使えない文字がある（/ \\ : や、空の名前は使えない）")
    if n.startswith("."):
        raise SessionError("名前を「.」で始めることはできない（隠しファイルになる）")
    if is_file and ext and not n.lower().endswith(ext.lower()):
        n += ext
    if len(n.encode("utf-8")) > MAX_NAME_BYTES:
        raise SessionError("名前が長すぎる")
    return n


def _unique(dest_dir: Path, name: str) -> Path:
    p = dest_dir / name
    if not p.exists() and not p.is_symlink():
        return p
    stem, suf = (name[: -len(Path(name).suffix)], Path(name).suffix) if Path(name).suffix else (name, "")
    stamp = time.strftime("%H.%M.%S")
    for i in range(1, 1000):
        q = dest_dir / f"{stem} {stamp}{'' if i == 1 else f' {i}'}{suf}"
        if not q.exists() and not q.is_symlink():
            return q
    raise SessionError("ゴミ箱に同じ名前が多すぎる")


def move_to_trash(path: Path, trash_dir: Path | None = None) -> Path:
    """ゴミ箱へ移す（永久削除ではない）。macOS では ~/.Trash、権限がなければ Finder 経由。

    trash_dir を渡すと、そこへ移す（試験用）。
    """
    path = Path(path)
    if trash_dir is None and sys.platform != "darwin":  # Windows のごみ箱・Linux の Trash（FreeDesktop の仕様）
        from ..osutil import OSFeatureError, move_to_trash_native

        try:
            return move_to_trash_native(path) or path
        except (OSFeatureError, OSError) as e:
            raise SessionError(f"ゴミ箱に移せなかった: {e}", 500) from e
    td = Path(trash_dir) if trash_dir is not None else Path.home() / ".Trash"
    if trash_dir is not None:
        td.mkdir(parents=True, exist_ok=True)
    dest = _unique(td, path.name)
    try:
        shutil.move(str(path), str(dest))
        return dest
    except PermissionError:
        if trash_dir is not None:
            raise
    # ~/.Trash に書けない場合（アプリの権限）は、Finder に頼む。パスは引数で渡す（文字列に埋め込まない）。
    script = ['on run argv', 'tell application "Finder" to delete (POSIX file (item 1 of argv) as alias)', 'end run']
    cmd = ["osascript"] + [x for line in script for x in ("-e", line)] + [str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or path.exists():
        raise SessionError(f"ゴミ箱に移せなかった: {(r.stderr or '').strip()[:200]}", 500)
    return td / path.name
