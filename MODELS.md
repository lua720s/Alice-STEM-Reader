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
