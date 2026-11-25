"""OS ごとの違いをまとめる（macOS・Windows・Linux）。

- 設定・ログの置き場所、仮想環境の Python の場所
- プロセスの生存確認（Windows では os.kill(pid, 0) が Ctrl+C を送ってしまうので使わない）
- 子プロセスを切り離して起動する引数
- クリップボード・通知・画面の範囲の切り取り・ファイル選択の画面・ゴミ箱
- ブラウザの「アプリのウィンドウ」（Chrome・Edge などの --app）
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
APP_NAME = "Alice STEM Reader"


class OSFeatureError(RuntimeError):
    """この OS・環境では使えない（必要な道具がない）。メッセージは画面にそのまま出す。"""


# ---------------------------------------------------------------- 置き場所
def config_dir() -> Path:
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if IS_WIN:
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "alice-stem-reader"


def log_file() -> Path:
    if IS_MAC:
        return Path.home() / "Library" / "Logs" / f"{APP_NAME}.log"
    if IS_WIN:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME / f"{APP_NAME}.log"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "alice-stem-reader" / "alice-stem-reader.log"


def venv_python(venv: Path) -> Path:
    """仮想環境の Python（Windows は Scripts\\python.exe）。"""
    return Path(venv) / ("Scripts/python.exe" if IS_WIN else "bin/python")


def gui_python(python: str | Path | None = None) -> Path:
    """画面を持たない起動用の Python（Windows では黒い窓が出ない pythonw.exe）。"""
    p = Path(python or sys.executable)
    if IS_WIN and p.name.lower() == "python.exe" and (p.parent / "pythonw.exe").exists():
        return p.parent / "pythonw.exe"
    return p


# ---------------------------------------------------------------- プロセス
def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WIN:
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.GetLastError() == 5  # ERROR_ACCESS_DENIED: 存在はする
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def detached() -> dict:
    """親（アプリ・ターミナル）から切り離して起動する Popen の引数。"""
    if IS_WIN:
        flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        return {"creationflags": flags, "close_fds": True}
    return {"start_new_session": True}


def no_window() -> dict:
    """Windows で、子プロセスのコンソール窓を出さない。"""
    return {"creationflags": 0x08000000} if IS_WIN else {}


# ---------------------------------------------------------------- クリップボード
def copy_to_clipboard(text: str) -> bool:
    """文字列をクリップボードに入れる。できなければ False。"""
    try:
        if IS_MAC and shutil.which("pbcopy"):
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), timeout=10, check=True, env={**os.environ, "LANG": "en_US.UTF-8"})
            return True
        if IS_WIN:
            return _win_set_clipboard(text)
        for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
            if shutil.which(cmd[0]) and (cmd[0] != "wl-copy" or os.environ.get("WAYLAND_DISPLAY")):
                subprocess.run(cmd, input=text.encode("utf-8"), timeout=10, check=True)
                return True
    except (OSError, subprocess.SubprocessError):
        return False
    return False


def _win_set_clipboard(text: str) -> bool:
    from ctypes import wintypes

    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32  # type: ignore[attr-defined]
    k32.GlobalAlloc.restype = wintypes.HGLOBAL
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    data = text.encode("utf-16-le") + b"\x00\x00"
    for _ in range(10):  # ほかのアプリが開いていることがある
        if u32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False
    try:
        u32.EmptyClipboard()
        h = k32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
        p = k32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        k32.GlobalUnlock(h)
        return bool(u32.SetClipboardData(13, h))  # CF_UNICODETEXT
    finally:
        u32.CloseClipboard()


# ---------------------------------------------------------------- 通知
def notify(title: str, message: str) -> None:
    message = message[:200]
    try:
        if IS_MAC:
            esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
            subprocess.run(["osascript", "-e", f'display notification "{esc(message)}" with title "{esc(title)}"'],
                           timeout=10, capture_output=True)
        elif IS_LINUX and shutil.which("notify-send"):
            subprocess.run(["notify-send", "-a", title, title, message], timeout=10, capture_output=True)
        elif IS_WIN:
            # PowerShell の通知（BalloonTip）。値は引数で渡す（スクリプトに埋め込まない）
            ps = ("param($t,$m) Add-Type -AssemblyName System.Windows.Forms; $n=New-Object System.Windows.Forms.NotifyIcon;"
                  "$n.Icon=[System.Drawing.SystemIcons]::Information; $n.Visible=$true; $n.ShowBalloonTip(4000,$t,$m,'Info');"
                  "Start-Sleep -Seconds 5; $n.Dispose()")
            subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", f"& {{{ps}}}", title, message],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **detached())
    except (OSError, subprocess.SubprocessError):
        pass


# ---------------------------------------------------------------- 画面の範囲の切り取り
LINUX_CAPTURE_TOOLS = ("grim+slurp", "gnome-screenshot", "spectacle", "flameshot", "maim", "scrot", "import")


def screen_capture_available() -> str | None:
    """使える切り取りの方法の名前。なければ None。"""
    if IS_MAC:
        return "screencapture" if shutil.which("screencapture") else None
    if IS_WIN:
        return "snipping-tool"
    for t in LINUX_CAPTURE_TOOLS:
        if t == "grim+slurp":
            if shutil.which("grim") and shutil.which("slurp") and os.environ.get("WAYLAND_DISPLAY"):
                return t
        elif shutil.which(t):
            return t
    return None


def capture_screen(timeout_s: float = 300.0) -> bytes | None:
    """画面の範囲をドラッグで選んで切り取り、PNG（など）のバイト列を返す。取り消したら None。"""
    how = screen_capture_available()
    if how is None:
        raise OSFeatureError("画面を切り取る道具がない（Linux: grim と slurp・gnome-screenshot・spectacle・flameshot・maim・scrot のどれかを入れる）")
    if how == "snipping-tool":
        return _capture_windows(timeout_s)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "snip.png"
        try:
            if how == "screencapture":
                subprocess.run(["screencapture", "-i", "-x", "-t", "png", str(out)], timeout=timeout_s, check=False)
            elif how == "grim+slurp":
                r = subprocess.run(["slurp"], capture_output=True, text=True, timeout=timeout_s)
                if r.returncode != 0 or not r.stdout.strip():
                    return None
                subprocess.run(["grim", "-g", r.stdout.strip(), str(out)], timeout=60, check=False)
            elif how == "gnome-screenshot":
                subprocess.run(["gnome-screenshot", "-a", "-f", str(out)], timeout=timeout_s, check=False)
            elif how == "spectacle":
                subprocess.run(["spectacle", "-r", "-b", "-n", "-o", str(out)], timeout=timeout_s, check=False)
            elif how == "flameshot":
                r = subprocess.run(["flameshot", "gui", "-r"], capture_output=True, timeout=timeout_s)
                return r.stdout if r.returncode == 0 and r.stdout.startswith(b"\x89PNG") else None
            elif how == "maim":
                subprocess.run(["maim", "-s", str(out)], timeout=timeout_s, check=False)
            elif how == "scrot":
                subprocess.run(["scrot", "-s", "-o", str(out)], timeout=timeout_s, check=False)
            elif how == "import":
                subprocess.run(["import", str(out)], timeout=timeout_s, check=False)
        except subprocess.TimeoutExpired:
            raise OSFeatureError("画面の切り取りが時間内に終わらなかった")
        if not out.is_file() or out.stat().st_size == 0:
            return None
        return out.read_bytes()


def _clipboard_image_png() -> bytes | None:
    import io

    from PIL import Image, ImageGrab

    try:
        im = ImageGrab.grabclipboard()
    except Exception:
        return None
    if not isinstance(im, Image.Image):
        return None
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _capture_windows(timeout_s: float) -> bytes | None:
    """Windows の「切り取り領域」（Win+Shift+S と同じ画面）を出し、クリップボードに入った画像を受け取る。"""
    import hashlib

    before = _clipboard_image_png()
    h0 = hashlib.sha256(before).hexdigest() if before else None
    os.startfile("ms-screenclip:")  # type: ignore[attr-defined]
    t0 = time.monotonic()
    seen_tool = False
    while time.monotonic() - t0 < min(timeout_s, 120):
        time.sleep(0.25)
        img = _clipboard_image_png()
        if img and hashlib.sha256(img).hexdigest() != h0:
            return img
        running = _win_process_running(("ScreenClippingHost.exe", "SnippingTool.exe"))
        if running:
            seen_tool = True
        elif seen_tool and time.monotonic() - t0 > 2:
            img = _clipboard_image_png()  # 閉じた直後に入ることがある
            return img if img and hashlib.sha256(img).hexdigest() != h0 else None  # Esc で取り消した
    return None


def _win_process_running(names) -> bool:
    try:
        r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=10, **no_window())
    except (OSError, subprocess.SubprocessError):
        return False
    low = r.stdout.lower()
    return any(f'"{n.lower()}"' in low for n in names)


# ---------------------------------------------------------------- ファイル・フォルダの選択画面
def pick_paths(kind: str, prompt: str) -> list[str] | None:
    """OS の選択画面を出す。kind: files（複数可）/ folder。取り消したら None。"""
    if IS_MAC:
        if kind == "files":
            script = ('tell current application to activate\n'
                      f'set fs to choose file with prompt "{prompt}" with multiple selections allowed\n'
                      'set out to ""\nrepeat with f in fs\nset out to out & POSIX path of f & linefeed\nend repeat\nreturn out')
        else:
            script = f'tell current application to activate\nset f to choose folder with prompt "{prompt}"\nreturn POSIX path of f'
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=900)
        paths = [ln for ln in r.stdout.splitlines() if ln.strip()]
        return paths if r.returncode == 0 and paths else None
    if IS_LINUX:
        for tool in ("zenity", "kdialog"):
            if not shutil.which(tool):
                continue
            if tool == "zenity":
                cmd = ["zenity", "--file-selection", f"--title={prompt}"] + (["--multiple", "--separator=\n"] if kind == "files" else ["--directory"])
            else:
                cmd = ["kdialog", "--title", prompt] + (["--getopenfilename", str(Path.home()), "--multiple", "--separate-output"]
                                                         if kind == "files" else ["--getexistingdirectory", str(Path.home())])
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            paths = [ln for ln in r.stdout.splitlines() if ln.strip()]
            return paths if r.returncode == 0 and paths else None
    # Windows（と、zenity・kdialog のない Linux）: tkinter の選択画面を別プロセスで出す
    code = ("import sys,tkinter as tk; from tkinter import filedialog as fd; r=tk.Tk(); r.withdraw(); r.attributes('-topmost',True);"
            "k=sys.argv[1]; t=sys.argv[2];"
            "p=fd.askopenfilenames(title=t) if k=='files' else fd.askdirectory(title=t);"
            "p=[p] if isinstance(p,str) else list(p); print('\\n'.join(x for x in p if x))")
    try:
        r = subprocess.run([sys.executable, "-c", code, kind, prompt], capture_output=True, text=True, timeout=900, **no_window())
    except (OSError, subprocess.SubprocessError) as e:
        raise OSFeatureError(f"選択画面を出せなかった: {e}")
    if r.returncode != 0 and "tkinter" in (r.stderr or ""):
        raise OSFeatureError("選択画面に tkinter が必要（Linux: sudo apt install python3-tk、または zenity）")
    paths = [ln for ln in r.stdout.splitlines() if ln.strip()]
    return paths or None


# ---------------------------------------------------------------- ゴミ箱
def move_to_trash_native(path: Path) -> Path | None:
    """OS のゴミ箱へ移す。移した先が分かればそのパス（分からなければ None）。失敗は OSFeatureError。"""
    path = Path(path)
    if IS_WIN:
        return _win_recycle(path)
    if IS_LINUX:
        if shutil.which("gio"):
            r = subprocess.run(["gio", "trash", "--", str(path)], capture_output=True, text=True, timeout=60)
            if r.returncode == 0 and not path.exists():
                return None
        return _freedesktop_trash(path)
    raise OSFeatureError("未対応の OS")


def _freedesktop_trash(path: Path) -> Path:
    """FreeDesktop.org のゴミ箱の仕様（~/.local/share/Trash）に従って移す。"""
    from urllib.parse import quote

    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "Trash"
    (base / "files").mkdir(parents=True, exist_ok=True)
    (base / "info").mkdir(parents=True, exist_ok=True)
    name, n = path.name, 1
    while (base / "files" / name).exists() or (base / "info" / f"{name}.trashinfo").exists():
        n += 1
        name = f"{path.stem} {n}{path.suffix}"
    (base / "info" / f"{name}.trashinfo").write_text(
        f"[Trash Info]\nPath={quote(str(path.resolve()))}\nDeletionDate={time.strftime('%Y-%m-%dT%H:%M:%S')}\n", encoding="utf-8")
    shutil.move(str(path), str(base / "files" / name))
    return base / "files" / name


def _win_recycle(path: Path) -> None:
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", wintypes.LPCWSTR)]

    op = SHFILEOPSTRUCTW()
    op.wFunc = 3  # FO_DELETE
    op.pFrom = str(path.resolve()) + "\0"
    op.fFlags = 0x0040 | 0x0010 | 0x0004 | 0x0400  # ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))  # type: ignore[attr-defined]
    if rc != 0 or op.fAnyOperationsAborted or path.exists():
        raise OSFeatureError(f"ごみ箱に移せなかった（コード {rc}）")
    return None


# ---------------------------------------------------------------- アプリのウィンドウ
def chromium_browsers() -> list[str]:
    """--app でウィンドウを開ける、Chromium 系のブラウザの実行ファイル（見つかった順）。"""
    cands: list[str] = []
    if IS_MAC:
        for app, exe in (("Google Chrome", "Google Chrome"), ("Microsoft Edge", "Microsoft Edge"), ("Brave Browser", "Brave Browser"),
                         ("Chromium", "Chromium"), ("Vivaldi", "Vivaldi"), ("Arc", "Arc")):
            for root in ("/Applications", str(Path.home() / "Applications")):
                cands.append(f"{root}/{app}.app/Contents/MacOS/{exe}")
    elif IS_WIN:
        roots = [os.environ.get(k) for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
        for r in filter(None, roots):
            cands += [rf"{r}\Microsoft\Edge\Application\msedge.exe", rf"{r}\Google\Chrome\Application\chrome.exe",
                      rf"{r}\BraveSoftware\Brave-Browser\Application\brave.exe", rf"{r}\Chromium\Application\chrome.exe"]
    else:
        for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "microsoft-edge-stable",
                  "brave-browser", "vivaldi"):
            w = shutil.which(n)
            if w:
                cands.append(w)
    seen, out = set(), []
    for c in cands:
        if c not in seen and Path(c).is_file():
            seen.add(c)
            out.append(c)
    return out


def open_app_window(url: str, profile_dir: Path, width: int = 1440, height: int = 920) -> str | None:
    """アプリのウィンドウ（アドレスバーなし）で開く。使った方法の名前。開けなければ None。

    1. Chrome・Edge などの --app（このアプリ専用のプロフィールで、ふだんのブラウザとは別の窓・別の Dock アイコンになる）
    2. pywebview（pip install pywebview。入っていれば、OS の WebView で開く）
    """
    for exe in chromium_browsers():
        try:
            profile_dir.mkdir(parents=True, exist_ok=True)
            subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile_dir}", f"--window-size={width},{height}",
                              "--no-first-run", "--no-default-browser-check", "--disable-sync"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **detached())
            return Path(exe).stem
        except OSError:
            continue
    try:
        import webview  # type: ignore  # noqa: F401
    except ImportError:
        return None
    subprocess.Popen([str(gui_python()), "-m", "pdf_latex_overlay", "window", url, "--width", str(width), "--height", str(height)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **detached())
    return "pywebview"
