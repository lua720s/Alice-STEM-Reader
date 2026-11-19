"""macOS の .app を作る（AppleScript のアプレット + Info.plist の書き換え）。

- Finder で PDF を右クリック →「このアプリケーションで開く」に出る（PDFを開く既定のアプリは、奪わない: LSHandlerRank = Alternate）。
- PDF やフォルダを .app のアイコンにドロップしても開く。ダブルクリックすると、フォルダを選んで開く。
- アプリは薄い起動部分だけで、プログラム本体・仮想環境は、このプロジェクトのものを使う（場所を埋め込むので、プロジェクトを動かしたら作り直す）。
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .applaunch import APP_NAME, log_path

BUNDLE_ID = "com.harukaze.pdf-latex-overlay"
LSREGISTER = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"


def _q(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def applescript(python: str, log: str) -> str:
    return f'''property py : "{_q(python)}"
property logf : "{_q(log)}"

on buildCmd(args)
	return quoted form of py & " -m pdf_latex_overlay " & args
end buildCmd

on run
	-- すでに動いているビューアがあれば、画面を開くだけ
	set rc to do shell script (my buildCmd("app-open --if-running") & " >> " & quoted form of logf & " 2>&1; echo $?")
	if rc is "0" then return
	set lastF to ""
	try
		set lastF to do shell script (my buildCmd("app-open --print-last") & " 2>/dev/null")
	end try
	-- PDF を直接選ぶか、フォルダを選ぶか
	set choice to button returned of (display dialog "開くものを選んでください" buttons {{"キャンセル", "フォルダを選ぶ", "PDFを選ぶ"}} default button "PDFを選ぶ" cancel button "キャンセル" with title "PDF LaTeX Overlay")
	try
		if choice is "PDFを選ぶ" then
			if lastF is not "" then
				set picked to choose file of type {{"com.adobe.pdf"}} with prompt "PDFを選んでください（複数可）" default location (POSIX file lastF as alias) with multiple selections allowed
			else
				set picked to choose file of type {{"com.adobe.pdf"}} with prompt "PDFを選んでください（複数可）" with multiple selections allowed
			end if
			set args to ""
			repeat with f in picked
				set args to args & " " & quoted form of POSIX path of f
			end repeat
			my startViewer(args)
		else
			if lastF is not "" then
				set f to choose folder with prompt "PDFが入っているフォルダを選んでください" default location (POSIX file lastF as alias)
			else
				set f to choose folder with prompt "PDFが入っているフォルダを選んでください"
			end if
			my startViewer(" --folder " & quoted form of POSIX path of f)
		end if
	on error number -128
		return -- キャンセル
	end try
end run

on open theItems
	set args to ""
	repeat with f in theItems
		set args to args & " " & quoted form of POSIX path of f
	end repeat
	my startViewer(args)
end open

on startViewer(args)
	try
		do shell script (my buildCmd("app-open") & args & " 2>&1")
	on error errMsg
		-- 失敗の理由（例: 同じ PDF が別のビューアで開かれている）を、ダイアログで知らせる
		display alert "PDF LaTeX Overlay" message errMsg as warning
	end try
end startViewer
'''


def build_app(dest_dir, python: str | None = None, register: bool = True) -> Path:
    if sys.platform != "darwin" or not shutil.which("osacompile"):
        raise RuntimeError(".app は macOS（osacompile が必要）でだけ作れる")
    python = python or sys.executable
    dest = Path(dest_dir).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    app = dest / f"{APP_NAME}.app"
    if app.exists():
        shutil.rmtree(app)
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "launcher.applescript"
        src.write_text(applescript(python, str(log_path())), encoding="utf-8")
        subprocess.run(["osacompile", "-o", str(app), str(src)], check=True, capture_output=True, text=True)
    plist_path = app / "Contents" / "Info.plist"
    info = plistlib.loads(plist_path.read_bytes())
    info.update({
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleDocumentTypes": [{
            "CFBundleTypeName": "PDF Document",
            "CFBundleTypeRole": "Viewer",
            "LSHandlerRank": "Alternate",  # 既定のアプリは奪わず、「このアプリケーションで開く」に出す
            "LSItemContentTypes": ["com.adobe.pdf"],
        }, {
            "CFBundleTypeName": "Folder",
            "CFBundleTypeRole": "Viewer",
            "LSHandlerRank": "Alternate",
            "LSItemContentTypes": ["public.folder"],
        }],
    })
    plist_path.write_bytes(plistlib.dumps(info))
    subprocess.run(["codesign", "--force", "--deep", "-s", "-", str(app)], check=True, capture_output=True, text=True)
    if register and Path(LSREGISTER).exists():
        subprocess.run([LSREGISTER, "-f", str(app)], check=False, capture_output=True)
    return app
