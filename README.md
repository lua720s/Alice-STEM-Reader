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
.venv/bin/python -m pdf_latex_overlay prepare 教材フォルダ/          # 開く前にまとめて認識（中断・再開可）
.venv/bin/python -m pdf_latex_overlay library                       # 事前処理した PDF の一覧
.venv/bin/python -m pdf_latex_overlay viewer in.pdf --backend unimernet   # 127.0.0.1 のみ
```

- `embed` は原本を読み取り専用で扱い、外観（画素差ゼロ）・構造・本文・コピー（MuPDF、PDFKit）の検証に通った場合だけ `out.pdf` を作る。失敗時は出力を作らず、終了コード（2 設定、3 原本保護・衝突、4 未対応PDF、5 検証不合格、6 中断、7 対象なし）とレポートを返す。
- **開く前に認識しておける（`prepare`）**: Mathpix Snip Webと同じく、PDFやフォルダを先にまとめて処理しておく。中断しても続きから再開でき、`--time-limit` と `--max-pages` で上限を決めて夜間に流せる。結果は、PDFの中身のハッシュで引くライブラリ（`library/`、Git管理外）に保存する。名前を変えたり移動したりしたPDFでも、そのまま使える。準備済みのPDFは、ビューアを起動して約0.4秒で全部の式をコピーできる（実教材2ページ・48式で実測）。事前処理の実行中にビューアで開くと、表示だけになり、結果が順に反映される。
- `--backend unimernet` でビューアを開くと、**準備していないページだけ、その場で全ページの数式を自動で検出・認識**する（数式検出は PDF-Extract-Kit MFD、MODELS.md）。進み具合は上部に表示し、認識済みの枠から使える。枠を**クリックするとLaTeXをすぐコピー**する（Mathpix Snip Webと同様）。`--no-auto` で自動認識を止められる。`detect` コマンドで、式JSONだけを作ることもできる。
- ビューアでは、ページ上を**ドラッグすると枠を追加して認識**する（`--backend unimernet`。Mathpix Snipと同様に、既存の枠の上からでも描ける）。枠を**クリックすると選択**し、LaTeXのコピー・修正・確定・除外、枠の引き直し、再認識、派生PDFの保存ができる。
- 複数行を囲んだ枠は、行ごとに分けて認識し、改行で区切ったLaTeXにする。`\begin{array}`・`\displaystyle`・`\!`・余分な波括弧などの表示用の記法は取り除き、OCR原文は別に残す。派生PDFでも行ごとに文字層を置くので、コピーすると改行付きで取れる。
- ほぼ同じ位置に描き直した枠は、未確定・未修正の古い枠と置き換える。一部だけ重なる枠は両方残し、PDF保存のときは「確定 → 修正済み → 小さい枠」の順に1つだけ埋め込む（残りは `overlaps:` で除外）。修正の履歴は作業領域の `history.jsonl` に残る。
- 生成PDF・作業領域（`runs/`、`outputs/`）・モデル重み（`models/`）はGit管理外。
