# Roadmap

| Step | Deliverable and exit condition | Example commit |
| --- | --- | --- |
| 0 | Design, synthetic sample generation, expected formulas and regions, original-vs-rendering comparison | `test: add synthetic PDF fixtures and appearance checks` |
| 1 | Four Mathpix jobs and a copy table. If API terms are unavailable, record it as not done and continue locally with synthetic data | `feat: add Mathpix overlay validation workflow` |
| 2a | UniMERNet recognition from manual rectangles. Record CPU run time, memory and matches against expected formulas on a Mac | `feat: recognize formula crops with UniMERNet` |
| 2b | Whole-page detection, coordinate mapping, duplicate suppression. Report IoU against expected rectangles, misses and false positives | `feat: detect and map formula regions` |
| 2c | Implement both embedding methods with known LaTeX, compare copying and structure preservation, choose the default | `feat: compare invisible text and ActualText embedding` |
| 2d | End-to-end CLI tests; failure, interruption and resume tests; example of calling from another application | `feat: add verified PDF processing CLI` |
| 3 | Non-PDF documents (EPUB, DjVu, images, Markdown, LaTeX), Snip (screen, page region, images, handwriting), Snip dashboard, Mathpix-style customizable shortcuts, menu bar agent | `feat: multi-format documents, Snip OCR, launcher apps` |

Commit locally at each step after reviewing the diff and the relevant tests. If tests fail, report the state and do not create a commit that claims success. Sensitive data, generated PDFs, logs and model weights stay out of Git; the generation procedure and expected values of the synthetic samples are tracked.

## First unattended (overnight) run

Run a one-page trial on synthetic samples first. Provisional limits: 6 hours overall, 20 pages, 1 worker, 2 retries, 4 new Mathpix jobs; adjust after measuring. Before starting, check authentication, permissions, budget, models, output location and free disk space. If success cannot be determined, stop in a "needs review" state.

On deadline or interruption, stop accepting new work, give the current page a short grace period, and terminate child processes if needed. HTTP 429 and transient network errors are retried with a cap, only for requests that are safe to resend (fetching and polling). Authentication failures, spec violations, lack of disk space, rendering differences and changes to the original are stop conditions. The morning summary includes elapsed time, success and exclusion counts, failure reasons, resume conditions, output locations and commit IDs. GUI checks that cannot be performed unattended are recorded as not done.

## Implementation plan (2026-11-18)

| Step | Content | Main technology |
| --- | --- | --- |
| 0 | Package skeleton; synthetic fixtures (vector and rasterized, rotation 90/180/270, non-zero CropBox, no formulas, overlap with body text, long LaTeX and backslashes); expected formula/rectangle JSON; original hash and appearance checks | Python 3.12 `.venv`, PyMuPDF, numpy, pytest |
| 2c | Two embedding methods with known LaTeX (invisible text `3 Tr`, `/Span /ActualText`); region extraction and copy comparison in PDFKit | pikepdf (content stream append), Swift PDFKit |
| 2d | `process_pdf()` and CLI: refuse output collisions, temp file → verify → publish by link, nothing published on failure, SIGINT/SIGTERM, JSON report | Standard library |
| V | Local viewer: show detected boxes, click to copy LaTeX, edit and save (keeping both the raw OCR and the edited version), redraw and re-recognize boxes, save the derived PDF | `http.server` (127.0.0.1 only, token), plain JS |
| 2a | Crop recognition with UniMERNet (CPU, small inputs first). Models live in `models/`, outside Git. Stop installing if free space is below 4 GB | PyTorch, UniMERNet |
| 2b | Import MinerU results (page, rectangle, LaTeX), coordinate mapping, duplicate suppression, IoU against expected rectangles | MinerU output JSON |
| 1 | Dry run of the Mathpix validation workflow; real submissions only after individual approval | existing `mpx` |

Each step is committed after its tests pass. Licenses of PyMuPDF (AGPL) and the models are rechecked before distribution.
