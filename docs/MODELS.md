# Models and recognition: evaluation record

All measurements below were made locally on an Apple M1 with 8 GB RAM (macOS 27.0.1, CPU only) unless noted. Copyrighted test documents, ground-truth files and derived PDFs are kept in `runs/` (not tracked by Git); only numbers and findings are recorded here. Ground truth was transcribed by hand from magnified images, and the samples are small: treat the results as indications, not benchmarks.

Model weights are installed with `scripts/setup_models.sh`, which downloads them from their publishers and checks the SHA-256 hashes listed here.

## UniMERNet (step 2a)

| Item | Details |
| --- | --- |
| Source | [opendatalab/UniMERNet](https://github.com/opendatalab/UniMERNet); weights on Hugging Face `wanderkid/unimernet_tiny` / `unimernet_small` / `unimernet_base` |
| Versions | package `unimernet==0.2.3`; tiny weights revision `3f09ac4b1cd583be47ea20a7d7daef839473028a` |
| Weights | `unimernet_tiny.pth` 430,075,701 bytes, SHA-256 `6f7608624e2d7549c7f0f05fcfbe073ae521328cf70f1d46374d96f9881d7371` (matches the publisher's LFS hash) |
| License | Apache-2.0 for both package and weights (as stated on PyPI and Hugging Face); recheck before distribution |
| Location | `models/unimernet_*/`, `.venv-ml/` (not tracked) |

The model pins dependencies such as `transformers==4.42.4`, so it runs in a separate venv as a child process (`recognize/unimernet_worker.py`, JSON Lines).

### Manual installation

```bash
python3.12 -m venv .venv-ml
.venv-ml/bin/pip install --no-cache-dir -r requirements-ml.txt
REV=3f09ac4b1cd583be47ea20a7d7daef839473028a
mkdir -p models/unimernet_tiny
for f in config.json preprocessor_config.json tokenizer.json tokenizer_config.json README.md unimernet_tiny.pth; do
  curl -sSfL -o "models/unimernet_tiny/$f" "https://huggingface.co/wanderkid/unimernet_tiny/resolve/$REV/$f"
done
shasum -a 256 models/unimernet_tiny/unimernet_tiny.pth   # must match the hash above
```

Locations can be changed with `PLO_ML_PYTHON` and `PLO_UNIMERNET_DIR`. Without weights or the ML Python, `recognize` stops with exit code 8 (model missing) and writes nothing.

### First measurement (2026-11-18, synthetic fixtures)

- Model load about 19 s, about 1.3 s per formula, peak worker memory about 1.0 GB. MPS not measured.
- 11 synthetic formulas drawn in Computer Modern (matplotlib mathtext). Rasterized PDF at 150 dpi cropped at 200 dpi; vector PDF cropped at 300 dpi.

| Metric | Rasterized PDF | Vector PDF |
| --- | --- | --- |
| Exact match ignoring whitespace | 2 / 11 | 3 / 11 |
| Loose match (ignoring `\textstyle`, `\!`, `\,`, `\mathop{=}`, single-line `array` wrappers; does not guarantee equal meaning) | 7 / 11 | 7 / 11 |
| Mean string similarity | 0.79 | 0.79 |

By inspection, all 9 formulas other than `E=mc^2` drawn as body text and a formula clipped at the page edge were mathematically identical (e.g. `\operatorname*{lim}` vs `\lim`, extra braces). With matplotlib's default sans-serif math font the error rate was much higher (e.g. `\ln x` read as `|\boldsymbol{\cap}\times`): typeface strongly affects accuracy.

- The recognizer emits spaces between tokens. `ocr_latex` keeps the raw output; `latex` holds a version with whitespace collapsed where meaning is unaffected (`tidy_latex`). Spaces before letters following control words, control spaces and spaces inside `\text{...}` are kept.

## Importing MinerU results (step 2b)

`import-mineru` converts MinerU output JSON (produced separately by the user) into this project's formula JSON. MinerU itself is not installed because (1) MinerU 4.2.3 requires torch, transformers 5, gradio, llama-cpp and VLM models, which did not fit the available disk space; (2) it uses its own "MinerU Open Source License", which needs review before distribution.

Supported formats (verified against the MinerU 4.2.3 and docvortex 0.6 sources):

| Format | Formulas | Coordinates |
| --- | --- | --- |
| MinerU 4 `middle.json` (`"schema": "docvortex.middle"`) | blocks with `type: "equation"` | normalized 0–1 |
| `content_list.json` (V1) | `type: "equation"`, `text` (outer `$$` removed) | integers 0–1000 |
| legacy `middle.json` (`pdf_info`) | `interline_equation` / `inline_equation` spans | points, relative to `page_size` |

- **MinerU 4 inline formulas have no rectangle** (an `equation_inline` span has only `type` and `content`). They cannot be embedded individually, so they are recorded as excluded (`inline_no_bbox`); a box drawn in the viewer adds and re-recognizes them.
- Coordinates are taken to be relative to the page as displayed (rotated, top-left origin) and are converted to unrotated storage coordinates. Legacy files are disambiguated with `page_size`. Pages rotated by 180° have the same size before and after rotation and cannot be disambiguated; a `rotate180_frame_ambiguous` warning is recorded, and `--frame unrotated` sets the frame explicitly.
- Formulas overlapping another on the same page with IoU > 0.5 are excluded as `duplicate_of:`.
- Tested with synthetic JSON in all three formats, including rotation 0/90/180/270 and non-zero CropBox: mean IoU against expected rectangles above 0.98 (above 0.9 for content_list because of integer rounding), no misses or false positives; embedding and verification passed.
- With a real MinerU 4 output (`docvortex.middle` 2.0) for a scanned two-page spread, the 3 display-formula blocks were imported with coordinates matching an independent conversion; the 12 inline formulas had no rectangles and were excluded as expected. Two of the display blocks covered several lines and repeated the same terms in their LaTeX, so they were not usable.

## Real-document test (2026-11-19)

A scanned spread (2 pages, image only, no text layer) from a commercial Japanese calculus workbook (integration examples and exercises) was processed locally; nothing was sent externally. The original's SHA-256 was unchanged.

**Target:** 11 single-line formulas (5 definite integrals from the problems, 6 lines from the solutions), rectangles measured by hand. While transcribing, the lower limit of exercise (3) turned out to be 4, not 1 — earlier automated readings had all read 1.

**UniMERNet tiny (CPU)**

| Crop resolution | Loose match | Mathematically correct (by inspection) | Mean string similarity |
| --- | --- | --- | --- |
| 300 dpi | 3 / 11 | 5 / 11 | 0.75 |
| 200 dpi | 4 / 11 | 6 / 11 | 0.78 |
| 150 dpi | 4 / 11 | 6 / 11 | 0.82 |

Resolution changed which formulas were correct. Typical errors: upper limit `e` read as `\infty`; `e^{-4}` as `e^{-1}`; small fractions as `\stackrel`; one formula collapsed into repeated integral signs. **Conclusion at this stage:** recognition of real scans could not be embedded unreviewed; the viewer's check-and-correct workflow is assumed. (Post-processing improvements below later raised tiny to 9 / 11.)

**Embedding and copying (corrected LaTeX, 11 formulas)**
- The first run was blocked by verification (stop-on-failure worked) for two reasons: (1) a 51-character LaTeX squeezed into 59 pt, where MuPDF discarded one of two adjacent `}}` as a duplicate glyph; (2) a formula to the lower right of a tall formula was merged into the same line by PDFKit, so rectangle selection returned nothing.
- Fix: the glyph band is fitted into 65% of the rectangle height and centered, with a minimum horizontal scale of 0.35 (`BAND`, `MIN_HSCALE` in `embed.py`). Regression tests reproducing both situations fail on the old code.
- After the fix: zero pixel difference at 72 and 150 dpi; PDFKit rectangle selection exactly matched all 11 formulas and each appeared once in the page text; MuPDF extraction matched all. Embedding raw UniMERNet output also passed verification (its correctness is as in the table above).
- Not done: manual drag selection in macOS Preview.

### Line splitting and cleanup (2026-11-19)

When a box in the viewer covers two lines of calculation, UniMERNet joined them in `\begin{array}`, mixed in display-only markup (`\displaystyle`, `\!`, double braces) and misread some symbols (a bold 1 as `\bf\Pi`, a leading `=` as `\overline`). Pre- and post-processing was added (`recognize/postprocess.py`, `recognize/region.py`):

- **Line splitting:** horizontal ink profiles find blank gaps of at least 2.5 pt. Fragments under 6 pt tall or narrower than 35% of the widest line (limits of ∑ and ∫) are not counted as lines; partial neighbouring lines cut by the box edge are dropped; thin vertical rules and dotted lines at the left and right edges (frame borders) are whitened.
- **Retrying degenerate output:** output such as repeated tokens is re-read from the image scaled by 0.75 and 1.3.
- **Cleanup:** display-only markup is removed and brace balance repaired; lines are joined with newlines.

On a user-drawn two-line box at 200 dpi, the second line matched exactly; the first line's ending `\int\frac{1}{2}x\,dx` became `\int_{2}xdx` (recognition error). Another three-line box on the same spread was also split correctly. All synthetic fixture formulas stay single-line (not split).

## Formula detection: MFD (2026-11-19)

| Item | Details |
| --- | --- |
| Source | [PDF-Extract-Kit](https://github.com/opendatalab/PDF-Extract-Kit) (the detector recommended by UniMERNet); weights on Hugging Face `opendatalab/PDF-Extract-Kit-1.0`, `models/MFD/YOLO/yolo_v8_ft.pt` |
| Versions | revision `ed6b654c018d742e65a17671e379c5e6ecc87ec9`, `ultralytics==8.4.215` |
| Weights | 349,867,002 bytes, SHA-256 `41029d5abb9b0b6df825ecaf8adf9151762ea0688ac0fc4ea0ea34ab9a5808fc` (matches the LFS hash) |
| License | ultralytics is AGPL-3.0; the PDF-Extract-Kit repository is also marked AGPL-3.0; the model card states none. Always check before distribution |
| Location | `models/mfd_yolo_v8/`, `.venv-ml/` (not tracked) |

**Settings (tuned on the real document):** 200 dpi page images, imgsz 1888, conf 0.1; boxes are enlarged by 3 pt horizontally and 6 pt vertically; boxes at least 80% inside a larger box are dropped; multi-line (isolated) blocks are split into per-line boxes.

| Setting | Of the 11 hand-labelled formulas, boxes covering ≥ 90% |
| --- | --- |
| conf 0.25, no enlargement (close to MinerU defaults) | 5 |
| conf 0.1, enlarged 3 pt / 6 pt (adopted) | 8 |

Missed: the integral in example (2), `∫e^{-t}dt=-e^{-t}+C` in the right column, and the three formulas of exercise (3).

**Whole-spread recognition (2 pages, CPU):** detection took about 20 s and produced 41 boxes (excluding hand-drawn ones); recognizing all 41 took about 85 s. Many solution lines came out nearly correct (e.g. `=-48e^{-4}+16e^{-2}-4e^{-4}+4e^{-2}`, `\int xdx=\frac{1}{2}x^{2}+C`, `\sqrt{x}=t`). Errors included about 8 false detections of Japanese text or circled numbers as inline math, two lines merged into one box through wavy underlines or large brackets, and small-glyph misreadings (`e^{-4}` → `e^{-i}`, subscript `1` → `!`). False boxes are excluded or deleted in the viewer; missed formulas are added by dragging.

## Model size comparison (2026-11-19)

| Model | Weights | Revision | SHA-256 |
| --- | --- | --- | --- |
| tiny | 430 MB | `3f09ac4b…` | `6f760862…` (above) |
| small | 810 MB | `82cb8544…` | `fa54b0a8126bb60060bc90818ce20a5ca1b5dd5d7da5c0983579f5c3a2cc90ea` |
| base | 1.3 GB | `af898d48…` | `16cd0891233cfee3c11215a7b87306f160f7e7f3f52091a6253751c149a8c180` |

Same 11 formulas, same rectangles and post-processing, crops at 150 / 200 / 300 dpi, CPU:

| Model | Load | Per formula | Worker memory | Auto-judged match (exact / allowing notation differences, 200 dpi) | String similarity (200 dpi) |
| --- | --- | --- | --- | --- | --- |
| tiny | 15 s | 0.85 s | ~440 MB | 6 / 7 | 0.935 |
| small | 18 s | 1.2 s | ~800 MB | 5 / 5 | 0.931 |
| base | 28 s | 1.9 s | ~1250 MB | 7 / 8 | 0.956 |

Matches allowing notation differences by resolution (150 / 200 / 300 dpi): tiny 6 / 7 / 4, small 4 / 5 / 5, base 5 / 8 / 7. **200 dpi was best for every model.**

**By inspection (200 dpi)**, counting only mathematically different readings as errors:

| Model | Mathematically correct | Actual errors |
| --- | --- | --- |
| tiny | 9 / 11 | `e^{-4}` → `e^{-1}`, `+` → `\pm`, `e^{-t}` → `e^{-\prime}` |
| small | 7 / 11 | three swapped integration limits (1, `\prime`), a leading `\Big|` |
| base | 9 / 11 | lower limit `4` → `1`, a wavy underline read as `\underbrace` |

- Larger models were not clearly better on this sample: base tied tiny with different errors; small was worse. One formula is about 9 points, so the differences are within noise.
- **Comparing tiny and base is informative:** the 7 formulas where they agreed were all correct, and the 4 where they disagreed all contained a real error — no misses on this sample. This motivated the "needs review" flag below.

## "Needs review": cross-checking two models (2026-11-19)

The primary and a second model read each box with the same procedure; **formulas whose readings differ mathematically are flagged** (`--verify`, judged by `recognize/verify.py`). Differences that do not count: font commands (`\textit`, `\mathrm`, …), spacing (`\,`, `\ `, `~`, …), bracket sizes (`\left`, `\Big`, …), `\dfrac` vs `\frac`, `\to` vs `\rightarrow`, `\operatorname{lim}` vs `\lim`, `\prime` vs `'`, alternative vertical bars, `\ge` vs `\geq`, punctuation-only lines.

**11 labelled formulas (real pipeline, 200 dpi, tiny primary / base verifier)**

| Item | Result |
| --- | --- |
| Flagged | 4 / 11 |
| Not flagged (models agree) | 7 / 11, **all correct** |
| Real errors of the primary model | 2, **both flagged** (no misses) |
| Flagged although the primary was correct | 2 (the verifier was wrong) |
| 150 / 200 / 300 dpi | at all three, the set of disagreements equalled the set where at least one model was wrong |

**48 automatically detected boxes on the same spread:** 30 were flagged. 11 of them were inline boxes that were really Japanese text or circled numbers, where both models produced garbage (flagging is appropriate); most others were real errors such as `_{!}` vs `_{1}`, `e^{-i}` vs `e^{-4}`, `^{!}` vs `^{2}`. The 18 unflagged boxes showed no obvious errors when compared with the image. Only the 11 labelled formulas were scored individually.

**Cost:** about 259 s for 2 pages / 48 boxes (vs about 85 s for tiny alone), roughly 3×; memory for two workers plus detection.

**Limitations:** formulas that both models misread the same way are missed (none on this sample, but possible in general); the sample is small; difficult pages produce many flags (30 of 48). A flag means "needs checking", not "wrong".

## PP-FormulaNet and engine selection (2026-11-19)

The claim that PP-FormulaNet is more accurate was checked against public figures and local measurements.

**Public evaluation** (PaddleOCR documentation, PaddleX internal set, BLEU-style metric that penalizes notation differences): UniMERNet 85.91, PP-FormulaNet-S 87.00, -L 90.36, plus-S 88.71, plus-M 91.45, plus-L 92.22. The set is Paddle's own, not Japanese textbooks.

**Local measurement (11 labelled formulas, same boxes and preprocessing, CPU)**

| Model | 200 dpi | Per formula | Memory | Size |
| --- | --- | --- | --- | --- |
| UniMERNet tiny | mathematically equal 9/11 (exact 6/11) | 0.83 s | ~440 MB | ~430 MB |
| UniMERNet base | 9/11 (exact 7/11) | 1.93 s | ~1250 MB | ~1.3 GB |
| PP-FormulaNet plus-M | 3/11 (exact 2/11) | 0.70 s | ~1050 MB | ~620 MB |
| PP-FormulaNet plus-S | 1/11 (exact 1/11) | 0.27 s | ~580 MB | ~260 MB |

Of plus-M's 8 mismatches, 5 **dropped a leading `=`** (textbook continuation lines often start with `=`; PP-FormulaNet seems to assume a complete equation), 2 misread digits (`4` → `1`) and 1 added a trailing `.`. plus-S had more errors (`\operatorname{l o g}`, `\limits`, `\boxed{}`, duplicated `\frac`, broken subscripts). plus-L and -L were not tried (disk space).

**Conclusion:** on this sample, PP-FormulaNet was not more accurate; it does worse on continuation lines. It remains selectable in the UI and CLI.

- Implementation: `recognize/ppformulanet.py` (worker in the separate venv `.venv-pp`); spaced output is tidied by `tidy_pp`; the model is fetched by PaddleOCR 3.7.0 (`PLO_PP_MODEL`, default plus-M). License: Apache-2.0 for PaddleOCR and the models (as stated by the publisher); recheck before distribution.

```bash
python3.12 -m venv .venv-pp
.venv-pp/bin/pip install --no-cache-dir paddlepaddle==3.3.1 paddleocr==3.7.0 tokenizers ftfy
PADDLE_PDX_CACHE_HOME=$PWD/models/pp_cache PADDLE_PDX_MODEL_SOURCE=huggingface \
  .venv-pp/bin/python -c "from paddleocr import FormulaRecognition as F; F(model_name='PP-FormulaNet_plus-M')"
```

**Mathpix (external service):** `recognize/mathpix.py` sends formula images with `mpx scs convert` (/v3/text). **Without consent (`--allow-external`, or the confirmation dialog in the UI) nothing is sent.** Credentials are left to `mpx configure`; this program neither reads nor stores them. With approval, only 3 crops (cases other models got wrong) were sent: 2 were correct (`\int_{4}^{16}\sqrt{x}e^{-\sqrt{x}}dx`, `=-52e^{-4}+20e^{-2}=\frac{-52+20e^{2}}{e^{4}}`), 1 was misread (`\int e^{-t}dt=-e^{-t}+C` → `\partial+,\partial-=pp_{i}\partial\int`). Three formulas say nothing about a trend. Some outputs kept `\( … \)` delimiters, so delimiter stripping was strengthened (unit-tested only).

## Re-comparison on 33 formulas (2026-11-19)

Of the 48 automatically detected boxes on the same spread, **33 that are readable as formulas** were labelled by hand (Japanese sentences, tables, cut-off boxes and formulas containing Japanese were excluded; the two pages did not contain 50 usable formulas). Boxes are as detected, so crops include line numbers like `1)`, underlines and clipped edges. 200 dpi, same preprocessing.

| Model | Mathematically equal | Ignoring leading `=` and trailing `.` | Time |
| --- | --- | --- | --- |
| UniMERNet base | **24 / 33** | 24 / 33 | 189 s |
| PP-FormulaNet plus-M | 8 / 33 | 22 / 33 | 44 s |
| UniMERNet tiny | 17 / 33 | 18 / 33 | 61 s |

- base > tiny. PP-FormulaNet's biggest loss is dropping the leading `=` (22 / 33 when ignored — above tiny, close to base), which makes it awkward for textbooks with many continuation lines.
- The ranking differs from the 11-formula sample (where tiny and base tied): with small samples the ranking moves.
- Typical errors: tiny adds spurious `\frac{…}{\sqrt{…}}` and picks up `1)`; base adds `\underline` or `\quad`; PP misreads subscripts and integral signs (`\iint`, `\int_{\frac{1}{2}}^{\frac{1}{2}}`).
- Limitations: 33 formulas, hand-made ground truth, the same two pages; Mathpix not included.

**As a result, the default engine is UniMERNet base**; tiny is available as `--backend unimernet_tiny` ("UniMERNet tiny" in the UI). Since the primary is now base, the default second model for `--verify` is the lighter tiny (`PLO_VERIFY_DIR` to change). Trade-off: base is about 3× slower than tiny and uses about 1.25 GB; pre-processing with `prepare` hides the wait.

## User interface languages (2026-11-19)

Japanese, English, Chinese, Spanish and Russian (`viewer/static/i18n.js`, a dictionary keyed by the Japanese strings). The translations have not been reviewed by native speakers. Error messages returned by the server are still in Japanese.

## Text recognition: PaddleOCR (2026-11-19)

UniMERNet reads formulas only; text (including Japanese) is read with PaddleOCR (PP-OCR).

| Item | Details |
| --- | --- |
| Installation | `paddleocr 3.7.0` in `.venv-pp`; models are fetched on first use into `models/pp_cache/official_models/` (not tracked) |
| Models | `PP-OCRv6_medium_det` (line detection) and `PP-OCRv6_medium_rec` (recognition), `lang="japan"` (`PLO_OCR_LANG`) |
| Implementation | `recognize/ppocr_worker.py` (separate process, JSON Lines), `recognize/textocr.py`, `prepare.process_text` |
| Usage | "Recognize text on this page" in the viewer; text becomes green boxes (kind `text`) that can be copied and edited |

**Real document (one page of a Japanese math workbook, 150 dpi, CPU):** about 14–34 s. Headings and instructions were mostly read correctly; errors included a simplified-Chinese character for a Japanese one (例題 → 例题), a misread kanji (積 → 秸) and a stray page number at the start of a line. Not scored individually (inspection only).

**Division of labour with math:** formula boxes (detected, or detected only for this purpose) are whitened before reading text. Undetected formulas or table numbers can still appear as text, so lines that contain neither Japanese characters nor a run of three or more Latin letters are dropped (`looks_like_text`), as are lines with confidence below 0.5.

**Limitations:** embedding positions for vertical text, ruby and multi-column reading order have not been checked.

### Embedding text (2026-11-19)

Text boxes are embedded into the derived PDF by "Save PDF" as **invisible text (render mode 3)**.

- **Font:** only the glyphs actually used are subset with fontTools from the CJK font bundled with PyMuPDF (Droid Sans Fallback Full, Apache-2.0) and embedded (Identity-H, real widths in `/W`, `/ToUnicode`). A few lines per page add a few KB to a few tens of KB. `fonttools` is a dependency.
- **Why not a non-embedded CJK font (UniJIS-UCS2-H):** in a prototype MuPDF could read it, but **PDFKit rectangle selection dropped the last characters of lines** (assumed glyph widths differed from the real font), and a simplified-Chinese character was lost. Subset embedding has exact widths and passed both.
- **Verification:** appearance (zero pixel difference), structure, body text, MuPDF extraction and PDFKit rectangle selection (macOS) all pass. Unlike formulas (which must appear exactly once per page), text may repeat on a page ("Solution", "(1)" …); only the selection result is compared.
- **Exclusions:** characters missing from the bundled font (emoji, etc.: `unsupported_char`), places where the original PDF already has text (`existing_text_conflict`), text overlapping a formula (the formula wins).

**One real page (41 OCR'd lines):** 35 lines embedded, all checks passed; file size 191,039 → 201,768 bytes. Excluded: 5 `too_small` (noise boxes about 5 pt wide that cannot be rectangle-selected) and 1 `unsupported_char` (a formula fragment containing U+2212). Initially the PDFKit check failed and **nothing was published** (stop-on-failure) because adjacent noise boxes leaked into selections; boxes smaller than 8 × 6 pt are now dropped. If only text lines fail the selection check, those lines are excluded as `copy_check_failed` and the run is retried (up to 3 times); failures of any other check, or of formulas, still stop publication.

## Mixed text-and-math regions (Mathpix-Snip-like reading, 2026-11-19)

A hand-drawn box containing both text and formulas used to be sent to UniMERNet as a single formula image; since UniMERNet cannot read Japanese text, the output collapsed.

**Approach** (`recognize/mixed.py`): if the region contains text, it is read in these steps:
1. Collect the formula boxes inside the region from page-wide formula detection (cached per page).
2. Read the region with PaddleOCR **with per-character positions**. Formula boxes that contain Japanese characters are treated as false detections and dropped.
3. Read the remaining boxes with the selected engine. Formula-like character runs that MFD missed (no Japanese characters) are also re-read as math.
4. Assemble text and math line by line: math as `\( … \)` when it shares a line with text, `\[ … \]` when it stands alone.
- Regions with no text are read as math as before. The result is a box of kind `text`, source `mixed`; it can be copied, edited and re-recognized, and is embedded as text in derived PDFs.

**Example** (a problem statement from a Japanese workbook; first run about 42 s, mostly model loading, later runs a few seconds):

```
次の定積分を求めよ.
(1) \(\underline{\int_{1}^{e}x(\log x)^{2}dx}\) \((2)\quad\int_{4}^{16}\sqrt{\,x}\,e^{-\sqrt{\,x}}\,dx\) J4 (早稲田大)
```

Japanese text, numbering and inline math came out as one text; the `(2)` formula missed by MFD was still read as math. Remaining errors: an underline captured as `\underline`, a circled number read as `J4`, and two problems on the same line merged. Not scored individually.

### Follow-up: text detected as a whole-line formula (2026-11-19)

On a binomial-coefficient problem, Japanese words in the middle of a line ("のとき", "when") were garbled into LaTeX. **Cause (an implementation mistake):** a shortcut skipped OCR when formula boxes covered more than 60% of the region, but MFD often boxes a whole line, including Japanese words, as one formula, so the region was wrongly treated as math-only.

**Fixes:**
- The shortcut was removed: OCR always checks for text first (math-only regions now take a few extra seconds).
- A formula box containing **two or more Japanese characters** is dropped and treated as text; formula-like runs left over are re-read as math. A single character (e.g. an integral sign misread as kana) does not count.
- "・" (U+30FB, often a misread "⋯") and "ー" (U+30FC, often a misread minus) are not counted as Japanese.
- Formula boxes extending beyond the region are clipped to it; leading numbers such as "(1)" stay as text; separated terms (`+ ⋯ +`) are merged into one formula.

After the fix "のとき，" was separated as text. Remaining errors: prefix subscripts such as `_{n}C_{r}` read as `\mathrm{\Omega}_{n}\mathrm{C}_{r}` (a UniMERNet limitation, common on short crops), a formula split in the middle of a line, a frame line captured as `|`, a fragment of a kana captured as `\sigma`. Inspection only; first run about 50 s (mostly model loading).

### Follow-up: line breaks and layout (2026-11-19)

On a polynomial-division problem, the output had no line structure and text and formulas were out of order.

**Causes:** lines were grouped by vertical overlap while adding elements one by one, so tall formula boxes and superscripts chained separate lines together; boxes spanning several lines were read as one formula; detection sometimes boxed only one of two side-by-side columns "(1) …  (2) …"; OCR sometimes merged ")" and "(" of adjacent numbers into one word ")(", pulling numbers into formulas.

**Fixes** (`recognize/mixed.py`):
1. **Lines are determined from horizontal bands of the region image** (gaps without ink, using the existing line splitter). Words and formula boxes go to the line containing their vertical centre; boxes spanning several lines are split per line and clipped to the line height.
2. For display-formula boxes, words on the same line are split into **columns**, and columns touching the box are read as one formula including words outside the box; columns containing Japanese stay text.
3. Leading numbers "(1)", "①" are split off as text **even inside a word** (")("); separate "(", "1", ")" are merged into "(1)".
4. A single Latin letter inside Japanese text ("x について…") becomes the variable `\(x\)`. Very short inline formulas (three characters or fewer) use the OCR characters directly, because UniMERNet tends to pick up neighbouring glyphs.

**Result** (real model, first run about 50 s):

```
次の式を \(x\) についての多項式とみて、 商と余りを求めよ.
(1) \((2x^{3}+7x^{2}+5)\div(x+3)\) (2) \((2x^{3}-x^{2}-2x+1)\div(x^{2}+4)\)
(3) \((x^{4}+x^{2}y^{2}+y^{4})\div(x^{2}+xy+y^{2})\)
```

The three lines (instruction, (1) and (2) side by side, (3)) and the split between text and math are correct. This single example was checked by inspection only. The binomial example still shows the prefix-subscript misreading, a split formula and a stray `\sigma`.

## Non-PDF documents, Snip and handwriting (2026-11-25)

- Snip uses the same pipeline as boxes in the viewer: auto mode reads "text + inline math" (detection → mixed recognition), falls back to math, then to text. Images are treated as 200 dpi (one pixel = 1/200 inch), the same resolution used for crops.
- Photos of handwritten notes are rotated according to EXIF before recognition. UniMERNet's training data (UniMER-1M) includes handwritten formulas, but **handwriting accuracy has not been measured in this project**. Handwritten Japanese text read by PaddleOCR is expected to be noticeably less accurate than print.
- For Markdown, plain text and LaTeX sources that could not be compiled, the text layer contains the source, so region Snips return it without OCR (exact).
