"""全体のショートカット（Windows・Linux）。macOS は、メニューバーの Snip アプリ（agent/snip_agent.swift）が受け持つ。

- Windows: Win32 の RegisterHotKey（追加のライブラリは要らない）。メッセージループで WM_HOTKEY を受け、
  2 秒ごとの WM_TIMER で設定（app.json の global_hotkeys）の変更を見て、登録し直す。
- Linux（X11）: pynput の GlobalHotKeys（pip install pynput）。Wayland では、アプリが全体のショートカットを
  横取りできないので、デスクトップの設定で `alice-stem-reader app-snip` などをショートカットに割り当てる（README）。
- 既定は Mathpix Snip と同じ Ctrl+Alt+M（Snip）・O（アプリ）・L（LaTeX）・N（Markdown）。
- 1 つだけ動かす（pid ファイル）。動作は、このプロジェクトの Python に任せる（`app-snip` など）。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

from . import applaunch, osutil

ACTIONS = {
    "snip": ["app-snip"],
    "open": ["app-open", "--view", "reader"],
    "latex": ["app-open", "--new", "latex"],
    "markdown": ["app-open", "--new", "markdown"],
}
ORDER = ["snip", "open", "latex", "markdown"]
_running_snip: subprocess.Popen | None = None


def parse(s: str) -> tuple[set[str], str] | None:
    """"ctrl+alt+m" → ({"ctrl", "alt"}, "m")。修飾キーなし・未知のキーは None。"""
    parts = [p for p in (s or "").lower().split("+") if p]
    if len(parts) < 2:
        return None
    mods, key = set(parts[:-1]), parts[-1]
    if not mods <= {"ctrl", "alt", "shift", "cmd"}:
        return None
    if not (len(key) == 1 and key.isalnum() or key == "space" or (key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 19)):
        return None
    return mods, key


def run_action(name: str) -> None:
    global _running_snip
    if name == "snip" and _running_snip is not None and _running_snip.poll() is None:
        return  # Snip の途中
    log = applaunch.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as fh:
        p = subprocess.Popen([str(osutil.gui_python()), "-m", "pdf_latex_overlay", *ACTIONS[name]], stdin=subprocess.DEVNULL,
                             stdout=fh, stderr=fh, **osutil.no_window())
    if name == "snip":
        _running_snip = p


def _config_mtime() -> float:
    try:
        return applaunch.config_path().stat().st_mtime
    except OSError:
        return 0.0


def _claim_pid() -> bool:
    """ほかのエージェントが動いていれば False。動いていなければ、自分の pid を書いて True。"""
    pp = applaunch.agent_pid_path()
    try:
        pid = int(pp.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        pid = 0
    if pid and pid != os.getpid() and osutil.pid_alive(pid):
        return False
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_text(str(os.getpid()), encoding="utf-8")
    return True


def run_agent(once: bool = False) -> int:
    if osutil.IS_MAC:
        print("macOS では、メニューバーの Alice STEM Snip アプリが全体のショートカットを受け持つ（build-app で作る）", file=sys.stderr)
        return 2
    if not once and not _claim_pid():
        print("すでに動いている", file=sys.stderr)
        return 0
    try:
        if osutil.IS_WIN:
            return _run_windows(once)
        return _run_pynput(once)
    finally:
        if not once:
            try:
                if applaunch.agent_pid_path().read_text(encoding="utf-8").strip() == str(os.getpid()):
                    applaunch.agent_pid_path().unlink()
            except OSError:
                pass


# ---------------------------------------------------------------- Windows
MOD = {"alt": 0x0001, "ctrl": 0x0002, "shift": 0x0004, "cmd": 0x0008}
MOD_NOREPEAT = 0x4000
WM_HOTKEY, WM_TIMER = 0x0312, 0x0113


def win_vk(key: str) -> int:
    if key == "space":
        return 0x20
    if key.startswith("f") and key[1:].isdigit():
        return 0x6F + int(key[1:])  # VK_F1 = 0x70
    return ord(key.upper())


def _run_windows(once: bool) -> int:
    from ctypes import wintypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    registered: dict[int, str] = {}

    def register() -> list[str]:
        for hid in list(registered):
            user32.UnregisterHotKey(None, hid)
        registered.clear()
        failed = []
        hk = applaunch.global_hotkeys()
        for i, name in enumerate(ORDER, 1):
            got = parse(hk.get(name, ""))
            if not hk.get(name):
                continue
            if got is None:
                failed.append(name)
                continue
            mods, key = got
            flags = sum(MOD[m] for m in mods) | MOD_NOREPEAT
            if user32.RegisterHotKey(None, i, flags, win_vk(key)):
                registered[i] = name
            else:
                failed.append(name)  # ほかのアプリが使っている
        if failed:
            print(f"登録できなかったショートカット: {', '.join(failed)}", file=sys.stderr, flush=True)
        return failed

    failed = register()
    if once:
        for hid in registered:
            user32.UnregisterHotKey(None, hid)
        print(f"registered: {sorted(registered.values())}; failed: {failed}")
        return 0 if not failed else 1
    user32.SetTimer(None, 0, 2000, None)
    mtime = _config_mtime()
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY and msg.wParam in registered:
            run_action(registered[msg.wParam])
        elif msg.message == WM_TIMER:
            m = _config_mtime()
            if m != mtime:
                mtime = m
                register()
    return 0


# ---------------------------------------------------------------- Linux（X11、pynput）
def pynput_combo(mods: set[str], key: str) -> str:
    order = ["ctrl", "alt", "shift", "cmd"]
    k = f"<{key}>" if (key == "space" or (len(key) > 1 and key.startswith("f"))) else key
    return "+".join([f"<{m}>" for m in order if m in mods] + [k])


def _run_pynput(once: bool) -> int:
    if os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
        print("Wayland では、アプリが全体のショートカットを登録できません。デスクトップの設定（キーボード → ショートカット）で、"
              "次のコマンドを割り当ててください: alice-stem-reader app-snip / app-open / app-open --new latex / app-open --new markdown",
              file=sys.stderr)
        return 3
    try:
        from pynput import keyboard  # type: ignore
    except ImportError:
        print("全体のショートカットには pynput が必要です（pip install pynput）。または、デスクトップの設定で "
              "`alice-stem-reader app-snip` をショートカットに割り当ててください。", file=sys.stderr)
        return 3

    def build() -> dict:
        hk = applaunch.global_hotkeys()
        out = {}
        for name in ORDER:
            got = parse(hk.get(name, ""))
            if got:
                out[pynput_combo(*got)] = (lambda n=name: run_action(n))
        return out

    combos = build()
    if once:
        print(f"registered: {sorted(combos)}")
        return 0
    while True:
        listener = keyboard.GlobalHotKeys(combos)
        listener.start()
        mtime = _config_mtime()
        changed = False
        while listener.is_alive():
            time.sleep(2)
            if _config_mtime() != mtime:
                changed = True
                break
        listener.stop()
        if not changed:
            return 1  # 聞き取りが止まった（X サーバーが閉じた など）
        combos = build()


__all__ = ["run_agent", "parse", "pynput_combo", "win_vk", "ACTIONS"]
