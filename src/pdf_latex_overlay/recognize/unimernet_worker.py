"""UniMERNet 推論ワーカー（ML 用の別 venv で、ファイルパス指定で実行する）。

本体の venv に transformers==4.42.4 などの固定依存を持ち込まないため、別プロセスに分ける。
このファイルは pdf_latex_overlay を import しない。

プロトコル（JSON Lines）
  起動: python unimernet_worker.py --model-dir DIR [--device cpu|mps] [--threads N]
  出力1行目: {"ready": true, "versions": {...}, "load_s": 秒, "device": "..."}
  入力: {"id": "...", "image": "/path/to/crop.png"}
  出力: {"id": "...", "latex": "..." | null, "error": null | "...", "elapsed_s": 秒}
  標準入力が閉じたら終了する。
"""

import argparse
import json
import os
import sys
import time


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--max-seq-len", type=int, default=1536)
    a = ap.parse_args()

    t0 = time.monotonic()
    # 進捗表示などを標準出力に出させない
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        import torch
        import transformers
        from omegaconf import OmegaConf
        from PIL import Image

        import unimernet
        import unimernet.models  # noqa: F401  （モデル登録）
        import unimernet.processors  # noqa: F401
        from unimernet.common.registry import registry
        from unimernet.processors import load_processor

        if a.threads > 0:
            torch.set_num_threads(a.threads)
        model_dir = os.path.abspath(a.model_dir)
        pths = [f for f in os.listdir(model_dir) if f.endswith(".pth")]
        if len(pths) != 1:
            raise RuntimeError(f"重みファイル（.pth）が1つではない: {pths}")
        cfg = OmegaConf.create({
            "arch": "unimernet",
            "model_name": "unimernet",
            "model_config": {"model_name": model_dir, "max_seq_len": a.max_seq_len},
            "tokenizer_name": "nougat",
            "tokenizer_config": {"path": model_dir},
            "load_pretrained": True,
            "load_finetuned": False,
            "pretrained": os.path.join(model_dir, pths[0]),
        })
        model = registry.get_model_class("unimernet").from_config(cfg)
        device = torch.device(a.device)
        model = model.to(device).eval()
        vis = load_processor("formula_image_eval", OmegaConf.create({"name": "formula_image_eval", "image_size": [192, 672]}))
        versions = {"torch": torch.__version__, "transformers": transformers.__version__,
                    "unimernet": getattr(unimernet, "__version__", "0.2.3?"), "weights": pths[0]}
    except Exception as e:  # モデル不足・依存不足は失敗として返す
        sys.stdout = real_stdout
        emit({"ready": False, "error": f"{type(e).__name__}: {e}"})
        return 3
    sys.stdout = real_stdout
    emit({"ready": True, "versions": versions, "load_s": round(time.monotonic() - t0, 2), "device": str(device)})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        t1 = time.monotonic()
        rid = None
        try:
            req = json.loads(line)
            rid = req.get("id")
            img = Image.open(req["image"]).convert("RGB")
            x = vis(img).unsqueeze(0).to(device)
            saved = sys.stdout
            sys.stdout = sys.stderr
            try:
                out = model.generate({"image": x})
            finally:
                sys.stdout = saved
            latex = out["pred_str"][0]
            emit({"id": rid, "latex": latex, "error": None, "elapsed_s": round(time.monotonic() - t1, 3)})
        except Exception as e:
            emit({"id": rid, "latex": None, "error": f"{type(e).__name__}: {e}", "elapsed_s": round(time.monotonic() - t1, 3)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
