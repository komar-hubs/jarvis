// Small Python code editor: a textarea over a highlighted <pre>, line numbers, Python-style indenting.
// No libraries and no network: the window must work offline.
const PY_KW = new Set(("False None True and as assert async await break class continue def del elif else except finally for from " +
  "global if import in is lambda nonlocal not or pass raise return try while with yield match case").split(" "));
const PY_BI = new Set(("print len range int str float bool list dict set tuple min max sum abs round sorted enumerate zip map filter " +
  "open isinstance any all repr format reversed super type Exception ValueError KeyError TypeError self").split(" "));
const PY_RX = /([rRbBfFuU]{0,2}(?:"""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$)|"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?))|(#[^\n]*)|(\b\d[\d_]*(?:\.\d+)?\b)|(@[\w.]+)|([A-Za-z_Ѐ-ӿ][\wЀ-ӿ]*)/g;

function pyHighlight(src) {
  let out = "", last = 0, prev = "";
  for (const m of src.matchAll(PY_RX)) {
    out += esc(src.slice(last, m.index));
    const [t, s, c, n, d, w] = m;
    let cls = "";
    if (s) cls = "s";
    else if (c) cls = "c";
    else if (n) cls = "n";
    else if (d) cls = "d";
    else if (w) {
      if (prev === "def" || prev === "class") cls = "f";
      else if (PY_KW.has(w)) cls = "k";
      else if (w === "api" || w === "args" || w === "text") cls = "a";
      else if (PY_BI.has(w)) cls = "b";
      else if (/^[A-Z][A-Z0-9_]+$/.test(w)) cls = "u";
      prev = w;
    }
    if (!w) prev = "";
    out += cls ? `<i class="h${cls}">${esc(t)}</i>` : esc(t);
    last = m.index + t.length;
  }
  return out + esc(src.slice(last)) + "\n ";
}

function codeEditor(host, value, opts = {}) {
  host.innerHTML = `<div class="ced"><div class="ced-gut"><div></div></div><div class="ced-main"><pre class="ced-hl" aria-hidden="true"></pre>
    <textarea class="ced-ta" spellcheck="false" autocomplete="off" autocapitalize="off" wrap="off"></textarea></div></div>`;
  const ta = $(".ced-ta", host), hl = $(".ced-hl", host), gut = $(".ced-gut > div", host);
  let marked = 0, lines = 0;
  const paint = () => {
    hl.innerHTML = pyHighlight(ta.value);
    const n = ta.value.split("\n").length;
    if (n !== lines || marked) {
      lines = n;
      gut.innerHTML = Array.from({ length: n }, (_, i) => `<b class="${i + 1 === marked ? "err" : ""}">${i + 1}</b>`).join("");
    }
    sync();
  };
  const sync = () => {
    hl.style.transform = `translate(${-ta.scrollLeft}px, ${-ta.scrollTop}px)`;
    gut.style.transform = `translateY(${-ta.scrollTop}px)`;
  };
  const insert = (s) => { ta.focus(); document.execCommand("insertText", false, s); };  // keeps Ctrl+Z history
  const lineStart = (pos) => ta.value.lastIndexOf("\n", pos - 1) + 1;
  // replace whole lines [a, b) with fn(lines) and keep them selected
  const editLines = (fn) => {
    const v = ta.value, a = lineStart(ta.selectionStart);
    let b = v.indexOf("\n", ta.selectionEnd - (ta.selectionEnd > ta.selectionStart && v[ta.selectionEnd - 1] === "\n" ? 1 : 0));
    if (b < 0) b = v.length;
    const block = fn(v.slice(a, b).split("\n")).join("\n");
    ta.setSelectionRange(a, b);
    insert(block);
    ta.setSelectionRange(a, a + block.length);
  };
  ta.addEventListener("keydown", (e) => {
    const multi = ta.value.slice(ta.selectionStart, ta.selectionEnd).includes("\n");
    if (e.key === "Tab" && !e.ctrlKey) {
      e.preventDefault();
      if (e.shiftKey) editLines((ls) => ls.map((l) => l.replace(/^ {1,4}/, "")));
      else if (multi) editLines((ls) => ls.map((l) => (l.trim() ? "    " + l : l)));
      else insert(" ".repeat(4 - ((ta.selectionStart - lineStart(ta.selectionStart)) % 4)));
    } else if (e.key === "Enter" && !e.ctrlKey && !e.shiftKey) {
      e.preventDefault();
      const line = ta.value.slice(lineStart(ta.selectionStart), ta.selectionStart);
      let ind = line.match(/^ */)[0];
      if (/:\s*(#.*)?$/.test(line)) ind += "    ";
      else if (/^\s*(return|pass|break|continue|raise)\b/.test(line)) ind = ind.slice(4);
      insert("\n" + ind);
    } else if (e.key === "Backspace" && ta.selectionStart === ta.selectionEnd) {
      const ls = lineStart(ta.selectionStart), before = ta.value.slice(ls, ta.selectionStart);
      if (before.length && /^ +$/.test(before)) {
        e.preventDefault();
        ta.setSelectionRange(ta.selectionStart - (before.length % 4 || 4), ta.selectionStart);
        insert("");
      }
    } else if (e.key === "/" && e.ctrlKey) {
      e.preventDefault();
      editLines((ls) => {
        const on = ls.filter((l) => l.trim()).every((l) => /^\s*#/.test(l));
        return ls.map((l) => (!l.trim() ? l : on ? l.replace(/^(\s*)# ?/, "$1") : l.replace(/^(\s*)/, "$1# ")));
      });
    } else if (e.key === "s" && e.ctrlKey) {
      e.preventDefault();
      opts.onSave && opts.onSave();
    }
  });
  ta.addEventListener("input", () => { if (marked) { marked = 0; lines = 0; } paint(); opts.onChange && opts.onChange(); });
  ta.addEventListener("scroll", sync);
  ta.value = value || "";
  paint();
  return {
    get: () => ta.value,
    set: (v) => { ta.value = v || ""; marked = 0; lines = 0; paint(); },
    insert: (s) => { insert(s); },
    focus: () => ta.focus(),
    // highlight an error line and scroll to it; 0 clears
    mark: (n) => {
      marked = n || 0; lines = 0; paint();
      if (marked) { const lh = parseFloat(getComputedStyle(ta).lineHeight) || 20; ta.scrollTop = Math.max(0, (marked - 4) * lh); sync(); }
    },
  };
}
