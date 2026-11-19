"""macOS の .app（Finder の「このアプリケーションで開く」・ドック）から使う起動部分。

- ビューアは1つだけ常駐させる（状態ファイルに接続情報を置く）。すでに動いていれば、そこへ PDF を加えて開くだけ。
- 常駐するビューアは、操作が一定時間なければ自分で終了する。
- 状態ファイルは自分だけが読める（トークンを含む）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "PDF LaTeX Overlay"
IDLE_EXIT_MIN = 120


def support_dir() -> Path:
    return Path(os.environ.get("PLO_APP_DIR") or Path.home() / "Library" / "Application Support" / APP_NAME)


def state_path() -> Path:
    return support_dir() / "server.json"


def config_path() -> Path:
    return support_dir() / "app.json"


def log_path() -> Path:
    return Path(os.environ.get("PLO_APP_LOG") or Path.home() / "Library" / "Logs" / f"{APP_NAME}.log")


def read_state() -> dict | None:
    try:
        st = json.loads(state_path().read_text(encoding="utf-8"))
        return st if all(k in st for k in ("pid", "port", "token", "url")) else None
    except (OSError, ValueError):
        return None


def _call(st: dict, method: str, path: str, body=None, timeout: float = 5.0):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"X-Token": st["token"]}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"http://127.0.0.1:{st['port']}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:  # サーバーの説明（例: 別のビューアで開かれている）を、そのまま伝える
        try:
            msg = json.loads(e.read()).get("error")
        except (ValueError, OSError):
            msg = None
        raise RuntimeError(msg or f"ビューアがエラーを返した（HTTP {e.code}）") from None


def running_state() -> dict | None:
    """動いているビューアの接続情報。動いていなければ None（古い状態ファイルは消す）。"""
    st = read_state()
    if not st:
        return None
    try:
        info = _call(st, "GET", "/api/ping", timeout=2.0)
        if info.get("pid") == st["pid"]:
            st["idle_s"] = info.get("idle_s", 999)
            return st
    except (urllib.error.URLError, OSError, ValueError):
        pass
    try:
        state_path().unlink()
    except OSError:
        pass
    return None


def last_folder() -> str | None:
    try:
        p = json.loads(config_path().read_text(encoding="utf-8")).get("last_folder")
        return p if p and Path(p).is_dir() else None
    except (OSError, ValueError):
        return None


def _remember_folder(folder: str):
    support_dir().mkdir(parents=True, exist_ok=True)
    config_path().write_text(json.dumps({"last_folder": folder}, ensure_ascii=False), encoding="utf-8")


def _spawn(roots: list[str], backend: str, wait_s: float) -> dict:
    sp = state_path()
    try:
        sp.unlink()
    except OSError:
        pass
    sp.parent.mkdir(parents=True, exist_ok=True)
    lp = log_path()
    lp.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "pdf_latex_overlay", "viewer", *roots, "--no-open", "--backend", backend,
           "--state-file", str(sp), "--idle-exit", str(IDLE_EXIT_MIN)]
    with open(lp, "ab") as log:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    t0 = time.monotonic()
    while time.monotonic() - t0 < wait_s:
        st = running_state()
        if st:
            return st
        time.sleep(0.2)
    raise RuntimeError(f"ビューアが起動しなかった（ログ: {lp}）")


def app_open(files: list[str] | None = None, folder: str | None = None, backend: str = "unimernet",
             wait_s: float = 60.0, opener=webbrowser.open) -> dict:
    """PDF（複数可）またはフォルダをビューアで開く。動いていれば再利用する。"""
    files = [str(Path(f).expanduser()) for f in (files or [])]
    for f in files:
        if not Path(f).is_file() or Path(f).suffix.lower() != ".pdf":
            raise ValueError(f"PDF ではない、または見つからない: {f}")
    if folder:
        folder = str(Path(folder).expanduser().resolve())
        if not Path(folder).is_dir():
            raise ValueError(f"フォルダが見つからない: {folder}")
        _remember_folder(folder)
    st = running_state()
    fresh = st is None
    if fresh:
        roots = [folder] if folder else ([last_folder()] if last_folder() else [])
        if not roots and files:
            roots = [str(Path(files[0]).resolve().parent)]
        if not roots:
            raise ValueError("開くフォルダまたはPDFがない")
        st = _spawn(roots, backend, wait_s)
    elif folder:
        raise ValueError("ビューアはすでに動いている。フォルダを変えるには、先に app-stop で止める")
    opened = None
    if files:
        ids = _call(st, "POST", "/api/addfiles", {"paths": [str(Path(f).resolve()) for f in files]})["ids"]
        _call(st, "POST", "/api/open", {"id": ids[0], "auto": False}, timeout=60.0)  # 開いただけでは認識しない（「このページを認識」で行う。事前OCRの結果はそのまま読み込む）
        opened = ids[0]
    # すでに画面が開いている（直前まで通信があった）ときは、新しいタブを開かない。画面は、開いた PDF に追従する
    if fresh or st.get("idle_s", 999) > 8:
        opener(st["url"])
    return {"url": st["url"], "fresh": fresh, "opened": opened, "pid": st["pid"]}


def app_stop() -> bool:
    st = running_state()
    if not st:
        return False
    try:
        _call(st, "POST", "/api/quit", {})
    except (urllib.error.URLError, OSError):
        pass
    return True
