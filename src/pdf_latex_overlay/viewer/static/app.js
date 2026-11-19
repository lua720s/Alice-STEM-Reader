// PDF LaTeX Overlay ローカルビューア。
// OCR 結果やファイル名はデータとして扱う: textContent / textarea にだけ入れ、HTML として解釈しない（アイコンは固定の文字列のみ）。
"use strict";

const token = new URLSearchParams(location.hash.slice(1)).get("token") || sessionStorage.getItem("plo-token") || "";
if (token) { sessionStorage.setItem("plo-token", token); history.replaceState(null, "", location.pathname); }

const $ = (id) => document.getElementById(id);
const LS = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* 記憶できなくても動く */ } },
};
const reduceMotion = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
const state = {
  doc: null, docId: null, formulas: [], selected: null, redrawFor: null,
  fit: LS.get("plo-fit", "page") === "width" ? "width" : "page", cur: 0,
};

async function api(method, path, body) {
  const opt = { method, headers: { "X-Token": token } };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  const ct = r.headers.get("Content-Type") || "";
  const data = ct.startsWith("application/json") ? await r.json() : await r.blob();
  if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
  return data;
}

function toast(msg, ms = 2200) {
  const t = $("toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), ms);
}

// 上のバーの実際の高さ（折り返すと増える）を CSS に伝える。狭い画面のコレクションの位置などに使う
new ResizeObserver(() => document.documentElement.style.setProperty("--topbar-h", `${$("topbar").offsetHeight}px`)).observe($("topbar"));

// ============================================================ テーマ（自動・ライト・ダーク）
function syncThemeButtons() {
  const cur = window.ploTheme ? window.ploTheme.get() : "system";
  document.querySelectorAll("[data-theme-set]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.themeSet === cur)));
}
document.querySelectorAll("[data-theme-set]").forEach((b) => b.addEventListener("click", () => {
  if (window.ploTheme) window.ploTheme.set(b.dataset.themeSet);
  syncThemeButtons();
  toast({ system: "テーマ: 自動（OSに合わせる）", light: "テーマ: ライト", dark: "テーマ: ダーク" }[b.dataset.themeSet], 1200);
}));
syncThemeButtons();

// ============================================================ ページ（SpeakerDeck 風）
const pagesEl = $("pages");
let slots = [];
let layouting = false;
let holdUntil = 0;

const imgObserver = new IntersectionObserver((entries) => {
  for (const e of entries) if (e.isIntersecting) ensureImage(e.target);
}, { root: pagesEl, rootMargin: "100% 0px" });

function buildDeck() {
  imgObserver.disconnect();
  for (const s of slots) { const i = s.querySelector("img"); if (i && i.dataset.url) URL.revokeObjectURL(i.dataset.url); }
  pagesEl.textContent = "";
  slots = state.doc.pages.map((p, pno) => {
    const slot = document.createElement("div"); slot.className = "slot"; slot.dataset.page = pno;
    const page = document.createElement("div"); page.className = "page"; page.dataset.page = pno;
    const img = document.createElement("img"); img.alt = `ページ ${pno + 1}`; img.draggable = false; img.dataset.dpi = "0";
    page.append(img); slot.append(page); pagesEl.append(slot);
    attachDraw(page, pno);
    imgObserver.observe(slot);
    return slot;
  });
  applyFit();
}

function applyFit() {
  pagesEl.className = state.fit === "width" ? "fit-width" : "fit-page";
  document.querySelectorAll("[data-fit]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.fit === state.fit)));
  layoutDeck();
}

// ページの大きさを決める。「全体」: 1ページを1画面に収める。「幅」: 幅に合わせ、縦にスクロール。
function layoutDeck() {
  if (!state.doc || !slots.length) return;
  const keep = state.cur;
  layouting = true;
  const fitPage = state.fit === "page";
  const W = Math.max(60, pagesEl.clientWidth - 48);
  const H = Math.max(60, pagesEl.clientHeight - 92);
  slots.forEach((slot, i) => {
    const p = state.doc.pages[i];
    let s = W / p.width;
    if (fitPage) s = Math.min(s, H / p.height);
    s = Math.max(s, 0.05);
    const page = slot.firstChild;
    page.style.width = `${p.width * s}px`; page.style.height = `${p.height * s}px`;
    slot.style.height = fitPage ? `${pagesEl.clientHeight}px` : "";
    slot.dataset.scale = String(s);
  });
  pagesEl.scrollTo({ top: slots[Math.min(keep, slots.length - 1)].offsetTop, behavior: "auto" });
  requestAnimationFrame(() => { layouting = false; });
  drawBoxes();
  slots.forEach((s) => { if (s.dataset.seen) ensureImage(s); });
}

// 表示の大きさに合わせた解像度で、見える付近のページだけ読み込む
async function ensureImage(slot) {
  slot.dataset.seen = "1";
  const i = +slot.dataset.page, s = +slot.dataset.scale || 1;
  const dpi = Math.min(240, Math.max(72, Math.ceil((s * (window.devicePixelRatio || 1) * 72) / 24) * 24));
  const img = slot.querySelector("img");
  if (+img.dataset.dpi >= dpi) return;
  img.dataset.dpi = String(dpi);
  try {
    const url = URL.createObjectURL(await api("GET", `/api/page/${i}.png?dpi=${dpi}`));
    if (img.dataset.url) URL.revokeObjectURL(img.dataset.url);
    img.src = url; img.dataset.url = url;
  } catch (e) { img.dataset.dpi = "0"; }
}

function currentIndex() {
  const c = pagesEl.scrollTop + pagesEl.clientHeight / 2;
  let best = 0;
  for (let i = 0; i < slots.length; i++) { if (slots[i].offsetTop <= c) best = i; else break; }
  return best;
}

function setCur(i) {
  if (i === state.cur && $("pageslider").value === String(i + 1)) return;
  state.cur = i;
  updatePageUI();
  if (state.docId) LS.set(`plo-page:${state.docId}`, String(i));
}

function updatePageUI() {
  const n = slots.length;
  $("pageno").textContent = n ? `${state.cur + 1} / ${n}` : "- / -";
  const sl = $("pageslider"); sl.max = String(Math.max(1, n)); sl.value = String(state.cur + 1); sl.disabled = n < 2;
  for (const id of ["prev", "prev2"]) $(id).disabled = state.cur <= 0;
  for (const id of ["next", "next2"]) $(id).disabled = state.cur >= n - 1;
}

// ページ送り。移動中は途中のページ番号を表示せず、行き先を先に表示する（SpeakerDeck と同じ）。
// なめらかな移動が始まらなかったとき（描画が止まっている、省電力など）は、最後に行き先へ直接移す。
function goto(n, smooth = true) {
  if (!slots.length) return;
  n = Math.max(0, Math.min(slots.length - 1, n));
  const target = slots[n].offsetTop, startTop = pagesEl.scrollTop;
  holdUntil = performance.now() + (smooth ? 500 : 80);
  pagesEl.scrollTo({ top: target, behavior: smooth && !reduceMotion ? "smooth" : "auto" });
  setCur(n);
  setTimeout(() => {
    if (performance.now() < holdUntil) return;  // もっと新しい移動が始まっている
    if (pagesEl.scrollTop === startTop && Math.abs(startTop - target) > 2) pagesEl.scrollTo({ top: target, behavior: "auto" });
    setCur(currentIndex());
  }, smooth ? 640 : 100);
}

let scrollRaf = 0;
pagesEl.addEventListener("scroll", () => {
  if (scrollRaf || layouting || performance.now() < holdUntil) return;
  scrollRaf = requestAnimationFrame(() => { scrollRaf = 0; setCur(currentIndex()); });
});
new ResizeObserver(() => { if (state.doc) layoutDeck(); }).observe(pagesEl);

for (const id of ["prev", "prev2"]) $(id).addEventListener("click", () => goto(state.cur - 1));
for (const id of ["next", "next2"]) $(id).addEventListener("click", () => goto(state.cur + 1));
$("pageslider").addEventListener("input", (ev) => goto(+ev.target.value - 1, false));
document.querySelectorAll("[data-fit]").forEach((b) => b.addEventListener("click", () => setFit(b.dataset.fit)));
function setFit(v) { state.fit = v === "width" ? "width" : "page"; LS.set("plo-fit", state.fit); if (state.doc) applyFit(); }

// ============================================================ 式の枠
async function refresh() {
  if (!state.doc) return;
  const d = await api("GET", "/api/formulas");
  state.formulas = d.formulas;
  drawBoxes();
  const n = state.formulas.length, conf = state.formulas.filter((f) => f.status === "confirmed").length;
  $("stats").textContent = `式 ${n} 件・確定 ${conf} 件`;
  if (state.selected) showDetail(state.formulas.find((f) => f.id === state.selected));
}

function pageScale(div) { const p = state.doc.pages[+div.dataset.page]; return div.clientWidth / p.width; }

function drawBoxes() {
  document.querySelectorAll(".box").forEach((b) => b.remove());
  if (!state.doc) return;
  for (const div of document.querySelectorAll(".page")) {
    const pno = +div.dataset.page, s = pageScale(div);
    for (const f of state.formulas.filter((x) => x.page === pno && x.view_rect)) {
      const [x0, y0, x1, y1] = f.view_rect;
      const b = document.createElement("div");
      b.className = `box ${f.status}${f.edited ? " edited" : ""}${f.id === state.selected ? " selected" : ""}`;
      Object.assign(b.style, { left: `${x0 * s}px`, top: `${y0 * s}px`, width: `${(x1 - x0) * s}px`, height: `${(y1 - y0) * s}px` });
      b.title = f.latex || "(LaTeX なし)";
      b.dataset.id = f.id;
      // 重なったときは小さい枠を上にして、クリックで選べるようにする
      b.style.zIndex = String(2 + Math.max(0, 100000 - Math.round((x1 - x0) * (y1 - y0))));
      b.addEventListener("dblclick", (ev) => { ev.stopPropagation(); select(f.id); copyLatex(); });
      div.append(b);
    }
  }
}

function select(id) {
  state.selected = id;
  drawBoxes();
  showDetail(state.formulas.find((f) => f.id === id));
}

function renderPreview(latex) {
  const el = $("preview"); el.textContent = "";
  if (!latex) { el.textContent = "（LaTeX なし）"; return; }
  if (window.katex) {
    const lines = latex.split("\n").filter((x) => x.trim());
    const src = lines.length > 1 ? `\\begin{gathered}${lines.join("\\\\")}\\end{gathered}` : latex;
    try { window.katex.render(src, el, { throwOnError: false, displayMode: true, trust: false, strict: "ignore" }); return; }
    catch (e) { /* 下へ */ }
  }
  el.textContent = "（プレビューなし: KaTeX を読み込めない）";
}

async function showDetail(f) {
  if (!f) { $("detail").hidden = true; $("empty").hidden = false; return; }
  $("detail").hidden = false; $("empty").hidden = true;
  $("fid").textContent = f.id;
  $("fstatus").textContent = f.status;
  $("fsource").textContent = `${f.source}${f.confidence != null ? ` / 信頼度 ${f.confidence.toFixed(2)}` : ""}${f.edited ? " / 修正済み" : ""}`;
  if (document.activeElement !== $("latex")) $("latex").value = f.latex || "";
  $("ocr").textContent = f.ocr_latex || "（OCR 原文なし）";
  $("reason").textContent = f.reason ? `理由: ${f.reason}` : "";
  renderPreview($("latex").value);
  try { const blob = await api("GET", `/api/formulas/${encodeURIComponent(f.id)}/crop.png`); $("crop").src = URL.createObjectURL(blob); }
  catch (e) { $("crop").removeAttribute("src"); }
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast("LaTeX をコピーしました"); }
  catch (e) { toast("コピーできませんでした（ブラウザの許可を確認）", 4000); }
}

async function copyLatex() {
  const text = $("latex").value;
  if (!text) { toast("コピーする LaTeX がありません"); return; }
  try { await navigator.clipboard.writeText(text); toast("LaTeX をコピーしました"); }
  catch (e) { $("latex").select(); document.execCommand("copy"); toast("LaTeX をコピーしました"); }
}

async function act(fn, okMsg) {
  try { await fn(); await refresh(); if (okMsg) toast(okMsg); } catch (e) { toast(`失敗: ${e.message}`, 4000); }
}

// Mathpix Snip のように: ドラッグで枠を描く（既存の枠の上からでもよい）。動かさずに離したらクリック扱いで、
// 枠をクリックしたらその式の LaTeX をすぐコピーする。マウスの監視はページ全体で 1 組だけ持つ。
let drag = null;
function attachDraw(div, pno) {
  div.addEventListener("mousedown", (ev) => {
    if (ev.button !== 0) return;
    ev.preventDefault();
    const r = div.getBoundingClientRect();
    const box = ev.target.closest && ev.target.closest(".box");
    drag = { div, pno, start: [ev.clientX - r.left, ev.clientY - r.top], moved: false, rubber: null, hitId: box ? box.dataset.id : null };
  });
}
window.addEventListener("mousemove", (ev) => {
  if (!drag) return;
  const r = drag.div.getBoundingClientRect();
  const x = ev.clientX - r.left, y = ev.clientY - r.top, s0 = drag.start;
  if (!drag.moved && Math.hypot(x - s0[0], y - s0[1]) < 5) return;
  if (!drag.moved) { drag.moved = true; drag.rubber = document.createElement("div"); drag.rubber.className = "rubber"; drag.div.append(drag.rubber); }
  Object.assign(drag.rubber.style, { left: `${Math.min(x, s0[0])}px`, top: `${Math.min(y, s0[1])}px`,
    width: `${Math.abs(x - s0[0])}px`, height: `${Math.abs(y - s0[1])}px` });
});
window.addEventListener("mouseup", async (ev) => {
  if (!drag) return;
  const d = drag; drag = null;
  if (!d.moved) {
    if (d.hitId) { select(d.hitId); const f = state.formulas.find((x) => x.id === d.hitId); if (f && f.latex) copyText(f.latex); }
    return;
  }
  const r = d.div.getBoundingClientRect(), s = pageScale(d.div);
  const x = ev.clientX - r.left, y = ev.clientY - r.top, s0 = d.start;
  if (d.rubber) d.rubber.remove();
  const vr = [Math.min(x, s0[0]) / s, Math.min(y, s0[1]) / s, Math.max(x, s0[0]) / s, Math.max(y, s0[1]) / s];
  if (vr[2] - vr[0] < 3 || vr[3] - vr[1] < 3) return;
  if (state.redrawFor) {
    const id = state.redrawFor; state.redrawFor = null;
    await act(() => api("POST", `/api/formulas/${encodeURIComponent(id)}`, { view_rect: vr }), "枠を更新しました（必要なら再認識）");
    return;
  }
  const recognize = state.doc.backend && state.doc.backend !== "none";
  if (recognize) toast("認識しています…", 30000);
  await act(async () => {
    const f = await api("POST", "/api/formulas", { page: d.pno, view_rect: vr, recognize });
    state.selected = f.id;
  }, recognize ? "枠を追加して認識しました" : "枠を追加しました（LaTeX を入力してください）");
});

// ============================================================ コレクション（Obsidian 風のツリー）
const ICON = {
  folder: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  file: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M7 3h7l5 5v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M14 3v5h5"/></svg>',
  chev: '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>',
};
const STATE_TEXT = { done: "準備済み", partial: "一部のみ処理済み", running: "処理中", none: "未処理", unknown: "確認中" };
const tree = { data: null, json: "", expanded: new Set(), firstTime: false, filter: "", rows: [], els: new Map(), focusKey: null, fast: false };

try {
  const v = JSON.parse(LS.get("plo-expanded", "null"));
  if (Array.isArray(v)) tree.expanded = new Set(v); else tree.firstTime = true;
} catch (e) { tree.firstTime = true; }
const saveExpanded = () => LS.set("plo-expanded", JSON.stringify([...tree.expanded]));

function annotate(n) {
  if (n.type === "file") { n._files = 1; n._done = n.state === "done" ? 1 : 0; return; }
  n._files = 0; n._done = 0;
  for (const c of n.children) { annotate(c); n._files += c._files; n._done += c._done; }
}

function filterNode(n, q) {
  if (n.type === "file") return n.name.toLowerCase().includes(q) ? n : null;
  if (n.name.toLowerCase().includes(q)) return n;
  const kids = n.children.map((c) => filterNode(c, q)).filter(Boolean);
  return kids.length ? { ...n, children: kids } : null;
}

function ancestorsOf(nodes, id, trail = []) {
  for (const n of nodes) {
    if (n.type === "file") { if (n.id === id) return trail; continue; }
    const r = ancestorsOf(n.children, id, [...trail, n.key]);
    if (r) return r;
  }
  return null;
}

function renderTree() {
  const el = $("tree"), t = tree.data;
  if (!t) return;
  const q = tree.filter.trim().toLowerCase();
  const roots = q ? t.roots.map((n) => filterNode(n, q)).filter(Boolean) : t.roots;
  roots.forEach(annotate);
  tree.rows = []; tree.els = new Map();
  const frag = document.createDocumentFragment();
  const add = (node0, depth, parentKey) => {
    // 子がフォルダ1つだけの連なりは、1 行にまとめる（深い階層でも名前が読める）
    let node = node0, label = node0.name;
    while (node.type === "folder" && node.children.length === 1 && node.children[0].type === "folder") {
      node = node.children[0]; label += ` / ${node.name}`;
    }
    const isFolder = node.type === "folder";
    const key = isFolder ? node.key : node.id;
    const open = isFolder && (q ? true : tree.expanded.has(node.key));
    tree.rows.push({ key, node, depth, parentKey, isFolder, open });
    const row = makeRow(node, key, depth, isFolder, open, label);
    tree.els.set(key, row);
    frag.append(row);
    if (isFolder && open) node.children.forEach((c) => add(c, depth + 1, key));
  };
  roots.forEach((r) => add(r, 0, null));
  el.textContent = "";
  if (!tree.rows.length) {
    const d = document.createElement("div"); d.className = "tree-empty";
    d.textContent = q ? "一致するPDFがありません" : "PDFがありません";
    el.append(d);
  } else {
    el.append(frag);
  }
  if (!tree.rows.some((r) => r.key === tree.focusKey)) tree.focusKey = (tree.rows.find((r) => r.key === t.current) || tree.rows[0] || {}).key || null;
  updateFocus(false);
  t.roots.forEach(annotate);
  const ready = t.roots.reduce((a, r) => a + r._done, 0);
  $("tree-foot").textContent = `${t.files} 個のPDF・準備済み ${ready}` + (t.hashing > 0 ? `・状態を確認中 ${t.hashing}` : "")
    + (t.truncated ? "・一部のみ表示（上限）" : "");
}

function makeRow(node, key, depth, isFolder, open, label) {
  const isCur = !isFolder && tree.data && key === tree.data.current;
  const row = document.createElement("div");
  row.className = `trow ${isFolder ? "folder" : "file"}${isCur ? " current" : ""}`;
  row.setAttribute("role", "treeitem");
  row.setAttribute("aria-level", String(depth + 1));
  if (isFolder) row.setAttribute("aria-expanded", String(open));
  if (isCur) row.setAttribute("aria-current", "true");
  row.dataset.key = key; row.tabIndex = -1; row.style.setProperty("--d", String(depth));
  const chev = document.createElement("span"); chev.className = `chev${open ? " open" : ""}`; if (isFolder) chev.innerHTML = ICON.chev;
  const ico = document.createElement("span"); ico.className = "ico"; ico.innerHTML = isFolder ? ICON.folder : ICON.file;
  const name = document.createElement("span"); name.className = "name"; name.textContent = label || node.name;
  row.append(chev, ico, name);
  const meta = document.createElement("span"); meta.className = "meta";
  if (isFolder) {
    meta.textContent = node._files ? `${node._done}/${node._files}` : "";
    row.title = `${label || node.name}\n${node._files} 個のPDF（準備済み ${node._done}）`;
    row.append(meta);
    row.addEventListener("click", () => { tree.focusKey = key; toggleFolder(node.key); });
    return row;
  }
  const dot = document.createElement("span"); dot.className = `dot ${node.state}`;
  if (node.formulas != null) meta.textContent = `式 ${node.formulas}`;
  row.append(meta, dot);
  row.title = `${node.name}\n${STATE_TEXT[node.state] || ""}${node.pages ? `・${node.pages} ページ` : ""}`;
  row.addEventListener("click", () => { tree.focusKey = key; updateFocus(false); openDoc(node.id); });
  return row;
}

function toggleFolder(key, force) {
  const open = force === undefined ? !tree.expanded.has(key) : force;
  if (open) tree.expanded.add(key); else tree.expanded.delete(key);
  saveExpanded();
  renderTree();
  updateFocus(true);
}

function updateFocus(doFocus) {
  for (const [k, el] of tree.els) { const on = k === tree.focusKey; el.classList.toggle("focus", on && doFocus); el.tabIndex = on ? 0 : -1; }
  if (!tree.focusKey || !doFocus) return;
  const el = tree.els.get(tree.focusKey);
  if (el) { el.focus({ preventScroll: true }); el.scrollIntoView({ block: "nearest" }); }
}

$("tree").addEventListener("keydown", (ev) => {
  const i = tree.rows.findIndex((r) => r.key === tree.focusKey);
  if (i < 0) return;
  const r = tree.rows[i], q = tree.filter.trim();
  const go = (k) => { tree.focusKey = k; updateFocus(true); };
  switch (ev.key) {
    case "ArrowDown": if (tree.rows[i + 1]) go(tree.rows[i + 1].key); break;
    case "ArrowUp": if (tree.rows[i - 1]) go(tree.rows[i - 1].key); break;
    case "Home": go(tree.rows[0].key); break;
    case "End": go(tree.rows[tree.rows.length - 1].key); break;
    case "ArrowRight":
      if (r.isFolder && !r.open && !q) toggleFolder(r.node.key, true);
      else if (r.isFolder && r.open && tree.rows[i + 1] && tree.rows[i + 1].parentKey === r.key) go(tree.rows[i + 1].key);
      break;
    case "ArrowLeft":
      if (r.isFolder && r.open && !q) toggleFolder(r.node.key, false);
      else if (r.parentKey) go(r.parentKey);
      break;
    case "Enter": case " ":
      if (r.isFolder) toggleFolder(r.node.key); else openDoc(r.node.id);
      break;
    default: return;
  }
  ev.preventDefault();
});

$("tree-filter").addEventListener("input", (ev) => { tree.filter = ev.target.value; renderTree(); });
$("tree-filter").addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") { ev.target.value = ""; tree.filter = ""; renderTree(); ev.target.blur(); }
  if (ev.key === "ArrowDown" && tree.rows.length) { ev.preventDefault(); updateFocus(true); }
  if (ev.key === "Enter") { const f = tree.rows.find((r) => !r.isFolder); if (f) openDoc(f.node.id); }
});
$("tree-collapse").addEventListener("click", () => { tree.expanded = new Set(); saveExpanded(); renderTree(); });
$("tree-refresh").addEventListener("click", async () => { try { await loadTree(true); toast("一覧を更新しました", 1200); } catch (e) { toast(`失敗: ${e.message}`, 3000); } });

async function loadTree(force = false) {
  const t = await api("GET", force ? "/api/tree?refresh=1" : "/api/tree");
  if (tree.firstTime) { tree.firstTime = false; t.roots.forEach((r) => tree.expanded.add(r.key)); saveExpanded(); }
  const j = JSON.stringify(t);
  if (j !== tree.json) { tree.json = j; tree.data = t; renderTree(); }
  return t;
}

function revealCurrent() {
  const t = tree.data;
  if (!t || !t.current) return;
  const trail = ancestorsOf(t.roots, t.current) || [];
  let changed = false;
  for (const k of trail) if (!tree.expanded.has(k)) { tree.expanded.add(k); changed = true; }
  if (changed) saveExpanded();
  tree.focusKey = t.current;
  renderTree();
  const el = tree.els.get(t.current);
  if (el) el.scrollIntoView({ block: "nearest" });
}

let treeTimer = 0;
function pollTree() {
  clearTimeout(treeTimer);
  treeTimer = setTimeout(async () => {
    if (!document.hidden) { try { const t = await loadTree(); tree.fast = tree.fast || t.hashing > 0; } catch (e) { /* 次回 */ } }
    pollTree();
  }, tree.fast || (tree.data && tree.data.hashing > 0) ? 2000 : 8000);
}

// ---- コレクションの開閉と幅
// 開閉は、利用者が操作したときだけ記憶する（狭い画面の既定の「閉」を、広い画面に持ち越さない）
function setSidebar(open, remember = false) {
  $("layout").classList.toggle("sb-open", open);
  $("sidebar-toggle").setAttribute("aria-pressed", String(open));
  if (remember && innerWidth > 900) LS.set("plo-sb", open ? "1" : "0");
}
$("sidebar-toggle").addEventListener("click", () => setSidebar(!$("layout").classList.contains("sb-open"), true));
const savedW = parseInt(LS.get("plo-sbw", ""), 10);
if (savedW >= 180 && savedW <= 520) document.documentElement.style.setProperty("--sbw", `${savedW}px`);
setSidebar(innerWidth > 900 && LS.get("plo-sb", "1") !== "0");
// 画面の幅が変わって狭い画面になったら、コレクションは閉じる（広く戻ったら、記憶していた状態に戻す）
matchMedia("(max-width: 900px)").addEventListener("change", (e) => setSidebar(!e.matches && LS.get("plo-sb", "1") !== "0"));

(function splitter() {
  const sp = $("splitter");
  const setW = (w) => { w = Math.max(180, Math.min(520, Math.min(w, innerWidth * 0.5))); document.documentElement.style.setProperty("--sbw", `${w}px`); LS.set("plo-sbw", String(Math.round(w))); };
  sp.addEventListener("pointerdown", (ev) => { sp.setPointerCapture(ev.pointerId); sp.classList.add("drag"); });
  sp.addEventListener("pointermove", (ev) => { if (sp.classList.contains("drag")) setW(ev.clientX); });
  sp.addEventListener("pointerup", () => sp.classList.remove("drag"));
  sp.addEventListener("keydown", (ev) => {
    const cur = $("sidebar").getBoundingClientRect().width;
    if (ev.key === "ArrowLeft") { setW(cur - 16); ev.preventDefault(); }
    if (ev.key === "ArrowRight") { setW(cur + 16); ev.preventDefault(); }
  });
})();

// ============================================================ PDF の切り替え
let lastVersion = -1;

async function openDoc(id, quiet = false) {
  if (id === state.docId) { if (innerWidth <= 900) setSidebar(false); return; }
  try { await api("POST", "/api/open", { id }); }
  catch (e) { if (!quiet) toast(`開けませんでした: ${e.message}`, 5000); return; }
  LS.set("plo-last", id);
  await loadDoc();
  try { await loadTree(); } catch (e) { /* 次の更新で反映 */ }
  revealCurrent();
  focusDeck();
  if (innerWidth <= 900) setSidebar(false);
}

// 開いたら、ページ送りのキー（←→・Space）がすぐ効くよう、フォーカスをページ側へ移す
function focusDeck() { pagesEl.tabIndex = -1; pagesEl.focus({ preventScroll: true }); }

async function loadDoc() {
  let doc = null;
  try { doc = await api("GET", "/api/doc"); } catch (e) { doc = null; }
  state.doc = doc; state.docId = doc ? doc.id : null;
  state.formulas = []; state.selected = null; state.cur = 0; lastVersion = -1;
  showDetail(null);
  $("deck").classList.toggle("empty", !doc);
  $("emptydeck").hidden = !!doc;
  $("docname").textContent = doc ? doc.name : "PDF LaTeX Overlay";
  document.title = doc ? `${doc.name} – PDF LaTeX Overlay` : "PDF LaTeX Overlay";
  $("stats").textContent = "";
  $("autostat").textContent = "";
  $("autorun").disabled = !doc || !doc.backend || doc.backend === "none";
  $("autorun").classList.remove("attention");
  if (!doc) { imgObserver.disconnect(); pagesEl.textContent = ""; slots = []; updatePageUI(); return; }
  buildDeck();
  const saved = parseInt(LS.get(`plo-page:${doc.id}`, "0"), 10) || 0;
  state.cur = Math.max(0, Math.min(saved, slots.length - 1));
  requestAnimationFrame(() => { goto(state.cur, false); updatePageUI(); });
  await refresh();
}

// ============================================================ 画面の操作
function wire() {
  $("latex").addEventListener("input", () => renderPreview($("latex").value));
  $("copy").onclick = copyLatex;
  $("save").onclick = () => act(() => api("POST", `/api/formulas/${encodeURIComponent(state.selected)}`, { latex: $("latex").value }), "修正を保存しました");
  $("confirm").onclick = () => act(() => api("POST", `/api/formulas/${encodeURIComponent(state.selected)}`, { latex: $("latex").value, status: "confirmed" }), "確定しました");
  $("exclude").onclick = () => act(() => api("POST", `/api/formulas/${encodeURIComponent(state.selected)}`, { status: "excluded", reason: "user" }), "除外しました");
  $("recognize").onclick = () => act(() => api("POST", `/api/formulas/${encodeURIComponent(state.selected)}/recognize`), "再認識しました");
  $("redraw").onclick = () => { state.redrawFor = state.selected; toast("ページ上で新しい枠をドラッグしてください"); };
  $("delete").onclick = () => {
    if (!confirm(`${state.selected} を削除しますか？（履歴には残ります）`)) return;
    const id = state.selected; state.selected = null;
    act(() => api("DELETE", `/api/formulas/${encodeURIComponent(id)}`), "削除しました");
  };
  $("autorun").onclick = () => act(() => api("POST", "/api/auto"), "全ページの認識を始めました");
  $("export").onclick = async () => {
    if (!state.doc) { toast("PDFを選んでください"); return; }
    $("export").disabled = true; toast("派生PDFを作成・検証しています…", 60000);
    try {
      const r = await api("POST", "/api/export", { mode: $("mode").value, only_confirmed: $("onlyconfirmed").checked });
      const ex = r.excluded.filter((x) => x.reason && x.reason !== "user").map((x) => `${x.id}: ${x.reason}`);
      if (r.ok) toast(`保存しました（埋め込み ${r.counts.embedded} 件、除外 ${r.counts.excluded} 件）\n${r.output}${ex.length ? "\n" + ex.join("\n") : ""}`, 10000);
      else toast(`保存しませんでした（終了コード ${r.exit_code}）\n${r.errors.join("\n")}${r.failed_checks.length ? "\n検証不合格: " + r.failed_checks.join(", ") : ""}`, 10000);
    } catch (e) { toast(`失敗: ${e.message}`, 6000); }
    finally { $("export").disabled = false; }
  };

  // キーボード: ページ送りは SpeakerDeck と同じ（←→・Space・PageUp/Down・Home/End）
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") { state.redrawFor = null; return; }
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
    const t = ev.target;
    if (t.closest && t.closest("input, textarea, select, [contenteditable]")) return;
    if (t.closest && t.closest("#tree") && ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "Enter", " "].includes(ev.key)) return;
    const fitPage = state.fit === "page";
    let handled = true;
    switch (ev.key) {
      case "ArrowRight": case "PageDown": case "j": goto(state.cur + 1); break;
      case "ArrowLeft": case "PageUp": case "k": goto(state.cur - 1); break;
      case " ": goto(state.cur + (ev.shiftKey ? -1 : 1)); break;
      case "ArrowDown": if (fitPage) goto(state.cur + 1); else handled = false; break;
      case "ArrowUp": if (fitPage) goto(state.cur - 1); else handled = false; break;
      case "Home": goto(0); break;
      case "End": goto(slots.length - 1); break;
      case "f": setFit(state.fit === "page" ? "width" : "page"); toast(state.fit === "page" ? "表示: ページ全体" : "表示: 幅に合わせる", 1100); break;
      case "b": setSidebar(!$("layout").classList.contains("sb-open"), true); break;
      case "/": $("tree-filter").focus(); $("tree-filter").select(); break;
      case "c": if (state.selected) copyLatex(); else handled = false; break;
      default: handled = false;
    }
    if (handled) ev.preventDefault();
  });
}

async function pollAuto() {
  let delay = 5000;
  try {
    if (state.doc) {
      const a = await api("GET", "/api/auto");
      const el = $("autostat");
      tree.fast = a.state === "running" || a.state === "preparing";
      if (a.state === "running") {
        el.textContent = a.phase === "detect" ? `数式を検出中… ${a.page}/${a.pages} ページ` : `認識中… ${a.recognized}/${a.total || 0}`;
      } else if (a.state === "preparing") {
        el.textContent = "事前処理中（終わるまで表示のみ。結果は順に反映）";
      } else if (a.state === "done") {
        el.textContent = "認識済み";
      } else if (a.state === "error") {
        el.textContent = `自動認識に失敗: ${a.error}`;
      } else {
        el.textContent = state.formulas.length === 0 && state.doc.backend !== "none" ? "未処理" : "";
      }
      $("autorun").classList.toggle("attention", a.state === "idle" && state.formulas.length === 0 && state.doc.backend !== "none");
      if (a.version !== lastVersion) { lastVersion = a.version; await refresh(); }
      delay = tree.fast ? 1500 : 5000;
    }
  } catch (e) { /* 次回 */ }
  setTimeout(pollAuto, delay);
}

async function boot() {
  if (!token) { document.body.textContent = "トークンがありません。起動時に表示された URL を開いてください。"; return; }
  wire();
  let t;
  try { t = await loadTree(); } catch (e) { toast(`読み込みに失敗: ${e.message}`, 6000); return; }
  await loadDoc();  // 起動時に開かれている PDF があれば表示する
  revealCurrent();
  if (state.doc) focusDeck();
  if (!state.doc) {  // なければ、前回開いていた PDF（一覧に残っていれば）を開く
    const last = LS.get("plo-last", "");
    const has = (nodes) => nodes.some((n) => (n.type === "file" ? n.id === last : has(n.children)));
    if (last && has(t.roots)) await openDoc(last, true);
  }
  pollAuto(); pollTree();
}
boot();
