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

## 合成教科書での実測（2026-11-18）

B5相当、本文10.5pt、300 dpi、画像のみの合成教材2ページを設定オンで変換。既存のMathpix CLI設定を利用し、`accessible: true`、`math_latex_layer: true`、`language: ja` を指定。取得した出力に構造ツリーがあり、144 dpiで両ページの描画が元PDFと画素一致。原本SHA-256は変更なし。

PDFKitの範囲選択と一時クリップボードで確認。1ページ目は表示数式2件をそれぞれLaTeXとしてコピーできた。2ページ目は2式が一つの `aligned` ブロックになり、上の式の選択で両方が返り、下の式だけでは空だった。設定オフとの比較、ベクター入力、プレビューの手動操作は未実施。

出力ファイル名は `mathpix-stem-accessible-latex.pdf`。

## 検証ワークフローの実装（2026-11-18、送信なし）

`pdf-latex-overlay mathpix-plan outputs/fixtures` は、ベクター・画像化 × `math_latex_layer` false/true の計4ジョブについて、上記の `options_json`、入力SHA-256、`mpx scs convert` のコマンド（PDF API、`--async` なし）、取得後の検査コマンドを出力する。送信はせず、認証情報も読まない（試験でソケット接続を禁止して確認）。

取得したPDFは、次の2つで評価する。
- `verify-derived 原本 派生`: ページ属性と画素差を比べる。
- `copycheck 派生 --formulas 期待式.json`: MuPDFとPDFKitのコピー比較表を出す（生文字列と正規化後の両方）。

今回は送信の承認がないため、実ジョブは未実施。実施するときは、個別承認を得てから、計画の `command` を1件ずつ手で実行する。

## 合成フィクスチャでのA/B実測（2026-11-19）

ユーザーの承認を得て、`mathpix-plan` の4ジョブを PDF API（`mpx scs convert`、`--async` なし）へ1件ずつ送信した。送信は計4件で、再送はしていない。いずれも数秒で完了した。入力は合成フィクスチャ（6ページ、11式、Computer Modern の数式画像）。両入力のSHA-256は送信の前後で一致した。オプションは `accessible: true` と `math_latex_layer` false/true。出力は `outputs/mathpix/`（Git管理外）。

| 入力 | 設定 | ページ属性 | 画素差（72・144 dpi） | タグ付き | PDFKit矩形選択で得られる文字列 |
| --- | --- | --- | --- | --- | --- |
| ベクター | オフ | 一致 | **あり**（144 dpiで1ページ目14,650画素など。目視では同じで、文字の縁のずれ程度） | あり | 読み上げ表現（`StartFraction negative b …`） |
| ベクター | オン | 一致 | **あり**（同上） | あり | 表示数式は `\[ … \]` 付きのLaTeX |
| 画像化 | オフ | 一致 | なし | あり | 読み上げ表現 |
| 画像化 | オン | 一致 | なし | あり | 表示数式は `\[ … \]` 付きのLaTeX |

**設定オン**で、式の見た目の矩形を上下左右15%広げて選択し、外側の区切りと空白を除いて期待値と比べた結果（両入力とも同じ）:

- 表示数式7件（回転0/90/180/270と非ゼロCropBoxを含む）: 5件が完全一致。残る2件も数学的には同じ内容（`d x` と `\,dx`、`\rightarrow` と `\to`）。
- 行内数式 `a^{2}+b^{2}=c^{2}`: LaTeXにならない。ベクターでは置換文字（U+FFFC）、画像化では平文の `a 2 +b 2 =c 2` だった。
- 本文と重なる式 `\alpha+\beta=\gamma`: LaTeXとして取り出せなかった。
- ページ境界で切れた式: 何も取れなかった。

**分かったこと**
- LaTeXの文字層は、見た目の式より横に広い（`\[` と `\]` を含む）。式の見た目どおりに矩形で選ぶと、端の文字が欠ける。広げずに選んだ場合は、先頭が `[ x=…` のように `\` が欠けた。以前の実教材で見た「先頭の `\` の欠け」と同じ現象と考えられる。
- 設定オフでは、式は読み上げ表現になる。`math_latex_layer` の効果は確認できた。
- ベクター入力では、見た目はほぼ同じだが画素は一致しない。本プロジェクトの初期基準（画素差ゼロ）では不合格。画像化入力では一致した。

**本プロジェクトの方式との比較**（同じフィクスチャ、既知のLaTeX）: 不可視テキストの埋め込みは、両入力で画素差ゼロ。PDFKitでは、式の見た目どおりの矩形で、行内数式を含む全件のLaTeXが取れる。ただし、LaTeXそのものの正しさは認識器（UniMERNetなど）か手動修正に依存する。

**未実施**: macOSプレビューでの手動のドラッグ選択。Acrobat ReaderとChromeでのコピー。実教材での比較。
