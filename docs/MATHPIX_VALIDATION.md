# Mathpix validation record

## Official specification (checked 2026-11-18)

The [conversion options for mmd.overlay.pdf](https://docs.mathpix.com/reference/shared-types#conversion-options-for-mmdoverlaypdf) list an optional boolean `math_latex_layer`, default `false`. `true` switches the math text drawn by the conversion from a spoken-math representation to the recognized LaTeX. The spoken alternative for `/Formula` in tagged output is kept. How existing PDF text is handled has to be checked on samples.

Following [Accessible PDF](https://docs.mathpix.com/guides/accessible-pdf) and the [PDF API](https://docs.mathpix.com/reference/post-v3-pdf), `POST /v3/pdf` receives `file` and the following `options_json` as multipart. Authentication headers follow the [authentication spec](https://docs.mathpix.com/reference/authentication).

```json
{
  "conversion_formats": {"mmd.overlay.pdf": true},
  "conversion_options": {
    "mmd.overlay.pdf": {"math_latex_layer": true}
  }
}
```

Get the PDF ID, wait for completion with `GET /v3/pdf/{pdf_id}` and the conversion status, then save `GET /v3/pdf/{pdf_id}.mmd.overlay.pdf` under a separate name. Check conversion-specific errors too. Keys are read from environment variables or an approved secret store and never appear in logs or commits.

## A/B measurement plan

1. Prepare two synthetic PDFs with identical content: a vector PDF with normal text and a rasterized copy of the same pages. Include inline formulas, fractions, roots, multi-line display formulas and a page without formulas; record the expected LaTeX and regions in a separate file.
2. Compare `math_latex_layer=false/true` for each PDF (four jobs). Fix the default tagged output for the first run. External submission is limited to the synthetic PDFs and only done when authentication and quota can be confirmed. A POST whose response was lost is not retried automatically, to avoid duplicate jobs and charges.
3. In macOS Preview and another available viewer (Acrobat Reader or Chrome), select only the formula and paste into plain text. Also copy ranges that include surrounding text, and record omissions, duplicates, ordering and offsets. Run `pdftotext` region extraction as well and store CLI extraction and GUI copy results separately.
4. Compare the input SHA-256, page attributes and full-page rendering differences. Store both the raw LaTeX string and the normalized comparison. Normalization is limited to outer math delimiters, line endings and surrounding whitespace; commands and formula contents are never changed.
5. Record each cell as PASS, FAIL or not done, with viewer and OS versions, input kind, formula ID, request conditions, the actual copied text and the diff. HTTP success alone never counts as evidence that a setting worked. If both settings give the same result, separate "not applied", "existing text layer", "not recognized" and "viewer behavior".

## Synthetic textbook (2026-11-18)

Two image-only synthetic pages (B5, 10.5 pt body text, 300 dpi) were converted with the setting on, using the existing Mathpix CLI configuration with `accessible: true`, `math_latex_layer: true`, `language: ja`. The output had a structure tree, both pages rendered pixel-identical to the original at 144 dpi, and the original's SHA-256 was unchanged.

Checked with PDFKit range selection and a temporary clipboard: on page 1, both display formulas could be copied as LaTeX. On page 2 two formulas had become one `aligned` block — selecting the upper one returned both, and selecting only the lower one returned nothing. Comparison with the setting off, vector input and manual operation in Preview were not done.

## Validation workflow (2026-11-18, nothing sent)

`alice-stem-reader mathpix-plan outputs/fixtures` prints, for the four jobs (vector / rasterized × `math_latex_layer` false / true), the `options_json` above, the input SHA-256, the `mpx scs convert` command (PDF API, without `--async`) and the post-retrieval check commands. It sends nothing and reads no credentials (verified in tests by forbidding socket connections).

Retrieved PDFs are evaluated with:
- `verify-derived ORIGINAL DERIVED` — compares page attributes and pixel differences.
- `copycheck DERIVED --formulas expected.json` — MuPDF and PDFKit copy comparison table (raw and normalized).

## A/B measurement on the synthetic fixtures (2026-11-19)

With explicit approval, the four `mathpix-plan` jobs were sent one at a time to the PDF API (`mpx scs convert`, without `--async`): four submissions, no retries, each finished within seconds. Input: the synthetic fixtures (6 pages, 11 formulas, Computer Modern math images). The SHA-256 of both inputs was unchanged before and after. Options: `accessible: true` and `math_latex_layer` false/true. Output in `outputs/mathpix/` (not tracked).

| Input | Setting | Page attributes | Pixel difference (72 / 144 dpi) | Tagged | Text obtained by PDFKit rectangle selection |
| --- | --- | --- | --- | --- | --- |
| Vector | off | identical | **yes** (e.g. 14,650 pixels on page 1 at 144 dpi; visually identical, glyph-edge shifts) | yes | spoken math (`StartFraction negative b …`) |
| Vector | on | identical | **yes** (same) | yes | display math as LaTeX wrapped in `\[ … \]` |
| Rasterized | off | identical | none | yes | spoken math |
| Rasterized | on | identical | none | yes | display math as LaTeX wrapped in `\[ … \]` |

With the setting **on**, selecting each formula's visual rectangle enlarged by 15% on every side and comparing after removing outer delimiters and whitespace (same result for both inputs):

- 7 display formulas (including rotation 0/90/180/270 and a non-zero CropBox): 5 exact matches; the other 2 were mathematically identical (`d x` vs `\,dx`, `\rightarrow` vs `\to`).
- Inline formula `a^{2}+b^{2}=c^{2}`: not LaTeX — a replacement character (U+FFFC) for vector input, plain `a 2 +b 2 =c 2` for rasterized input.
- Formula overlapping body text `\alpha+\beta=\gamma`: could not be extracted as LaTeX.
- Formula clipped at the page edge: nothing was extracted.

**Findings**
- The LaTeX text layer is wider than the visible formula (it includes `\[` and `\]`). Selecting exactly the visible rectangle loses characters at the edges; without enlargement the result started like `[ x=…` with the `\` missing — the same "missing leading backslash" seen earlier on real documents.
- With the setting off, formulas become spoken math, so the effect of `math_latex_layer` is confirmed.
- For vector input the output is visually the same but not pixel-identical, which fails this project's initial criterion (zero pixel difference). Rasterized input matched.

**Compared with this project's method** (same fixtures, known LaTeX): invisible-text embedding gives zero pixel difference for both inputs, and PDFKit returns the LaTeX for every formula, including inline ones, when selecting the visible rectangle. The correctness of the LaTeX itself still depends on the recognizer (UniMERNet etc.) or manual correction.

**Not done:** manual drag selection in macOS Preview; copying in Acrobat Reader and Chrome; comparison on real documents.
