// PDF LaTeX Overlay ローカルビューア。
// OCR 結果やファイル名はデータとして扱う: textContent / textarea にだけ入れ、HTML として解釈しない（アイコンは固定の文字列のみ）。
"use strict";

const T = (k, v) => (window.ploI18n ? window.ploI18n.t(k, v) : k);
const token = new URLSearchParams(location.hash.slice(1)).get("token") || sessionStorage.getItem("plo-token") || "";
if (token) { sessionStorage.setItem("plo-token", token); history.replaceState(null, "", location.pathname); }

const $ = (id) => document.getElementById(id);
const LS = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* 記憶できなくても動く */ } },
};
const reduceMotion = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
const ZOOM_MIN = 0.5, ZOOM_MAX = 5;
const state = {
  doc: null, docId: null, formulas: [], selected: null, redrawFor: null,
  fit: LS.get("plo-fit", "page") === "width" ? "width" : "page", cur: 0, zoom: Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, parseFloat(LS.get("plo-zoom", "1")) || 1)),
};

async function api(method, path, body) {
  const opt = { method, headers: { "X-Token": token } };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  let r;
  try { r = await fetch(path, opt); }
  catch (e) { setConn(false); throw new Error(T("ビューアに接続できません（終了したか、起動し直して接続先が変わった可能性があります）")); }  // 「Failed to fetch」: サーバーに届かなかった
  setConn(true);
  const ct = r.headers.get("Content-Type") || "";
  const data = ct.startsWith("application/json") ? await r.json() : await r.blob();
  if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
  return data;
}

function toast(msg, ms = 2200, action = null) {
  const t = $("toast"); t.textContent = msg; t.classList.add("show");
  t.classList.toggle("has-action", !!action);
  if (action) {  // 「元に戻す」などのボタンつき
    const b = document.createElement("button"); b.type = "button"; b.textContent = action.label;
    b.addEventListener("click", () => { t.classList.remove("show"); action.run(); });
    t.append(" ", b);
  }
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), ms);
}

// ============================================================ 接続の状態
// ビューアが終了した、または起動し直して接続先（ポート・トークン）が変わると、ブラウザは「Failed to fetch」を返す。
// 何が起きたかを画面に出し、つながり直したら自動で消す。
let connOk = true;
function setConn(ok) { if (ok === connOk) return; connOk = ok; $("conn").hidden = ok; }
$("conn-retry").addEventListener("click", async () => {
  try { await loadTree(true); toast(T("再接続しました"), 1500); } catch (e) { toast(T("まだ接続できません。ビューアが動いているか確認してください"), 3500); }
});

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
  toast(T({ system: "テーマ: 自動（OSに合わせる）", light: "テーマ: ライト", dark: "テーマ: ダーク" }[b.dataset.themeSet]), 1200);
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
    const img = document.createElement("img"); img.alt = T("ページ {n}", { n: pno + 1 }); img.draggable = false; img.dataset.dpi = "0";
    page.append(img); slot.append(page); pagesEl.append(slot);
    attachDraw(page, pno);
    imgObserver.observe(slot);
    return slot;
  });
  applyFit();
}

function applyFit() {
  pagesEl.className = (state.fit === "width" ? "fit-width" : "fit-page") + (zoomed() ? " zoomed" : "");
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
    s = Math.max(s * state.zoom, 0.05);
    const page = slot.firstChild;
    page.style.width = `${p.width * s}px`; page.style.height = `${p.height * s}px`;
    slot.style.height = fitPage ? `${Math.max(pagesEl.clientHeight, zoomed() ? p.height * s + 92 : 0)}px` : "";
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
  const dpi = Math.min(300, Math.max(72, Math.ceil((s * (window.devicePixelRatio || 1) * 72) / 24) * 24));
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
  syncNotePage();
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
// ---- 拡大・縮小（1 = 「全体」「幅」の大きさ。Ctrl/⌘＋ホイール・ピンチ・＋/−/0 キーでも）
function zoomed() { return Math.abs(state.zoom - 1) > 0.001 && state.zoom > 1; }
function setZoom(z) {
  z = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(z * 1000) / 1000));
  if (Math.abs(z - 1) < 0.02) z = 1;
  if (z === state.zoom) return;
  const sl = slots[state.cur];
  const fy = sl ? (pagesEl.scrollTop - sl.offsetTop) / Math.max(1, sl.offsetHeight) : 0;
  const fx = pagesEl.scrollLeft / Math.max(1, pagesEl.scrollWidth - pagesEl.clientWidth);
  state.zoom = z; LS.set("plo-zoom", String(z));
  $("zoom-label").textContent = `${Math.round(z * 100)}%`;
  if (!state.doc) return;
  pagesEl.className = (state.fit === "width" ? "fit-width" : "fit-page") + (zoomed() ? " zoomed" : "");
  layoutDeck();
  const nl = slots[state.cur];
  if (nl) pagesEl.scrollTo({ top: nl.offsetTop + fy * nl.offsetHeight, left: fx * Math.max(0, pagesEl.scrollWidth - pagesEl.clientWidth), behavior: "auto" });
}
$("zoom-label").textContent = `${Math.round(state.zoom * 100)}%`;
$("zoom-in").addEventListener("click", () => setZoom(state.zoom * 1.25));
$("zoom-out").addEventListener("click", () => setZoom(state.zoom / 1.25));
$("zoom-label").addEventListener("click", () => setZoom(1));
pagesEl.addEventListener("wheel", (ev) => {
  if (!(ev.ctrlKey || ev.metaKey)) return;  // 普通のスクロールは、そのまま
  ev.preventDefault();
  setZoom(state.zoom * Math.exp(-ev.deltaY * 0.01));
}, { passive: false });

function setFit(v) { state.fit = v === "width" ? "width" : "page"; LS.set("plo-fit", state.fit); if (state.doc) applyFit(); }

// ============================================================ 式の枠
async function refresh() {
  if (!state.doc) return;
  const d = await api("GET", "/api/formulas");
  state.formulas = d.formulas;
  drawBoxes();
  const fo = state.formulas.filter((f) => f.kind !== "text"), n = fo.length, conf = fo.filter((f) => f.status === "confirmed").length;
  $("stats").textContent = T("式 {n} 件・確定 {c} 件", { n, c: conf }) + (state.doc.verify ? T("・2モデルで検証") : "");
  const rv = reviewList().length;
  $("reviewbtn").hidden = rv === 0; $("reviewcount").textContent = T("要確認 {n} 件 →", { n: rv });
  if (state.selected) showDetail(state.formulas.find((f) => f.id === state.selected));
}

// ---- 要確認（2つのモデルの読み取りが食い違った式）
const reviewList = () => state.formulas.filter((f) => f.review && f.status !== "excluded" && f.view_rect)
  .sort((a, b) => a.page - b.page || a.view_rect[1] - b.view_rect[1] || a.view_rect[0] - b.view_rect[0]);
const REVIEW_WHY = {
  get disagree() { return T("2つのモデルの読み取りが食い違っています。元の画像と見比べて確認してください。"); },
  get failed() { return T("どちらかのモデルが読み取れませんでした。確認してください。"); },
};
function nextReview(dir = 1) {
  const list = reviewList();
  if (!list.length) { toast(T("要確認の式はありません")); return; }
  const cur = list.findIndex((f) => f.id === state.selected);
  let i;
  if (cur >= 0) i = (cur + dir + list.length) % list.length;
  else i = dir > 0 ? Math.max(0, list.findIndex((f) => f.page > state.cur || (f.page === state.cur))) : list.length - 1;
  const f = list[i];
  if (f.page !== state.cur) goto(f.page);
  select(f.id);
  setTimeout(() => { const b = document.querySelector(`.box[data-id="${CSS.escape(f.id)}"]`); if (b) b.scrollIntoView({ block: "nearest", inline: "nearest" }); }, 60);
  toast(T("要確認 {i} / {n}", { i: i + 1, n: list.length }), 1200);
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
      b.className = `box ${f.status}${f.kind === "text" ? " text" : ""}${f.edited ? " edited" : ""}${f.review ? " review" : ""}${f.id === state.selected ? " selected" : ""}`;
      Object.assign(b.style, { left: `${x0 * s}px`, top: `${y0 * s}px`, width: `${(x1 - x0) * s}px`, height: `${(y1 - y0) * s}px` });
      b.title = f.latex || T("(LaTeX なし)");
      b.dataset.id = f.id;
      // 重なったときは小さい枠を上にして、クリックで選べるようにする
      b.style.zIndex = String(2 + Math.max(0, 100000 - Math.round((x1 - x0) * (y1 - y0))));
      b.addEventListener("dblclick", (ev) => { ev.stopPropagation(); select(f.id); copyLatex(); });
      b.addEventListener("contextmenu", (ev) => {  // 右クリック: コピー・削除
        ev.preventDefault(); ev.stopPropagation(); select(f.id);
        showMenu(ev.clientX, ev.clientY, [{ label: T("コピー"), run: () => copyLatex() }, "-",
          { label: T("この枠を削除"), run: () => deleteFormula(f.id), danger: true }], b);
      });
      if (f.id === state.selected) {  // 選んだ枠の右上の「×」で、すぐ削除できる
        const x = document.createElement("button");
        x.type = "button"; x.className = "box-x"; x.textContent = "×"; x.title = T("この枠を削除（Delete キー）"); x.setAttribute("aria-label", T("この枠を削除"));
        for (const evn of ["pointerdown", "mousedown", "click", "dblclick"]) x.addEventListener(evn, (ev) => ev.stopPropagation());
        x.addEventListener("click", () => deleteFormula(f.id));
        b.append(x);
      }
      div.append(b);
    }
  }
}

// ---- 枠の削除（履歴には残り、「元に戻す」で戻せる）
async function deleteFormula(id) {
  if (!id) return;
  try {
    const r = await api("DELETE", `/api/formulas/${encodeURIComponent(id)}`);
    if (state.selected === id) state.selected = null;
    await refresh();
    toast(T("削除しました"), 8000, { label: T("元に戻す"), run: () => restoreFormulas([r.formula]) });
  } catch (e) { toast(T("失敗: {m}", { m: e.message }), 4000); }
}
async function restoreFormulas(list) {
  try { await api("POST", "/api/formulas/restore", { formulas: list }); await refresh(); toast(T("元に戻しました"), 1500); }
  catch (e) { toast(T("失敗: {m}", { m: e.message }), 4000); }
}
async function deletePageFormulas(scope) {
  if (!state.doc) return;
  try {
    const r = await api("POST", "/api/formulas/delete_many", { page: state.cur, scope });
    state.selected = null;
    await refresh();
    if (!r.deleted.length) { toast(T("削除する枠がありません"), 1800); return; }
    toast(T("{n} 個の枠を削除しました", { n: r.deleted.length }), 10000, { label: T("元に戻す"), run: () => restoreFormulas(r.formulas) });
  } catch (e) { toast(T("失敗: {m}", { m: e.message }), 4000); }
}

function select(id) {
  state.selected = id;
  drawBoxes();
  showDetail(state.formulas.find((f) => f.id === id));
}

function renderPreview(latex) { renderPreviewInto($("preview"), latex); }
function renderPreviewInto(el, latex) {
  el.textContent = "";
  const sel = state.formulas.find((f) => f.id === state.selected);
  if (el === $("preview") && sel && sel.kind === "text") { el.textContent = latex || ""; el.classList.add("plain"); return; }  // 文章は、数式として描かない
  el.classList.remove("plain");
  if (!latex) { el.textContent = T("（LaTeX なし）"); return; }
  if (window.katex) {
    const lines = latex.split("\n").filter((x) => x.trim());
    const src = lines.length > 1 ? `\\begin{gathered}${lines.join("\\\\")}\\end{gathered}` : latex;
    try { window.katex.render(src, el, { throwOnError: false, displayMode: true, trust: false, strict: "ignore" }); return; }
    catch (e) { /* 下へ */ }
  }
  el.textContent = T("（プレビューなし: KaTeX を読み込めない）");
}

async function showDetail(f) {
  if (!f) { $("detail").hidden = true; $("empty").hidden = false; return; }
  $("detail").hidden = false; $("empty").hidden = true;
  $("fid").textContent = f.id;
  $("recognize").hidden = f.kind === "text" && f.source !== "mixed";  // 文章の枠は、数式の再認識の対象外（混在の枠は、読み直せる）
  $("fstatus").textContent = f.status;
  $("fsource").textContent = `${f.source}${f.confidence != null ? T(" / 信頼度 {c}", { c: f.confidence.toFixed(2) }) : ""}${f.edited ? T(" / 修正済み") : ""}`;
  if (document.activeElement !== $("latex")) $("latex").value = f.latex || "";
  $("ocr").textContent = f.ocr_latex || T("（OCR 原文なし）");
  $("reason").textContent = f.reason ? T("理由: {r}", { r: f.reason }) : "";
  renderPreview($("latex").value);
  const alt = $("altbox"); alt.hidden = !f.review;
  if (f.review) {
    $("altwhy").textContent = ` ${REVIEW_WHY[f.review] || ""}`;
    $("alttext").value = f.alt_latex || "";
    $("usealt").disabled = !f.alt_latex;
    renderPreviewInto($("altpreview"), f.alt_latex);
  }
  try { const blob = await api("GET", `/api/formulas/${encodeURIComponent(f.id)}/crop.png`); $("crop").src = URL.createObjectURL(blob); }
  catch (e) { $("crop").removeAttribute("src"); }
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast(T("LaTeX をコピーしました")); }
  catch (e) { toast(T("コピーできませんでした（ブラウザの許可を確認）"), 4000); }
}

// ---- 設定: 数式の区切り記号（コピーと、PDFに埋め込む文章に反映する）
const settings = { delim: LS.get("plo-delim", "paren"), single: LS.get("plo-delim-single", "0") === "1" };
if (!["none", "paren", "dollar"].includes(settings.delim)) settings.delim = "paren";
const DELIM_TOKEN = /\\[()\[\]]/g;
function convertDelims(text, style) {  // 内部の \(…\)・\[…\] を、選んだ書き方に（delims.py と同じ規則）
  if (style === "paren" || !text) return text;
  if (style === "none") return text.replace(DELIM_TOKEN, "");
  return text.replace(DELIM_TOKEN, (m) => (m === "\\[" || m === "\\]" ? "$$" : "$"));
}
function wrapSingle(latex, style) {  // 数式だけの枠に、区切りを付ける（1行は行内、複数行は表示数式）
  const t = latex.replace(/^\n+|\n+$/g, "");
  if (style === "none" || !t.trim()) return latex;
  const multi = t.trim().includes("\n");
  if (style === "paren") return multi ? "\\[" + t + "\\]" : "\\(" + t + "\\)";
  return multi ? "$$" + t + "$$" : "$" + t + "$";
}
function outText(f, text) {  // コピーする文字列（文章の枠は、中の区切りを変える。数式だけの枠は、設定で区切りを付ける）
  if (f && f.kind === "text") return convertDelims(text, settings.delim);
  return settings.single ? wrapSingle(text, settings.delim) : text;
}

async function copyLatex() {
  const text = outText(state.formulas.find((x) => x.id === state.selected), $("latex").value);
  if (!text) { toast(T("コピーする LaTeX がありません")); return; }
  try { await navigator.clipboard.writeText(text); toast(T("LaTeX をコピーしました")); }
  catch (e) { $("latex").select(); document.execCommand("copy"); toast(T("LaTeX をコピーしました")); }
}

async function act(fn, okMsg) {
  try { await fn(); await refresh(); if (okMsg) toast(okMsg); } catch (e) { toast(T("失敗: {m}", { m: e.message }), 4000); }
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
    if (d.hitId) { select(d.hitId); const f = state.formulas.find((x) => x.id === d.hitId); if (f && f.latex) copyText(outText(f, f.latex)); }
    return;
  }
  const r = d.div.getBoundingClientRect(), s = pageScale(d.div);
  const x = ev.clientX - r.left, y = ev.clientY - r.top, s0 = d.start;
  if (d.rubber) d.rubber.remove();
  const vr = [Math.min(x, s0[0]) / s, Math.min(y, s0[1]) / s, Math.max(x, s0[0]) / s, Math.max(y, s0[1]) / s];
  if (vr[2] - vr[0] < 3 || vr[3] - vr[1] < 3) return;
  if (state.redrawFor) {
    const id = state.redrawFor; state.redrawFor = null;
    await act(() => api("POST", `/api/formulas/${encodeURIComponent(id)}`, { view_rect: vr }), T("枠を更新しました（必要なら再認識）"));
    return;
  }
  const recognize = state.doc.backend && state.doc.backend !== "none";
  if (recognize) toast(T("認識しています…"), 30000);
  await act(async () => {
    const f = await api("POST", "/api/formulas", { page: d.pno, view_rect: vr, recognize });
    state.selected = f.id;
  }, recognize ? T("枠を追加して認識しました") : T("枠を追加しました（LaTeX を入力してください）"));
});

// ============================================================ コレクション（Obsidian 風のツリー）
const ICON = {
  folder: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  file: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M7 3h7l5 5v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M14 3v5h5"/></svg>',
  star: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.9L12 16.9 6.8 19.7l1-5.9L3.5 9.7l5.9-.8z"/></svg>',
  more: '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><circle cx="5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="19" cy="12" r="1.7"/></svg>',
  ocr: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2M8 9h8M8 12h8M8 15h5"/></svg>',
  chev: '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>',
};
const STATE_KEY = { done: "準備済み", partial: "一部のみ処理済み", running: "処理中", none: "未処理", unknown: "確認中" };
const stateText = (k) => (STATE_KEY[k] ? T(STATE_KEY[k]) : "");
const tree = { data: null, json: "", expanded: new Set(), firstTime: false, filter: "", rows: [], els: new Map(), focusKey: null, fast: false,
  sel: new Set(), selectMode: false, nodes: new Map(), ocr: null, ocrClosed: null, prevOcr: null, ocrRunning: false };

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
  const add = (node0, depth, parentKey, anc = false) => {
    // 子がフォルダ1つだけの連なりは、1 行にまとめる（深い階層でも名前が読める）
    let node = node0, label = node0.name;
    while (node.type === "folder" && !node.vroot && node.children.length === 1 && node.children[0].type === "folder") {  // ビューアのフォルダの一番上は、まとめない（新しいフォルダを作れるように）
      node = node.children[0]; label += ` / ${node.name}`;
    }
    const isFolder = node.type === "folder";
    const key = isFolder ? node.key : (node.rowKey || node.id);
    const open = isFolder && (q ? true : tree.expanded.has(node.key));
    tree.rows.push({ key, node, depth, parentKey, isFolder, open });
    const row = makeRow(node, key, depth, isFolder, open, label, anc);
    tree.els.set(key, row);
    frag.append(row);
    if (isFolder && open) node.children.forEach((c) => add(c, depth + 1, key, anc || tree.sel.has(key)));
  };
  roots.forEach((r) => add(r, 0, null));
  el.textContent = "";
  if (!tree.rows.length) {
    const d = document.createElement("div"); d.className = "tree-empty";
    d.textContent = q ? T("一致するPDFがありません") : T("PDFがありません");
    el.append(d);
  } else {
    el.append(frag);
  }
  if (!tree.rows.some((r) => r.key === tree.focusKey)) tree.focusKey = (tree.rows.find((r) => r.key === t.current) || tree.rows[0] || {}).key || null;
  updateFocus(false);
  t.roots.forEach(annotate);
  const ready = t.roots.filter((r) => !r.virtual).reduce((a, r) => a + r._done, 0);  // ビューアのフォルダは、同じ PDF を数え直すので除く
  $("tree-foot").textContent = T("{f} 個のPDF・準備済み {r}", { f: t.files, r: ready }) + (t.hashing > 0 ? T("・状態を確認中 {n}", { n: t.hashing }) : "")
    + (t.truncated ? T("・一部のみ表示（上限）") : "");
  updateSelBar();
}

function rowBtn(icon, title) {
  const b = document.createElement("button");
  b.type = "button"; b.tabIndex = -1; b.title = title; b.setAttribute("aria-label", title); b.innerHTML = icon;
  return b;
}

function makeRow(node, key, depth, isFolder, open, label, anc = false) {
  const isCur = !isFolder && tree.data && !!node.id && node.id === tree.data.current;
  const picked = tree.selectMode && (tree.sel.has(key) || anc);
  const row = document.createElement("div");
  row.className = `trow ${isFolder ? "folder" : "file"}${isCur ? " current" : ""}${picked ? " picked" : ""}`;
  row.setAttribute("role", "treeitem");
  row.setAttribute("aria-level", String(depth + 1));
  if (isFolder) row.setAttribute("aria-expanded", String(open));
  if (isCur) row.setAttribute("aria-current", "true");
  if (tree.selectMode) row.setAttribute("aria-selected", String(picked));
  row.dataset.key = key; row.tabIndex = -1; row.style.setProperty("--d", String(depth));
  if (tree.selectMode) {
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.className = "rowchk"; cb.checked = picked; cb.disabled = anc; cb.tabIndex = -1; cb.setAttribute("aria-label", T("選択"));
    cb.addEventListener("click", (ev) => { ev.stopPropagation(); tree.focusKey = key; toggleSel(key); });
    row.append(cb);
  }
  const chev = document.createElement("span"); chev.className = `chev${open ? " open" : ""}`; if (isFolder) chev.innerHTML = ICON.chev;
  const ico = document.createElement("span"); ico.className = "ico"; ico.innerHTML = isFolder ? (node.vroot ? ICON.star : ICON.folder) : ICON.file;
  if (node.virtual) row.classList.add("virtual");
  if (node.missing) row.classList.add("missing");
  attachDrag(row, node, key);
  const name = document.createElement("span"); name.className = "name"; name.textContent = label || node.name;
  row.append(chev, ico, name);
  // 行の右の操作（マウスを乗せたとき）: フォルダ全体のOCR、⋯ メニュー
  const btns = document.createElement("span"); btns.className = "rowbtns";
  if (isFolder) {
    const ob = rowBtn(ICON.ocr, T("このフォルダ全体をOCR"));
    ob.addEventListener("click", (ev) => { ev.stopPropagation(); actOcr([key]); });
    btns.append(ob);
  }
  const mb = rowBtn(ICON.more, T("メニュー（名前を変更・移動・削除・OCR）"));
  mb.addEventListener("click", (ev) => { ev.stopPropagation(); tree.focusKey = key; const r = mb.getBoundingClientRect(); showMenu(r.left, r.bottom + 4, menuItemsFor(key), row); });
  btns.append(mb);
  row.append(btns);
  row.addEventListener("contextmenu", (ev) => { ev.preventDefault(); tree.focusKey = key; showMenu(ev.clientX, ev.clientY, menuItemsFor(key), row); });
  const meta = document.createElement("span"); meta.className = "meta";
  // クリック: 開く / 開閉。⌘・Ctrl＋クリックと、複数選択モードでは、選択を切り替える
  const onClick = (ev) => {
    tree.focusKey = key;
    if (ev.metaKey || ev.ctrlKey) { if (!tree.selectMode) setSelectMode(true); toggleSel(key); return; }
    if (tree.selectMode) { toggleSel(key); return; }
    if (isFolder) toggleFolder(node.key);
    else if (!node.id) toast(T("このPDFは見つかりません（移動・削除された可能性があります）。フォルダから外せます"), 4500);
    else { updateFocus(false); openDoc(node.id); }
  };
  if (isFolder) {
    meta.textContent = node._files ? `${node._done}/${node._files}` : "";
    row.title = `${label || node.name}\n${T("{f} 個のPDF（準備済み {d}）", { f: node._files, d: node._done })}`;
    row.append(meta);
    chev.addEventListener("click", (ev) => { ev.stopPropagation(); tree.focusKey = key; toggleFolder(node.key); });
    row.addEventListener("click", onClick);
    return row;
  }
  const dot = document.createElement("span"); dot.className = `dot ${node.state}`;
  if (node.formulas != null) meta.textContent = T("式 {n}", { n: node.formulas }) + (node.review ? T("・要確認 {n}", { n: node.review }) : "");
  if (node.review) meta.classList.add("has-review");
  row.append(meta, dot);
  row.title = `${node.name}\n${stateText(node.state)}${node.pages ? T("・{n} ページ", { n: node.pages }) : ""}${node.review ? T("・要確認 {n} 件", { n: node.review }) : ""}`;
  row.addEventListener("click", onClick);
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
      if (ev.key === " " && tree.selectMode) toggleSel(r.key);
      else if (r.isFolder) toggleFolder(r.node.key); else openDoc(r.node.id);
      break;
    case "F2": if (!isRootKey(r.key)) actRename(r.key); break;
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
$("tree-refresh").addEventListener("click", async () => { try { await loadTree(true); toast(T("一覧を更新しました"), 1200); } catch (e) { toast(T("失敗: {m}", { m: e.message }), 3000); } });

async function loadTree(force = false) {
  const t = await api("GET", force ? "/api/tree?refresh=1" : "/api/tree");
  if (tree.firstTime) { tree.firstTime = false; t.roots.forEach((r) => tree.expanded.add(r.key)); saveExpanded(); }
  const { ocr, ...rest } = t;  // OCR の進み具合は、ツリーの再描画と分けて扱う
  const j = JSON.stringify(rest);
  if (j !== tree.json) { tree.json = j; tree.data = t; indexTree(); renderTree(); } else { tree.data.ocr = ocr; }
  if (tree.prevOcr === "running" && ocr && ocr.state !== "running") {
    toast(ocr.state === "done" ? T("OCRが終わりました（成功 {ok}・失敗 {f}・スキップ {s}）", { ok: ocr.ok, f: ocr.failed, s: ocr.skipped }) : T("OCRを止めました"), 5000);
    if (state.doc) refresh();
  }
  tree.prevOcr = ocr ? ocr.state : null; tree.ocr = ocr; tree.ocrRunning = !!(ocr && ocr.state === "running");
  syncEngine(t);
  renderOcrBar(ocr);
  return t;
}

// ---- 認識エンジン（PP-FormulaNet / UniMERNet / Mathpix）の選択
function syncEngine(t) {
  const sel = $("engine"); if (!sel || !t.engines) return;
  const key = t.engines.map((e) => e.name).join(",") + "|" + window.ploI18n.lang;
  if (sel.dataset.key !== key) {
    sel.textContent = "";
    const none = document.createElement("option"); none.value = "none"; none.textContent = T("エンジン未選択"); sel.append(none);
    for (const e of t.engines) { const o = document.createElement("option"); o.value = e.name; o.textContent = T(e.label); sel.append(o); }
    sel.dataset.key = key;
  }
  sel.value = t.backend || "none";
  sel.disabled = !!tree.ocrRunning || !!t.readonly;
  if (state.doc) { state.doc.backend = t.backend; $("autorun").disabled = !t.backend || t.backend === "none"; }
}
$("engine").addEventListener("change", async (ev) => {
  const sel = ev.target, name = sel.value, prev = tree.data ? tree.data.backend || "none" : "none";
  if (name === "none") { sel.value = prev; return; }
  const ext = (tree.data.engines.find((e) => e.name === name) || {}).external;
  if (ext) {
    const r = await ask({ title: T("Mathpix に式の画像を送りますか？"), ok: T("送ることに同意して使う"),
      message: T("認識する式の画像が、外部サービス（Mathpix）に送信されます。教材や資料が第三者に渡ってよい場合だけ同意してください。同意しない場合は、手元で動く PP-FormulaNet / UniMERNet を選べます。") });
    if (!r) { sel.value = prev; return; }
  }
  try {
    await api("POST", "/api/engine", { name, confirm_external: !!ext });
    await loadTree(true);
    toast(T("認識エンジンを切り替えました"), 2000);
  } catch (e) { sel.value = prev; toast(T("切り替えられません: {m}", { m: e.message }), 5000); }
});

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
  }, !connOk ? 3000 : (tree.fast || tree.ocrRunning || (tree.data && tree.data.hashing > 0)) ? 2000 : 8000);
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

// ============================================================ ⋯ メニュー
let menuEl = null;
function closeMenu() {
  if (!menuEl) return;
  menuEl.remove(); menuEl = null;
  document.removeEventListener("pointerdown", onMenuOutside, true);
}
function onMenuOutside(ev) { if (menuEl && !menuEl.contains(ev.target)) closeMenu(); }

function showMenu(x, y, items, returnFocus) {
  closeMenu();
  if (!items.length) return;
  const m = document.createElement("div"); m.className = "menu"; m.setAttribute("role", "menu");
  for (const it of items) {
    if (it === "-") { m.append(document.createElement("hr")); continue; }
    const b = document.createElement("button"); b.type = "button"; b.setAttribute("role", "menuitem");
    b.textContent = it.label;
    if (it.danger) b.classList.add("danger");
    if (it.disabled) { b.disabled = true; if (it.title) b.title = it.title; }
    b.addEventListener("click", () => { closeMenu(); if (returnFocus && returnFocus.focus) returnFocus.focus(); it.run(); });
    m.append(b);
  }
  document.body.append(m); menuEl = m;
  const r = m.getBoundingClientRect();
  m.style.left = `${Math.max(8, Math.min(x, innerWidth - r.width - 8))}px`;
  m.style.top = `${Math.max(8, Math.min(y, innerHeight - r.height - 8))}px`;
  const btns = [...m.querySelectorAll("button:not(:disabled)")];
  if (btns[0]) btns[0].focus();
  m.addEventListener("keydown", (ev) => {
    const i = btns.indexOf(document.activeElement);
    if (ev.key === "ArrowDown") { btns[(i + 1) % btns.length].focus(); ev.preventDefault(); }
    else if (ev.key === "ArrowUp") { btns[(i - 1 + btns.length) % btns.length].focus(); ev.preventDefault(); }
    else if (ev.key === "Escape") { closeMenu(); if (returnFocus && returnFocus.focus) returnFocus.focus(); ev.preventDefault(); ev.stopPropagation(); }
    else if (ev.key === "Tab") closeMenu();
  });
  setTimeout(() => document.addEventListener("pointerdown", onMenuOutside, true), 0);
}
function menuAt(btn, items) { const r = btn.getBoundingClientRect(); showMenu(r.left, r.bottom + 4, items, btn); }

// ============================================================ ダイアログ（名前の入力・フォルダの選択・確認）
const dlg = $("dlg");
function ask(opts) {
  return new Promise((resolve) => {
    $("dlg-title").textContent = opts.title;
    const msg = $("dlg-msg"); msg.textContent = opts.message || ""; msg.hidden = !opts.message;
    const inp = $("dlg-input"); inp.hidden = !("value" in opts);
    if (!inp.hidden) { inp.value = opts.value; inp.placeholder = opts.placeholder || ""; }
    const list = $("dlg-list"); list.hidden = !opts.folders; list.textContent = "";
    const ok = $("dlg-ok"); ok.textContent = opts.ok || "OK";
    ok.classList.toggle("danger-solid", !!opts.danger); ok.classList.toggle("primary", !opts.danger);
    ok.disabled = !!opts.folders;
    let picked = null, done = false;
    const finish = (yes) => {
      if (done) return;
      done = true; dlg.close();
      resolve(yes ? { value: inp.hidden ? null : inp.value, key: picked } : null);
    };
    for (const f of opts.folders || []) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "pick"; b.setAttribute("role", "option"); b.style.paddingLeft = `${10 + f.depth * 14}px`; b.textContent = f.name;
      b.addEventListener("click", () => { picked = f.key; list.querySelectorAll(".pick").forEach((x) => x.classList.toggle("on", x === b)); ok.disabled = false; });
      b.addEventListener("dblclick", () => { picked = f.key; finish(true); });
      list.append(b);
    }
    $("dlg-form").onsubmit = (ev) => { ev.preventDefault(); if (!ok.disabled) finish(true); };
    $("dlg-cancel").onclick = () => finish(false);
    dlg.oncancel = (ev) => { ev.preventDefault(); finish(false); };
    dlg.showModal();
    if (!inp.hidden) { inp.focus(); inp.select(); } else if (opts.folders) { const f = list.querySelector(".pick"); if (f) f.focus(); } else ok.focus();
  });
}

// ============================================================ コレクションの操作（名前の変更・移動・ゴミ箱・新しいフォルダ・OCR）
const isRootKey = (k) => /^r\d+$/.test(k) || k === "files";
const noModels = () => !tree.data || !tree.data.backend || tree.data.backend === "none";

function indexTree() {
  tree.nodes = new Map();
  const walk = (n) => { tree.nodes.set(n.type === "folder" ? n.key : (n.rowKey || n.id), n); if (n.type === "folder") n.children.forEach(walk); };
  tree.data.roots.forEach(walk);
}
function fileIdsUnder(n, out = new Set()) {
  if (n.type === "file") out.add(n.id); else n.children.forEach((c) => fileIdsUnder(c, out));
  return out;
}
function descendantKeys(n, out = []) {
  if (n.type === "folder") n.children.forEach((c) => { out.push(c.type === "folder" ? c.key : c.id); descendantKeys(c, out); });
  return out;
}
function countFiles(keys) {
  const s = new Set();
  for (const k of keys) { const n = tree.nodes.get(k); if (n) fileIdsUnder(n, s); }
  return s.size;
}
function pickerFolders(excludeKeys) {
  const out = [];
  const walk = (n, depth) => {
    if (n.type !== "folder" || n.key === "files") return;
    if (excludeKeys.some((k) => n.key === k || n.key.startsWith(`${k}/`))) return;  // 自分自身と、その中へは移せない
    out.push({ key: n.key, name: n.name, depth });
    n.children.forEach((c) => walk(c, depth + 1));
  };
  tree.data.roots.forEach((r) => walk(r, 0));
  return out;
}
function defaultFolderKey() {  // 選んでいる行のフォルダ（ファイルなら、その親）。なければ最初の一番上のフォルダ
  const row = tree.rows.find((r) => r.key === tree.focusKey);
  if (row) { const k = row.isFolder ? row.node.key : row.parentKey; if (k && k !== "files") return k; }
  const first = tree.data && tree.data.roots.find((r) => r.key !== "files");
  return first ? first.key : null;
}

function menuItemsFor(key) {
  const n = tree.nodes.get(key);
  if (!n || !tree.data) return [];
  const ro = !!tree.data.readonly, noModel = noModels();
  const fsItem = (label, run, extra = {}) => ({ label, run, disabled: ro, title: ro ? T("読み取り専用で起動しているため、ファイルは動かせません") : "", ...extra });
  const ocr = { label: n.type === "folder" ? T("OCRを行う（フォルダ全体）") : T("OCRを行う"), run: () => actOcr([key]), disabled: noModel,
    title: noModel ? T("認識エンジンが未選択です（上の「エンジン」で選ぶか、--backend で起動）") : "" };
  if (n.virtual) {  // ビューアのフォルダの中の行: 実際のファイルは動かさない
    if (n.type === "file") {
      return [{ ...ocr, run: () => actOcr([key]), disabled: noModel || !n.id }, "-",
        { label: T("別のビューアのフォルダへ移動"), run: () => actVMove([n]) },
        { label: T("このフォルダから外す"), run: () => actVRemove([n]), danger: true }];
    }
    const items = [ocr, { label: T("新しいフォルダ"), run: () => actVNewFolder(n.key) }];
    if (!n.vroot) items.push("-", { label: T("名前を変更"), run: () => actVRename(n.key) },
      { label: T("別のビューアのフォルダへ移動"), run: () => actVMove([n]) },
      { label: T("このフォルダを削除（PDFは消えません）"), run: () => actVDelete(n.key), danger: true });
    return items;
  }
  const addV = { label: T("ビューアのフォルダに追加…"), run: () => actVAdd([key]) };
  if (n.type === "file") {
    return [ocr, addV, fsItem(T("名前を変更"), () => actRename(key)), fsItem(T("別の場所へ移動"), () => actMove([key])), "-",
      fsItem(T("ゴミ箱へ移動"), () => actTrash([key]), { danger: true })];
  }
  const items = [ocr, addV];
  if (key !== "files") items.push(fsItem(T("新しいフォルダ"), () => actNewFolder(key)));
  if (!isRootKey(key)) {
    items.push(fsItem(T("名前を変更"), () => actRename(key)), fsItem(T("別の場所へ移動"), () => actMove([key])), "-",
      fsItem(T("ゴミ箱へ移動"), () => actTrash([key]), { danger: true }));
  }
  return items;
}

// 操作のあと: 一覧を読み直し、動いた・消えた PDF が開いていたなら、表示も合わせる
async function syncAfterFs() {
  await loadTree(true);
  for (const k of [...tree.sel]) if (!tree.nodes.has(k)) tree.sel.delete(k);
  const cur = tree.data.current;
  if (cur !== state.docId) { await loadDoc(); if (cur) LS.set("plo-last", cur); revealCurrent(); }
  renderTree();
}
async function doFs(path, body) {
  try { const r = await api("POST", path, body); await syncAfterFs(); return r; }
  catch (e) { toast(T("失敗: {m}", { m: e.message }), 6000); return null; }
}

async function actRename(key) {
  const n = tree.nodes.get(key);
  if (!n) return;
  const isFile = n.type === "file", cur = isFile ? n.name.replace(/\.pdf$/i, "") : n.name;
  const r = await ask({ title: isFile ? T("PDFの名前を変更") : T("フォルダの名前を変更"), message: isFile ? T("拡張子（.pdf）は自動で付きます。") : "", value: cur, ok: T("変更") });
  if (!r) return;
  const v = r.value.trim();
  if (!v || v === cur) return;
  if (await doFs("/api/fs/rename", { target: key, name: v })) toast(T("名前を変更しました"));
}
async function actMove(keys) {
  if (!keys.length) return;
  const folders = pickerFolders(keys.filter((k) => (tree.nodes.get(k) || {}).type === "folder"));
  if (!folders.length) { toast(T("移動先のフォルダがありません")); return; }
  const r = await ask({ title: T("別の場所へ移動"), message: T("移動先のフォルダを選んでください。"), folders, ok: T("移動") });
  if (!r || !r.key) return;
  const res = await doFs("/api/fs/move", { targets: keys, dest: r.key });
  if (res) { tree.sel.clear(); renderTree(); toast(T("{n} 件を「{d}」へ移動しました", { n: res.count, d: (tree.nodes.get(r.key) || {}).name || "" })); }
}
async function actTrash(keys) {
  const nodes = keys.map((k) => tree.nodes.get(k)).filter(Boolean);
  if (!nodes.length) return;
  const files = countFiles(keys), one = nodes.length === 1 ? nodes[0] : null;
  const msg = one ? (one.type === "folder" ? T("フォルダ「{name}」を、中のPDF {f} 個ごとゴミ箱へ移動します。", { name: one.name, f: files }) : T("「{name}」をゴミ箱へ移動します。", { name: one.name }))
    : T("選んだ {n} 件（PDF {f} 個）をゴミ箱へ移動します。", { n: nodes.length, f: files });
  const r = await ask({ title: T("ゴミ箱へ移動"), message: T("{msg}\nゴミ箱から元に戻せます。認識した結果は残ります。", { msg }), ok: T("ゴミ箱へ移動"), danger: true });
  if (!r) return;
  const res = await doFs("/api/fs/trash", { targets: keys });
  if (res) { tree.sel.clear(); renderTree(); toast(T("{n} 件をゴミ箱へ移動しました", { n: res.trashed.length })); }
}
async function actNewFolder(parentKey) {
  if (!tree.data || !parentKey) { toast(T("フォルダを作る場所がありません")); return; }
  const parent = tree.nodes.get(parentKey);
  const r = await ask({ title: T("新しいフォルダ"), message: T("「{p}」の中に作ります。", { p: parent ? parent.name : "" }), value: "", placeholder: T("フォルダの名前"), ok: T("作成") });
  if (!r || !r.value.trim()) return;
  const res = await doFs("/api/fs/mkdir", { parent: parentKey, name: r.value });
  if (res) {
    tree.expanded.add(parentKey); saveExpanded();
    tree.focusKey = res.key; renderTree(); updateFocus(true);
    toast(T("フォルダ「{name}」を作りました", { name: res.name }));
  }
}
// ---- ビューア専用のフォルダ構造（PC のフォルダは、そのまま。実際のファイルは動かさない）
function vfsFolders(excludeKeys = []) {
  const out = [];
  const walk = (n, depth) => {
    if (n.type !== "folder") return;
    if (excludeKeys.some((k) => n.key === k || n.key.startsWith(`${k}/`))) return;
    out.push({ key: n.key, name: n.vroot ? T("ビューアのフォルダ") : n.name, depth });
    n.children.forEach((c) => walk(c, depth + 1));
  };
  tree.data.roots.filter((r) => r.vroot).forEach((r) => walk(r, 0));
  return out;
}
async function doV(op, body, okMsg) {
  try { const r = await api("POST", `/api/vfs/${op}`, body); await loadTree(true); if (okMsg) toast(okMsg(r)); return r; }
  catch (e) { toast(T("失敗: {m}", { m: e.message }), 5000); return null; }
}
async function actVNewFolder(parentKey) {
  const r = await ask({ title: T("新しいフォルダ"), message: T("ビューアのフォルダを作ります（実際のフォルダは作りません）。"), value: "", placeholder: T("フォルダの名前"), ok: T("作成") });
  if (!r || !r.value.trim()) return;
  const res = await doV("mkdir", { parent: parentKey || "v", name: r.value });
  if (res) { tree.expanded.add(parentKey || "v"); saveExpanded(); renderTree(); }
}
async function actVRename(key) {
  const n = tree.nodes.get(key); if (!n) return;
  const r = await ask({ title: T("フォルダの名前を変更"), value: n.name, ok: T("変更") });
  if (!r || !r.value.trim() || r.value.trim() === n.name) return;
  await doV("rename", { target: key, name: r.value }, () => T("名前を変更しました"));
}
async function actVDelete(key) {
  const n = tree.nodes.get(key); if (!n) return;
  const r = await ask({ title: T("このフォルダを削除（PDFは消えません）"), message: T("「{name}」と、中のフォルダ・登録を削除します。実際のPDFのファイルは、消えません。", { name: n.name }), ok: T("削除"), danger: true });
  if (!r) return;
  await doV("delete", { target: key }, () => T("削除しました"));
}
async function actVAdd(keys) {
  const targets = keys.map((k) => { const n = tree.nodes.get(k); return n && n.virtual ? (n.type === "file" ? n.id : n.key) : k; }).filter(Boolean);
  if (!targets.length) return;
  const folders = vfsFolders();
  const r = await ask({ title: T("ビューアのフォルダに追加"), message: T("追加先を選んでください（実際のファイルは動かしません。同じPDFを、複数のフォルダに入れられます）。"), folders, ok: T("追加") });
  if (!r || !r.key) return;
  const res = await doV("add", { folder: r.key, targets });
  if (res) { tree.expanded.add(r.key); saveExpanded(); renderTree(); tree.sel.clear(); toast(T("{n} 件を追加しました", { n: res.added })); }
}
async function actVMove(nodes) {
  const own = nodes.filter((n) => n.type === "folder").map((n) => n.key);
  const folders = vfsFolders(own);
  const r = await ask({ title: T("別のビューアのフォルダへ移動"), message: T("移動先のフォルダを選んでください。"), folders, ok: T("移動") });
  if (!r || !r.key) return;
  const items = nodes.filter((n) => n.type === "file").map((n) => ({ folder: n.folder_key, sha: n.sha }));
  await doV("move", { items, folders: own, dest: r.key }, () => T("移動しました"));
}
async function actVRemove(nodes) {
  const items = nodes.filter((n) => n.type === "file").map((n) => ({ folder: n.folder_key, sha: n.sha }));
  await doV("remove", { items }, (r) => T("{n} 件をフォルダから外しました", { n: r.removed }));
}
// ドラッグ＆ドロップ: 実際の PDF・フォルダを、ビューアのフォルダへ（登録）。ビューアのフォルダの中は、並べ替え（移動）
function attachDrag(row, node, key) {
  if (node.vroot) return;
  row.draggable = true;
  row.addEventListener("dragstart", (ev) => {
    const payload = node.virtual ? { kind: node.type === "file" ? "vitem" : "vfolder", node: { type: node.type, key: node.key, folder_key: node.folder_key, sha: node.sha } }
      : { kind: "real", key: node.type === "folder" ? node.key : node.id };
    ev.dataTransfer.setData("application/x-plo", JSON.stringify(payload)); ev.dataTransfer.effectAllowed = "copyMove";
  });
  if (node.type === "folder" && node.virtual) {
    row.addEventListener("dragover", (ev) => { if ([...ev.dataTransfer.types].includes("application/x-plo")) { ev.preventDefault(); row.classList.add("dropok"); } });
    row.addEventListener("dragleave", () => row.classList.remove("dropok"));
    row.addEventListener("drop", async (ev) => {
      ev.preventDefault(); row.classList.remove("dropok");
      let p; try { p = JSON.parse(ev.dataTransfer.getData("application/x-plo")); } catch (e) { return; }
      if (p.kind === "real") {
        const res = await doV("add", { folder: node.key, targets: [p.key] });
        if (res) { tree.expanded.add(node.key); saveExpanded(); renderTree(); toast(T("{n} 件を追加しました", { n: res.added })); }
      } else {
        const items = p.kind === "vitem" ? [{ folder: p.node.folder_key, sha: p.node.sha }] : [];
        await doV("move", { items, folders: p.kind === "vfolder" ? [p.node.key] : [], dest: node.key }, () => T("移動しました"));
      }
    });
  }
}

async function actOcr(keys) {
  keys = keys.map((k) => { const n = tree.nodes.get(k); return n && n.virtual && n.type === "file" ? n.id : k; }).filter(Boolean);  // ビューアのフォルダの PDF は、実際の PDF の ID で
  if (noModels()) { toast(T("認識エンジンが未選択です（上の「エンジン」で選ぶか、--backend で起動）"), 4000); return; }
  const n = countFiles(keys);
  if (!n) { toast(T("対象のPDFがありません")); return; }
  if (n > 1) {
    const r = await ask({ title: T("OCRを行う"), message: T("{n} 個のPDFの数式を検出・認識します。\nCPUでは1ページあたり約40秒かかります。途中でキャンセルでき、あとで続きから再開できます。", { n }), ok: T("始める") });
    if (!r) return;
  }
  try { tree.ocrClosed = null; renderOcrBar(await api("POST", "/api/ocr", { targets: keys })); tree.ocrRunning = true; toast(T("OCRを始めました")); }
  catch (e) { toast(T("失敗: {m}", { m: e.message }), 6000); }
}

// ---- 複数選択
function setSelectMode(on) {
  tree.selectMode = on;
  if (!on) tree.sel.clear();
  $("tree-select").setAttribute("aria-pressed", String(on));
  renderTree();
}
function toggleSel(key) {
  const n = tree.nodes.get(key);
  if (!n) return;
  if (tree.sel.has(key)) tree.sel.delete(key);
  else { tree.sel.add(key); if (n.type === "folder") for (const d of descendantKeys(n)) tree.sel.delete(d); }  // フォルダを選んだら、中身は個別に選ばない
  renderTree();
}
function updateSelBar() {
  const n = tree.sel.size;
  $("selbar").hidden = n === 0;
  if (n) $("selcount").textContent = T("{n} 件を選択中（PDF {f} 個）", { n, f: countFiles([...tree.sel]) });
}

// ---- OCR の進み具合
function renderOcrBar(o) {
  const bar = $("ocrbar");
  if (!o || o.state === "idle" || (o.state !== "running" && tree.ocrClosed === o.started)) { bar.hidden = true; return; }
  bar.hidden = false;
  const running = o.state === "running", bad = o.items.filter((i) => i.state === "error" || i.state === "skipped");
  $("ocrtext").textContent = running ? `OCR ${o.finished}/${o.total}${o.current ? `・${o.current}` : ""}`
    : o.state === "cancelled" ? T("OCRを中止しました（{a}/{b}）", { a: o.finished, b: o.total })
    : o.state === "error" ? T("OCRが止まりました: {r}", { r: (o.items.find((i) => i.state === "error") || {}).reason || "" })
    : T("OCR完了（成功 {ok}・失敗 {f}・スキップ {s}）", { ok: o.ok, f: o.failed, s: o.skipped });
  $("ocrtext").title = bad.map((i) => `${i.name}: ${i.reason || i.state}`).join("\n");
  $("ocrprog").style.width = `${Math.round(((o.finished + (running ? 0.5 : 0)) / Math.max(1, o.total)) * 100)}%`;
  $("ocr-cancel").hidden = !running; $("ocr-close").hidden = running;
}

// ============================================================ PDF の切り替え
let lastVersion = -1;

async function openDoc(id, quiet = false) {
  if (id === state.docId) { if (innerWidth <= 900) setSidebar(false); return; }
  try { await api("POST", "/api/open", { id }); }
  catch (e) { if (!quiet) toast(T("開けませんでした: {m}", { m: e.message }), 5000); return; }
  LS.set("plo-last", id);
  await loadDoc();
  try { await loadTree(); } catch (e) { /* 次の更新で反映 */ }
  revealCurrent();
  focusDeck();
  if (innerWidth <= 900) setSidebar(false);
}

// 開いたら、ページ送りのキー（←→・Space）がすぐ効くよう、フォーカスをページ側へ移す
function focusDeck() { pagesEl.tabIndex = -1; pagesEl.focus({ preventScroll: true }); }

async function loadDoc(knownEmpty = false) {
  let doc = null;
  if (!knownEmpty) { try { doc = await api("GET", "/api/doc"); } catch (e) { doc = null; } }
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
  $("textrun").disabled = !doc;
  $("doc-menu").disabled = !doc;
  $("autorun").classList.remove("attention");
  if (!doc) { imgObserver.disconnect(); pagesEl.textContent = ""; slots = []; updatePageUI(); return; }
  buildDeck();
  const saved = parseInt(LS.get(`plo-page:${doc.id}`, "0"), 10) || 0;
  state.cur = Math.max(0, Math.min(saved, slots.length - 1));
  requestAnimationFrame(() => { goto(state.cur, false); updatePageUI(); });
  await refresh();
  updateAutoStatus();  // 「未処理」の表示と、「このページを認識」の強調を、開いてすぐ反映する
  loadNotes();
}

// ============================================================ メモ（PDFの外に保存。PDF全体と、ページごと）
const notes = { data: { doc: "", pages: {} }, scope: "doc", timer: 0, dirty: false };
function noteKey() { return notes.scope === "doc" ? null : state.cur; }
function showNote() {
  const ta = $("notetext");
  ta.value = notes.scope === "doc" ? (notes.data.doc || "") : (notes.data.pages[String(state.cur)] || "");
  $("note-doc").setAttribute("aria-pressed", String(notes.scope === "doc"));
  $("note-page").setAttribute("aria-pressed", String(notes.scope === "page"));
  $("note-pageno").textContent = `(${state.cur + 1})`;
  ta.disabled = !state.doc;
}
function syncNotePage() {
  $("note-pageno").textContent = `(${state.cur + 1})`;
  if (notes.scope === "page") { if (notes.dirty) saveNote().then(showNote); else showNote(); }  // 書きかけのメモは、元のページに保存してから切り替える
}
async function loadNotes() {
  notes.data = { doc: "", pages: {} }; notes.dirty = false;
  if (state.doc) { try { notes.data = await api("GET", "/api/notes"); } catch (e) { /* 次回 */ } }
  showNote(); $("notestat").textContent = "";
}
async function saveNote() {
  clearTimeout(notes.timer);
  if (!notes.dirty || !state.doc) return;
  const tg = notes.target || { scope: notes.scope, page: state.cur };
  const scope = tg.scope, page = tg.page, text = notes.draft;
  notes.dirty = false;
  try {
    notes.data = await api("POST", "/api/notes", { scope, page: scope === "page" ? page : undefined, text });
    $("notestat").textContent = T("メモを保存しました");
  } catch (e) { notes.dirty = true; $("notestat").textContent = T("メモを保存できません: {m}", { m: e.message }); }
}
$("notetext").addEventListener("input", () => {
  notes.dirty = true; notes.target = { scope: notes.scope, page: state.cur }; notes.draft = $("notetext").value;
  $("notestat").textContent = T("入力中…");
  if (notes.scope === "doc") notes.data.doc = $("notetext").value; else notes.data.pages[String(state.cur)] = $("notetext").value;
  clearTimeout(notes.timer); notes.timer = setTimeout(saveNote, 800);
});
$("notetext").addEventListener("blur", saveNote);
for (const [id, scope] of [["note-doc", "doc"], ["note-page", "page"]]) {
  $(id).addEventListener("click", async () => { await saveNote(); notes.scope = scope; showNote(); });
}
window.addEventListener("beforeunload", () => { if (notes.dirty) saveNote(); });

// ============================================================ 画面の操作
function wire() {
  $("latex").addEventListener("input", () => renderPreview($("latex").value));
  $("copy").onclick = copyLatex;
  $("save").onclick = () => act(() => api("POST", `/api/formulas/${encodeURIComponent(state.selected)}`, { latex: $("latex").value }), T("修正を保存しました"));
  $("confirm").onclick = () => act(() => api("POST", `/api/formulas/${encodeURIComponent(state.selected)}`, { latex: $("latex").value, status: "confirmed" }), T("確定しました"));
  $("exclude").onclick = () => act(() => api("POST", `/api/formulas/${encodeURIComponent(state.selected)}`, { status: "excluded", reason: "user" }), T("除外しました"));
  $("recognize").onclick = () => act(() => api("POST", `/api/formulas/${encodeURIComponent(state.selected)}/recognize`), T("再認識しました"));
  $("redraw").onclick = () => { state.redrawFor = state.selected; toast(T("ページ上で新しい枠をドラッグしてください")); };
  $("delete").onclick = () => deleteFormula(state.selected);  // 確認はせず、「元に戻す」を出す
  // 「このページを認識」: いま表示しているページだけを検出・認識する（PDF全体は、⋯ の「OCRを行う」）
  $("autorun").onclick = () => {
    if (!state.doc) return;
    const n = state.cur;
    act(() => api("POST", "/api/auto", { pages: [n], force: true }), T("{n} ページ目の認識を始めました", { n: n + 1 }));
  };
  // 「このページの文章を認識」: 日本語などの文章を読む（数式の枠は読まない）。初回はモデルの取得・読み込みで時間がかかる
  $("textrun").onclick = () => {
    if (!state.doc) return;
    const n = state.cur;
    act(() => api("POST", "/api/text", { pages: [n], force: true }), T("{n} ページ目の文章の認識を始めました", { n: n + 1 }));
  };
  $("reviewbtn").onclick = () => nextReview(1);
  $("usealt").onclick = () => {
    const f = state.formulas.find((x) => x.id === state.selected);
    if (f && f.alt_latex) act(() => api("POST", `/api/formulas/${encodeURIComponent(f.id)}`, { latex: f.alt_latex }), T("もう一方の読み取りを採用しました"));
  };
  const renameTitle = () => { if (state.docId) actRename(state.docId); };
  $("docname").addEventListener("click", renameTitle);
  $("docname").addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === "F2") { ev.preventDefault(); renameTitle(); } });
  $("doc-menu").onclick = () => menuAt($("doc-menu"), state.docId ? [...menuItemsFor(state.docId), "-",
    { label: T("このページの文章の枠をすべて削除"), run: () => deletePageFormulas("text"), danger: true },
    { label: T("このページの枠をすべて削除"), run: () => deletePageFormulas("all"), danger: true }] : []);
  $("tree-newfolder").onclick = () => actNewFolder(defaultFolderKey());
  $("tree-select").onclick = () => setSelectMode(!tree.selectMode);
  $("sel-ocr").onclick = () => actOcr([...tree.sel]);
  $("sel-vadd").onclick = () => actVAdd([...tree.sel]);
  $("sel-move").onclick = () => actMove([...tree.sel].filter((k) => !(tree.nodes.get(k) || {}).virtual));
  $("sel-trash").onclick = () => actTrash([...tree.sel]);
  $("sel-clear").onclick = () => { tree.sel.clear(); renderTree(); };
  $("ocr-cancel").onclick = async () => { try { renderOcrBar(await api("POST", "/api/ocr/cancel")); } catch (e) { toast(T("失敗: {m}", { m: e.message }), 4000); } };
  $("ocr-close").onclick = () => { if (tree.ocr) tree.ocrClosed = tree.ocr.started; renderOcrBar(tree.ocr); };
  $("export").onclick = async () => {
    if (!state.doc) { toast(T("PDFを選んでください。")); return; }
    $("export").disabled = true; toast(T("派生PDFを作成・検証しています…"), 60000);
    try {
      const r = await api("POST", "/api/export", { mode: $("mode").value, only_confirmed: $("onlyconfirmed").checked, delim: settings.delim });
      const ex = r.excluded.filter((x) => x.reason && x.reason !== "user").map((x) => `${x.id}: ${x.reason}`);
      if (r.ok) toast(T("保存しました（埋め込み {e} 件、除外 {x} 件）\n{out}{extra}", { e: r.counts.embedded, x: r.counts.excluded, out: r.output, extra: ex.length ? "\n" + ex.join("\n") : "" }), 10000);
      else toast(T("保存しませんでした（終了コード {c}）\n{errs}{checks}", { c: r.exit_code, errs: r.errors.join("\n"), checks: r.failed_checks.length ? T("\n検証不合格: {c}", { c: r.failed_checks.join(", ") }) : "" }), 10000);
    } catch (e) { toast(T("失敗: {m}", { m: e.message }), 6000); }
    finally { $("export").disabled = false; }
  };

  // キーボード: ページ送りは SpeakerDeck と同じ（←→・Space・PageUp/Down・Home/End）
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") { state.redrawFor = null; return; }
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
    const t = ev.target;
    if (t.closest && t.closest("input, textarea, select, [contenteditable], .menu, dialog")) return;
    if (t.closest && t.closest("#tree") && ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "Enter", " "].includes(ev.key)) return;
    const fitPage = state.fit === "page" && !zoomed();
    let handled = true;
    switch (ev.key) {
      case "ArrowRight": case "PageDown": case "j": goto(state.cur + 1); break;
      case "ArrowLeft": case "PageUp": case "k": goto(state.cur - 1); break;
      case " ": goto(state.cur + (ev.shiftKey ? -1 : 1)); break;
      case "ArrowDown": if (fitPage) goto(state.cur + 1); else handled = false; break;
      case "ArrowUp": if (fitPage) goto(state.cur - 1); else handled = false; break;
      case "Home": goto(0); break;
      case "End": goto(slots.length - 1); break;
      case "+": case "=": setZoom(state.zoom * 1.25); break;
      case "-": case "_": setZoom(state.zoom / 1.25); break;
      case "0": setZoom(1); break;
      case "f": setFit(state.fit === "page" ? "width" : "page"); toast(state.fit === "page" ? T("表示: ページ全体") : T("表示: 幅に合わせる"), 1100); break;
      case "b": setSidebar(!$("layout").classList.contains("sb-open"), true); break;
      case "/": $("tree-filter").focus(); $("tree-filter").select(); break;
      case "c": if (state.selected) copyLatex(); else handled = false; break;
      case "Delete": case "Backspace": if (state.selected) deleteFormula(state.selected); else handled = false; break;
      case "n": case "N": nextReview(ev.shiftKey || ev.key === "N" ? -1 : 1); break;
      default: handled = false;
    }
    if (handled) ev.preventDefault();
  });
}

// 認識の進み具合を 1 回だけ読んで画面に反映し、次に読むまでの間隔（ミリ秒）を返す
async function updateAutoStatus() {
  let delay = 5000;
  try {
    if (state.doc) {
      const a = await api("GET", "/api/auto");
      const el = $("autostat");
      tree.fast = a.state === "running" || a.state === "preparing" || (a.text && a.text.state === "running");
      if (a.text && a.text.state === "running") {
        el.textContent = T("文章を認識中… {p}/{n} ページ", { p: a.text.page || 0, n: a.text.pages || 0 });
      } else if (a.text && a.text.state === "error") {
        el.textContent = T("文章の認識に失敗: {m}", { m: a.text.error });
      } else if (a.state === "running") {
        el.textContent = a.phase === "detect" ? T("数式を検出中… {p}/{n} ページ", { p: a.page, n: a.pages }) : T("認識中… {r}/{t}", { r: a.recognized, t: a.total || 0 });
      } else if (a.state === "preparing") {
        el.textContent = T("事前処理中（終わるまで表示のみ。結果は順に反映）");
      } else if (a.state === "done") {
        el.textContent = T("認識済み");
      } else if (a.state === "error") {
        el.textContent = T("自動認識に失敗: {m}", { m: a.error });
      } else {
        el.textContent = state.formulas.length === 0 && state.doc.backend !== "none" ? T("未処理") : "";
      }
      $("autorun").classList.toggle("attention", a.state === "idle" && state.formulas.length === 0 && state.doc.backend !== "none");
      if (a.version !== lastVersion) { lastVersion = a.version; await refresh(); }
      delay = tree.fast ? 1500 : 5000;
    }
  } catch (e) { /* 次回 */ }
  return delay;
}
async function pollAuto() { setTimeout(pollAuto, await updateAutoStatus()); }

async function boot() {
  if (!token) { document.body.textContent = T("トークンがありません。起動時に表示された URL を開いてください。"); return; }
  wire();
  let t;
  // 画面に戻ってきたら、すぐ最新の状態にする（見ていない間は、負荷を減らすため更新を止めている）
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    loadTree().catch(() => {});
    if (state.doc) refresh().catch(() => {});
  });
  try { t = await loadTree(); } catch (e) { toast(T("読み込みに失敗: {m}", { m: e.message }), 6000); return; }
  await loadDoc(!t.current);  // 起動時に開かれている PDF があれば表示する
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


// 言語を切り替えたとき: 動的に作った表示を作り直す
document.addEventListener("plo-lang", () => {
  try {
    if (tree.data) { syncEngine(tree.data); renderTree(); renderOcrBar(tree.ocr); }
    if (state.doc) { $("stats").textContent = ""; refresh(); updateAutoStatus(); document.querySelectorAll("#pages img").forEach((im, i) => { im.alt = T("ページ {n}", { n: i + 1 }); }); }
  } catch (e) { /* 次の更新で反映 */ }
});


// 設定の画面
function renderSettings() {
  for (const r of document.querySelectorAll('#settings input[name="delim"]')) r.checked = r.value === settings.delim;
  $("set-single").checked = settings.single;
  $("set-single").disabled = settings.delim === "none";
  const mixed = "(1) \\(x^{2}+1\\) \\[a=b\\]";
  $("set-sample").textContent = convertDelims(mixed, settings.delim) + "\n" + wrapSingle("x^{2}+1", settings.single ? settings.delim : "none");
}
$("settings-btn").addEventListener("click", () => { renderSettings(); $("settings").showModal(); });
for (const r of document.querySelectorAll('#settings input[name="delim"]')) {
  r.addEventListener("change", () => { settings.delim = r.value; LS.set("plo-delim", r.value); renderSettings(); });
}
$("set-single").addEventListener("change", () => { settings.single = $("set-single").checked; LS.set("plo-delim-single", settings.single ? "1" : "0"); renderSettings(); });


// ---- PDF・フォルダを開く（macOS の選択画面を、ビューアの中から出す。.app の起動時の選択画面は、これに統合した）
async function pickOpen(kind) {
  try {
    toast(T("選択画面を開いています…"), 4000);
    const r = await api("POST", "/api/pick", { kind });
    if (r.cancelled) { toast(T("キャンセルしました"), 1500); return; }
    await loadTree(true);
    if (kind === "folder") { toast(T("フォルダを追加しました"), 2000); const k = r.root; if (k) { tree.expanded.add(k); saveExpanded(); renderTree(); } }
    else { await loadDoc(); toast(T("PDFを開きました"), 1500); }
  } catch (e) { toast(T("開けませんでした: {m}", { m: e.message }), 5000); }
}
$("tree-open").addEventListener("click", () => {
  const r = $("tree-open").getBoundingClientRect();
  showMenu(r.left, r.bottom + 4, [{ label: T("PDFを開く…"), run: () => pickOpen("pdf") }, { label: T("フォルダを追加…"), run: () => pickOpen("folder") }], $("tree-open"));
});
$("empty-open-pdf").addEventListener("click", () => pickOpen("pdf"));
$("empty-open-folder").addEventListener("click", () => pickOpen("folder"));
