// PDF LaTeX Overlay ローカルビューア。
// OCR 結果はデータとして扱う: LaTeX は textContent / textarea にだけ入れ、HTML として解釈しない。
"use strict";

const token = new URLSearchParams(location.hash.slice(1)).get("token") || sessionStorage.getItem("plo-token") || "";
if (token) { sessionStorage.setItem("plo-token", token); history.replaceState(null, "", location.pathname); }

const $ = (id) => document.getElementById(id);
const state = { doc: null, formulas: [], selected: null, scale: [], redrawFor: null };
const DPI = 110;

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

async function pageImage(pno) {
  const blob = await api("GET", `/api/page/${pno}.png?dpi=${DPI}`);
  return URL.createObjectURL(blob);
}

async function load() {
  if (!token) { document.body.textContent = "トークンがありません。起動時に表示された URL を開いてください。"; return; }
  state.doc = await api("GET", "/api/doc");
  $("docname").textContent = state.doc.name;
  const pages = $("pages"); pages.textContent = "";
  for (const [pno, p] of state.doc.pages.entries()) {
    const div = document.createElement("div");
    div.className = "page"; div.dataset.page = pno;
    div.style.width = `${Math.round(p.width * DPI / 72)}px`;
    div.style.maxWidth = "100%";
    div.style.aspectRatio = `${p.width} / ${p.height}`;
    const no = document.createElement("span"); no.className = "no"; no.textContent = `p.${pno + 1}`;
    const img = document.createElement("img"); img.alt = `ページ ${pno + 1}`; img.loading = "lazy";
    div.append(no, img); pages.append(div);
    pageImage(pno).then((u) => { img.src = u; });
    attachDraw(div, pno);
  }
  await refresh();
  new ResizeObserver(() => drawBoxes()).observe(pages);
}

async function refresh() {
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
  for (const div of document.querySelectorAll(".page")) {
    const pno = +div.dataset.page, s = pageScale(div);
    for (const f of state.formulas.filter((x) => x.page === pno && x.view_rect)) {
      const [x0, y0, x1, y1] = f.view_rect;
      const b = document.createElement("div");
      b.className = `box ${f.status}${f.edited ? " edited" : ""}${f.id === state.selected ? " selected" : ""}`;
      Object.assign(b.style, { left: `${x0 * s}px`, top: `${y0 * s}px`, width: `${(x1 - x0) * s}px`, height: `${(y1 - y0) * s}px` });
      b.title = f.latex || "(LaTeX なし)";
      b.addEventListener("click", (ev) => { if (drawing()) return; ev.stopPropagation(); select(f.id); });
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
    try { window.katex.render(latex, el, { throwOnError: false, displayMode: true, trust: false, strict: "ignore" }); return; }
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

async function copyLatex() {
  const text = $("latex").value;
  if (!text) { toast("コピーする LaTeX がありません"); return; }
  try { await navigator.clipboard.writeText(text); toast("LaTeX をコピーしました"); }
  catch (e) { $("latex").select(); document.execCommand("copy"); toast("LaTeX をコピーしました"); }
}

async function act(fn, okMsg) {
  try { await fn(); await refresh(); if (okMsg) toast(okMsg); } catch (e) { toast(`失敗: ${e.message}`, 4000); }
}

function drawing() { return $("drawmode").checked || state.redrawFor; }

function attachDraw(div, pno) {
  let start = null, rubber = null;
  const pos = (ev) => { const r = div.getBoundingClientRect(); return [ev.clientX - r.left, ev.clientY - r.top]; };
  div.addEventListener("mousedown", (ev) => {
    if (!drawing()) return;
    ev.preventDefault(); start = pos(ev);
    rubber = document.createElement("div"); rubber.className = "rubber"; div.append(rubber);
  });
  window.addEventListener("mousemove", (ev) => {
    if (!start) return;
    const [x, y] = pos(ev);
    Object.assign(rubber.style, { left: `${Math.min(x, start[0])}px`, top: `${Math.min(y, start[1])}px`,
      width: `${Math.abs(x - start[0])}px`, height: `${Math.abs(y - start[1])}px` });
  });
  window.addEventListener("mouseup", async (ev) => {
    if (!start) return;
    const [x, y] = pos(ev), s = pageScale(div);
    const vr = [Math.min(x, start[0]) / s, Math.min(y, start[1]) / s, Math.max(x, start[0]) / s, Math.max(y, start[1]) / s];
    start = null; rubber.remove(); rubber = null;
    if (vr[2] - vr[0] < 3 || vr[3] - vr[1] < 3) return;
    if (state.redrawFor) {
      const id = state.redrawFor; state.redrawFor = null; document.body.classList.remove("drawing");
      await act(() => api("POST", `/api/formulas/${encodeURIComponent(id)}`, { view_rect: vr }), "枠を更新しました（必要なら再認識）");
      return;
    }
    const recognize = state.doc.backend && state.doc.backend !== "none";
    await act(async () => {
      const f = await api("POST", "/api/formulas", { page: pno, view_rect: vr, recognize });
      state.selected = f.id;
    }, recognize ? "式を追加して認識しました" : "式を追加しました（LaTeX を入力してください）");
  });
}

function wire() {
  $("drawmode").addEventListener("change", () => document.body.classList.toggle("drawing", $("drawmode").checked));
  $("latex").addEventListener("input", () => renderPreview($("latex").value));
  $("copy").onclick = copyLatex;
  $("save").onclick = () => act(() => api("POST", `/api/formulas/${encodeURIComponent(state.selected)}`, { latex: $("latex").value }), "修正を保存しました");
  $("confirm").onclick = () => act(() => api("POST", `/api/formulas/${encodeURIComponent(state.selected)}`, { latex: $("latex").value, status: "confirmed" }), "確定しました");
  $("exclude").onclick = () => act(() => api("POST", `/api/formulas/${encodeURIComponent(state.selected)}`, { status: "excluded", reason: "user" }), "除外しました");
  $("recognize").onclick = () => act(() => api("POST", `/api/formulas/${encodeURIComponent(state.selected)}/recognize`), "再認識しました");
  $("redraw").onclick = () => { state.redrawFor = state.selected; document.body.classList.add("drawing"); toast("ページ上で新しい枠をドラッグしてください"); };
  $("delete").onclick = () => {
    if (!confirm(`${state.selected} を削除しますか？（履歴には残ります）`)) return;
    const id = state.selected; state.selected = null;
    act(() => api("DELETE", `/api/formulas/${encodeURIComponent(id)}`), "削除しました");
  };
  $("export").onclick = async () => {
    $("export").disabled = true; toast("派生PDFを作成・検証しています…", 60000);
    try {
      const r = await api("POST", "/api/export", { mode: $("mode").value, only_confirmed: $("onlyconfirmed").checked });
      if (r.ok) toast(`保存しました（埋め込み ${r.counts.embedded} 件、除外 ${r.counts.excluded} 件）\n${r.output}`, 8000);
      else toast(`保存しませんでした（終了コード ${r.exit_code}）\n${r.errors.join("\n")}${r.failed_checks.length ? "\n検証不合格: " + r.failed_checks.join(", ") : ""}`, 10000);
    } catch (e) { toast(`失敗: ${e.message}`, 6000); }
    finally { $("export").disabled = false; }
  };
  document.addEventListener("keydown", (ev) => {
    if (ev.target.tagName === "TEXTAREA" || ev.metaKey || ev.ctrlKey) return;
    if (ev.key === "c" && state.selected) copyLatex();
    if (ev.key === "d") { $("drawmode").checked = !$("drawmode").checked; $("drawmode").dispatchEvent(new Event("change")); }
    if (ev.key === "Escape") { state.redrawFor = null; document.body.classList.remove("drawing"); $("drawmode").checked = false; }
  });
}

wire();
load().catch((e) => toast(`読み込みに失敗: ${e.message}`, 6000));
