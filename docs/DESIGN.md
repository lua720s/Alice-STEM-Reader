# Design

## Independent module and interface

The Python package `alice_stem_reader` and its CLI are the product. The public entry point is `process_pdf(input_path, output_path, options) -> RunReport`. The CLI calls it, and other applications embed it through an adapter that calls the same entry point (`examples/embed_adapter.py`). The project has no dependency on any particular host application.

- `backends/mathpix` manages upload through retrieval. It is separate from the local path, and sending anything externally is an explicit choice.
- `detect` finds formula regions on rendered pages. It starts from the PDF-Extract-Kit MFD example recommended by UniMERNet, and later stages can also be exercised with manual rectangles and fixed detection results. [UniMERNet](https://github.com/opendatalab/UniMERNet)
- `recognize` sends cropped images to UniMERNet and gets LaTeX back. It starts on the Mac CPU with small inputs; MPS is adopted only after comparing accuracy, memory and time. Dependency versions, model IDs, weight hashes and licenses are recorded.
- `embed` takes confirmed formulas and regions and switches between invisible text and ActualText. The same regions and known LaTeX are used so that only the embedding layer is compared.
- `verify` reopens the output, checks it and returns a versioned JSON report. Detection and recognition results carry a 0-based page number, rectangle, confidence where available, LaTeX and error/exclusion reasons. Missing confidence is treated as missing, not as a value.

Rectangles are stored in unrotated CropBox coordinates with a top-left origin, in points. The transformation used for rendering (scale, rotation, crop origin) is kept, and the four corners are mapped back with its inverse. Conversion to PDF-writer coordinates happens inside the adapter, with round-trip tests for rotation, non-zero origins and clipping.

## Embedding comparison

| Method | Implementation | What is evaluated |
| --- | --- | --- |
| Invisible text | Add text render mode 3 to the original page, with a baseline and width fitted to the region; check the font's Unicode extraction. | Whether long formulas shift the selection; whether text is duplicated with existing text. |
| ActualText | Put `/ActualText` on a Span marked-content sequence per formula, wrapping selectable invisible text, using the library's PDF string encoder. In the comparison fixture the inner ASCII stand-in differs from the LaTeX, to tell which one the viewer uses. | Whether the viewer returns the replacement, whether the stand-in leaks, whether extraction order or existing tags break. |

PyMuPDF is used for drawing and embedding, pikepdf for low-level content editing. Dependency and model licenses are checked before distribution. Formulas that conflict with an existing text layer are excluded explicitly until their effect on surrounding text can be evaluated; the same formula is never appended unconditionally. Existing pages are rasterized only for detection. Methods that replace existing page content are admitted only after their own structure-preservation tests. The default method is chosen from measured copy and appearance results.

[PyMuPDF Shape](https://pymupdf.readthedocs.io/en/latest/shape.html) · [PDF Association: ActualText technique](https://pdfa.org/techniques-for-accessible-pdf/actualtext-provides-correct-extractable-characters-in-place-of-ocr-errors/UA1_Tpdf-G2_06/)

### Measured comparison (2026-11-18, synthetic fixtures)

macOS 27.0.1, PyMuPDF 2.1 (MuPDF 1.28.6), pikepdf 10.18.2. Both vector and rasterized synthetic PDFs (rotation 0/90/180/270, non-zero CropBox) were embedded with both methods (8 vector formulas, 10 rasterized).

| Item | Invisible text | ActualText (inner text replaced by the ASCII stand-in "ALT") |
| --- | --- | --- |
| Appearance (pixel difference on every page at 72 and 150 dpi) | 0 | 0 |
| Page attributes, links, annotations, outlines, forms, structure tree | identical | identical |
| MuPDF region extraction | all exactly equal to the LaTeX | all LaTeX (ActualText is honored) |
| PDFKit rectangle selection (the selection engine used by Preview) | all equal after normalization (one inline vector formula gained a trailing newline and space); each formula appears exactly once in the page text | all "ALT" — PDFKit does not honor ActualText |

**The default is invisible text.** ActualText with LaTeX as its inner text gives the same PDFKit result as invisible text anyway. LaTeX containing non-ASCII characters is excluded from invisible text with a reason (it is drawn with Helvetica / WinAnsi). Manual drag-selection in Preview has not been performed.

**Glyph placement (2026-11-19, changed after real-document results):** the glyph band (Helvetica ascent to descent) is fitted into 65% of the rectangle height and centered vertically. Long LaTeX is set smaller so the horizontal scale never drops below 0.35. This stops PDFKit from merging adjacent formulas into one line and MuPDF from discarding tightly packed identical characters as duplicates.

**Multiple lines and overlaps (2026-11-19)**
- When a box contains several lines of math, one text layer is placed per line in equal horizontal bands.
- Overlapping boxes are never embedded together (their text would mix when selected). Priority: confirmed → edited → smaller area → created earlier.

**Pre-processing and the library (2026-11-19)**
- Detection and recognition can be done before opening the viewer with `prepare` (the same idea as Mathpix Snip Web).
- Results are stored in `library/<first 16 hex digits of the PDF SHA-256>/`: `formulas.json` (same format as the viewer), `meta.json` (name, paths, page count, state, counts, elapsed time) and `history.jsonl`.
- Processed pages and recognized formulas are skipped, so processing can be interrupted and resumed.
- `.prepare.lock` is held during pre-processing and `.viewer.lock` while a document is open, so the same results are never written concurrently.
  - Documents open in the viewer are skipped by pre-processing.
  - While pre-processing runs, the viewer is display-only.
  - Locks whose owner is gone are cleaned up.

**Viewer structure (2026-11-19)**
- `Hub` in `viewer/hub.py` scans folders, builds the tree and switches documents. One document is open at a time; switching stops the previous document's automatic recognition and releases its lock. The Hub owns the recognition and detection models, so switching documents does not reload them.
- Only documents under the folders/files given at startup (or added from the UI) can be listed or opened. IDs are hashes of the path and only IDs from the latest scan are accepted (arbitrary paths cannot be opened). Links and hidden folders are not shown.
- Document state comes from the content-hash library shared with `prepare`. Hashes are computed in a background thread and not recomputed while (size, mtime) is unchanged (`library/.hashcache.json`).
- The UI has three columns: collection, pages, details. The theme is switched with `data-theme` (system / light / dark) and decided by `theme.js` before first paint (a separate file, because the CSP forbids inline scripts).

**Where data lives (2026-11-19)**

1. **The collection is the real folder (nothing is copied).** Folders given to `viewer` are read and listed as they are; documents stay where they are, like an Obsidian vault. Rename, move, delete and new-folder act on the real folders (delete moves to the Trash).
2. **App data is kept elsewhere.** Recognition results (boxes, LaTeX, history, exported PDFs) are stored under `library/<16-digit content hash>/` (`ASR_LIBRARY` to change it; not tracked by Git). Moving a document keeps its results as long as the content is the same. **Nothing is created inside the user's document folders.**
3. **Non-PDF documents** (EPUB, DjVu, images, Markdown, LaTeX, …) are converted to PDFs in `library/_converted/`, keyed by the source file's content hash, with a conversion record (tool, version, time) in a `.json` of the same name. Snip history lives in `library/_snips/`.

**"Failed to fetch" (2026-11-19, investigation):** the browser could not reach the viewer. Three causes were confirmed: (1) the viewer exited, (2) it was restarted and the port/token changed, (3) an unexpected server exception dropped the connection. (3) was reproduced by cropping a box outside the page and fixed (it now returns 400, and unexpected exceptions return a JSON 500). For (1) and (2), a banner with a "Reconnect" button is shown and disappears once the connection is back.

**"Needs review" flags (2026-11-19)**
- `Formula` has `alt_latex` (the other model's reading) and `review` (`disagree`, `failed`). Older JSON without these fields still loads.
- When `recognize_png` is given a `verifier` model, it reads the same image with the same procedure (line splitting, retries, cleanup); if `recognize/verify.py` finds a mathematical disagreement, `review` is set. The primary reading is never changed. If the verifier fails, the primary result is kept and marked `failed`. A missing verifier model is detected at startup (exit code 8).
- Confirming, editing or excluding a formula clears `review` (`alt_latex` is kept). The collection shows a per-document count (`counts.review`).
- Verification is **opt-in** (`--verify`); one model is the default.

## Output and resume

Inputs are read-only. Each run gets its own work area that records the input hash, configuration hash, model versions and page state; resuming is allowed only when these match. The temporary PDF is written on the same file system as the destination and published with an atomic, non-overwriting operation after all checks pass. Jobs requesting the same destination fail as a collision. On SIGINT/SIGTERM no new pages are started, only unpublished temporary files owned by the run are cleaned up, and resume information is kept. OCR results are treated as data; LaTeX is never executed.

## Recognition engines (2026-11-19)

- `recognize.ENGINES` (unimernet / ppformulanet / mathpix; `external` says whether images leave the machine), created with `get_backend(name, **kw)`.
- PP-FormulaNet runs in a separate PaddlePaddle venv (`.venv-pp`) as a JSON Lines worker (`ppformulanet_worker.py`), like UniMERNet.
- Mathpix calls `mpx` as a subprocess. **Without consent (`allow_external`), `_check` stops and nothing is sent.** Consent comes from `--allow-external` on the CLI or the confirmation dialog in the UI → `POST /api/engine` with `confirm_external: true` (boolean only, also checked on the server).
- `Hub.set_engine` refuses to switch while OCR or automatic recognition is running (409), returns 409 and keeps the current engine if weights or `mpx` are missing, and does not allow Mathpix while `--verify` is active.

## The app and the resident viewer (2026-11-19, updated 2026-11-25)

- `appbuild.py` builds an AppleScript applet with `osacompile`, adds document types (PDF, EPUB, DjVu, images, Markdown, LaTeX, text, folders; `LSHandlerRank = Alternate`) to Info.plist, signs it ad hoc and registers it with LaunchServices. `on open` calls `app-open`.
- `applaunch.py` finds the running viewer through a state file (`~/Library/Application Support/Alice STEM Reader/server.json`, mode 600, port and token). If one is running, documents are added with `POST /api/addfiles` and opened with `POST /api/open`. If the browser tab has talked to the server within the last few seconds, no new tab is opened (the page follows the opened document); view commands are queued via `POST /api/ui/push` instead.
- Server: `GET /api/ping` (pid, idle seconds), `POST /api/quit`, `--idle-exit MIN` (exit after inactivity), `--state-file`. All require the token.
- Settings from the previous support folder (`PDF LaTeX Overlay`) are copied over on first use.

## No automatic recognition on open (2026-11-19)

Opening a document does not detect and recognize every page (`serve(auto=False)` is the default; `--auto` restores the old behavior). `app-open` only opens. Recognition is started with "Recognize this page" or "Run OCR" in the ⋯ menu. Pre-processed (`prepare`) results load as before.

## Box deletion, notes, viewer-only folders (2026-11-19)

- **Deletion:** `DELETE /api/formulas/<id>` returns the deleted box. `POST /api/formulas/restore` (undo) and `POST /api/formulas/delete_many` (per page; scope text / all) were added. `delete`, `delete_many` and `restore` are recorded in history.jsonl.
- **Notes:** `GET/POST /api/notes`, stored in the library as `<hash>/notes.json` (`{doc, pages:{"0":…}, updated}`). The PDF itself is never changed. Up to 100,000 characters per note.
- **Viewer-only folders:** `viewer/vfs.py`, stored in `collections.json` at the library root (`folders`, `items:[{sha, name, folder}]`). Documents are referenced by content SHA-256, so they follow moved files. A virtual root (key `v`, folders `v/<id>`) is shown at the bottom of the tree; document rows use `rowKey` (`vf:<folder>:<sha>`) to stay distinct from real rows. `/api/vfs/{mkdir,rename,delete,add,remove,move}`. Real files are never touched.

## Settings (math delimiters) and the open dialog (2026-11-19)

- **Delimiters:** stored data never changes; conversion happens on copy (`outText`) and when text boxes are embedded on save (`export(delim=…)`, `delims.convert_delims`). The rules are identical in `delims.py` and `app.js` (`\( \)` → `$ $`, `\[ \]` → `$$ $$`, or none). The choice is also written to `app.json` so the menu bar Snip agent copies results in the same style.
- **No chooser on launch:** the app's `on run` just calls `app-open`. The viewer can start with nothing open (`Hub([])`); documents are chosen with "+" inside the viewer: `POST /api/pick` (`kind`: pdf / folder) shows `choose file` / `choose folder` via `osascript` on the server and adds the selection. Only fixed script text is executed; no user input is passed in. Returns 501 outside macOS.
- **Remembered folders:** folders added from the UI are saved to `roots.json` and listed on the next launch (`--roots-file`). `POST /api/addroot` adds one to a running viewer.

## Non-PDF documents and Snip (v0.2, 2026-11-25)

- `docformats.py` converts documents to PDF. The viewer, `prepare` and `convert` go through it, so every format shares recognition, export and derived-PDF code.
  - EPUB, MOBI, FB2, XPS, CBZ, SVG: laid out by MuPDF (A5, 11 pt); text layer kept.
  - Images: EXIF orientation applied, one page per image, multi-page TIFF supported. HEIC via pillow-heif, or `sips` on macOS.
  - DjVu: `ddjvu -format=pdf` from DjVuLibre; if missing, conversion stops with installation instructions.
  - Markdown and text: the source is typeset as is (math stays as written and is not interpreted as Markdown). Snipping a region returns the source from the text layer without OCR.
  - LaTeX: compiled with `uplatex+dvipdfmx` (Japanese document classes), `tectonic`, `lualatex`, `xelatex` or `pdflatex`, in that order (`-no-shell-escape`, writes only to the output directory). If compilation is impossible, the source is typeset and the reason recorded.
- `snip.py` reads an image (screen capture, paste, drop, file) or a region of the open document. Auto mode tries "text + inline math" (formula detection → mixed recognition), then math, then text. Results, images, provenance (document, page, region, hash) and engine/version are stored in `library/_snips/` and exported as Markdown, LaTeX, TSV or JSON. Blank entries can be created for writing (`POST /api/snips/new`, the equivalent of the Mathpix Snip editor).
- Screen capture uses macOS `screencapture -i`, run by the viewer server; the result goes to the clipboard with `pbcopy`. Screen Recording permission is needed the first time.
- The browser UI makes no external requests (CSP `'self'` only; KaTeX is bundled).
- Launch hook: if `~/Library/Application Support/Alice STEM Reader/hooks/on-launch` exists, is owned by the user and not writable by others, the app runs it in the background on every launch. Integrations with other applications live in such hooks, not in this project.

## Keyboard shortcuts and the Snip dashboard (v0.2, 2026-11-25)

- `viewer/static/keys.js` defines every action with a default binding. Defaults follow Mathpix Snip: ⌃⌘M snip the screen, ⌃⌘O open the app, ⌃⌘L / ⌃⌘N new LaTeX / Markdown Snip (Ctrl+Alt on other platforms); in the dashboard ←/→ previous/next Snip, ⇧←/⇧→ oldest/newest, ↑/↓ choose a format row, ⇧↑/⇧↓ switch tabs, Enter edit, ⇧Enter or ⌃Enter save, Delete delete. Bindings are changed in Settings (key capture, conflict check, per-action and global reset) and stored in `localStorage`; global ones are also written to `app.json` (`global_hotkeys`, only the values that differ from the defaults) via `POST /api/settings`.
- `agent/snip_agent.swift` is the **Alice STEM Snip** menu bar app (LSUIElement). It registers the global shortcuts with Carbon `RegisterEventHotKey` (no Accessibility permission needed), reloads them when `app.json` changes, and runs `python -m alice_stem_reader app-snip` / `app-open --view … / --new …`. Launching it from Launchpad, or opening it again while it runs, takes a Snip immediately; `--background` starts it silently (used by the Reader app and the optional login item). If `swiftc` is unavailable, `build-app` falls back to an applet that snips on launch, without global shortcuts.
- The dashboard (`viewer/static/dashboard.js`) is a full view with summary tiles, search and filters by kind and source, a list grouped by day with multi-select for export, and a detail pane with OCR (rendered preview plus copyable format rows), Data (provenance and JSON) and Original (image) tabs.

## Windows and Linux, app window, output and export settings (v0.3, 2026-11-25)

- **`osutil.py`** isolates OS differences: settings and log locations (`~/Library/…` on macOS, `%APPDATA%` / `%LOCALAPPDATA%` on Windows, XDG directories on Linux); virtual-environment Python paths (`Scripts\python.exe` on Windows); process liveness (`OpenProcess`/`GetExitCodeProcess` on Windows, because `os.kill(pid, 0)` sends Ctrl+C there); detached child processes (`DETACHED_PROCESS | CREATE_NO_WINDOW` vs. `setsid`); clipboard (`pbcopy`, Win32 `CF_UNICODETEXT`, `wl-copy`/`xclip`/`xsel`); notifications; screen capture (`screencapture -i`; the Windows snipping overlay `ms-screenclip:` with the clipboard polled for a new image and the overlay process watched to detect cancellation; on Linux `grim`+`slurp`, `gnome-screenshot`, `spectacle`, `flameshot`, `maim`, `scrot`, `import`); file dialogs (`osascript`, `zenity`/`kdialog`, tkinter in a subprocess); and moving files to the Trash (Finder, `SHFileOperationW` with undo, `gio trash` or the FreeDesktop.org Trash specification).
- Recognition workers are started with `-X utf8` and UTF-8 pipes so that Japanese text survives on Windows (where the locale encoding is cp932), and without console windows.
- **Launchers** (`build-app`): macOS apps as before; Windows Start-menu and "Send to" shortcuts created through `WScript.Shell` with a generated `.ico`; Linux `.desktop` entries with MIME types, an icon and desktop actions (Snip, dashboard, new LaTeX/Markdown). `--start-at-login` adds the background shortcut agent to the Startup folder / XDG autostart.
- **Global shortcuts on Windows and Linux** (`hotkeys.py`, `hotkey-agent`): Win32 `RegisterHotKey` with a message loop and a timer that re-reads `app.json`; `pynput` on X11. On Wayland applications cannot grab global keys, so the agent explains how to bind the commands in the desktop settings. Defaults follow Mathpix (Ctrl+Alt+M/O/L/N). A pid file keeps a single instance; `ensure_agent()` starts it from the Reader launcher.
- **App window:** `open_ui()` honours `open_mode` (`app` by default). App mode launches a Chromium-family browser with `--app=URL` and a dedicated profile under the settings folder (its own window and taskbar/Dock entry, independent of the user's browsing profile), falling back to pywebview (`window` command) and then to the default browser.
- **Output format and export settings** (`outfmt.py`, mirrored by `viewer/static/format.js`; a test runs both over every combination of options and requires identical output): inline/display delimiters, math-only copy style, multi-line environments and text line handling; export defaults and options for Snips (Markdown, LaTeX, TSV, CSV, JSON; provenance, notes, images as ZIP, compilable LaTeX with text outside math escaped) and for document exports. Stored in `app.json` (`format`, `export`, `open_mode`) and validated by `POST /api/settings`; the earlier `delim`/`single` settings are migrated.
- CI (`.github/workflows/tests.yml`) runs the test suite on Ubuntu, Windows and macOS with Python 3.11 and 3.12. POSIX-only tests (file modes, symlinks) are skipped on Windows.
