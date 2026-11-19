# モデルと認識の記録

## UniMERNet（段階2a）

| 項目 | 内容 |
| --- | --- |
| 配布元 | [opendatalab/UniMERNet](https://github.com/opendatalab/UniMERNet)、重みは Hugging Face `wanderkid/unimernet_tiny` |
| 版 | パッケージ `unimernet==0.2.3`、重みのリビジョン `3f09ac4b1cd583be47ea20a7d7daef839473028a` |
| 重み | `unimernet_tiny.pth`（430,075,701 バイト、SHA-256 `6f7608624e2d7549c7f0f05fcfbe073ae521328cf70f1d46374d96f9881d7371`、配布元のLFSハッシュと一致） |
| ライセンス | パッケージ・重みとも Apache-2.0（PyPI と Hugging Face の表示）。AliceArchiveへ配布する前に再確認する |
| 置き場所 | `models/unimernet_tiny/`、`.venv-ml/`（いずれもGit管理外） |

8GB RAMのM1で動かすため、tiny（small 810MB、base はさらに大きい）を選んだ。依存が `transformers==4.42.4` などを固定するので、本体とは別の venv に入れ、子プロセス（`recognize/unimernet_worker.py`、JSON Lines）で呼ぶ。

### 導入手順

```bash
/opt/homebrew/bin/python3.12 -m venv .venv-ml
.venv-ml/bin/pip install --no-cache-dir -r requirements-ml.txt
REV=3f09ac4b1cd583be47ea20a7d7daef839473028a
mkdir -p models/unimernet_tiny
for f in config.json preprocessor_config.json tokenizer.json tokenizer_config.json README.md unimernet_tiny.pth; do
  curl -sSfL -o "models/unimernet_tiny/$f" "https://huggingface.co/wanderkid/unimernet_tiny/resolve/$REV/$f"
done
shasum -a 256 models/unimernet_tiny/unimernet_tiny.pth   # 上表の値と一致すること
```

場所は環境変数 `PLO_ML_PYTHON`、`PLO_UNIMERNET_DIR` で変えられる。重みやML用Pythonがなければ、`recognize` は終了コード8（モデル不足）で止まり、出力を作らない。

### 実測（2026-11-18、Apple M1・8GB・macOS 27.0.1、CPU）

- モデル読み込み 約19秒、1式あたり 約1.3秒、ワーカーの最大常駐メモリ 約1.0GB。MPS は未計測。
- 対象は合成フィクスチャ11式。式は matplotlib mathtext の Computer Modern で描いている。画像化PDFは150 dpi、切り出しは200 dpi。ベクターPDFは300 dpiで切り出した。

| 指標 | 画像化PDF | ベクターPDF |
| --- | --- | --- |
| 空白を除いた完全一致 | 2 / 11 | 3 / 11 |
| 緩い一致（`\textstyle`・`\!`・`\,`・`\mathop{=}`・1行の `array` 包みを除く。意味の一致は保証しない） | 7 / 11 | 7 / 11 |
| 文字列類似度の平均 | 0.79 | 0.79 |

目視では、本文の文字として描いた `E=mc^2` とページ境界で切れた式を除く9式は、すべて数学的に同じ内容だった（例: `\operatorname*{lim}` と `\lim`、余分な波括弧）。最初にmatplotlibの既定の書体（サンセリフ）で描いたときは、`\ln x` を `|\boldsymbol{\cap}\times` と読むなど誤りが多かった。書体の違いが精度に強く影響する。

- 認識器の出力は字句ごとに空白が入る。`ocr_latex` には原文を保存し、`latex` には意味を変えない範囲で空白を詰めたものを入れる（`tidy_latex`）。制御語の直後の英字の前、制御空白、`\text{...}` の中の空白は残す。
- 実教材での精度は未測定。著作物PDFはリポジトリに入れない。

## MinerU 結果の取り込み（段階2b）

`import-mineru` は、利用者が別途実行したMinerUの結果JSONを式JSONに変換する。MinerU本体は導入していない。理由は次の3点。
- MinerU 4.2.3 は torch、transformers 5、gradio、llama-cpp、VLMモデルを要求し、空き容量（4.2GB）では導入後に停止基準の4GBを下回る。
- ライセンスが独自の「MinerU Open Source License」で、配布前に確認が要る。
- 2026-11-18時点で、このMacにMinerUの実行環境も過去の出力も見つからなかった（`~/.mineru` にONNXの配置・OCRモデルだけがある）。

対応形式は、MinerU 4.2.3 と docvortex 0.6 のソースで確認した。

| 形式 | 数式の取り方 | 座標 |
| --- | --- | --- |
| MinerU 4 `middle.json`（`"schema": "docvortex.middle"`） | `type: "equation"` のブロック | 0〜1 の正規化 |
| `content_list.json`（V1） | `type: "equation"`、`text`（`$$` を外す） | 0〜1000 の整数 |
| 旧 `middle.json`（`pdf_info`） | `interline_equation`・`inline_equation` の span | pt、`page_size` 基準 |

- **MinerU 4 の行内数式には矩形がない**（`equation_inline` の span は `type` と `content` だけ）。個別に埋め込めないので、取り込み時に `inline_no_bbox` として除外記録する。ビューアで枠を描けば、手動で追加・再認識できる。
- 座標は表示どおりのページ画像（回転後、左上原点）を基準とみなし、回転前の保存座標へ変換する。旧形式は `page_size` で判定する。回転180度のページは回転前後で寸法が同じため判別できず、`rotate180_frame_ambiguous` の警告を残す。`--frame unrotated` で基準を明示できる。
- 同じページで IoU 0.5 を超えて重なる式は、後のものを `duplicate_of:` として除外する。
- 合成した3形式のJSONで取り込みを試験した。回転0/90/180/270と非ゼロCropBoxを含め、期待矩形とのIoUは平均0.98超（content_listは整数丸めのため0.9超）で、検出漏れ・誤検出は0件。そのまま埋め込みと検証も通った。
- **未実施**: 実際のMinerUの出力での照合と、座標基準（特に回転ページ）の実機確認。ページ全体からの独自の数式検出（PDF-Extract-Kit の MFD など）は実装していない。

## 実教材での試験（2026-11-19）

市販の数学参考書（積分法の例題・練習、見開き2ページ）をスキャンしたPDFで試した。文字層はなく、画像だけのページ。ローカルだけで処理し、外部には送っていない。教材PDF、正解ファイル、派生PDFは `runs/realtext/`（Git管理外）に置き、ここには数値と所見だけを記録する。元ファイルのSHA-256は処理の前後で一致した。

**対象**: 1行の式11件（問題の定積分5件と、解答の行6件）。矩形は手作業で測った。正解は目視で書き起こし、拡大画像で確認した。書き起こしの途中で、練習(3)の下端は1ではなく4と判明した。以前の MinerU と Codex の UniMERNet はどちらも1と読んでいた。

**MinerU（Codexが2026-11-18に実行した結果）の取り込み**
- 実際のMinerU 4出力（`docvortex.middle` 2.0）を `import-mineru` で読めた。表示数式のブロックは3件で、座標はCodexが別に換算した値と一致した。
- 行内数式12件には矩形がなく、`inline_no_bbox` として除外された（スキーマ調査どおり）。
- 表示数式ブロックのうち2件は、複数行をまとめた大きな領域だった。LaTeXも同じ項の繰り返しで、使えなかった。

**UniMERNet-tiny の認識（CPU）**

| 切り出し解像度 | 緩い一致 | 目視で数学的に正しい | 文字列類似度の平均 |
| --- | --- | --- | --- |
| 300 dpi | 3 / 11 | 5 / 11 | 0.75 |
| 200 dpi | 4 / 11 | 6 / 11 | 0.78 |
| 150 dpi | 4 / 11 | 6 / 11 | 0.82 |

- 解像度で結果が変わり、どの式が正しくなるかも入れ替わった。主な誤りは次のとおり。
  - 積分の上端 `e` を `\infty` と読む。
  - `e^{-4}` を `e^{-1}` と読む。
  - 小さい分数を `\stackrel` にする。
  - 1件は積分記号の繰り返しで崩れた。
- 問題の5式に限ると、MinerUの行内数式（Codexの実行結果）は目視で4件が正しかった。UniMERNet-tinyは3件だった。
- **結論**: 実際のスキャンでは、tinyモデルの認識をそのまま埋め込める水準ではない。ビューアでの確認・修正を前提にする。より大きいモデル（small 810MB）は、空き容量の制約で試していない。

**埋め込みとコピー（修正後のLaTeX、11件）**
- 最初の試験で、検証が2件の不合格を検出し、派生PDFは公開されなかった（失敗時停止が働いた）。
  1. 51文字のLaTeXを幅59ptに詰めた式で、MuPDFが隣り合う `}}` の片方を重複字形として捨てた。
  2. 背の高い式の右下にある式が、PDFKitで同じ行にまとめられ、矩形選択が空になった。
- 対策: 字形の帯を矩形の高さの65%に収めて上下中央に置き、横方向の最小倍率（0.35）を設けた（`embed.py` の `BAND`、`MIN_HSCALE`）。両方の状況を合成した回帰試験を追加し、旧コードで失敗することを確認した。
- 対策後の結果は次のとおり。
  - 72・150 dpiで画素差0。
  - PDFKitの矩形選択で11件すべてが完全一致し、ページ全文にも各1回だけ出現した。
  - MuPDF抽出も全件一致した。
  - UniMERNetの生の出力を埋め込んだ場合も、検証は通った（中身の正しさは上表のとおり）。
- **未実施**: macOSプレビューでの手動のドラッグ選択。
