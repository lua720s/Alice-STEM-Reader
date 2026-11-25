"""macOS のアプリ（.app）を作る。Launchpad・Spotlight・Raycast などのランチャーから開ける。

- **Alice STEM Reader.app**: ビューアを起動して、いつものブラウザで開く。
  Finder で資料を右クリック →「このアプリケーションで開く」にも出る（PDF・EPUB・DjVu・画像・Markdown・LaTeX など）。
  既定で開くアプリは奪わない（LSHandlerRank = Alternate）。資料やフォルダを、アイコンにドロップしても開く。
- **Alice STEM Snip.app**: 画面の範囲をドラッグで選ぶと、その部分を読んで、結果をクリップボードに入れる（Mathpix Snip と同じ使い方）。
  ショートカットに割り当てると便利（システム設定 → キーボード → ショートカット、Raycast・Alfred のホットキーなど）。

アプリは薄い起動部分（AppleScript のアプレット）だけで、プログラムと仮想環境は、このプロジェクトのものを使う
（場所を埋め込むので、プロジェクトを動かしたら作り直す）。署名は自分用（ad-hoc）で、配布用ではない。
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .applaunch import APP_NAME, SNIP_APP_NAME, log_path

BUNDLE_ID = "io.github.alice-stem-reader.reader"
SNIP_BUNDLE_ID = "io.github.alice-stem-reader.snip"
LSREGISTER = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
ICON_PNG = Path(__file__).resolve().parent / "assets" / "icon-1024.png"

# Finder の「このアプリケーションで開く」に出す形式（UTI と拡張子）
DOC_TYPES = [
    ("PDF Document", ["com.adobe.pdf"], ["pdf"]),
    ("EPUB Book", ["org.idpf.epub-container", "org.idpf.epub-folder"], ["epub"]),
    ("DjVu Document", ["com.lizardtech.djvu", "org.djvu.djvu"], ["djvu", "djv"]),
    ("Image", ["public.image"], ["png", "jpg", "jpeg", "tif", "tiff", "heic", "heif", "webp", "bmp", "gif"]),
    ("Markdown", ["net.daringfireball.markdown"], ["md", "markdown"]),
    ("LaTeX Source", ["org.tug.tex", "public.tex"], ["tex", "latex", "ltx"]),
    ("Plain Text", ["public.plain-text"], ["txt"]),
    ("E-book", [], ["mobi", "fb2", "xps", "oxps", "cbz"]),
    ("Folder", ["public.folder"], []),
]


def _q(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def applescript(python: str, log: str) -> str:
    return f'''property py : "{_q(python)}"
property logf : "{_q(log)}"

on buildCmd(args)
	return quoted form of py & " -m pdf_latex_overlay " & args
end buildCmd

on run
	-- 選択画面は出さない。ビューアを起動（または画面を開く）して、開くものは画面の「開く」から選ぶ
	my startViewer("")
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
		do shell script (my buildCmd("app-open --hook") & args & " 2>&1")
	on error errMsg
		-- 失敗の理由（例: 同じ資料が別のビューアで開かれている、変換に必要な道具がない）を、ダイアログで知らせる
		display alert "{APP_NAME}" message errMsg as warning
	end try
end startViewer
'''


def snip_applescript(python: str) -> str:
    return f'''property py : "{_q(python)}"

on run
	try
		do shell script quoted form of py & " -m pdf_latex_overlay app-snip 2>&1"
	on error errMsg
		display alert "{SNIP_APP_NAME}" message errMsg as warning
	end try
end run
'''


def _icns(dest_icns: Path) -> bool:
    """同梱の 1024px の PNG から .icns を作る（sips と iconutil。macOS のみ）。"""
    if not ICON_PNG.is_file() or not shutil.which("iconutil") or not shutil.which("sips"):
        return False
    with tempfile.TemporaryDirectory() as td:
        iset = Path(td) / "icon.iconset"
        iset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = size * scale
                name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
                subprocess.run(["sips", "-z", str(px), str(px), str(ICON_PNG), "--out", str(iset / name)],
                               check=True, capture_output=True)
        r = subprocess.run(["iconutil", "-c", "icns", str(iset), "-o", str(dest_icns)], capture_output=True)
        return r.returncode == 0 and dest_icns.is_file()


def _compile(app: Path, script: str):
    if app.exists():
        shutil.rmtree(app)
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "launcher.applescript"
        src.write_text(script, encoding="utf-8")
        subprocess.run(["osacompile", "-o", str(app), str(src)], check=True, capture_output=True, text=True)


def _finish(app: Path, info_update: dict, register: bool):
    res = app / "Contents" / "Resources"
    plist_path = app / "Contents" / "Info.plist"
    info = plistlib.loads(plist_path.read_bytes())
    info.update(info_update)
    if _icns(res / "applet.icns"):
        (res / "Assets.car").unlink(missing_ok=True)  # 新しい macOS の osacompile は、こちらのアイコンを優先する
        info.pop("CFBundleIconName", None)
        info["CFBundleIconFile"] = "applet"
    plist_path.write_bytes(plistlib.dumps(info))
    subprocess.run(["codesign", "--force", "--deep", "-s", "-", str(app)], check=True, capture_output=True, text=True)
    if register and Path(LSREGISTER).exists():
        subprocess.run([LSREGISTER, "-f", str(app)], check=False, capture_output=True)


def _common_info(name: str, bundle_id: str) -> dict:
    from . import __version__

    return {"CFBundleIdentifier": bundle_id, "CFBundleName": name, "CFBundleDisplayName": name,
            "CFBundleShortVersionString": __version__, "LSApplicationCategoryType": "public.app-category.education",
            "NSHumanReadableCopyright": "Alice STEM Reader - runs locally; documents never leave this Mac."}


def build_app(dest_dir, python: str | None = None, register: bool = True) -> Path:
    if sys.platform != "darwin" or not shutil.which("osacompile"):
        raise RuntimeError(".app は macOS（osacompile が必要）でだけ作れる")
    python = python or sys.executable
    dest = Path(dest_dir).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    app = dest / f"{APP_NAME}.app"
    _compile(app, applescript(python, str(log_path())))
    doc_types = []
    for name, utis, exts in DOC_TYPES:
        t = {"CFBundleTypeName": name, "CFBundleTypeRole": "Viewer", "LSHandlerRank": "Alternate"}  # 既定のアプリは奪わない
        if utis:
            t["LSItemContentTypes"] = utis
        if exts:
            t["CFBundleTypeExtensions"] = exts
        doc_types.append(t)
    _finish(app, {**_common_info(APP_NAME, BUNDLE_ID), "CFBundleDocumentTypes": doc_types,
                  "NSScreenCaptureUsageDescription": "Reads the area of the screen you select. Images never leave this Mac."}, register)
    return app


AGENT_SRC = Path(__file__).resolve().parent / "agent" / "snip_agent.swift"
AGENT_EXE = "AliceSTEMSnip"


def compile_agent(dest_exe: Path) -> str | None:
    """メニューバーのアプリ（Swift）をコンパイルする。失敗したら理由を返す。"""
    swiftc = shutil.which("swiftc")
    if not swiftc:
        return "swiftc が見つからない（Xcode か Command Line Tools: xcode-select --install）"
    dest_exe.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([swiftc, "-O", "-o", str(dest_exe), str(AGENT_SRC), "-framework", "Cocoa", "-framework", "Carbon"],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not dest_exe.is_file():
        return (r.stderr or r.stdout or "swiftc failed").strip()[-1500:]
    return None


def build_snip_app(dest_dir, python: str | None = None, register: bool = True) -> Path:
    """Alice STEM Snip.app: メニューバーに常駐し、全体のショートカット（既定 ⌃⌘M など）で Snip する。

    swiftc がなければ（またはコンパイルに失敗したら）、起動するたびに Snip するだけのアプレットにする（ショートカットは使えない）。
    """
    if sys.platform != "darwin" or not shutil.which("osacompile"):
        raise RuntimeError(".app は macOS（osacompile が必要）でだけ作れる")
    import json

    from .applaunch import config_path

    python = python or sys.executable
    dest = Path(dest_dir).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    app = dest / f"{SNIP_APP_NAME}.app"
    with tempfile.TemporaryDirectory() as td:
        exe = Path(td) / AGENT_EXE
        err = compile_agent(exe)
        if err is None:
            if app.exists():
                shutil.rmtree(app)
            (app / "Contents" / "MacOS").mkdir(parents=True)
            res = app / "Contents" / "Resources"
            res.mkdir()
            shutil.copy2(exe, app / "Contents" / "MacOS" / AGENT_EXE)
            (res / "agent.json").write_text(json.dumps({"python": python, "config": str(config_path()),
                                                         "log": str(log_path())}, ensure_ascii=False), encoding="utf-8")
            info = {**_common_info(SNIP_APP_NAME, SNIP_BUNDLE_ID), "CFBundleExecutable": AGENT_EXE, "CFBundlePackageType": "APPL",
                    "CFBundleInfoDictionaryVersion": "6.0", "CFBundleVersion": "1", "LSUIElement": True,
                    "LSMinimumSystemVersion": "11.0", "NSHighResolutionCapable": True,
                    "NSScreenCaptureUsageDescription": "Reads the area of the screen you select. Images never leave this Mac."}
            (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
            if _icns(res / "applet.icns"):
                info["CFBundleIconFile"] = "applet"
                (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
            subprocess.run(["codesign", "--force", "--deep", "-s", "-", str(app)], check=True, capture_output=True, text=True)
            if register and Path(LSREGISTER).exists():
                subprocess.run([LSREGISTER, "-f", str(app)], check=False, capture_output=True)
            _remember_snip_app(app)
            return app
    print(f"注意: メニューバーのアプリを作れなかったので、全体のショートカットは使えません（理由: {err}）。"
          "起動するたびに Snip するだけのアプリを作ります。", file=sys.stderr)
    _compile(app, snip_applescript(python))
    _finish(app, {**_common_info(SNIP_APP_NAME, SNIP_BUNDLE_ID),
                  "NSScreenCaptureUsageDescription": "Reads the area of the screen you select. Images never leave this Mac."}, register)
    _remember_snip_app(app)
    return app


def _remember_snip_app(app: Path):
    from .applaunch import write_config

    write_config({"snip_app": str(app)})
