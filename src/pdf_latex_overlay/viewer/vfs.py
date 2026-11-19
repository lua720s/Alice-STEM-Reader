"""ビューア専用のフォルダ構造（仮想フォルダ）。PC のフォルダ構造は、そのまま使う。

- 実際のファイルは動かさない・コピーしない。PDF は中身のハッシュ（sha256）で指すので、ファイルを移動・名前変更しても、
  仮想フォルダの中の並びは壊れない。同じ PDF を、複数の仮想フォルダに入れられる。
- 保存先は、ライブラリの `collections.json`（Git 管理外）。PDF 自体は、変えない。
- キー: ルートは "v"、フォルダは "v/<id>"。
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

NAME_MAX = 100
_BAD = re.compile(r"[\x00-\x1f/\\]")


class VFSError(ValueError):
    pass


def clean_vname(name) -> str:
    if not isinstance(name, str):
        raise VFSError("名前が不正")
    n = " ".join(name.split())
    if not n or len(n) > NAME_MAX or _BAD.search(n) or n in (".", ".."):
        raise VFSError(f"名前が不正（空でない、{NAME_MAX} 文字まで、/ や制御文字は使えない）")
    return n


class VFS:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data = {"version": 1, "next": 1, "folders": {}, "items": []}
        self._load()

    # ---- 保存 ----
    def _load(self):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("folders"), dict) and isinstance(d.get("items"), list):
                self.data = {"version": 1, "next": int(d.get("next", 1)), "folders": d["folders"],
                             "items": [i for i in d["items"] if isinstance(i, dict) and isinstance(i.get("sha"), str)]}
        except (OSError, ValueError, TypeError):
            pass

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    # ---- キー ----
    @staticmethod
    def key_of(fid) -> str:
        return "v" if not fid else f"v/{fid}"

    def fid_of(self, key) -> str | None:
        """キーからフォルダ ID。ルートは None。存在しなければ VFSError。"""
        if key == "v":
            return None
        m = re.fullmatch(r"v/(f\d+)", key or "")
        if not m or m.group(1) not in self.data["folders"]:
            raise VFSError("そのビューアのフォルダはない")
        return m.group(1)

    def _siblings(self, parent):
        return [f for f in self.data["folders"].values() if f["parent"] == parent]

    def _descendants(self, fid) -> set[str]:
        out = {fid}
        grew = True
        while grew:
            grew = False
            for i, f in self.data["folders"].items():
                if f["parent"] in out and i not in out:
                    out.add(i)
                    grew = True
        return out

    def _unique(self, parent, name, ignore=None):
        low = name.lower()
        if any(f["name"].lower() == low and f["id"] != ignore for f in self._siblings(parent)):
            raise VFSError(f"同じ名前のフォルダがすでにある: {name}")

    # ---- 操作 ----
    def mkdir(self, parent_key, name) -> str:
        with self.lock:
            parent = self.fid_of(parent_key)
            name = clean_vname(name)
            self._unique(parent, name)
            fid = f"f{self.data['next']}"
            self.data["next"] += 1
            self.data["folders"][fid] = {"id": fid, "name": name, "parent": parent}
            self._save()
            return self.key_of(fid)

    def rename(self, key, name):
        with self.lock:
            fid = self.fid_of(key)
            if fid is None:
                raise VFSError("一番上の「ビューアのフォルダ」は、名前を変えられない")
            name = clean_vname(name)
            f = self.data["folders"][fid]
            self._unique(f["parent"], name, ignore=fid)
            f["name"] = name
            self._save()

    def delete(self, key) -> dict:
        """フォルダと、その中のフォルダ・登録を消す（実際のファイルは消えない）。"""
        with self.lock:
            fid = self.fid_of(key)
            if fid is None:
                raise VFSError("一番上の「ビューアのフォルダ」は、削除できない")
            gone = self._descendants(fid)
            n_items = sum(1 for i in self.data["items"] if i.get("folder") in gone)
            self.data["items"] = [i for i in self.data["items"] if i.get("folder") not in gone]
            for g in gone:
                self.data["folders"].pop(g, None)
            self._save()
            return {"folders": len(gone), "items": n_items}

    def add(self, folder_key, entries) -> int:
        """entries: [(sha, 名前)]。同じフォルダにすでにある PDF は足さない。足した件数を返す。"""
        with self.lock:
            folder = self.fid_of(folder_key)
            have = {(i.get("folder"), i["sha"]) for i in self.data["items"]}
            n = 0
            for sha, name in entries:
                if (folder, sha) in have:
                    continue
                self.data["items"].append({"sha": sha, "name": name, "folder": folder})
                have.add((folder, sha))
                n += 1
            if n:
                self._save()
            return n

    def remove(self, items) -> int:
        """items: [{"folder": キー, "sha": ...}]。登録を外す（実際のファイルは、そのまま）。"""
        with self.lock:
            drop = set()
            for it in items:
                if not isinstance(it, dict) or not isinstance(it.get("sha"), str):
                    raise VFSError("items が不正")
                drop.add((self.fid_of(it.get("folder")), it["sha"]))
            before = len(self.data["items"])
            self.data["items"] = [i for i in self.data["items"] if (i.get("folder"), i["sha"]) not in drop]
            n = before - len(self.data["items"])
            if n:
                self._save()
            return n

    def move(self, items, folders, dest_key) -> int:
        """登録（items）とフォルダ（folders）を、別のビューアのフォルダへ移す。移した件数を返す。"""
        with self.lock:
            dest = self.fid_of(dest_key)
            moved = 0
            for fk in folders or []:
                fid = self.fid_of(fk)
                if fid is None:
                    raise VFSError("一番上の「ビューアのフォルダ」は、移せない")
                if dest is not None and dest in self._descendants(fid):
                    raise VFSError("フォルダを、自分の中には移せない")
                f = self.data["folders"][fid]
                if f["parent"] != dest:
                    self._unique(dest, f["name"], ignore=fid)
                    f["parent"] = dest
                    moved += 1
            for it in items or []:
                if not isinstance(it, dict) or not isinstance(it.get("sha"), str):
                    raise VFSError("items が不正")
                src = self.fid_of(it.get("folder"))
                if src == dest:
                    continue
                dup = any(i.get("folder") == dest and i["sha"] == it["sha"] for i in self.data["items"])
                for i in list(self.data["items"]):
                    if i.get("folder") == src and i["sha"] == it["sha"]:
                        if dup:
                            self.data["items"].remove(i)  # 移し先にすでにある: 重ねない
                        else:
                            i["folder"] = dest
                        moved += 1
                        break
            if moved:
                self._save()
            return moved

    # ---- 表示用 ----
    def folder_tree(self) -> list[dict]:
        """フォルダの入れ子（名前の自然順）。各フォルダに、その直下の登録（items）を持たせる。"""
        def build(parent):
            out = []
            for f in sorted(self._siblings(parent), key=lambda x: x["name"].lower()):
                out.append({"id": f["id"], "name": f["name"], "key": self.key_of(f["id"]),
                            "folders": build(f["id"]),
                            "items": [i for i in self.data["items"] if i.get("folder") == f["id"]]})
            return out
        return build(None)

    def root_items(self) -> list[dict]:
        return [i for i in self.data["items"] if not i.get("folder")]

    def folder_items(self, key) -> list[dict]:
        fid = self.fid_of(key)
        ids = self._descendants(fid) if fid else None
        return [i for i in self.data["items"] if ids is None or i.get("folder") in ids]
