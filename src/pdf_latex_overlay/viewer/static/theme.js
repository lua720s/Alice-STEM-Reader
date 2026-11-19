// テーマ（自動・ライト・ダーク）。<head> で同期的に読み込み、描画前に data-theme を決めてちらつきを防ぐ。
// 選択は localStorage に記憶する（使えない環境では記憶せず動く）。
(function () {
  "use strict";
  var KEY = "plo-theme";
  var VALID = { system: 1, light: 1, dark: 1 };
  function read() {
    try { var v = localStorage.getItem(KEY); return VALID[v] ? v : "system"; } catch (e) { return "system"; }
  }
  function apply(t) { document.documentElement.setAttribute("data-theme", t); }
  apply(read());
  window.ploTheme = {
    get: read,
    set: function (t) {
      if (!VALID[t]) return;
      try { localStorage.setItem(KEY, t); } catch (e) { /* 記憶できなくても切り替える */ }
      apply(t);
    },
    // いま実際に表示されている配色（"light" か "dark"）
    effective: function () {
      var t = read();
      if (t !== "system") return t;
      return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    },
  };
})();
