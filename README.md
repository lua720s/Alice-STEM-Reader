# PDF LaTeX Overlay

PDF上で数式を選択し、編集可能な LaTeX としてコピーできる派生PDFを生成する独立プロジェクト。元PDFは読み取り専用とし、ページの見た目を維持する。

- [エージェント・AIの作業ルール](AGENTS.md)
- [会話・指示ログ](AI_LOG.md)
- [要件と合格基準](REQUIREMENTS.md)
- [モジュール設計とPDFへの埋め込み方式](DESIGN.md)
- [実装順序、夜間実行、Git](ROADMAP.md)
- [Mathpix公式仕様と実機検証手順](MATHPIX_VALIDATION.md)
- [モデルと認識の記録（UniMERNet）](MODELS.md)
- [Claude Desktop / Claude Codeへの実装依頼](CLAUDE_TASK.md)

実装状況（2026-11-18）: 合成フィクスチャ・外観検査（段階0）、埋め込み2方式の比較（2c、既定は不可視テキスト）、検証付きCLI（2d）、ローカルビューア、UniMERNetによる切り出し認識（2a）、MinerU結果の取り込み（2b）、Mathpix検証（1）を実装し、合成データで試験済み（72件）。実教材（スキャン2ページ・11式）では、埋め込みとコピーは全件合格。ただしUniMERNet-tinyの認識は約半数が誤りで、修正が要る（MODELS.md）。macOSプレビューでの手動コピー確認は未実施。Mathpixは合成フィクスチャで4ジョブを実測済み（2026-11-19）。Codexの試作とMathpixの実測は [検証記録](MATHPIX_VALIDATION.md) を参照。

## 使い方（開発版）

```bash
/opt/homebrew/bin/python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q                     # 合成フィクスチャで全試験
.venv/bin/python -m pdf_latex_overlay fixtures outputs/fixtures
.venv/bin/python -m pdf_latex_overlay embed in.pdf out.pdf --formulas formulas.json --report report.json
.venv/bin/python -m pdf_latex_overlay viewer in.pdf --formulas formulas.json   # 127.0.0.1 のみ
```

- `embed` は原本を読み取り専用で扱い、外観（画素差ゼロ）・構造・本文・コピー（MuPDF、PDFKit）の検証に通った場合だけ `out.pdf` を作る。失敗時は出力を作らず、終了コード（2 設定、3 原本保護・衝突、4 未対応PDF、5 検証不合格、6 中断、7 対象なし）とレポートを返す。
- ビューアは式の枠をクリックしてLaTeXをコピー・修正・確定・除外し、枠の追加や引き直し、再認識（`--backend unimernet`）、派生PDFの保存ができる。修正してもOCR原文は残り、作業領域の `history.jsonl` に履歴が残る。
- 生成PDF・作業領域（`runs/`、`outputs/`）・モデル重み（`models/`）はGit管理外。
