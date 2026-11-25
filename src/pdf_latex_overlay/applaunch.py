"""macOS の .app（Launchpad・Spotlight・Dock・Finder の「このアプリケーションで開く」）から使う起動部分。

- ビューアは1つだけ常駐させる（状態ファイルに接続情報を置く）。すでに動いていれば、そこへ資料を加えて開くだけ。
  画面は、いつものブラウザで開く（http://127.0.0.1 のみ。外部には公開しない）。
- 常駐するビューアは、操作が一定時間なければ自分で終了する。
- 状態ファイルは自分だけが読める（トークンを含む）。
- 起動時のフック: `~/Library/Application Support/Alice STEM Reader/hooks/on-launch` に実行できるファイルを置くと、
  アプリを開くたびに裏で実行する（ほかのアプリを一緒に起動したいときなど。置かなければ何もしない）。
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "Alice STEM Reader"
SNIP_APP_NAME = "Alice STEM Snip"
IDLE_EXIT_MIN = 120
_OLD_SUPPORT = "PDF LaTeX Overlay"  # 以前の版の置き場所（あれば、そのまま使う）


def support_dir() -> Path:
    if os.environ.get("PLO_APP_DIR"):
        return Path(os.environ["PLO_APP_DIR"])
    base = Path.home() / "Library" / "Application Support"
    new = base / APP_NAME
    _migrate(base / _OLD_SUPPORT, new)
    return new


def _migrate(old: Path, new: Path):
    """以前の版の設定（追加したフォルダ・前回のフォルダ）を引き継ぐ（コピーするだけ。古い方は消さない）。"""
    if not old.is_dir():
        return
    for name in ("roots.json", "app.json"):
        src, dst = old / name, new / name
        if src.is_file() and not dst.exists():
            try:
                new.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(src.read_bytes())
            except OSError:
                pass


def state_path() -> Path:
    return support_dir() / "server.json"


def roots_path() -> Path:
    return support_dir() / "roots.json"


def config_path() -> Path:
    return support_dir() / "app.json"


def hooks_dir() -> Path:
    return support_dir() / "hooks"


def log_path() -> Path:
    return Path(os.environ.get("PLO_APP_LOG") or Path.home() / "Library" / "Logs" / f"{APP_NAME}.log")


def run_hook(name: str = "on-launch") -> bool:
    """利用者が置いたフック（実行できるファイル）があれば、裏で実行する。自分が持ち主で、他人が書き換えられないものだけ。"""
    h = hooks_dir() / name
    try:
        st = h.stat()
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode) or not os.access(h, os.X_OK) or st.st_uid != os.getuid() or st.st_mode & 0o022:
        return False
    with open(log_path(), "ab") as log:
        subprocess.Popen([str(h)], stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    return True


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
    try:
        cfg = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    cfg["last_folder"] = folder
    config_path().write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


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
           "--state-file", str(sp), "--roots-file", str(roots_path()), "--idle-exit", str(IDLE_EXIT_MIN)]
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
    from .docformats import is_supported

    for f in files:
        if not Path(f).is_file() or not is_supported(f):
            raise ValueError(f"対応していない形式か、見つからない: {f}")
    if folder:
        folder = str(Path(folder).expanduser().resolve())
        if not Path(folder).is_dir():
            raise ValueError(f"フォルダが見つからない: {folder}")
        _remember_folder(folder)
    st = running_state()
    fresh = st is None
    if fresh:
        # 何も指定されなくても起動する（開くものは、ビューアの画面の「開く」で選ぶ）。前に追加したフォルダは、並ぶ
        st = _spawn([folder] if folder else [], backend, wait_s)
    elif folder:
        _call(st, "POST", "/api/addroot", {"path": folder})
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


def app_snip(mode: str = "auto", backend: str = "unimernet", wait_s: float = 60.0, notify=True) -> dict:
    """画面の範囲を選んで読み、結果をクリップボードへ（Snip アプリ・ショートカットから）。ビューアがなければ、画面を開かずに起動する。"""
    from .snip import notify as _notify

    st = running_state() or _spawn([], backend, wait_s)
    cfg = {}
    try:
        cfg = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    body = {"mode": mode, "copy": True, "delim": cfg.get("delim", "paren"), "single": bool(cfg.get("single", False))}
    r = _call(st, "POST", "/api/snip/screen", body, timeout=600.0)
    if notify and not r.get("cancelled"):
        if r.get("latex"):
            _notify(APP_NAME, ("コピーしました: " if r.get("copied") else "読み取りました: ") + r["latex"].replace("\n", " ")[:120])
        else:
            _notify(APP_NAME, "読み取れませんでした" + (f"（{r.get('error')}）" if r.get("error") else ""))
    return r
