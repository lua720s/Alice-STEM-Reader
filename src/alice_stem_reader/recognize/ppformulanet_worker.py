"""PP-FormulaNet 推論ワーカー（PaddlePaddle 用の別 venv で実行する）。

このファイルは alice_stem_reader を import しない。プロトコルは unimernet_worker.py と同じ（JSON Lines）。
  起動: python ppformulanet_worker.py --model-dir DIR --model-name PP-FormulaNet_plus-M
  出力1行目: {"ready": true, "versions": {...}, "load_s": 秒}
  入力: {"id": "...", "image": "/path/to/crop.png"}
  出力: {"id": "...", "latex": "..." | null, "error": null | "...", "elapsed_s": 秒}
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
    ap.add_argument("--model-name", required=True)
    ap.add_argument("--threads", type=int, default=0)
    a = ap.parse_args()
    t0 = time.monotonic()
    real_stdout = sys.stdout
    sys.stdout = sys.stderr  # 進捗表示などが標準出力に混ざらないように
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    try:
        import paddle
        import paddleocr
        from paddleocr import FormulaRecognition

        kw = {"model_name": a.model_name, "model_dir": os.path.abspath(a.model_dir)}
        if a.threads > 0:
            kw["cpu_threads"] = a.threads
        model = FormulaRecognition(**kw)
        versions = {"paddle": paddle.__version__, "paddleocr": paddleocr.__version__, "model": a.model_name}
    except Exception as e:  # noqa: BLE001
        sys.stdout = real_stdout
        emit({"ready": False, "error": f"{type(e).__name__}: {e}"})
        return 1
    sys.stdout = real_stdout
    emit({"ready": True, "versions": versions, "load_s": round(time.monotonic() - t0, 2)})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = {}
        try:
            req = json.loads(line)
            t1 = time.monotonic()
            sys.stdout = sys.stderr
            try:
                res = model.predict(req["image"])
            finally:
                sys.stdout = real_stdout
            latex = res[0].get("rec_formula") if res else None
            emit({"id": req.get("id"), "latex": latex or None, "error": None, "elapsed_s": round(time.monotonic() - t1, 3)})
        except Exception as e:  # noqa: BLE001
            emit({"id": req.get("id"), "latex": None, "error": f"{type(e).__name__}: {e}", "elapsed_s": None})
    return 0


if __name__ == "__main__":
    sys.exit(main())
