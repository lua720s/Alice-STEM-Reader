"""Windows・Linux への対応（osutil・hotkeys・ランチャー・アプリのウィンドウ）。ここでは、OS の部分を差し替えて流れを調べる。"""

import os
import sys
from pathlib import Path

import pytest

from pdf_latex_overlay import appbuild, applaunch, hotkeys, osutil


def test_paths_per_os(monkeypatch, tmp_path):
    assert osutil.venv_python(Path("v")).as_posix() == ("v/Scripts/python.exe" if osutil.IS_WIN else "v/bin/python")
    monkeypatch.setattr(osutil, "IS_MAC", False)
    monkeypatch.setattr(osutil, "IS_WIN", True)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert osutil.config_dir() == tmp_path / "Roaming" / "Alice STEM Reader"
    assert osutil.venv_python(Path("v")).as_posix() == "v/Scripts/python.exe"
    monkeypatch.setattr(osutil, "IS_WIN", False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert osutil.config_dir() == tmp_path / "cfg" / "alice-stem-reader"


def test_pid_alive():
    assert osutil.pid_alive(os.getpid()) and not osutil.pid_alive(0) and not osutil.pid_alive(2 ** 22 + 12345)


def test_detached_flags(monkeypatch):
    monkeypatch.setattr(osutil, "IS_WIN", True)
    assert osutil.detached()["creationflags"] & 0x08 and "start_new_session" not in osutil.detached()
    assert osutil.no_window() == {"creationflags": 0x08000000}
    monkeypatch.setattr(osutil, "IS_WIN", False)
    assert osutil.detached() == {"start_new_session": True} and osutil.no_window() == {}


def test_linux_screen_capture_tools(monkeypatch, tmp_path):
    monkeypatch.setattr(osutil, "IS_MAC", False)
    monkeypatch.setattr(osutil, "IS_WIN", False)
    have = {"grim", "slurp"}
    monkeypatch.setattr(osutil.shutil, "which", lambda n: f"/usr/bin/{n}" if n in have else None)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert osutil.screen_capture_available() == "grim+slurp"
    calls = []

    class R:
        def __init__(self, rc=0, out=""):
            self.returncode, self.stdout = rc, out

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "slurp":
            return R(0, "10,20 300x200\n")
        Path(cmd[-1]).write_bytes(b"\x89PNG fake")
        return R()

    monkeypatch.setattr(osutil.subprocess, "run", fake_run)
    assert osutil.capture_screen() == b"\x89PNG fake" and calls[1][:3] == ["grim", "-g", "10,20 300x200"]
    monkeypatch.setattr(osutil.subprocess, "run", lambda cmd, **kw: R(1, ""))  # slurp を Esc で取り消した
    assert osutil.capture_screen() is None
    have.clear()
    monkeypatch.delenv("WAYLAND_DISPLAY")
    with pytest.raises(osutil.OSFeatureError, match="grim"):
        osutil.capture_screen()


def test_linux_clipboard_and_trash(monkeypatch, tmp_path):
    monkeypatch.setattr(osutil, "IS_MAC", False)
    monkeypatch.setattr(osutil, "IS_WIN", False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(osutil.shutil, "which", lambda n: "/usr/bin/xclip" if n == "xclip" else None)
    got = {}
    monkeypatch.setattr(osutil.subprocess, "run", lambda cmd, **kw: got.update(cmd=cmd, data=kw["input"]))
    assert osutil.copy_to_clipboard("式 \\(x\\)") and got["cmd"][0] == "xclip" and got["data"] == "式 \\(x\\)".encode()
    monkeypatch.setattr(osutil, "IS_LINUX", True)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    f = tmp_path / "教科書.pdf"
    f.write_bytes(b"x")
    dest = osutil.move_to_trash_native(f)
    assert not f.exists() and dest.read_bytes() == b"x"
    info = (tmp_path / "data" / "Trash" / "info" / "教科書.pdf.trashinfo").read_text(encoding="utf-8")
    assert info.startswith("[Trash Info]\nPath=") and "DeletionDate=" in info


def test_app_window_uses_chromium_with_own_profile(monkeypatch, tmp_path):
    exe = tmp_path / "chrome"
    exe.write_text("")
    monkeypatch.setattr(osutil, "chromium_browsers", lambda: [str(exe)])
    launched = []
    monkeypatch.setattr(osutil.subprocess, "Popen", lambda cmd, **kw: launched.append(cmd))
    assert osutil.open_app_window("http://127.0.0.1:1/#token=t", tmp_path / "prof") == "chrome"
    cmd = launched[0]
    assert "--app=http://127.0.0.1:1/#token=t" in cmd and f"--user-data-dir={tmp_path / 'prof'}" in cmd
    monkeypatch.setattr(osutil, "chromium_browsers", lambda: [])
    monkeypatch.setitem(sys.modules, "webview", None)  # pywebview もない
    assert osutil.open_app_window("http://x", tmp_path / "prof") is None


def test_open_ui_follows_setting(monkeypatch, tmp_path):
    monkeypatch.setenv("PLO_APP_DIR", str(tmp_path / "support"))
    opened = []
    monkeypatch.setattr(applaunch.webbrowser, "open", lambda u: opened.append(("browser", u)))
    monkeypatch.setattr(osutil, "open_app_window", lambda u, prof: opened.append(("app", u)) or "chrome")
    assert applaunch.open_ui("http://a") == "chrome"  # 既定はアプリのウィンドウ
    applaunch.write_config({"open_mode": "browser"})
    assert applaunch.open_ui("http://b") == "browser" and opened == [("app", "http://a"), ("browser", "http://b")]
    applaunch.write_config({"open_mode": "app"})
    monkeypatch.setattr(osutil, "open_app_window", lambda u, prof: None)  # 開けなければブラウザ
    assert applaunch.open_ui("http://c") == "browser"


def test_hotkey_parsing():
    assert hotkeys.parse("ctrl+alt+m") == ({"ctrl", "alt"}, "m")
    assert hotkeys.parse("shift+cmd+f12") == ({"shift", "cmd"}, "f12")
    for bad in ("m", "", "ctrl+", "hyper+m", "ctrl+alt+mm", "ctrl+f20"):
        assert hotkeys.parse(bad) is None, bad
    assert hotkeys.pynput_combo({"alt", "ctrl"}, "m") == "<ctrl>+<alt>+m"
    assert hotkeys.pynput_combo({"ctrl"}, "space") == "<ctrl>+<space>" and hotkeys.pynput_combo({"ctrl"}, "f5") == "<ctrl>+<f5>"
    assert hotkeys.win_vk("m") == ord("M") and hotkeys.win_vk("f1") == 0x70 and hotkeys.win_vk("space") == 0x20
    assert applaunch.PC_HOTKEYS["snip"] == "ctrl+alt+m"  # Mathpix の Windows・Linux と同じ


def test_hotkey_agent_explains_wayland(monkeypatch, capsys):
    monkeypatch.setattr(osutil, "IS_MAC", False)
    monkeypatch.setattr(osutil, "IS_WIN", False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("DISPLAY", raising=False)
    assert hotkeys.run_agent(once=True) == 3
    assert "app-snip" in capsys.readouterr().err


def test_linux_launchers(tmp_path):
    made = appbuild.build_linux(python="/opt/my venv/bin/python", start_at_login=True, register=False, home=tmp_path)
    reader = (tmp_path / ".local/share/applications/alice-stem-reader.desktop").read_text(encoding="utf-8")
    assert 'Exec="/opt/my venv/bin/python" -m pdf_latex_overlay app-open --hook %F' in reader
    assert "MimeType=application/pdf;application/epub+zip;" in reader and "image/vnd.djvu" in reader and "text/x-tex" in reader
    assert "[Desktop Action snip]" in reader and (tmp_path / ".local/share/icons/hicolor/256x256/apps/alice-stem-reader.png").is_file()
    assert any(p.name == "alice-stem-hotkeys.desktop" for p in made)
    assert appbuild._desktop_quote('a "b" $c') == '"a \\"b\\" \\$c"' and appbuild._desktop_quote("plain") == "plain"


def test_windows_launchers_use_powershell_shortcuts(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("PLO_APP_DIR", str(tmp_path / "support"))
    calls = []
    monkeypatch.setattr(appbuild.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    made = appbuild.build_windows(python=str(tmp_path / "Scripts" / "python.exe"), start_at_login=True)
    names = [p.name for p in made]
    assert names.count("Alice STEM Reader.lnk") == 2 and "Alice STEM Snip.lnk" in names  # スタートメニューと「送る」
    assert any(p.parent.name == "Startup" for p in made)
    first = calls[0]
    assert first[0] == "powershell" and first[-6].endswith("Alice STEM Reader.lnk") and "app-open --hook" in first[-4]
    assert (tmp_path / "support" / "alice-stem-reader.ico").is_file()


def test_hook_on_windows_runs_cmd_file(monkeypatch, tmp_path):
    monkeypatch.setenv("PLO_APP_DIR", str(tmp_path / "support"))
    monkeypatch.setenv("PLO_APP_LOG", str(tmp_path / "log" / "app.log"))
    monkeypatch.setattr(osutil, "IS_WIN", True)
    launched = []
    monkeypatch.setattr(applaunch.subprocess, "Popen", lambda cmd, **kw: launched.append((cmd, kw)))
    assert applaunch.run_hook() is False
    (applaunch.hooks_dir()).mkdir(parents=True)
    (applaunch.hooks_dir() / "on-launch.cmd").write_text("@echo off\n")
    assert applaunch.run_hook() is True and launched[0][0][:2] == ["cmd", "/c"] and "creationflags" in launched[0][1]
