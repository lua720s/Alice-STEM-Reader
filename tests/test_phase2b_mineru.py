"""段階2b: MinerU 結果の取り込み、座標変換（回転・CropBox）、重複抑制、IoU 評価。

MinerU 本体は使わない。MinerU 4.2.3 / docvortex 0.6 のスキーマに沿った JSON を、
フィクスチャの既知の見た目矩形から合成して試験する。
"""

import json
import random
import shutil

import pymupdf
import pytest

from pdf_latex_overlay import Options, process_pdf
from pdf_latex_overlay.detect.mineru import detect_format, import_mineru, strip_delimiters
from pdf_latex_overlay.evaluate import compare_regions
from pdf_latex_overlay.models import FormulaSet


def _truth(fx):
    fs = FormulaSet.load(fx["scan_json"])
    doc = pymupdf.open(fx["scan"])
    items = []
    for f in fs.formulas:
        if f.id in ("p0_textlayer", "p5_clip"):
            continue  # 本文の文字・境界外は MinerU の対象外として扱う
        page = doc[f.page]
        items.append({"id": f.id, "page": f.page, "vr": fs.meta["visual_rects"][f.id],
                      "W": page.rect.width, "H": page.rect.height, "latex": f.latex, "kind": f.kind,
                      "unrot": (page.cropbox.width, page.cropbox.height)})
    return fs, items


def _jitter(r, amt, rnd):
    return [v + rnd.uniform(-amt, amt) for v in r]


def make_v4_middle(items, jitter=0.0, seed=0):
    rnd = random.Random(seed)
    pages = {}
    for it in items:
        x0, y0, x1, y1 = _jitter(it["vr"], jitter, rnd)
        pages.setdefault(it["page"], []).append({
            "type": "equation", "index": len(pages.get(it["page"], [])),
            "bbox": [x0 / it["W"], y0 / it["H"], x1 / it["W"], y1 / it["H"]], "content": it["latex"],
        })
    out = {"schema": "docvortex.middle", "schema_version": "x", "is_full_document": True, "pages": []}
    for p in sorted(pages):
        blocks = pages[p]
        # 行内数式（bbox なし）を含む本文ブロック
        blocks.append({"type": "text", "index": 99, "bbox": [0.1, 0.9, 0.9, 0.95],
                       "content": [{"type": "text", "content": "see "}, {"type": "equation_inline", "content": "y=mx+b"}]})
        out["pages"].append({"page_idx": p, "blocks": blocks})
    return out


def make_content_list(items):
    return [{"type": "equation", "img_path": "", "text": f"$$\n{it['latex']}\n$$", "text_format": "latex",
             "bbox": [int(it["vr"][0] / it["W"] * 1000), int(it["vr"][1] / it["H"] * 1000),
                      int(it["vr"][2] / it["W"] * 1000 + 1), int(it["vr"][3] / it["H"] * 1000 + 1)],
             "page_idx": it["page"]} for it in items] + [{"type": "text", "text": "body", "page_idx": 0}]


def make_legacy(items, unrotated_frame=False):
    pdf_info = {}
    for it in items:
        p = it["page"]
        size = list(it["unrot"]) if unrotated_frame else [it["W"], it["H"]]
        info = pdf_info.setdefault(p, {"page_idx": p, "page_size": size, "para_blocks": [], "preproc_blocks": []})
        bbox = list(it["vr"])
        if unrotated_frame:
            doc_rot = it["rot_matrix_inv"]
            bbox = list(pymupdf.Rect(bbox) * doc_rot)
        t = "interline_equation" if it["kind"] == "display" else "inline_equation"
        span = {"type": t, "bbox": bbox, "content": it["latex"]}
        info["para_blocks"].append({"type": t if t == "interline_equation" else "text", "bbox": bbox,
                                    "lines": [{"bbox": bbox, "spans": [span]}]})
        info["preproc_blocks"].append({"type": "text", "bbox": bbox, "lines": [{"spans": [span]}]})  # 重複
    return {"pdf_info": [pdf_info[k] for k in sorted(pdf_info)], "_backend": "pipeline", "_version_name": "2.x"}


def _write(tmp_path, name, obj):
    p = tmp_path / name
    p.write_text(json.dumps(obj))
    return p


def test_strip_delimiters():
    assert strip_delimiters("$$\nx^2\n$$") == "x^2"
    assert strip_delimiters(r"\[a\]") == "a"
    assert strip_delimiters("$a$") == "a"
    assert strip_delimiters("a$b") == "a$b"


def test_detect_format():
    assert detect_format({"schema": "docvortex.middle", "pages": []}) == "mineru4_middle"
    assert detect_format({"pdf_info": []}) == "legacy_middle"
    assert detect_format([{"type": "text", "page_idx": 0}]) == "content_list_v1"
    with pytest.raises(ValueError):
        detect_format({"foo": 1})


@pytest.mark.parametrize("fmt", ["v4", "content_list", "legacy"])
def test_import_maps_rotated_and_cropbox_pages(fx, tmp_path, fmt):
    truth, items = _truth(fx)
    obj = {"v4": lambda: make_v4_middle(items), "content_list": lambda: make_content_list(items),
           "legacy": lambda: make_legacy(items)}[fmt]()
    fs = import_mineru(_write(tmp_path, "m.json", obj), fx["scan"])
    expected = FormulaSet(formulas=[f for f in truth.formulas if f.id not in ("p0_textlayer", "p5_clip")])
    r = compare_regions(expected, fs)
    assert not r["missed"] and not r["false_positives"], r
    assert r["mean_iou"] > (0.9 if fmt == "content_list" else 0.98), r
    assert all(f.source == "mineru" and f.status == "pending" for f in fs.formulas)
    assert all("$" not in f.latex for f in fs.formulas)
    if fmt == "v4":
        inl = [s for s in fs.meta["skipped"] if s["reason"] == "inline_no_bbox"]
        assert inl and inl[0]["latex"] == "y=mx+b"
    if fmt == "legacy":  # para_blocks と preproc_blocks の重複を一つにする
        assert len(fs.formulas) == len(items)


def test_legacy_unrotated_frame(fx, tmp_path):
    truth, items = _truth(fx)
    doc = pymupdf.open(fx["scan"])
    for it in items:
        it["rot_matrix_inv"] = doc[it["page"]].derotation_matrix
    m = _write(tmp_path, "m.json", make_legacy(items, unrotated_frame=True))
    expected = FormulaSet(formulas=[f for f in truth.formulas if f.id not in ("p0_textlayer", "p5_clip")])
    # auto: 90/270 度は寸法で判別できる。180 度は判別できず、警告を残して表示どおりと仮定する
    fs = import_mineru(m, fx["scan"])
    r = compare_regions(expected, fs)
    assert [x["expected"] for x in r["missed"]] == ["p_rot180"], r
    assert any(w["warning"] == "rotate180_frame_ambiguous" for w in fs.meta["warnings"])
    # 明示すれば全件一致
    fs = import_mineru(m, fx["scan"], frame="unrotated")
    r = compare_regions(expected, fs)
    assert not r["missed"] and r["mean_iou"] > 0.98, r


def test_jitter_and_iou_report(fx, tmp_path):
    truth, items = _truth(fx)
    fs = import_mineru(_write(tmp_path, "m.json", make_v4_middle(items, jitter=1.5, seed=3)), fx["scan"])
    expected = FormulaSet(formulas=[f for f in truth.formulas if f.id not in ("p0_textlayer", "p5_clip")])
    r = compare_regions(expected, fs)
    assert not r["missed"] and 0.7 < r["mean_iou"] < 1.0


def test_duplicates_and_out_of_range(fx, tmp_path):
    _, items = _truth(fx)
    obj = make_v4_middle(items[:1] + items[:1])  # 同じ式が2回
    obj["pages"].append({"page_idx": 99, "blocks": [{"type": "equation", "index": 0, "bbox": [0.1, 0.1, 0.2, 0.2], "content": "z"}]})
    fs = import_mineru(_write(tmp_path, "m.json", obj), fx["scan"])
    reasons = [s["reason"] for s in fs.meta["skipped"]]
    assert len(fs.formulas) == 1
    assert any(r.startswith("duplicate_of:") for r in reasons) and "page_out_of_range" in reasons


def test_import_then_embed_end_to_end(fx, tmp_path):
    _, items = _truth(fx)
    src = tmp_path / "scan.pdf"
    shutil.copy(fx["scan"], src)
    fs = import_mineru(_write(tmp_path, "m.json", make_v4_middle(items)), src)
    rep = process_pdf(src, tmp_path / "out.pdf", fs, Options(pdfkit="auto"))
    assert rep.ok, (rep.errors, {k: v for k, v in rep.checks.items() if v.get("ok") is False})
    assert rep.counts["embedded"] == len(items)


def test_cli_import(fx, tmp_path):
    from pdf_latex_overlay.cli import main

    _, items = _truth(fx)
    m = _write(tmp_path, "m.json", make_content_list(items))
    out = tmp_path / "f.json"
    assert main(["import-mineru", str(fx["scan"]), str(m), "--out", str(out)]) == 0
    assert len(FormulaSet.load(out).formulas) == len(items)
    assert main(["import-mineru", str(fx["scan"]), str(m), "--out", str(out)]) == 3  # 上書きしない
