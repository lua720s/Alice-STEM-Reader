# PDF LaTeX Overlay

PDF上で数式を選択し、編集可能な LaTeX としてコピーできる派生PDFを生成する独立プロジェクト。元PDFは読み取り専用とし、ページの見た目を維持する。

- [エージェント・AIの作業ルール](AGENTS.md)
- [会話・指示ログ](AI_LOG.md)
- [要件と合格基準](REQUIREMENTS.md)
- [モジュール設計とPDFへの埋め込み方式](DESIGN.md)
- [実装順序、夜間実行、Git](ROADMAP.md)
- [Mathpix公式仕様と実機検証手順](MATHPIX_VALIDATION.md)
- [Claude Desktop / Claude Codeへの実装依頼](CLAUDE_TASK.md)

開発着手可能。Mac上の試作では、合成教材の表示数式4件を自動検出し、UniMERNetで認識してから不可視テキストとして埋め込み、PDFKitで4件とも全文をコピーできた。実装コードはこのリポジトリへの取り込み前。Mathpixも合成教材でLaTeX付きAccessible PDFの取得を確認済み。詳しくは検証記録を参照。
