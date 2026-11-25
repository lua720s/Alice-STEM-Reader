#!/usr/bin/env bash
# 認識モデルを導入する（プロジェクト直下の .venv-ml・.venv-pp・models/ に入れる。いずれも Git 管理外）。
#
#   scripts/setup_models.sh            # 既定: 数式認識 UniMERNet base + 数式検出 MFD + 文章認識 PaddleOCR
#   scripts/setup_models.sh --tiny     # 軽い UniMERNet tiny も入れる（「要確認」の2つ目のモデルにも使う）
#   scripts/setup_models.sh --no-paddle
#
# 重みは配布元（Hugging Face）から取り、記録した SHA-256 と照合する。一致しなければ止まる。
# 必要な空き容量の目安: 約 6GB（base 1.3GB、MFD 350MB、PyTorch・PaddlePaddle の環境）。
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-$(command -v python3.12 || command -v python3)}"
WANT_TINY=0; WANT_PADDLE=1
for a in "$@"; do
  case "$a" in
    --tiny) WANT_TINY=1 ;;
    --no-paddle) WANT_PADDLE=0 ;;
    -h|--help) sed -n 2,9p "$0"; exit 0 ;;
    *) echo "不明な指定: $a" >&2; exit 2 ;;
  esac
done

sha() { if command -v shasum >/dev/null; then shasum -a 256 "$1" | cut -d' ' -f1; else sha256sum "$1" | cut -d' ' -f1; fi; }
fetch() {  # fetch URL 保存先 [SHA-256]
  local url="$1" out="$2" want="${3:-}"
  if [[ -f "$out" && -n "$want" && "$(sha "$out")" == "$want" ]]; then echo "  取得済み: $out"; return; fi
  mkdir -p "$(dirname "$out")"
  curl -fL --retry 3 -o "$out.partial" "$url"
  if [[ -n "$want" && "$(sha "$out.partial")" != "$want" ]]; then
    echo "SHA-256 が一致しない: $out（取得したファイルは $out.partial に残した）" >&2; exit 3
  fi
  mv "$out.partial" "$out"
}

echo "== 1/4 ML 用の環境（.venv-ml: PyTorch・UniMERNet・数式検出）"
[[ -x .venv-ml/bin/python ]] || "$PY" -m venv .venv-ml
.venv-ml/bin/pip install --no-cache-dir -q -r requirements-ml.txt

echo "== 2/4 数式認識 UniMERNet base（既定のエンジン）"
HF=https://huggingface.co
for f in config.json preprocessor_config.json tokenizer.json tokenizer_config.json README.md; do
  fetch "$HF/wanderkid/unimernet_base/resolve/main/$f" "models/unimernet_base/$f"
done
fetch "$HF/wanderkid/unimernet_base/resolve/main/pytorch_model.pth" "models/unimernet_base/pytorch_model.pth" \
  16cd0891233cfee3c11215a7b87306f160f7e7f3f52091a6253751c149a8c180

if [[ $WANT_TINY == 1 ]]; then
  echo "== 2b UniMERNet tiny"
  REV=3f09ac4b1cd583be47ea20a7d7daef839473028a
  for f in config.json preprocessor_config.json tokenizer.json tokenizer_config.json README.md; do
    fetch "$HF/wanderkid/unimernet_tiny/resolve/$REV/$f" "models/unimernet_tiny/$f"
  done
  fetch "$HF/wanderkid/unimernet_tiny/resolve/$REV/unimernet_tiny.pth" "models/unimernet_tiny/unimernet_tiny.pth" \
    6f7608624e2d7549c7f0f05fcfbe073ae521328cf70f1d46374d96f9881d7371
fi

echo "== 3/4 数式検出（PDF-Extract-Kit MFD, YOLOv8）"
fetch "$HF/opendatalab/PDF-Extract-Kit-1.0/resolve/ed6b654c018d742e65a17671e379c5e6ecc87ec9/models/MFD/YOLO/yolo_v8_ft.pt" \
  models/mfd_yolo_v8/yolo_v8_ft.pt 41029d5abb9b0b6df825ecaf8adf9151762ea0688ac0fc4ea0ea34ab9a5808fc

if [[ $WANT_PADDLE == 1 ]]; then
  echo "== 4/4 文章の認識（PaddleOCR, .venv-pp）"
  [[ -x .venv-pp/bin/python ]] || "$PY" -m venv .venv-pp
  .venv-pp/bin/pip install --no-cache-dir -q paddlepaddle==3.3.1 paddleocr==3.7.0 tokenizers ftfy
  PADDLE_PDX_CACHE_HOME="$PWD/models/pp_cache" PADDLE_PDX_MODEL_SOURCE=huggingface \
    .venv-pp/bin/python -c "from paddleocr import PaddleOCR; PaddleOCR(lang='japan', use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False)" \
    || echo "  （PaddleOCR のモデルは、初めて文章を読むときにも自動で取得します）"
fi

echo "完了。ライセンス（UniMERNet: Apache-2.0、MFD/ultralytics: AGPL-3.0、PaddleOCR: Apache-2.0）は docs/MODELS.md を参照。"
