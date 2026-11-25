// 出力の形式（outfmt.py と同じ規則。試験で、同じ入力に同じ結果を返すことを確かめる）。
// 内部: 数式だけの結果は区切りなしの LaTeX（複数行は改行）、文章＋数式は \( … \)・\[ … \] を含む文字列。
"use strict";
(function (root) {
  const FORMAT_DEFAULTS = { inline: "paren", display: "bracket", math_copy: "raw", multiline: "lines", text_lines: "keep" };
  const MATH_RE = /\\\(([\s\S]+?)\\\)|\\\[([\s\S]+?)\\\]/g;

  function lines(latex) { return (latex || "").split("\n").filter((l) => l.trim()); }
  function joinMultiline(latex, mode) {
    const ls = lines(latex);
    if (ls.length <= 1 || mode === "lines") return ls.length ? ls.join("\n") : (latex || "");
    const env = mode === "aligned" ? "aligned" : "gathered";
    return `\\begin{${env}}\n` + ls.join(" \\\\\n") + `\n\\end{${env}}`;
  }
  function wrapInline(t, style) {
    if (style === "paren") return `\\(${t}\\)`;
    if (style === "dollar") return `$${t}$`;
    return t;
  }
  function wrapDisplay(t, style) {
    const multi = t.trim().includes("\n");
    if (style === "bracket") return multi ? `\\[\n${t}\n\\]` : `\\[${t}\\]`;
    if (style === "dollars") return multi ? `$$\n${t}\n$$` : `$$${t}$$`;
    if (style === "equation" || style === "equation*") return `\\begin{${style}}\n${t}\n\\end{${style}}`;
    return t;
  }
  function formatMath(latex, fmt, how) {
    how = how || fmt.math_copy;
    const t = joinMultiline((latex || "").replace(/^\n+|\n+$/g, ""), fmt.multiline);
    if (!t.trim() || how === "raw") return t;
    const multi = t.trim().includes("\n");
    if (how === "inline" || (how === "auto" && !multi)) return multi ? wrapDisplay(t, fmt.display) : wrapInline(t, fmt.inline);
    return wrapDisplay(t, fmt.display);
  }
  function convertText(text, fmt) {
    if (!text) return text || "";
    if (fmt.text_lines === "join") {
      text = text.split(/\n\s*\n/).map((p) => p.replace(/\s*\n\s*/g, " ").trim()).join("\n\n");
    }
    return text.replace(MATH_RE, (m, a, b) => (a !== undefined ? wrapInline(a, fmt.inline) : wrapDisplay(joinMultiline(b, fmt.multiline), fmt.display)));
  }
  function copyText(kind, latex, fmt) {
    return kind === "mixed" || kind === "text" ? convertText(latex || "", fmt) : formatMath(latex || "", fmt);
  }
  function fromLegacy(delim, single) {
    const f = { ...FORMAT_DEFAULTS };
    if (delim === "dollar") Object.assign(f, { inline: "dollar", display: "dollars" });
    else if (delim === "none") Object.assign(f, { inline: "none", display: "none" });
    if (single) f.math_copy = "auto";
    return f;
  }
  const api = { FORMAT_DEFAULTS, joinMultiline, wrapInline, wrapDisplay, formatMath, convertText, copyText, fromLegacy };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.ploFormat = api;
})(typeof window !== "undefined" ? window : this);
