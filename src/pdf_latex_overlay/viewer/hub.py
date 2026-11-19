"""複数の PDF（フォルダ）を扱うビューアの中枢。

- フォルダを渡すと、PDF をフォルダの階層のまま「コレクション」として返す（Obsidian のファイルツリー）。
- 一覧に出す PDF と、開ける PDF は、起動時に渡したフォルダ・ファイルの下にあるものだけ。
  ID は PDF のパスのハッシュで、直前の走査で見つかったものしか受け付けない（任意のパスは開けない）。
- 処理の状態（準備済み・一部・未処理）は、PDF の中身のハッシュで引くライブラリ（prepare と共通）から読む。
  ハッシュの計算は裏のスレッドで行い、(サイズ, 更新時刻) が同じなら再計算しない。
- 一度に開くのは 1 つ。切り替えると、前の PDF の自動認識を止め、ロックを手放す。
  認識・検出のモデルは Hub が持ち、PDF を切り替えても読み込み直さない。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path


from .. import safety
from ..prepare import entry_dir, hold_lock, library_root, lock_holder, read_meta, write_meta
from .session import SessionError, ViewerSession

MAX_FILES = 20000
SKIP_DIRS = {"node_modules", "__pycache__"}
SCAN_TTL_S = 5.0
_ID_RE = re.compile(r"^[0-9a-f]{12}$")


def natural_key(name: str):
    """331-360 が 31-40 の後ろに来るような、数字を数として比べる並べ方。"""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def file_id(path: Path) -> str:
    return hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]


class Hub:
    def __init__(self, roots, backend="none", backend_obj=None, detector=None, library=None, auto_on_open=False):
        self.roots = [Path(r).resolve() for r in roots]
        for r in self.roots:
            if not r.exists():
                raise SessionError(f"パスが見つからない: {r}", 404)
        self.backend_name = backend
        self.backend_obj = backend_obj
        self.detector = detector
        self._own_models = False
        if backend not in (None, "", "none") and backend_obj is None:
            from ..detect.mfd import MFDDetector
            from ..recognize import get_backend

            self.backend_obj = get_backend(backend)  # 読み込みは最初に認識するときまで遅らせる
            self.detector = detector or MFDDetector()
            self._own_models = True
        self.library = Path(library) if library else None
        self.auto_on_open = auto_on_open
        self.lock = threading.RLock()
        self.session: ViewerSession | None = None
        self.current_id: str | None = None
        self._lock_cm = None
        self.index: dict[str, Path] = {}
        self._scan: tuple[float, list, bool] | None = None
        self._hash: dict[str, dict] = {}
        self._hash_loaded = False
        self._hash_queue: list[Path] = []
        self._hasher: threading.Thread | None = None
        self._hash_busy = 0

    @classmethod
    def from_session(cls, session: ViewerSession) -> "Hub":
        """1 つのセッションだけを持つ Hub（単体利用と試験用）。ロックや後始末はセッションの持ち主が行う。"""
        h = cls([session.input], backend="none", backend_obj=session._backend, detector=session._detector,
                library=session.workdir.parent)
        h.backend_name = session.backend_name  # 表示用（モデルはセッションが持つ）
        h.session = session
        h.current_id = file_id(session.input)
        h.index[h.current_id] = session.input
        return h

    # ---------------------------------------------------------------- 走査
    def _walk(self) -> tuple[list[tuple[int, Path]], bool]:
        files: list[tuple[int, Path]] = []
        truncated = False
        for ri, root in enumerate(self.roots):
            if root.is_file():
                if root.suffix.lower() == ".pdf":
                    files.append((ri, root))
                continue
            for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                dirnames[:] = sorted((d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS), key=natural_key)
                for fn in sorted(filenames, key=natural_key):
                    if fn.startswith(".") or not fn.lower().endswith(".pdf"):
                        continue
                    p = Path(dirpath) / fn
                    if p.is_symlink():  # 外のファイルを指すリンクは出さない
                        continue
                    if len(files) >= MAX_FILES:
                        truncated = True
                        break
                    files.append((ri, p))
                if truncated:
                    break
        return files, truncated

    def scan(self, force: bool = False):
        with self.lock:
            now = time.monotonic()
            if force or self._scan is None or now - self._scan[0] > SCAN_TTL_S:
                files, trunc = self._walk()
                self._scan = (now, files, trunc)
                self.index = {file_id(p): p for _, p in files}
            return self._scan[1], self._scan[2]

    def id_of(self, path) -> str:
        return file_id(Path(path).resolve())

    # ---------------------------------------------------------------- ハッシュ（裏のスレッド）
    def _cache_path(self) -> Path:
        return library_root(self.library) / ".hashcache.json"

    def _load_cache(self):
        if self._hash_loaded:
            return
        self._hash_loaded = True
        try:
            self._hash = json.loads(self._cache_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._hash = {}

    def _save_cache(self):
        p = self._cache_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text(json.dumps(self._hash), encoding="utf-8")
            tmp.replace(p)
        except OSError:
            pass

    def _cached_sha(self, path: Path) -> str | None:
        with self.lock:
            self._load_cache()
            e = self._hash.get(str(path))
            try:
                st = path.stat()
            except OSError:
                return None
            if e and e.get("size") == st.st_size and e.get("mtime_ns") == st.st_mtime_ns:
                return e["sha"]
            return None

    def _sha(self, path: Path) -> str:
        sha = self._cached_sha(path)
        if sha:
            return sha
        sha = safety.sha256_file(path)
        st = path.stat()
        with self.lock:
            self._hash[str(path)] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha": sha}
            self._save_cache()
        return sha

    def _ensure_hashing(self, files):
        with self.lock:
            if self._hasher and self._hasher.is_alive():
                return
            missing = [p for _, p in files if self._cached_sha(p) is None]
            if not missing:
                return
            self._hash_queue = missing
            self._hasher = threading.Thread(target=self._hash_run, daemon=True)
            self._hasher.start()

    def _hash_run(self):
        n = 0
        while True:
            with self.lock:
                if not self._hash_queue:
                    break
                p = self._hash_queue.pop(0)
                self._hash_busy = 1
            try:
                self._sha(p)
            except OSError:
                pass
            n += 1
            if n % 25 == 0:
                with self.lock:
                    self._save_cache()
        with self.lock:
            self._hash_busy = 0
            self._save_cache()

    def hashing_remaining(self) -> int:
        with self.lock:
            return len(self._hash_queue) + self._hash_busy

    # ---------------------------------------------------------------- ツリー
    def _file_node(self, path: Path) -> dict:
        fid = file_id(path)
        node = {"type": "file", "id": fid, "name": path.name, "state": "unknown", "formulas": None,
                "with_latex": None, "pages": None}
        try:
            node["size"] = path.stat().st_size
        except OSError:
            pass
        sha = self._cached_sha(path)
        if sha:
            m = read_meta(entry_dir(path, self.library, sha)[0])
            st = m.get("state")
            node["state"] = {"done": "done", "running": "running", "partial": "partial",
                             "interrupted": "partial"}.get(st, "none")
            c = m.get("counts") or {}
            node["formulas"], node["with_latex"], node["pages"] = c.get("formulas"), c.get("with_latex"), m.get("pages")
        return node

    def tree(self, refresh: bool = False) -> dict:
        files, truncated = self.scan(force=refresh)
        self._ensure_hashing(files)
        top: dict[int, dict] = {}
        loose: list[dict] = []

        def sort_children(n):
            n["children"].sort(key=lambda c: (c["type"] != "folder", natural_key(c["name"])))
            for c in n["children"]:
                if c["type"] == "folder":
                    sort_children(c)

        for ri, p in files:
            root = self.roots[ri]
            node = self._file_node(p)
            if root.is_file():
                loose.append(node)
                continue
            folder = top.setdefault(ri, {"type": "folder", "name": root.name or str(root), "key": f"r{ri}", "children": []})
            parts = p.relative_to(root).parts[:-1]
            cur = folder
            for k, part in enumerate(parts):
                key = f"r{ri}/" + "/".join(parts[:k + 1])
                nxt = next((c for c in cur["children"] if c["type"] == "folder" and c["key"] == key), None)
                if nxt is None:
                    nxt = {"type": "folder", "name": part, "key": key, "children": []}
                    cur["children"].append(nxt)
                cur = nxt
            cur["children"].append(node)
        roots = [top[ri] for ri in sorted(top)]
        for r in roots:
            sort_children(r)
        if loose:
            loose.sort(key=lambda c: natural_key(c["name"]))
            roots.append({"type": "folder", "name": "開いたPDF", "key": "files", "children": loose})
        return {"roots": roots, "current": self.current_id, "hashing": self.hashing_remaining(),
                "truncated": truncated, "backend": self.backend_name, "files": len(files)}

    # ---------------------------------------------------------------- 開く・切り替える
    def _allowed(self, path: Path) -> bool:
        if path.is_symlink() or not path.is_file() or path.suffix.lower() != ".pdf":
            return False
        rp = path.resolve()
        for root in self.roots:
            if root.is_file() and rp == root:
                return True
            if root.is_dir() and rp.is_relative_to(root):
                return True
        return False

    def _release(self):
        try:
            if self.session is not None:
                self.session.close()
        finally:
            self.session = None
            self.current_id = None
            if self._lock_cm is not None:
                try:
                    self._lock_cm.__exit__(None, None, None)
                finally:
                    self._lock_cm = None

    def open(self, fid: str, formulas_path=None, workdir=None, auto: bool | None = None) -> ViewerSession:
        if not isinstance(fid, str) or not _ID_RE.match(fid):
            raise SessionError("ID が不正")
        with self.lock:
            if fid == self.current_id and self.session is not None:
                return self.session
            path = self.index.get(fid)
            if path is None:
                self.scan(force=True)
                path = self.index.get(fid)
            if path is None or not self._allowed(path):
                raise SessionError("その PDF は一覧にない", 404)
            sha = self._sha(path)
            d = Path(workdir) if workdir else entry_dir(path, self.library, sha)[0]
            d.mkdir(parents=True, exist_ok=True)
            holder = lock_holder(d / ".viewer.lock")
            if holder and holder != os.getpid():
                raise SessionError(f"この PDF は別のビューア（pid {holder}）で開かれている", 409)
            self._release()
            cm = hold_lock(d / ".viewer.lock")
            cm.__enter__()
            try:
                session = ViewerSession(path, d, formulas_path, backend=self.backend_name,
                                        backend_obj=self.backend_obj, detector_obj=self.detector)
            except BaseException:
                cm.__exit__(None, None, None)
                raise
            self._lock_cm = cm
            meta = read_meta(d)  # ライブラリの一覧に出るよう、元の名前とパスを記録
            meta.setdefault("state", "viewer")
            meta.update(name=path.name, input_sha256=session.input_sha, pages=session.doc.page_count,
                        source_paths=list(dict.fromkeys([*meta.get("source_paths", []), str(path)])))
            write_meta(d, meta)
            self.session, self.current_id = session, fid
            if (self.auto_on_open if auto is None else auto) and self.backend_name not in (None, "", "none"):
                session.start_auto()
            return session

    def require(self) -> ViewerSession:
        with self.lock:
            if self.session is None:
                raise SessionError("PDF が選ばれていない（左のコレクションから選ぶ）", 409)
            return self.session

    def close(self):
        with self.lock:
            if self._lock_cm is not None or self.session is not None:
                self._release()
            if self._own_models:
                for m in (self.detector, self.backend_obj):
                    if m is not None and hasattr(m, "close"):
                        try:
                            m.close()
                        except Exception:
                            pass


__all__ = ["Hub", "natural_key", "file_id"]
