# Mathpix 検証記録

## 公式仕様の確認（2026-11-18）

[mmd.overlay.pdf の変換オプション](https://docs.mathpix.com/reference/shared-types#conversion-options-for-mmdoverlaypdf) に、任意の真偽値 `math_latex_layer` が掲載されている。既定値は `false`。`true` は変換が描画する数式テキストを読み上げ表現から認識済みLaTeXに切り替える。タグ付き出力の `/Formula` の読み上げ用代替は維持される。既存のPDFテキストの扱いはサンプルで確認する。

[Accessible PDF](https://docs.mathpix.com/guides/accessible-pdf) と [PDF API](https://docs.mathpix.com/reference/post-v3-pdf) に従い、`POST /v3/pdf` の multipart に `file` と以下の `options_json` を渡す。認証ヘッダーは [認証仕様](https://docs.mathpix.com/reference/authentication) に従う。

```json
{
  "conversion_formats": {"mmd.overlay.pdf": true},
  "conversion_options": {
    "mmd.overlay.pdf": {"math_latex_layer": true}
  }
}
```

PDF IDを取得し、`GET /v3/pdf/{pdf_id}` と変換ステータスで完了を確認してから `GET /v3/pdf/{pdf_id}.mmd.overlay.pdf` で別名保存する。変換固有のエラーも確認する。キーは環境変数または承認済みの秘密管理機構から読み、ログやコミットに含めない。

## A/B実測

1. 同一内容の合成PDFを2種類用意する。通常テキストを含むベクターPDFと、同じページを画像化したPDF。インライン数式、分数、根号、複数行の表示数式、数式なしページを入れ、期待LaTeXと領域を別ファイルに記録する。
2. 各PDFで `math_latex_layer=false/true` の計4件を比較する。初回は既定のタグ付き出力で固定する。外部送信は合成PDFに限定し、認証と利用枠を確認できる場合だけ実施する。応答を失ったPOSTは請求やジョブの重複を避けるため自動再送しない。
3. macOSプレビューと利用可能な別系統のビューア（Acrobat ReaderまたはChrome）で式だけを選択し、プレーンテキストに貼り付ける。周辺文を含む範囲もコピーし、欠落、二重コピー、順序、位置ずれを記録する。併せて `pdftotext` の領域抽出を実行し、CLI抽出とGUIコピーの結果を別々に保存する。
4. 入力SHA-256とページ属性、全ページ描画差分を比較する。LaTeXの生文字列と正規化後の一致結果を両方保存。正規化は外側の数式デリミタ、改行コード、前後空白に限定し、コマンドや数式の中身を変更しない。
5. 各セルを PASS、FAIL、未実施で記録する。ビューアとOSの版、入力種別、式ID、リクエスト条件、実際のコピー結果と差分を残す。HTTP成功だけで設定の効果を判定しない。両条件で同じ結果なら、未適用・既存文字層・認識漏れ・ビューア挙動を切り分ける。

現状の判定は **公式仕様に掲載あり、実測未実施**。
