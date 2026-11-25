<p align="center"><img src="src/alice_stem_reader/assets/icon-1024.png" width="112" alt=""></p>

# Alice STEM Reader

**A local-first STEM reader: snip any part of a document and get editable LaTeX or Markdown.**

Read textbooks, papers and lecture notes in a PDF viewer and **make flashcards or write reports and papers as you go**. Draw a box around a formula — or press ⌃⌘M (Ctrl+Alt+M on Windows and Linux) anywhere, just like Mathpix Snip — and editable LaTeX lands on your clipboard. Runs on **macOS, Windows and Linux**, as its own app window or in your browser. The same works for anything on screen, photos of handwritten notes, and EPUB, DjVu, Markdown and LaTeX sources.

- **Runs entirely on your computer.** Documents and images are never uploaded (an external recognition service is used only if you explicitly choose it and consent).
- **Originals are never modified.** Recognition results, notes and converted files are stored separately in a library.
- **Provenance is kept.** Every Snip records the document, page, region, document hash, recognition engine and version, and time.

---

## Features

| | |
| --- | --- |
| **Snip** | Read any region of the screen (macOS), of an open document, or of an image (file, paste, drop). **Formulas become LaTeX; text with formulas becomes text with `\( … \)`.** |
| **Snip dashboard** | All Snips in one place: summary tiles, search, filters by kind and source, a list grouped by day, and a detail view with **OCR / Data / Original** tabs. Each result offers copyable formats (raw LaTeX, `\( \)`, `\[ \]`, Markdown `$ $` / `$$ $$`, text layer), a note field and provenance. Write new LaTeX or Markdown Snips from scratch. |
| **Mathpix-style shortcuts** | ⌃⌘M snip the screen, ⌃⌘O open the app, ⌃⌘L / ⌃⌘N new LaTeX / Markdown Snip (Ctrl+Alt+M/O/L/N on Windows and Linux) — system-wide. In the dashboard ←/→, ⇧←/⇧→, ↑/↓, ⇧↑/⇧↓, Enter, ⇧Enter and Delete work as in Mathpix. **Every shortcut can be changed in Settings.** |
| **Output format settings** | Choose inline (`\( \)`, `$ $`, none) and display (`\[ \]`, `$$ $$`, `equation`, `equation*`, none) delimiters, how math-only results are copied, `aligned` / `gathered` for multi-line formulas, and line handling for text. Applies to copying, Snips (including from shortcuts) and text embedded in PDFs. |
| **Export settings** | Default Snip export format (Markdown, LaTeX, TSV, CSV, JSON); include provenance, notes and images (Markdown/LaTeX as a ZIP); compilable LaTeX documents; flashcard columns and in-cell line breaks; page markers and text boxes in document exports. |
| **App window or browser** | Open the interface as its own app window (via Chrome, Edge, Brave… in app mode, or pywebview) or as a tab in your usual browser. |
| **Click-to-copy formulas** | Detect and recognize the formulas on a page, then click any box to copy its LaTeX. Drag to add missed formulas. |
| **Many formats** | PDF (including scans), EPUB, MOBI, FB2, XPS, CBZ, DjVu, images (PNG, JPEG, TIFF, HEIC, WebP …), Markdown, LaTeX and plain text. |
| **Handwriting** | Read photos of handwritten formulas and notes (phone photos are rotated automatically). |
| **Export** | Snips as **Markdown, LaTeX, TSV (import into flashcard apps) or JSON (with provenance)**. Per document, an ordered **Markdown / LaTeX draft** of all recognized text and formulas, including your notes. |
| **PDFs with copyable formulas** | Create a derived PDF that looks exactly like the original but lets any PDF viewer (e.g. macOS Preview) copy formulas as LaTeX. It is saved only after appearance, structure and copy checks pass. |
| **Review support** | Optionally cross-check with a second model and flag formulas where the two disagree. |
| **Library management** | Folders shown as a tree (like Obsidian); rename, move, move to Trash; viewer-only folders; batch OCR that can be cancelled and resumed. |
| **Usability** | UI in English, Japanese, Chinese, Spanish and Russian; light and dark themes; keyboard-driven; per-document and per-page notes. |

## Getting started

Install once (Python 3.11 or later), then open the app from your launcher. The interface opens in its own app window (or in your browser — see Settings → General).

### macOS

```bash
git clone https://github.com/lua720s/Alice-STEM-Reader.git && cd Alice-STEM-Reader
python3.12 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/python -m alice_stem_reader setup-models         # recognition models (~6 GB, SHA-256 verified)
.venv/bin/python -m alice_stem_reader build-app            # two apps in ~/Applications
```

- **Alice STEM Reader** opens the reader (Launchpad, Spotlight, Raycast…). Add documents or folders with "+" at the top left, or right-click a file in Finder → "Open With" → Alice STEM Reader, or drop it on the icon. Your default apps are not changed.
- **Alice STEM Snip** lives in the menu bar (✂︎) and provides the system-wide shortcuts. Launching it from Launchpad, or pressing ⌃⌘M, starts a screen snip; the result is copied with a notification. Choose **Start at Login** in its menu. Building it requires the Xcode Command Line Tools (`xcode-select --install`); without them, `build-app` creates a simpler app that snips when launched, without global shortcuts.
- macOS asks once for **Screen Recording** permission (System Settings → Privacy & Security) and for access to folders such as Documents.

### Windows

```powershell
git clone https://github.com/lua720s/Alice-STEM-Reader.git; cd Alice-STEM-Reader
py -3.12 -m venv .venv; .venv\Scripts\pip install -e .
.venv\Scripts\python -m alice_stem_reader setup-models
.venv\Scripts\python -m alice_stem_reader build-app --start-at-login
```

- **Start menu:** "Alice STEM Reader" and "Alice STEM Snip" (search for them, or pin them). Documents can also be opened with right-click → **Send to → Alice STEM Reader**, or by dropping them on the shortcut.
- **Global shortcuts** (Ctrl+Alt+M/O/L/N) are handled by a small background process that starts with the Reader app, and at sign-in with `--start-at-login`. No extra packages are needed.
- **Screen snips** use the Windows snipping overlay (the same as Win+Shift+S); the result is read and copied.
- The app window uses Microsoft Edge in app mode (installed with Windows).

### Linux

```bash
git clone https://github.com/lua720s/Alice-STEM-Reader.git && cd Alice-STEM-Reader
python3 -m venv .venv && .venv/bin/pip install -e ".[hotkeys]"
.venv/bin/python -m alice_stem_reader setup-models
.venv/bin/python -m alice_stem_reader build-app --start-at-login
```

- **Application menu / launcher:** "Alice STEM Reader" (also offered under "Open With" for supported files, with quick actions for Snip and the dashboard) and "Alice STEM Snip".
- **Screen snips** use the first available of `grim`+`slurp` (Wayland), `gnome-screenshot`, `spectacle`, `flameshot`, `maim`, `scrot` or ImageMagick `import`. Clipboard: `wl-copy`, `xclip` or `xsel`. File dialogs: `zenity`, `kdialog` or tkinter (`python3-tk`).
- **Global shortcuts** (Ctrl+Alt+M/O/L/N) work on X11 with `pynput` (installed by `.[hotkeys]`). On **Wayland**, applications cannot grab global keys: bind `alice-stem-reader app-snip` (and `app-open`, `app-open --new latex`, `app-open --new markdown`) in your desktop's keyboard-shortcut settings instead.
- The app window uses Chrome, Chromium, Edge or Brave in app mode if installed; otherwise install `.[app]` (pywebview) or use the browser.

Only one viewer runs at a time; it exits automatically after 120 minutes without activity.

## Usage

### Snip

| Action | What it does |
| --- | --- |
| **⌃⌘M** / **Ctrl+Alt+M** (anywhere), **S**, or **Snip screen** | Drag to select a region of the screen; the result is copied (Esc cancels) |
| **Page area** (toggle with **R**), then drag on a page | Read a region of the open document without creating a box. For documents with a text layer, "Use text layer" is offered too |
| Paste an image with **⌘V / Ctrl+V**, drop it on the Snip panel, or choose **Images…** | Read screenshots, photos of handwritten notes, etc. (several at once) |
| **Reading mode** | Auto (tells text and math apart) / Math (LaTeX) / Text |
| **D** or **Dashboard** | Open the Snip dashboard |

Snipping a region of a **Markdown, plain-text, or uncompiled LaTeX source** returns **the source text itself**, without OCR (`$…$` stays intact).

### Snip dashboard

| Key (default) | Action |
| --- | --- |
| ← / → | Previous / next Snip |
| ⇧← / ⇧→ | Oldest / most recent Snip |
| ↑ / ↓ | Choose a format row |
| ⇧↑ / ⇧↓ | Switch tabs (OCR, Data, Original) |
| C or Space (or click a row) | Copy the selected format |
| Enter | Edit; then Enter inserts a new line, **⇧Enter or ⌃Enter saves**, Esc cancels |
| Delete | Delete the Snip |
| / | Search |
| Esc or D | Back to the reader |

The first format row, **Your format**, follows your output-format settings and is selected by default. Select Snips with their checkboxes to export just those; otherwise the export covers the Snips currently shown.

### Settings

Open **Settings** (⌘, / Ctrl+,) — four tabs:

| Tab | Options |
| --- | --- |
| General | Open the interface in an **app window** or a **browser tab** |
| Output format | Inline delimiters (`\( … \)`, `$ … $`, none) · display delimiters (`\[ … \]`, `$$ … $$`, `equation`, `equation*`, none) · math-only copies (no delimiters, auto, always inline, always display) · multi-line formulas (one per line, `aligned`, `gathered`) · text line breaks (keep, join into paragraphs). A live example shows the result. |
| Export | Default Snip export format (Markdown, LaTeX, TSV, CSV, JSON) · math style in Markdown · include provenance / notes / snipped images (Markdown and LaTeX become a ZIP with an `images/` folder) · compilable LaTeX document (`\documentclass`, amsmath, luatexja for Japanese with lualatex) · TSV/CSV card front (note or source) and in-cell line breaks (`<br>`, `\n`, space) · page markers and text boxes in document exports |
| Keyboard shortcuts | Change any shortcut (see below) |

Settings are stored in `app.json` in the settings folder, so the menu bar / background Snip uses the same output format.

### Formula detection, recognition and derived PDFs

- **Recognize this page** (⇧R) detects and recognizes the formulas on the current page. For a whole document, use "Run OCR" in the ⋯ menu (also for folders and multiple selections; progress, cancel and resume are supported).
- **Click a box to copy** its LaTeX; correct, confirm, exclude or re-recognize it in the right panel.
- **Recognize text on this page** (⇧T) reads text, including Japanese (green boxes).
- **Save PDF** (⌘S) creates the derived PDF with formulas (and text) embedded as invisible text. It is not saved if verification fails — for example if the appearance changes by a single pixel or the structure cannot be preserved.
- **Export recognized content as Markdown / LaTeX** (⋯ menu, ⇧⌘E) creates a draft in reading order (top to bottom, left to right), including your notes.

### Keyboard shortcuts

Defaults follow Mathpix Snip. Open **Settings** (⌘,) → **Keyboard shortcuts** to change any of them: click a key, press the new combination (Esc cancels, ⌫ removes the shortcut). Conflicts are detected, and each shortcut or all of them can be reset. Press **?** for the full list.

| Global | macOS (menu bar app) | Windows / Linux (background agent) |
| --- | --- | --- |
| Snip screen | ⌃⌘M | Ctrl+Alt+M |
| Open the app | ⌃⌘O | Ctrl+Alt+O |
| New Snip (LaTeX) | ⌃⌘L | Ctrl+Alt+L |
| New Snip (Markdown) | ⌃⌘N | Ctrl+Alt+N |

| Reader | Key | Reader | Key |
| --- | --- | --- | --- |
| Next / previous page | → ← (also Space, PgDn/PgUp, J/K) | Fit page ⇄ fit width | F |
| Zoom in / out / reset | + − 0 | Show/hide collection | B |
| Copy selected box | C | Delete selected box (undoable) | Delete |
| Next / previous to review | N / ⇧N | Search documents | / |
| Recognize page / text | ⇧R / ⇧T | Page-area Snip mode | R |
| Open document | ⌘O | Save PDF | ⌘S |
| Snip dashboard | D | Settings / shortcut list | ⌘, / ? |

On Windows and Linux, ⌘ in this table is Ctrl.

### Command line

```bash
alice-stem-reader viewer my-documents/ --backend unimernet    # the viewer (127.0.0.1 only)
alice-stem-reader snip photo.jpg --mode auto --copy           # read one image (--screen for a screen region)
alice-stem-reader prepare my-documents/ --time-limit 28800    # recognize ahead of time, e.g. overnight (resumable)
alice-stem-reader convert book.epub book.pdf                  # convert a non-PDF document (original untouched)
alice-stem-reader formats                                     # supported formats and missing tools
alice-stem-reader embed in.pdf out.pdf --formulas f.json      # derived PDF with copyable formulas (verified)
```

`.venv/bin/python -m alice_stem_reader …` (Windows: `.venv\Scripts\python -m alice_stem_reader …`) works the same way. More: `viewer --open-in app|browser`, `setup-models [--tiny]`, `hotkey-agent` (Windows/Linux), `build-app [--start-at-login]`.

## Notes on formats

| Format | How it is opened | Requirements |
| --- | --- | --- |
| PDF | as is | — |
| EPUB, MOBI, FB2, XPS, CBZ, SVG | laid out (A5, 11 pt) and converted to PDF, with a text layer | — |
| Images (PNG, JPEG, TIFF incl. multi-page, WebP, BMP, GIF) | one page per image, orientation corrected | — |
| HEIC (iPhone photos) | same as images | built in on macOS; elsewhere `pip install 'alice-stem-reader[heic]'` |
| DjVu | converted to PDF | DjVuLibre (`brew install djvulibre`) |
| Markdown, plain text | the source is typeset as is (math kept as written) | — |
| LaTeX (.tex) | **compiled to a typeset PDF** (tries `uplatex` for Japanese document classes, then `tectonic`, `lualatex`, `xelatex`, `pdflatex`; shell escape disabled). Without TeX, or if compilation fails, the source is typeset and the reason recorded | a TeX distribution such as MacTeX (optional) |

Converted PDFs are cached by the content hash of the original file and are not regenerated while the content is unchanged.

## Accuracy and limitations

- Formula recognition can use [UniMERNet](https://github.com/opendatalab/UniMERNet) base (default) or tiny, PP-FormulaNet, or Mathpix (external, optional). On 33 formulas from a real scanned textbook, base read 24 correctly, tiny 17 and PP-FormulaNet 8 ([details](docs/MODELS.md)). **Recognition errors do occur.** Check important formulas against the preview and the original image.
- The "needs review" flag (`--verify`) misses formulas that both models misread in the same way.
- Handwriting accuracy depends heavily on legibility, lighting and focus, and has not been measured systematically. Handwritten text (especially Japanese) is less accurate than print.
- On Linux under Wayland, global shortcuts must be bound in the desktop settings (see Getting started). The Windows and Linux launchers and background agent have been tested with automated tests only, not yet by hand on those systems.
- On a CPU, detecting and recognizing one page takes tens of seconds. Process frequently used documents ahead of time with `prepare` so they are ready to copy immediately.

## Data and privacy

| Location | Contents |
| --- | --- |
| Your document folders | Nothing is created (rename, move and Trash only happen when you ask) |
| `library/<document hash>/` | Formula boxes and LaTeX, edit history (`history.jsonl`), notes, exported PDFs |
| `library/_converted/` | PDFs converted from other formats, with a conversion record |
| `library/_snips/` | Snip images and records (source, engine, time) |
| Settings folder — macOS `~/Library/Application Support/Alice STEM Reader/`, Windows `%APPDATA%\Alice STEM Reader\`, Linux `~/.config/alice-stem-reader/` | App settings (`app.json`: added folders, shortcuts, output format, export options, window mode) |

Set `ASR_LIBRARY` to move `library/`.

- The viewer listens on `127.0.0.1` only and requires a per-launch token (it cannot be used from other computers).
- The browser interface makes no external requests (Content-Security-Policy restricted to itself; KaTeX for math rendering is bundled).
- Mathpix is used only after you consent in the confirmation dialog, or with `--allow-external` on the command line.

## Dependencies and licenses

Main dependencies: [PyMuPDF](https://pymupdf.readthedocs.io/) (AGPL-3.0), [pikepdf](https://pikepdf.readthedocs.io/) (MPL-2.0), [UniMERNet](https://github.com/opendatalab/UniMERNet) (Apache-2.0), [PDF-Extract-Kit MFD](https://github.com/opendatalab/PDF-Extract-Kit) / [ultralytics](https://github.com/ultralytics/ultralytics) (AGPL-3.0), [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) (Apache-2.0), [KaTeX](https://katex.org/) (MIT, bundled in `viewer/static/vendor/katex/`), and DjVuLibre (GPL, optional, installed separately). Follow the terms of each license when distributing or embedding. Model weights are not included in the repository; `scripts/setup_models.sh` downloads them from their publishers.

## Development

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q                 # tests on synthetic data (no models needed)
.venv/bin/python -m pytest -q -m model        # tests with real models (after installing them)
```

- [Requirements](docs/REQUIREMENTS.md) · [Design](docs/DESIGN.md) · [Model evaluation](docs/MODELS.md) · [Roadmap](docs/ROADMAP.md)
- To start other programs whenever the app opens, place an executable `hooks/on-launch` in the settings folder (Windows: `on-launch.cmd`, `.bat`, `.ps1` or `.exe`); it runs in the background on each launch.
- CI runs the test suite on Ubuntu, Windows and macOS (`.github/workflows/tests.yml`).
- Example of creating derived PDFs from another application: [examples/embed_adapter.py](examples/embed_adapter.py)

### "Failed to fetch"

The browser could not reach the viewer running on this computer: it has exited (for example after 120 minutes of inactivity) or was restarted with a new address. Open the app again, or open the new URL printed when you started it from the command line. The log is at `~/Library/Logs/Alice STEM Reader.log` (macOS), `%LOCALAPPDATA%\Alice STEM Reader\` (Windows) or `~/.local/state/alice-stem-reader/` (Linux).
