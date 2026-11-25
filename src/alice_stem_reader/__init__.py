"""Alice STEM Reader: 資料（PDF・EPUB・DjVu・画像・Markdown・LaTeX）を読みながら、数式と文章を LaTeX / Markdown として取り出す。

すべて手元の PC で動く。原本は変えない。数式をコピーできる派生 PDF も作れる。
"""

__version__ = "0.2.0"

import os as _os

# 旧名（PLO_）の環境変数も、新しい名前（ASR_）が無ければそのまま使う
for _k, _v in list(_os.environ.items()):
    if _k.startswith("PLO_") and "ASR_" + _k[4:] not in _os.environ:
        _os.environ["ASR_" + _k[4:]] = _v

from .pipeline import ExitCode, Options, RunReport, process_pdf  # noqa: E402

__all__ = ["process_pdf", "Options", "RunReport", "ExitCode", "__version__"]
