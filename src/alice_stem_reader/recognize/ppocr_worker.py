"""PaddleOCR（PP-OCR）の文字認識ワーカー（PaddlePaddle 用の別 venv で実行する）。日本語の文章を行ごとに読む。

このファイルは alice_stem_reader を import しない。プロトコルは他のワーカーと同じ（JSON Lines）。
  起動: python ppocr_worker.py --cache-dir DIR [--lang japan]
  出力1行目: {"ready": true, "versions": {...}, "load_s": 秒}
  入力: {"id": "...", "image": "/path/to/page.png"}
  出力: {"id": "...", "lines": [{"text": "...", "score": 0.97, "box": [x0, y0, x1, y1]}], "error": null, "elapsed_s": 秒}
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
    ap.add_argument("--cache-dir", required=True)
    ap.add_argument("--lang", default="japan")
    a = ap.parse_args()
    t0 = time.monotonic()
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    os.environ["PADDLE_PDX_CACHE_HOME"] = os.path.abspath(a.cache_dir)
    os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "huggingface")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    try:
        import paddle
        import paddleocr
        from paddleocr import PaddleOCR

        ocr = PaddleOCR(lang=a.lang, use_doc_orientation_classify=False, use_doc_unwarping=False,
                        use_textline_orientation=False)
        versions = {"paddle": paddle.__version__, "paddleocr": paddleocr.__version__, "lang": a.lang}
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
                res = ocr.predict(req["image"], return_word_box=bool(req.get("words")))
            finally:
                sys.stdout = real_stdout
            lines = []
            if res:
                r = res[0]
                tw, twr = r.get("text_word") or [], r.get("text_word_region") or []
                for i, (txt, sc, box) in enumerate(zip(r["rec_texts"], r["rec_scores"], r["rec_boxes"])):
                    ln = {"text": txt, "score": round(float(sc), 4), "box": [int(v) for v in box]}
                    if req.get("words") and i < len(tw):  # 文字・単語ごとの位置（混在した領域で、数式と文章を分けるため）
                        ln["words"] = []
                        for w, reg in zip(tw[i], twr[i]):
                            xs, ys = [p[0] for p in reg], [p[1] for p in reg]
                            ln["words"].append({"text": w, "box": [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))]})
                    lines.append(ln)
            emit({"id": req.get("id"), "lines": lines, "error": None, "elapsed_s": round(time.monotonic() - t1, 3)})
        except Exception as e:  # noqa: BLE001
            emit({"id": req.get("id"), "lines": [], "error": f"{type(e).__name__}: {e}", "elapsed_s": None})
    return 0


if __name__ == "__main__":
    sys.exit(main())
