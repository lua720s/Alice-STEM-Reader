# Requirements and acceptance criteria

| Area | Acceptance criterion |
| --- | --- |
| Original protection | The SHA-256 of the input PDF is identical before and after on every code path. Collisions with existing files, with the input itself (same inode), or via links are refused. |
| Appearance | Page count, order, MediaBox, CropBox and rotation match. With the same renderer, DPI and color settings, zero pixel difference on every page is the initial criterion; any difference is recorded (location and count) and publication stops. |
| Mathpix | Compare settings on and off for the same sheet, and determine per viewer which combination yields the expected LaTeX when the target formula is selected. Upload results alone do not count as a pass. |
| Local | Measure recognition and embedding separately on both image-only and text PDFs. macOS Preview is the primary target; other viewers and extraction CLIs are measured too. Supported formulas must be fully retrievable in the primary viewer, with no body text mixed in and no double copies. |
| PDF structure | Check body text extraction, links, annotations, outlines, forms and existing tags. Structures whose preservation cannot yet be confirmed, and encrypted or signed PDFs, stop with a reason. |
| Failures | Configuration errors, missing models, timeouts, failure to create selection ranges and failed verification never publish a derived PDF; an exit code and the failing location are reported. |

The synthetic fixtures include vector and scanned pages, rotations of 90/180/270 degrees, non-zero CropBox, pages without formulas, inline and display formulas, and overlap with body text. Edge clipping, conflicts with an existing text layer, long LaTeX, and strings containing backslashes and brackets are verified as well. Known unsupported cases and partial processing are stated in the report and are not counted as successes.
