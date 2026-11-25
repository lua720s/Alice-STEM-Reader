// Snip のダッシュボード（Mathpix Snip のアプリの画面に相当）。
// 上: 検索・種類・出所の絞り込み、集計。左: Snip の一覧（複数選択して書き出せる）。右: 選んだ Snip（OCR・データ・元の画像のタブ）。
// キー操作は keys.js の dashboard の割り当て（←→ 前後、⇧←→ 最古・最新、↑↓ 形式、⇧↑↓ タブ、Enter 編集、⇧Enter 保存、Delete 削除）。
// app.js の関数・状態（api, T, $, snipUI, settings など）を使う。OCR の結果は textContent にだけ入れる。
"use strict";

const dash = { cur: null, tab: "ocr", row: 0, kind: "", src: "", q: "", picked: new Set(), editing: false, noteT: 0, imgUrl: null };
const TABS = ["ocr", "data", "orig"];

function showDashboard(on) {
  $("dash").hidden = !on;
  $("layout").hidden = on;
  $("dash-open").setAttribute("aria-pressed", String(on));
  document.body.classList.toggle("dash-mode", on);
  if (on) { renderDash(); if (!dash.editing) $("dash-list").focus({ preventScroll: true }); }
  else if (typeof focusDeck === "function" && state.doc) focusDeck();
}
window.showDashboard = showDashboard;

// ---- 形式（コピーできる書き方の一覧）
function formatRows(rec, base) {
  const t = base ?? (rec.latex || "");
  const rows = [{ label: T("設定の形式"), text: PF.copyText(rec.kind, t, prefs.format) }];  // 先頭は、設定（出力の形式）どおり
  const F = prefs.format;
  if (rec.kind === "math") {
    rows.push({ label: "LaTeX", text: PF.formatMath(t, { ...F, multiline: "lines" }, "raw") });
    rows.push({ label: "LaTeX  \\( … \\)", text: PF.formatMath(t, { ...F, inline: "paren", display: "bracket" }, "auto") });
    rows.push({ label: "LaTeX  \\[ … \\]", text: PF.formatMath(t, { ...F, display: "bracket" }, "display") });
    rows.push({ label: "LaTeX  aligned", text: PF.formatMath(t, { ...F, multiline: "aligned", display: "bracket" }, "display") });
    rows.push({ label: "Markdown  $ … $", text: PF.formatMath(t, { ...F, inline: "dollar", display: "dollars" }, "auto") });
    rows.push({ label: "Markdown  $$ … $$", text: PF.formatMath(t, { ...F, display: "dollars" }, "display") });
  } else {
    rows.push({ label: `${T("文章＋数式")}  \\( … \\)`, text: t });
    rows.push({ label: "Markdown  $ … $", text: convertDelims(t, "dollar") });
    rows.push({ label: T("区切りなし"), text: convertDelims(t, "none") });
  }
  if (rec.text_layer) rows.push({ label: T("文字層（資料に埋め込まれた文字）"), text: rec.text_layer });
  if (rec.alt_latex) rows.push({ label: T("もう一方のモデルの読み"), text: rec.alt_latex });
  const seen = new Set();  // 同じ中身の行は 1 つに
  return rows.filter((r) => r.text && !seen.has(r.text) && seen.add(r.text));
}

function renderMixedInto(el, text, kind) {
  el.textContent = "";
  el.classList.toggle("plain", kind !== "math");
  if (!text) { el.textContent = T("（内容なし）"); el.classList.add("plain"); return; }
  if (!window.katex) { el.textContent = text; el.classList.add("plain"); return; }
  const opt = { throwOnError: false, trust: false, strict: "ignore" };
  if (kind === "math") {
    const lines = text.split("\n").filter((x) => x.trim());
    try { window.katex.render(lines.length > 1 ? `\\begin{gathered}${lines.join("\\\\")}\\end{gathered}` : text, el, { ...opt, displayMode: true }); }
    catch (e) { el.textContent = text; }
    return;
  }
  const re = /\\\(([\s\S]+?)\\\)|\\\[([\s\S]+?)\\\]|\$\$([\s\S]+?)\$\$|\$([^$\n]+?)\$/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) el.append(document.createTextNode(text.slice(last, m.index)));
    const span = document.createElement("span");
    try { window.katex.render(m[1] || m[2] || m[3] || m[4], span, { ...opt, displayMode: m[2] !== undefined || m[3] !== undefined }); }
    catch (e) { span.textContent = m[0]; }
    el.append(span); last = re.lastIndex;
  }
  if (last < text.length) el.append(document.createTextNode(text.slice(last)));
}

// ---- 絞り込み・集計
function srcKey(rec) {
  const s = rec.source || {};
  return s.type === "document" ? `doc:${s.name || ""}` : (s.type || "image");
}
function filtered() {
  const q = dash.q.trim().toLowerCase();
  return snipUI.items.filter((r) => (!dash.kind || r.kind === dash.kind) && (!dash.src || srcKey(r) === dash.src)
    && (!q || `${r.latex || ""}\n${r.note || ""}\n${snipSourceLabel(r.source)}`.toLowerCase().includes(q)));
}
function renderKpis() {
  const items = snipUI.items;
  const week = Date.now() - 7 * 864e5;
  const docs = new Set(items.filter((r) => r.source && r.source.type === "document").map((r) => r.source.name));
  const tiles = [
    [T("Snip の数"), items.length],
    [T("この7日間"), items.filter((r) => Date.parse(r.created) >= week).length],
    [T("数式"), items.filter((r) => r.kind === "math").length],
    [T("文章＋数式"), items.filter((r) => r.kind === "mixed").length],
    [T("文章"), items.filter((r) => r.kind === "text").length],
    [T("出所の資料"), docs.size],
    [T("修正した"), items.filter((r) => r.edited).length],
    [T("要確認"), items.filter((r) => r.review).length],
  ];
  const box = $("dash-kpis"); box.textContent = "";
  for (const [label, n] of tiles) {
    const d = document.createElement("div"); d.className = "kpi"; d.setAttribute("role", "listitem");
    const v = document.createElement("b"); v.textContent = String(n);
    const l = document.createElement("span"); l.textContent = label;
    d.append(v, l); box.append(d);
  }
}
function renderSrcOptions() {
  const sel = $("dash-src"); const cur = dash.src; sel.textContent = "";
  const opts = [["", T("すべての出所")], ["screen", T("画面")], ["paste", T("貼り付けた画像")], ["drop", T("画像")], ["image", T("画像（ファイル）")], ["editor", T("エディタ")]];
  const docs = [...new Set(snipUI.items.filter((r) => r.source && r.source.type === "document").map((r) => r.source.name))].sort();
  for (const d of docs) opts.push([`doc:${d}`, d]);
  for (const [v, l] of opts) { const o = document.createElement("option"); o.value = v; o.textContent = l; sel.append(o); }
  sel.value = opts.some((o) => o[0] === cur) ? cur : "";
  dash.src = sel.value;
}

// ---- 一覧
function renderList(list) {
  const box = $("dash-list"); box.textContent = "";
  let lastDay = "";
  for (const rec of list) {
    const day = (rec.created || "").slice(0, 10);
    if (day !== lastDay) { const h = document.createElement("div"); h.className = "dash-day"; h.textContent = day; box.append(h); lastDay = day; }
    const card = document.createElement("div"); card.className = "dcard"; card.setAttribute("role", "option");
    card.setAttribute("aria-selected", String(rec.id === dash.cur)); card.dataset.id = rec.id; card.tabIndex = -1;
    if (rec.id === dash.cur) card.classList.add("current");
    const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = dash.picked.has(rec.id); cb.setAttribute("aria-label", T("書き出しに含める"));
    cb.addEventListener("click", (ev) => { ev.stopPropagation(); cb.checked ? dash.picked.add(rec.id) : dash.picked.delete(rec.id); updateCount(list); });
    const th = document.createElement("div"); th.className = "dthumb";
    if (rec.has_image !== false) {
      const im = document.createElement("img"); im.alt = ""; im.loading = "lazy";
      api("GET", `/api/snips/${encodeURIComponent(rec.id)}.png`).then((b) => { im.src = URL.createObjectURL(b); }).catch(() => im.remove());
      th.append(im);
    } else th.textContent = rec.source && rec.source.format === "markdown" ? "M↓" : "TeX";
    const body = document.createElement("div"); body.className = "dbody";
    const line = document.createElement("div"); line.className = "dline"; line.textContent = (rec.note || rec.latex || T("（内容なし）")).split("\n")[0].slice(0, 140);
    const meta = document.createElement("div"); meta.className = "dmeta";
    meta.textContent = `${T(SNIP_KIND[rec.kind] || "Snip")} · ${snipSourceLabel(rec.source)} · ${(rec.created || "").slice(11, 16)}${rec.review ? " · " + T("要確認") : ""}`;
    body.append(line, meta);
    card.append(cb, th, body);
    card.addEventListener("click", () => selectSnip(rec.id));
    box.append(card);
  }
  if (!list.length) { const p = document.createElement("p"); p.className = "muted small"; p.textContent = snipUI.items.length ? T("一致する Snip がありません") : T("まだ Snip はありません。"); box.append(p); }
  updateCount(list);
}
function updateCount(list) {
  $("dash-count").textContent = dash.picked.size ? T("{n} 件中 {s} 件を選択", { n: list.length, s: dash.picked.size }) : T("{n} 件", { n: list.length });
  $("dash-selall").textContent = dash.picked.size ? T("選択を解除") : T("すべて選択");
}

// ---- 選んだ Snip
function current() { return snipUI.items.find((r) => r.id === dash.cur) || null; }
function selectSnip(id, keepRow = false) {
  if (dash.editing) cancelEdit();
  dash.cur = id; if (!keepRow) dash.row = 0;
  renderDash();
  const el = $("dash-list").querySelector(`[data-id="${CSS.escape(id)}"]`);
  if (el) el.scrollIntoView({ block: "nearest" });
}
function setTab(t) {
  dash.tab = t;
  for (const x of TABS) {
    $(`tab-${x}`).setAttribute("aria-selected", String(x === t));
    $(`pane-${x}`).hidden = x !== t;
  }
  if (t === "orig") loadBigImage();
}
async function loadBigImage() {
  const rec = current(); const img = $("dash-img-big");
  img.removeAttribute("src");
  if (dash.imgUrl) { URL.revokeObjectURL(dash.imgUrl); dash.imgUrl = null; }
  if (!rec || rec.has_image === false) { img.alt = T("画像なし（エディタで書いた Snip）"); return; }
  try { const b = await api("GET", `/api/snips/${encodeURIComponent(rec.id)}.png`); dash.imgUrl = URL.createObjectURL(b); img.src = dash.imgUrl; img.alt = T("切り取った画像"); }
  catch (e) { img.alt = T("画像がありません"); }
}
function renderDetail(list) {
  const rec = current();
  $("dash-main").hidden = !rec; $("dash-empty").hidden = !!rec;
  if (!rec) { $("dash-empty").textContent = snipUI.items.length ? T("左の一覧から Snip を選んでください。") : T("画面を Snip するか、画像を貼り付けてください。"); return; }
  const i = list.findIndex((r) => r.id === rec.id);
  $("dash-pos").textContent = i >= 0 ? `${i + 1} / ${list.length}` : "";
  $("dash-meta").textContent = `${T(SNIP_KIND[rec.kind] || "Snip")} · ${snipSourceLabel(rec.source)} · ${(rec.created || "").replace("T", " ").slice(0, 16)}`;
  if (!dash.editing) renderMixedInto($("dash-preview"), rec.latex || "", rec.kind);
  const rows = formatRows(rec);
  dash.row = Math.max(0, Math.min(dash.row, rows.length - 1));
  const box = $("dash-rows"); box.textContent = "";
  rows.forEach((r, k) => {
    const row = document.createElement("div"); row.className = "frow"; row.setAttribute("role", "option"); row.setAttribute("aria-selected", String(k === dash.row));
    if (k === dash.row) row.classList.add("sel");
    const lab = document.createElement("span"); lab.className = "flabel"; lab.textContent = r.label;
    const val = document.createElement("code"); val.className = "fval"; val.textContent = r.text;
    const btn = document.createElement("button"); btn.type = "button"; btn.className = "small-btn"; btn.textContent = T("コピー");
    btn.addEventListener("click", (ev) => { ev.stopPropagation(); dash.row = k; copyRow(); });
    row.addEventListener("click", () => { dash.row = k; copyRow(); });  // Mathpix と同じく、行をクリックでコピー
    row.append(lab, val, btn); box.append(row);
  });
  if (document.activeElement !== $("dash-note")) $("dash-note").value = rec.note || "";
  $("dash-note").placeholder = T("例: この式の意味・使う場面（TSV に書き出すと、カードの表面になります）");
  // データ
  const prov = $("dash-prov"); prov.textContent = "";
  const s = rec.source || {};
  const pairs = [
    [T("作成日時"), (rec.created || "").replace("T", " ")], [T("更新日時"), (rec.updated || "").replace("T", " ") || "—"],
    [T("出所"), snipSourceLabel(s)], [T("範囲（pt）"), s.rect ? s.rect.join(", ") : "—"],
    [T("資料のハッシュ（SHA-256）"), s.sha256 || "—"], [T("認識エンジン"), rec.engine ? `${rec.engine.name} ${rec.engine.version || ""}`.trim() : "—"],
    [T("読み方"), rec.mode || "—"], [T("修正"), rec.edited ? T("あり") : T("なし")], [T("要確認"), rec.review || "—"],
  ];
  for (const [k, v] of pairs) { const dt = document.createElement("dt"); dt.textContent = k; const dd = document.createElement("dd"); dd.textContent = v; prov.append(dt, dd); }
  $("dash-json").textContent = JSON.stringify(rec, null, 2);
  setTab(dash.tab);
}
function renderDash() {
  if ($("dash").hidden) return;
  renderKpis(); renderSrcOptions();
  const list = filtered();
  if (!list.some((r) => r.id === dash.cur)) dash.cur = list.length ? list[0].id : null;
  renderList(list); renderDetail(list);
}
window.renderDash = renderDash;

async function copyRow() {
  const rec = current(); if (!rec) return;
  const rows = formatRows(rec); const r = rows[dash.row]; if (!r) return;
  try { await navigator.clipboard.writeText(r.text); toast(T("コピーしました: {f}", { f: r.label }), 1400); }
  catch (e) { toast(T("コピーできませんでした（ブラウザの許可を確認）"), 4000); }
  renderDetail(filtered());
}

// ---- 編集（Enter で改行、⇧Enter・⌃Enter で保存、Esc で取り消し）
function startEdit() {
  const rec = current(); if (!rec) return;
  setTab("ocr");
  dash.editing = true; $("dash-edit").hidden = false; $("dash-preview").hidden = true;
  const box = $("dash-editbox"); box.value = rec.latex || "";
  box.rows = Math.min(14, Math.max(4, box.value.split("\n").length + 1));
  box.focus(); box.setSelectionRange(box.value.length, box.value.length);
}
function cancelEdit() { dash.editing = false; $("dash-edit").hidden = true; $("dash-preview").hidden = false; renderDetail(filtered()); }
async function saveEdit() {
  const rec = current(); if (!rec) return;
  try {
    const r = await api("POST", `/api/snips/${encodeURIComponent(rec.id)}`, { latex: $("dash-editbox").value });
    Object.assign(rec, r); dash.editing = false; $("dash-edit").hidden = true; $("dash-preview").hidden = false;
    renderSnips(); renderDash(); toast(T("保存しました"), 1200);
  } catch (e) { toast(T("失敗: {m}", { m: e.message }), 4000); }
}
$("dash-editbox").addEventListener("input", () => { const rec = current(); if (rec) renderMixedInto($("dash-preview"), $("dash-editbox").value, rec.kind); });
$("dash-editbox").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && (ev.shiftKey || ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); ev.stopPropagation(); saveEdit(); }
  else if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); cancelEdit(); }
});
$("dash-save").addEventListener("click", saveEdit);
$("dash-cancel").addEventListener("click", cancelEdit);
$("dash-note").addEventListener("input", () => {
  const rec = current(); if (!rec) return;
  clearTimeout(dash.noteT);
  const v = $("dash-note").value;
  dash.noteT = setTimeout(async () => { try { Object.assign(rec, await api("POST", `/api/snips/${encodeURIComponent(rec.id)}`, { note: v })); } catch (e) { toast(T("失敗: {m}", { m: e.message }), 4000); } }, 600);
});

async function deleteCurrent() {
  const rec = current(); if (!rec) return;
  const list = filtered(); const i = list.findIndex((r) => r.id === rec.id);
  try {
    await api("DELETE", `/api/snips/${encodeURIComponent(rec.id)}`);
    snipUI.items = snipUI.items.filter((x) => x.id !== rec.id); dash.picked.delete(rec.id);
    const next = list[i + 1] || list[i - 1];
    dash.cur = next ? next.id : null;
    renderSnips(); renderDash(); toast(T("削除しました"), 1500);
  } catch (e) { toast(T("失敗: {m}", { m: e.message }), 4000); }
}

async function dashNew(fmt) {
  try {
    const rec = await api("POST", "/api/snips/new", { format: fmt });
    snipUI.items = [rec, ...snipUI.items];
    renderSnips();
    if ($("dash").hidden) showDashboard(true);
    dash.kind = ""; dash.src = ""; dash.q = ""; $("dash-q").value = "";
    syncKindButtons();
    selectSnip(rec.id); startEdit();
  } catch (e) { toast(T("失敗: {m}", { m: e.message }), 4000); }
}
window.dashNew = dashNew;

// ---- キー操作（app.js の onKey から呼ばれる）
function move(delta, absolute) {
  const list = filtered(); if (!list.length) return;
  let i = list.findIndex((r) => r.id === dash.cur);
  i = absolute === "oldest" ? list.length - 1 : absolute === "newest" ? 0 : Math.max(0, Math.min(list.length - 1, i + delta));
  selectSnip(list[i].id);
}
window.dashAction = function (id) {
  if (dash.editing) return false;
  switch (id) {
    case "dbPrev": move(+1); break;  // 一覧は新しい順。← は1つ前（古い方）
    case "dbNext": move(-1); break;
    case "dbOldest": move(0, "oldest"); break;
    case "dbNewest": move(0, "newest"); break;
    case "dbRowUp": if (dash.tab !== "ocr") return false; dash.row = Math.max(0, dash.row - 1); renderDetail(filtered()); break;
    case "dbRowDown": if (dash.tab !== "ocr") return false; dash.row += 1; renderDetail(filtered()); break;
    case "dbTabPrev": setTab(TABS[(TABS.indexOf(dash.tab) + TABS.length - 1) % TABS.length]); break;
    case "dbTabNext": setTab(TABS[(TABS.indexOf(dash.tab) + 1) % TABS.length]); break;
    case "dbCopy": copyRow(); break;
    case "dbEdit": startEdit(); break;
    case "dbDelete": deleteCurrent(); break;
    case "dbSearch": $("dash-q").focus(); $("dash-q").select(); break;
    case "dbClose": showDashboard(false); break;
    default: return false;
  }
  return true;
};

// ---- 画面の部品
function syncKindButtons() { for (const b of $("dash-kind").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.kind === dash.kind)); }
$("dash-kind").addEventListener("click", (ev) => { const b = ev.target.closest("button"); if (!b) return; dash.kind = b.dataset.kind; syncKindButtons(); renderDash(); });
$("dash-src").addEventListener("change", () => { dash.src = $("dash-src").value; renderDash(); });
$("dash-q").addEventListener("input", () => { dash.q = $("dash-q").value; renderDash(); });
$("dash-q").addEventListener("keydown", (ev) => { if (ev.key === "Escape" || ev.key === "Enter") { ev.preventDefault(); $("dash-list").focus(); } });
$("dash-open").addEventListener("click", () => showDashboard($("dash").hidden));
$("dash-close").addEventListener("click", () => showDashboard(false));
$("dash-snip").addEventListener("click", () => snipScreen());
$("dash-img").addEventListener("click", () => $("snip-input").click());
$("dash-new").addEventListener("click", () => menuAt($("dash-new"), [
  { label: `${T("新しい Snip を書く（LaTeX）")}  ${K.label(K.primary("newLatex"))}`, run: () => dashNew("latex") },
  { label: `${T("新しい Snip を書く（Markdown）")}  ${K.label(K.primary("newMarkdown"))}`, run: () => dashNew("markdown") },
]));
$("dash-export").addEventListener("click", () => {
  const ids = dash.picked.size ? [...dash.picked] : filtered().map((r) => r.id);
  const go = async (fmt) => {
    try { const n = await downloadPost("/api/snips/export", { fmt, ids }, `snips.${fmt}`); toast(T("書き出しました: {n}", { n }), 2500); }
    catch (e) { toast(T("失敗: {m}", { m: e.message }), 4000); }
  };
  const what = dash.picked.size ? T("選んだ {n} 件", { n: ids.length }) : T("表示中の {n} 件", { n: ids.length });
  menuAt($("dash-export"), exportMenu((fmt) => go(fmt), what));
});
$("dash-selall").addEventListener("click", () => {
  if (dash.picked.size) dash.picked.clear(); else for (const r of filtered()) dash.picked.add(r.id);
  renderDash();
});
$("dash-prev").addEventListener("click", () => move(+1));
$("dash-next").addEventListener("click", () => move(-1));
for (const t of TABS) $(`tab-${t}`).addEventListener("click", () => setTab(t));
$("dash-del").addEventListener("click", deleteCurrent);
$("dash-copyjson").addEventListener("click", async () => { const rec = current(); if (rec) { await navigator.clipboard.writeText(JSON.stringify(rec, null, 2)); toast(T("コピーしました"), 1200); } });
$("dash-copytsv").addEventListener("click", async () => {
  const rec = current(); if (!rec) return;
  const back = snipCopyText(rec, rec.latex || "").replace(/\n/g, "<br>");
  await navigator.clipboard.writeText([rec.note || snipSourceLabel(rec.source), back, snipSourceLabel(rec.source)].join("\t"));
  toast(T("コピーしました"), 1200);
});
$("dash-copyimg").addEventListener("click", async () => {
  const rec = current(); if (!rec || rec.has_image === false) return;
  try { const b = await api("GET", `/api/snips/${encodeURIComponent(rec.id)}.png`); await navigator.clipboard.write([new ClipboardItem({ "image/png": b })]); toast(T("画像をコピーしました"), 1400); }
  catch (e) { toast(T("コピーできませんでした（ブラウザの許可を確認）"), 4000); }
});
$("dash-openimg").addEventListener("click", () => { if (dash.imgUrl) window.open(dash.imgUrl, "_blank", "noopener"); });
$("dash-list").tabIndex = 0;

// 新しい Snip が加わったら、ダッシュボードでもそれを選ぶ
window.addSnipHook = (rec) => { if (!$("dash").hidden) { dash.cur = rec.id; renderDash(); } };

// ---- アプリ・ショートカットからの指示（#view=snips&new=latex、または /api/ui/pull）
function applyCmd(c) {
  if (!c) return;
  if (c.new === "latex" || c.new === "markdown") { dashNew(c.new); return; }
  if (c.view === "snips") showDashboard(true);
  else if (c.view === "reader") showDashboard(false);
  else if (c.view === "settings") { renderSettings(); $("settings").showModal(); }
}
async function pullUi() {
  if (!document.hidden) {
    try { const r = await api("GET", "/api/ui/pull"); for (const c of r.commands || []) applyCmd(c); } catch (e) { /* 次回 */ }
  }
  setTimeout(pullUi, 1500);
}
document.addEventListener("plo-lang", () => { if (!$("dash").hidden) renderDash(); });
setTimeout(() => { applyCmd(INITIAL_CMD); updateKeyHints(); pullUi(); }, 0);
