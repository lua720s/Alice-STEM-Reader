"""題名クリックでの名前変更と、PDFの拡大・縮小（2026-11-19 のユーザー指示）。動作は、ブラウザで確認した。"""

from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "pdf_latex_overlay" / "viewer" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
KEYS = (STATIC / "keys.js").read_text(encoding="utf-8")


def test_title_is_clickable_and_uses_the_rename_dialog():
    assert 'id="docname" tabindex="0"' in HTML
    assert '$("docname").addEventListener("click", renameTitle)' in JS and "actRename(state.docId)" in JS
    assert "#docname" in CSS


def test_zoom_controls_keys_and_limits():
    for ident in ("zoom-in", "zoom-out", "zoom-label"):
        assert f'id="{ident}"' in HTML, ident
    assert "ZOOM_MIN = 0.5" in JS and "ZOOM_MAX = 5" in JS
    assert "ev.ctrlKey || ev.metaKey" in JS and "zoomReset: () => setZoom(1)" in JS and '"zoomReset"' in KEYS  # ピンチ・⌘ホイール・キー
    assert "#pages.zoomed" in CSS and "min-width: max-content" in CSS  # 拡大すると、左端まで横にスクロールできる
