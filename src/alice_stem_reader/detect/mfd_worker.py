"""数式検出（PDF-Extract-Kit MFD、YOLOv8）のワーカー。ML 用の別 venv で、ファイルパス指定で実行する。

このファイルは alice_stem_reader を import しない。

プロトコル（JSON Lines）
  起動: python mfd_worker.py --weights PATH
  出力1行目: {"ready": true, "classes": {...}, "versions": {...}}
  入力: {"id": "...", "image": "/path/page.png", "imgsz": 1888, "conf": 0.1, "iou": 0.45}
  出力: {"id": "...", "boxes": [[x0, y0, x1, y1, cls, conf], ...]（ピクセル）, "error": null}
"""

import argparse
import json
import os
import sys


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", required=True)
    ap.add_argument("--device", default="cpu")
    a = ap.parse_args()
    os.environ.setdefault("YOLO_OFFLINE", "1")  # 学習済み重み・フォントなどを取りに行かない
    os.environ.setdefault("YOLO_VERBOSE", "False")
    real = sys.stdout
    sys.stdout = sys.stderr
    try:
        import torch
        import ultralytics
        from ultralytics import YOLO

        model = YOLO(a.weights)
        info = {"ready": True, "classes": {str(k): v for k, v in model.names.items()},
                "versions": {"ultralytics": ultralytics.__version__, "torch": torch.__version__}}
    except Exception as e:
        sys.stdout = real
        emit({"ready": False, "error": f"{type(e).__name__}: {e}"})
        return 3
    sys.stdout = real
    emit(info)
    for line in sys.stdin:
        if not line.strip():
            continue
        rid = None
        try:
            req = json.loads(line)
            rid = req.get("id")
            sys.stdout = sys.stderr
            try:
                r = model.predict(req["image"], imgsz=int(req.get("imgsz", 1888)), conf=float(req.get("conf", 0.1)),
                                  iou=float(req.get("iou", 0.45)), verbose=False, device=a.device)[0]
            finally:
                sys.stdout = real
            boxes = [[*map(float, xyxy), int(c), float(cf)]
                     for xyxy, c, cf in zip(r.boxes.xyxy.tolist(), r.boxes.cls.tolist(), r.boxes.conf.tolist())]
            emit({"id": rid, "boxes": boxes, "error": None})
        except Exception as e:
            emit({"id": rid, "boxes": [], "error": f"{type(e).__name__}: {e}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
