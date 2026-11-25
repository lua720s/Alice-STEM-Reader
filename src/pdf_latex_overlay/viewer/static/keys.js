// キーボードショートカットの定義と、利用者による変更（設定画面）。
// 既定は Mathpix Snip に合わせる（⌃⌘M で画面を Snip、結果の画面では ←→・⇧←→・↑↓・⇧↑↓・Enter・⇧Enter・Delete）。
// 割り当ては "Ctrl+Cmd+M" の形の文字列。Cmd は macOS の ⌘（他の OS では Windows キー）、Mod は macOS で Cmd・他で Ctrl。
// 変更は localStorage（plo-keymap）に保存する。「全体」の動作は、メニューバーの Snip アプリにも渡す（サーバーの app.json）。
"use strict";
(function () {
  const IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || "");
  const G = IS_MAC ? "Ctrl+Cmd+" : "Ctrl+Alt+";  // Mathpix: macOS ⌃⌘、Windows・Linux Ctrl+Alt
  // group: global（どこでも。メニューバーのアプリからも）/ reader（資料を読む画面）/ dashboard（Snip のダッシュボード）
  // keys: 既定の割り当て（変更できるのは先頭）。alt: 変更しない別の割り当て（矢印キーと同じ動きの PageDown など）
  const ACTIONS = [
    { id: "snipScreen", group: "global", label: "画面を Snip", keys: [G + "M"], alt: ["S"], hotkey: "snip" },
    { id: "openApp", group: "global", label: "アプリの画面を開く（資料を読む画面）", keys: [G + "O"], hotkey: "open" },
    { id: "newLatex", group: "global", label: "新しい Snip を書く（LaTeX）", keys: [G + "L"], hotkey: "latex" },
    { id: "newMarkdown", group: "global", label: "新しい Snip を書く（Markdown）", keys: [G + "N"], hotkey: "markdown" },

    { id: "toggleDashboard", group: "reader", label: "Snip のダッシュボードを開く・閉じる", keys: ["D"] },
    { id: "snipRegion", group: "reader", label: "ページ上のドラッグ: Snip ⇄ 枠を追加", keys: ["R"] },
    { id: "nextPage", group: "reader", label: "次のページ", keys: ["ArrowRight"], alt: ["PageDown", "J", "Space"] },
    { id: "prevPage", group: "reader", label: "前のページ", keys: ["ArrowLeft"], alt: ["PageUp", "K", "Shift+Space"] },
    { id: "firstPage", group: "reader", label: "最初のページ", keys: ["Home"] },
    { id: "lastPage", group: "reader", label: "最後のページ", keys: ["End"] },
    { id: "zoomIn", group: "reader", label: "拡大", keys: ["+"], alt: ["="] },
    { id: "zoomOut", group: "reader", label: "縮小", keys: ["-"], alt: ["_"] },
    { id: "zoomReset", group: "reader", label: "拡大率を元に戻す", keys: ["0"] },
    { id: "toggleFit", group: "reader", label: "ページ全体 ⇄ 幅に合わせる", keys: ["F"] },
    { id: "toggleSidebar", group: "reader", label: "コレクションの開閉", keys: ["B"] },
    { id: "search", group: "reader", label: "資料を検索", keys: ["/"] },
    { id: "copyFormula", group: "reader", label: "選んだ枠をコピー", keys: ["C"] },
    { id: "deleteBox", group: "reader", label: "選んだ枠を削除", keys: ["Delete"], alt: ["Backspace"] },
    { id: "nextReview", group: "reader", label: "次の要確認", keys: ["N"] },
    { id: "prevReview", group: "reader", label: "前の要確認", keys: ["Shift+N"] },
    { id: "recognizePage", group: "reader", label: "このページを認識", keys: ["Shift+R"] },
    { id: "recognizeText", group: "reader", label: "このページの文章を認識", keys: ["Shift+T"] },
    { id: "openDocument", group: "reader", label: "資料を開く…", keys: ["Mod+O"] },
    { id: "savePdf", group: "reader", label: "PDFを保存", keys: ["Mod+S"] },
    { id: "exportMarkdown", group: "reader", label: "認識した内容を Markdown に書き出す", keys: ["Mod+Shift+E"] },
    { id: "settings", group: "reader", label: "設定", keys: ["Mod+,"] },
    { id: "shortcuts", group: "reader", label: "ショートカットの一覧", keys: ["?"] },

    { id: "dbPrev", group: "dashboard", label: "前の Snip", keys: ["ArrowLeft"] },
    { id: "dbNext", group: "dashboard", label: "次の Snip", keys: ["ArrowRight"] },
    { id: "dbOldest", group: "dashboard", label: "いちばん古い Snip", keys: ["Shift+ArrowLeft"] },
    { id: "dbNewest", group: "dashboard", label: "いちばん新しい Snip", keys: ["Shift+ArrowRight"] },
    { id: "dbRowUp", group: "dashboard", label: "上の形式を選ぶ", keys: ["ArrowUp"] },
    { id: "dbRowDown", group: "dashboard", label: "下の形式を選ぶ", keys: ["ArrowDown"] },
    { id: "dbTabPrev", group: "dashboard", label: "前のタブ", keys: ["Shift+ArrowUp"] },
    { id: "dbTabNext", group: "dashboard", label: "次のタブ", keys: ["Shift+ArrowDown"] },
    { id: "dbCopy", group: "dashboard", label: "選んだ形式をコピー", keys: ["C"], alt: ["Space"] },
    { id: "dbEdit", group: "dashboard", label: "編集する", keys: ["Enter"] },
    { id: "dbDelete", group: "dashboard", label: "この Snip を削除", keys: ["Delete"], alt: ["Backspace"] },
    { id: "dbSearch", group: "dashboard", label: "Snip を検索", keys: ["/"] },
    { id: "dbClose", group: "dashboard", label: "資料を読む画面に戻る", keys: ["Escape"], alt: ["D"] },
  ];
  // 編集中（Mathpix と同じ）: Enter は改行、⇧Enter か ⌃Enter で保存、Esc で取り消し。これは変えない
  const KEY = "plo-keymap";
  let custom = {};
  try { custom = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch (e) { custom = {}; }

  const MOD_ORDER = ["Ctrl", "Alt", "Shift", "Cmd"];
  function normalize(b) {  // "mod+shift+e" → "Shift+Cmd+E"（macOS）の並びにそろえる
    if (!b) return "";
    const parts = b.split("+").filter((x, i, a) => x !== "" || (i === a.length - 1));
    let key = parts.pop();
    if (key === "") key = "+";
    const mods = new Set(parts.map((m) => ({ ctrl: "Ctrl", control: "Ctrl", alt: "Alt", option: "Alt", opt: "Alt", shift: "Shift",
      cmd: "Cmd", meta: "Cmd", command: "Cmd", mod: IS_MAC ? "Cmd" : "Ctrl" }[m.toLowerCase()] || m)));
    if (key.length === 1) key = key.toUpperCase();
    return [...MOD_ORDER.filter((m) => mods.has(m)), key].join("+");
  }
  function fromEvent(ev) {  // キーボードの操作 → 割り当ての文字列（修飾キーだけのときは ""）
    if (["Control", "Alt", "Shift", "Meta", "OS", "CapsLock", "Fn"].includes(ev.key)) return "";
    let key;
    if (/^Key[A-Z]$/.test(ev.code || "")) key = ev.code.slice(3);
    else if (/^Digit\d$/.test(ev.code || "") && (ev.ctrlKey || ev.metaKey || ev.altKey)) key = ev.code.slice(5);
    else if (ev.key === " ") key = "Space";
    else if (ev.key === "Esc") key = "Escape";
    else key = ev.key.length === 1 ? ev.key.toUpperCase() : ev.key;
    const mods = [];
    if (ev.ctrlKey) mods.push("Ctrl");
    if (ev.altKey) mods.push("Alt");
    // 記号（? + _ など）は Shift と一緒に打つので、Shift を含めない
    if (ev.shiftKey && !(key.length === 1 && !/[A-Z0-9]/.test(key))) mods.push("Shift");
    if (ev.metaKey) mods.push("Cmd");
    return [...mods, key].join("+");
  }
  const SYM = { Ctrl: "⌃", Alt: "⌥", Shift: "⇧", Cmd: "⌘" };
  const KEYSYM = { ArrowLeft: "←", ArrowRight: "→", ArrowUp: "↑", ArrowDown: "↓", Enter: "↩", Escape: "Esc", Backspace: "⌫",
    Delete: IS_MAC ? "⌦" : "Del", Space: "Space", PageUp: "PgUp", PageDown: "PgDn" };
  function label(b) {  // 表示用（macOS は ⌃⌘M、他は Ctrl+Alt+M）
    if (!b) return "—";
    const p = normalize(b).split("+");
    let key = p.pop();
    if (p.length === 0 && key === "") key = "+";
    const k = KEYSYM[key] || key;
    if (IS_MAC) return p.map((m) => SYM[m] || m).join("") + k;
    return [...p.map((m) => (m === "Cmd" ? "Win" : m)), k].join("+");
  }
  const byId = new Map(ACTIONS.map((a) => [a.id, a]));
  function primary(id) { const a = byId.get(id); if (!a) return ""; return id in custom ? normalize(custom[id]) : normalize(a.keys[0]); }
  function bindings(id) {
    const a = byId.get(id); if (!a) return [];
    const p = primary(id);
    const alts = (a.alt || []).map(normalize).filter((x) => !isTakenByOther(x, id, a.group));
    return [p, ...alts].filter(Boolean);
  }
  function sameContext(g1, g2) { return g1 === g2 || g1 === "global" || g2 === "global"; }
  function isTakenByOther(b, id, group) {
    return ACTIONS.some((o) => o.id !== id && sameContext(o.group, group) && primary(o.id) === b);
  }
  function conflict(id, b) {  // ほかの動作が同じ割り当てを使っていれば、その動作
    const a = byId.get(id); b = normalize(b);
    if (!b) return null;
    return ACTIONS.find((o) => o.id !== id && sameContext(o.group, a.group) && (primary(o.id) === b)) || null;
  }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(custom)); } catch (e) { /* 保存できなくても、この画面では効く */ } }
  function set(id, b) { custom[id] = normalize(b); save(); }
  function reset(id) { if (id) delete custom[id]; else custom = {}; save(); }
  function match(ev, groups) {  // この操作に当たる動作の ID（groups の順に探す）
    const b = fromEvent(ev);
    if (!b) return null;
    for (const g of groups) for (const a of ACTIONS) if (a.group === g && bindings(a.id).includes(b)) return a.id;
    return null;
  }
  function hotkeyString(b) {  // メニューバーのアプリ用（"ctrl+cmd+m"）。使えない割り当てなら ""
    const n = normalize(b); if (!n) return "";
    const p = n.split("+"); const key = p.pop();
    if (!p.length || !/^([A-Z0-9]|F([1-9]|1[0-9])|Space)$/.test(key)) return "";
    return [...p, key].join("+").toLowerCase();
  }
  function globalHotkeys() {  // 既定から変えたものだけ（既定はメニューバーのアプリが知っている）
    const out = {};
    for (const a of ACTIONS) if (a.hotkey && a.id in custom) out[a.hotkey] = hotkeyString(primary(a.id));
    return out;
  }
  function applyServer(hk) {  // サーバー（app.json）に保存された、利用者が変えた全体のショートカットを、この画面にも反映する
    if (!hk) return;
    for (const a of ACTIONS) {
      if (!a.hotkey) continue;
      if (a.hotkey in hk) custom[a.id] = hk[a.hotkey] ? normalize(hk[a.hotkey]) : "";
      else delete custom[a.id];
    }
    save();
  }
  window.ploKeys = { ACTIONS, IS_MAC, normalize, fromEvent, label, primary, bindings, conflict, set, reset, match, globalHotkeys, applyServer, hotkeyString };
})();
