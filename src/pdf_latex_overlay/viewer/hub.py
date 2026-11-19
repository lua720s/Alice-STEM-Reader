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

import errno
import hashlib
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path


from .. import safety
from ..prepare import entry_dir, hold_lock, library_root, lock_holder, prepare_one, read_meta, write_meta
from ..recognize.base import RecognizerUnavailable
from .fsops import clean_name, move_to_trash
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


def engine_list() -> list[dict]:
    from ..recognize import ENGINES

    return [{"name": k, **v} for k, v in ENGINES.items()]


class Hub:
    def __init__(self, roots, backend="none", backend_obj=None, detector=None, library=None, auto_on_open=False,
                 readonly=False, trash_dir=None, verify=False, verify_backend_obj=None, allow_external=False):
        self.roots = [Path(r).resolve() for r in roots]
        self.last_request = time.monotonic()
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

            self.backend_obj = get_backend(backend, **({"allow_external": True} if backend == "mathpix" and allow_external else {}))  # 読み込みは最初に認識するときまで遅らせる
            self.detector = detector or MFDDetector()
            self._own_models = True
        # 2つ目のモデル（検証用）。あれば、2つの読み取りが食い違った式に「要確認」の印を付ける
        self.verify_obj = verify_backend_obj
        self._own_verify = False
        if verify and self.verify_obj is None:
            if backend in (None, "", "none"):
                raise SessionError("--verify は、--backend unimernet と一緒に使う", 2)
            from ..recognize.unimernet import make_verifier

            self.verify_obj = make_verifier()  # 重みがなければ、RecognizerUnavailable（起動時に分かる）
            self._own_verify = True
        self.library = Path(library) if library else None
        self.auto_on_open = auto_on_open
        self.readonly = readonly  # True なら、名前の変更・移動・削除・フォルダ作成を受け付けない
        # 試験・確認用。None なら OS のゴミ箱。環境変数 PLO_TRASH_DIR でも指定できる（実際のゴミ箱を使わずに動作を確かめるため）
        self._trash_dir = trash_dir or os.environ.get("PLO_TRASH_DIR") or None
        self.folders: dict[str, Path] = {}
        self._dirs: list[tuple[int, Path]] = []
        self._ocr: dict | None = None
        self._ocr_thread: threading.Thread | None = None
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
        h.verify_obj = session._verifier
        h.session = session
        h.current_id = file_id(session.input)
        h.index[h.current_id] = session.input
        return h

    # ---------------------------------------------------------------- 走査
    def _walk(self):
        files: list[tuple[int, Path]] = []
        dirs: list[tuple[int, Path]] = []
        truncated = False
        for ri, root in enumerate(self.roots):
            if root.is_file():
                if root.suffix.lower() == ".pdf":
                    files.append((ri, root))
                continue
            for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                dirnames[:] = sorted((d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS), key=natural_key)
                dirs.append((ri, Path(dirpath)))  # PDF のない（空の）フォルダも一覧に出す
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
        return files, dirs, truncated

    def _dir_key(self, ri: int, d: Path) -> str:
        rel = d.relative_to(self.roots[ri]).parts
        return f"r{ri}" + ("/" + "/".join(rel) if rel else "")

    def scan(self, force: bool = False):
        with self.lock:
            now = time.monotonic()
            if force or self._scan is None or now - self._scan[0] > SCAN_TTL_S:
                files, dirs, trunc = self._walk()
                self._scan = (now, files, trunc)
                self._dirs = dirs
                self.index = {file_id(p): p for _, p in files}
                self.folders = {self._dir_key(ri, d): d for ri, d in dirs}
            return self._scan[1], self._scan[2]

    def add_paths(self, paths) -> list[str]:
        """Finder の「このアプリケーションで開く」などで渡された PDF を、一覧（「開いたPDF」）に加える。IDを返す。"""
        if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
            raise SessionError("paths は文字列のリスト")
        ids = []
        with self.lock:
            for p in paths:
                q = Path(p).expanduser()
                if q.is_symlink() or not q.is_file() or q.suffix.lower() != ".pdf":
                    raise SessionError(f"PDF ではない、または見つからない: {q.name}", 400)
                q = q.resolve()
                if not any((r.is_file() and r == q) or (r.is_dir() and q.is_relative_to(r)) for r in self.roots):
                    self.roots.append(q)
                ids.append(file_id(q))
            self.scan(force=True)
        return ids

    def touch(self):
        self.last_request = time.monotonic()

    def idle_s(self) -> float:
        return time.monotonic() - self.last_request

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
                "with_latex": None, "pages": None, "review": None}
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
            node["review"] = c.get("review")
        return node

    def tree(self, refresh: bool = False) -> dict:
        files, truncated = self.scan(force=refresh)
        self._ensure_hashing(files)
        nodes: dict[str, dict] = {}

        def folder(ri: int, rel: tuple) -> dict:
            key = f"r{ri}" + ("/" + "/".join(rel) if rel else "")
            n = nodes.get(key)
            if n is None:
                root = self.roots[ri]
                n = {"type": "folder", "name": rel[-1] if rel else (root.name or str(root)), "key": key, "children": []}
                nodes[key] = n
                if rel:
                    folder(ri, rel[:-1])["children"].append(n)
            return n

        for ri, d in self._dirs:
            folder(ri, d.relative_to(self.roots[ri]).parts)
        loose: list[dict] = []
        for ri, p in files:
            root = self.roots[ri]
            node = self._file_node(p)
            if root.is_file():
                loose.append(node)
            else:
                folder(ri, p.relative_to(root).parent.parts)["children"].append(node)

        def sort_children(n):
            n["children"].sort(key=lambda c: (c["type"] != "folder", natural_key(c["name"])))
            for c in n["children"]:
                if c["type"] == "folder":
                    sort_children(c)

        roots = [nodes[f"r{ri}"] for ri, r in enumerate(self.roots) if r.is_dir() and f"r{ri}" in nodes]
        for r in roots:
            sort_children(r)
        if loose:
            loose.sort(key=lambda c: natural_key(c["name"]))
            roots.append({"type": "folder", "name": "開いたPDF", "key": "files", "children": loose})
        return {"roots": roots, "current": self.current_id, "hashing": self.hashing_remaining(),
                "truncated": truncated, "backend": self.backend_name, "engines": engine_list(), "files": len(files),
                "readonly": self.readonly, "ocr": self.ocr_status(), "verify": self.verify_obj is not None}

    # ---------------------------------------------------------------- ファイル操作（名前の変更・移動・削除・フォルダ作成）
    def _check_fs(self):
        if self.readonly:
            raise SessionError("読み取り専用で起動しているため、ファイルは動かせない（--readonly）", 403)

    def _resolve(self, t) -> tuple[Path, bool]:
        """PDF の ID かフォルダのキーから、実際のパスを引く。直前の走査にあるものだけを受け付ける。"""
        if not isinstance(t, str) or not t:
            raise SessionError("対象が不正")
        self.scan()
        if _ID_RE.match(t):
            p = self.index.get(t)
            if p is None:
                self.scan(force=True)
                p = self.index.get(t)
            if p is None or not self._allowed(p):
                raise SessionError("その PDF は一覧にない", 404)
            return p, False
        d = self.folders.get(t)
        if d is None:
            self.scan(force=True)
            d = self.folders.get(t)
        if d is None or d.is_symlink() or not d.is_dir():
            raise SessionError("そのフォルダは一覧にない", 404)
        return d, True

    def _not_root(self, p: Path):
        if p in self.roots:
            raise SessionError("一覧の一番上（起動時に指定した場所）は、名前の変更・移動・削除ができない")

    def _pdfs_under(self, d: Path) -> list[Path]:
        files, _ = self.scan()
        return [p for _, p in files if d in p.parents]

    def _log_op(self, action: str, **kw):
        """操作の記録（いつ・何を・どこへ）。library/operations.jsonl に追記する。"""
        try:
            p = library_root(self.library) / "operations.jsonl"
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "action": action, **kw}, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _rekey_cache(self, old: Path, new: Path):
        """移動・名前変更したファイルのハッシュの記録を、新しいパスに付け替える（再計算しない）。"""
        with self.lock:
            self._load_cache()
            for k in list(self._hash):
                kp = Path(k)
                if kp == old or old in kp.parents:
                    self._hash[str(new / kp.relative_to(old))] = self._hash.pop(k)
            self._save_cache()

    def _rename_path(self, src: Path, dest: Path):
        try:
            os.rename(src, dest)
        except OSError as e:
            if e.errno != errno.EXDEV:
                raise SessionError(f"動かせなかった: {e.strerror}", 500) from e
            try:
                shutil.move(str(src), str(dest))  # 別のディスクへ（コピーしてから元を消す）
            except OSError as e2:
                raise SessionError(f"動かせなかった: {e2.strerror}", 500) from e2

    def _do_move(self, src: Path, dest: Path, isdir: bool) -> dict[str, str]:
        """src を dest に動かす。PDF の ID の対応（旧→新）を返す。開いている PDF が動くなら、開き直す。"""
        old = {file_id(p): p for p in self._pdfs_under(src)} if isdir else {file_id(src): src}
        cur = self.session.input if self.session is not None else None
        self._rename_path(src, dest)
        self._rekey_cache(src, dest)
        mapping = {oid: file_id(dest / op.relative_to(src) if isdir else dest) for oid, op in old.items()}
        self._log_op("move", src=str(src), dest=str(dest), kind="folder" if isdir else "pdf")
        self._scan = None
        self.scan(force=True)
        if cur is not None and (cur == src or src in cur.parents):
            new_id = mapping.get(file_id(cur))
            self._release()
            if new_id:
                self.open(new_id, auto=False)
        return mapping

    def mkdir(self, parent_key: str, name: str) -> dict:
        self._check_fs()
        with self.lock:
            parent, isdir = self._resolve(parent_key)
            if not isdir:
                raise SessionError("フォルダの中にしか作れない")
            dest = parent / clean_name(name, False)
            if dest.exists() or dest.is_symlink():
                raise SessionError("同じ名前のフォルダ・ファイルがある", 409)
            try:
                dest.mkdir()  # 1 つだけ作る（途中のフォルダは作らない）
            except OSError as e:
                raise SessionError(f"作れなかった: {e.strerror}", 500) from e
            self._log_op("mkdir", path=str(dest))
            self._scan = None
            self.scan(force=True)
            key = next((k for k, d in self.folders.items() if d == dest), None)
            return {"key": key, "name": dest.name}

    def rename(self, target: str, name: str) -> dict:
        self._check_fs()
        with self.lock:
            src, isdir = self._resolve(target)
            self._not_root(src)
            dest = src.with_name(clean_name(name, not isdir))
            if dest == src:
                raise SessionError("名前が変わっていない")
            if (dest.exists() or dest.is_symlink()) and not (dest.exists() and os.path.samefile(src, dest)):
                raise SessionError("同じ名前のフォルダ・ファイルがある", 409)
            mapping = self._do_move(src, dest, isdir)
            return {"moved": mapping, "current": self.current_id, "name": dest.name}

    def move(self, targets: list, dest_key: str) -> dict:
        self._check_fs()
        with self.lock:
            dest_dir, d_isdir = self._resolve(dest_key)
            if not d_isdir:
                raise SessionError("移動先はフォルダを選ぶ")
            if not isinstance(targets, list) or not targets:
                raise SessionError("対象がない")
            items = [self._resolve(t) for t in targets]
            for src, _ in items:
                self._not_root(src)
            # フォルダとその中身の両方が選ばれていたら、フォルダだけを動かす
            dirs = [src for src, isdir in items if isdir]
            items = [(src, isdir) for src, isdir in items if not any(d in src.parents for d in dirs)]
            plan = []
            for src, isdir in items:
                if src.parent == dest_dir:
                    raise SessionError(f"「{src.name}」はすでにその場所にある")
                if isdir and (dest_dir == src or src in dest_dir.parents):
                    raise SessionError(f"フォルダ「{src.name}」は、自分の中には移せない")
                dest = dest_dir / src.name
                if dest.exists() or dest.is_symlink():
                    raise SessionError(f"移動先に同じ名前がある: {src.name}", 409)
                plan.append((src, dest, isdir))
            if len({d.name for _, d, _ in plan}) != len(plan):
                raise SessionError("同じ名前のものを同時には移せない", 409)
            mapping: dict[str, str] = {}
            for src, dest, isdir in plan:  # 事前の検査をすべて通してから動かす
                mapping.update(self._do_move(src, dest, isdir))
            return {"moved": mapping, "count": len(plan), "current": self.current_id}

    def trash(self, targets: list) -> dict:
        """ゴミ箱へ移す（永久削除ではない）。認識の結果（ライブラリ）は残る。"""
        self._check_fs()
        with self.lock:
            if not isinstance(targets, list) or not targets:
                raise SessionError("対象がない")
            items = [self._resolve(t) for t in targets]
            for src, _ in items:
                self._not_root(src)
            dirs = [src for src, isdir in items if isdir]
            items = [(src, isdir) for src, isdir in items if not any(d in src.parents for d in dirs)]
            cur = self.session.input if self.session is not None else None
            if cur is not None and any(cur == src or src in cur.parents for src, _ in items):
                self._release()
            names = []
            for src, isdir in items:
                dest = move_to_trash(src, self._trash_dir)
                self._log_op("trash", src=str(src), trash=str(dest), kind="folder" if isdir else "pdf")
                names.append(src.name)
            self._scan = None
            self.scan(force=True)
            return {"trashed": names, "current": self.current_id}

    # ---------------------------------------------------------------- OCR（検出＋認識）のジョブ
    def _ocr_targets(self, targets: list) -> list[Path]:
        files, _ = self.scan()
        out: list[Path] = []
        seen: set[Path] = set()
        for t in targets:
            if t == "files":  # 「開いたPDF」（起動時に指定したファイル）
                cand = [p for ri, p in files if self.roots[ri].is_file()]
            else:
                p, isdir = self._resolve(t)
                cand = self._pdfs_under(p) if isdir else [p]
            for q in cand:
                if q not in seen:
                    seen.add(q)
                    out.append(q)
        return out

    def set_engine(self, name: str, confirm_external: bool = False) -> dict:
        """認識エンジンを切り替える。OCR や自動認識の実行中は切り替えない。外部サービスは、同意（confirm_external）がなければ拒否する。"""
        from ..detect.mfd import MFDDetector
        from ..recognize import ENGINES, get_backend

        if name not in ENGINES:
            raise SessionError(f"未知のエンジン: {name}")
        info = ENGINES[name]
        if info["external"] and not confirm_external:
            raise SessionError("このエンジンは式の画像を外部のサービスに送る。確認して同意してから選ぶ", 409)
        with self.lock:
            if self._ocr and self._ocr["state"] == "running":
                raise SessionError("OCR を実行中は、エンジンを切り替えられない", 409)
            if self.session is not None and (self.session.auto.get("state") == "running" or self.session.preparing()):
                raise SessionError("認識を実行中は、エンジンを切り替えられない（終わってから）", 409)
            if self.verify_obj is not None and name == "mathpix":
                raise SessionError("--verify 中は Mathpix を選べない（検証用の2つ目のモデルとの組み合わせは未対応）", 409)
            try:
                new = get_backend(name, **({"allow_external": True} if info["external"] else {}))
                if hasattr(new, "_check"):
                    new._check()
            except Exception as e:  # RecognizerUnavailable: 重みや mpx がない
                raise SessionError(str(e), 409)
            old, own = self.backend_obj, self._own_models
            self.backend_obj, self.backend_name = new, name
            if self.detector is None:
                self.detector = MFDDetector()
            self._own_models = True
            if self.session is not None:
                self.session._backend, self.session.backend_name = new, name
            if own and old is not None and hasattr(old, "close"):
                try:
                    old.close()
                except Exception:
                    pass
            return {"backend": name, "engines": engine_list()}

    def ocr_start(self, targets: list) -> dict:
        if self.detector is None or self.backend_obj is None:
            raise SessionError("認識エンジンが選ばれていない（画面の「エンジン」で選ぶか、--backend で起動）", 409)
        if not isinstance(targets, list) or not targets:
            raise SessionError("対象がない")
        with self.lock:
            if self._ocr and self._ocr["state"] == "running":
                raise SessionError("OCR を実行中（終わるか、キャンセルしてから）", 409)
            paths = self._ocr_targets(targets)
            if not paths:
                raise SessionError("対象の PDF がない")
            job = {"state": "running", "cancel": False, "started": time.time(), "current": None,
                   "items": [{"id": file_id(p), "name": p.name, "state": "queued", "reason": None, "found": 0,
                              "recognized": 0, "failed": 0} for p in paths]}
            self._ocr = job
            self._ocr_thread = threading.Thread(target=self._ocr_run, args=(job, paths), daemon=True)
            self._ocr_thread.start()
            return self.ocr_status()

    def _ocr_run(self, job: dict, paths: list[Path]):
        try:
            for item, path in zip(job["items"], paths):
                if job["cancel"]:
                    item["state"] = "cancelled"
                    continue
                item["state"] = "running"
                job["current"] = item["name"]
                try:
                    with self.lock:
                        cur = self.session if (self.session is not None and self.session.input == path) else None
                    if cur is not None:  # 開いている PDF は、ビューアのセッションで処理する（結果の書き込みを 1 か所にするため）
                        cur.start_auto()
                        while cur.auto_status()["state"] == "running" and not job["cancel"]:
                            time.sleep(0.4)
                        st = cur.auto_status()
                        item["state"] = "cancelled" if job["cancel"] else ("error" if st["state"] == "error" else "done")
                        item["reason"] = st.get("error")
                        continue
                    r = prepare_one(path, detector=self.detector, backend=self.backend_obj, library=self.library,
                                    should_stop=lambda: job["cancel"], log=lambda *_: None, verifier=self.verify_obj)
                    st = r.get("state")
                    if st == "done":
                        item.update(state="done", found=r.get("found", 0), recognized=r.get("recognized", 0),
                                    failed=r.get("failed", 0))
                    elif st == "interrupted":
                        item["state"] = "cancelled"
                    elif st == "skipped":
                        item.update(state="skipped", reason=r.get("reason"))
                    else:
                        item.update(state="error", reason=r.get("reason"))
                except RecognizerUnavailable as e:
                    item.update(state="error", reason=str(e))
                    job["state"] = "error"
                    break
                except Exception as e:  # 1 つの PDF の失敗で、残りを止めない
                    item.update(state="error", reason=f"{type(e).__name__}: {e}")
            if job["state"] == "running":
                job["state"] = "cancelled" if job["cancel"] else "done"
        finally:
            job["current"] = None
            job["finished"] = time.time()

    def ocr_status(self) -> dict | None:
        with self.lock:
            j = self._ocr
            if j is None:
                return None
            items = [dict(i) for i in j["items"]]
            finished = [i for i in items if i["state"] in ("done", "error", "skipped", "cancelled")]
            return {"state": j["state"], "started": j["started"], "total": len(items), "finished": len(finished),
                    "current": j["current"],
                    "ok": sum(1 for i in items if i["state"] == "done"),
                    "failed": sum(1 for i in items if i["state"] == "error"),
                    "skipped": sum(1 for i in items if i["state"] == "skipped"),
                    "elapsed_s": round((j.get("finished") or time.time()) - j["started"], 1), "items": items}

    def ocr_cancel(self) -> dict | None:
        with self.lock:
            if self._ocr and self._ocr["state"] == "running":
                self._ocr["cancel"] = True
            return self.ocr_status()

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
                                        backend_obj=self.backend_obj, detector_obj=self.detector,
                                        verifier_obj=self.verify_obj)
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
        if self._ocr and self._ocr["state"] == "running":
            self._ocr["cancel"] = True
            if self._ocr_thread is not None:
                self._ocr_thread.join(timeout=30)
        with self.lock:
            if self._lock_cm is not None or self.session is not None:
                self._release()
            if self._own_models:
                for m in (self.detector, self.backend_obj, self.verify_obj if self._own_verify else None):
                    if m is not None and hasattr(m, "close"):
                        try:
                            m.close()
                        except Exception:
                            pass


__all__ = ["Hub", "natural_key", "file_id"]
