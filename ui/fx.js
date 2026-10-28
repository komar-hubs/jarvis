// Visual & audio polish: boot sequence, typewriter subtitles, sparklines, mic oscilloscope, weather icons,
// themes, UI sounds, nav glow, seconds ring. Loaded before app.js; app.js calls FX.* hooks.
const FX = (() => {
  const q = (s) => document.querySelector(s);
  const THEMES = {
    jarvis: { name: "J.A.R.V.I.S.", acc: "#4fd8ff", hot: "#ffb347" },
    mark3: { name: "Mark III", acc: "#ff4b5c", hot: "#ffcc4d" },
    nano: { name: "Nano", acc: "#9b86ff", hot: "#ff7ad9" },
    emerald: { name: "Emerald", acc: "#3ff0a2", hot: "#ffe066" },
    stealth: { name: "Stealth", acc: "#e4ecf2", hot: "#74d4ff" },
  };
  let soundsOn = true;

  // ---------------------------------------------------------------- UI sounds (WebAudio, tiny and quiet)
  let actx = null;
  function sound(kind = "tick") {
    if (!soundsOn) return;
    try {
      actx = actx || new (window.AudioContext || window.webkitAudioContext)();
      const t = actx.currentTime, o = actx.createOscillator(), g = actx.createGain();
      const f = { tick: [1800, 2400, 0.035], tab: [880, 1320, 0.07], ok: [660, 990, 0.12], hover: [2600, 2600, 0.02], toast: [990, 1480, 0.14] }[kind] || [1200, 1600, 0.05];
      o.type = "sine"; o.frequency.setValueAtTime(f[0], t); o.frequency.exponentialRampToValueAtTime(f[1], t + f[2]);
      g.gain.setValueAtTime(kind === "hover" ? 0.012 : 0.035, t); g.gain.exponentialRampToValueAtTime(0.0001, t + f[2] + 0.05);
      o.connect(g).connect(actx.destination); o.start(t); o.stop(t + f[2] + 0.06);
    } catch (e) {}
  }
  document.addEventListener("click", (e) => { if (e.target.closest("button, .card, .li, .theme-sw")) sound("tick"); }, true);

  // ---------------------------------------------------------------- themes
  function setTheme(name, announce) {
    if (!THEMES[name]) name = "jarvis";
    document.body.dataset.theme = name;
    const th = THEMES[name];
    if (window.reactor) reactor.setTheme(th.acc, th.hot);
    document.body.classList.add("glitch"); setTimeout(() => document.body.classList.remove("glitch"), 380);
    if (announce) toast(`Тема: ${th.name}`, "Интерфейс");
    try { localStorage.setItem("jarvis.theme", name); } catch (e) {}
  }
  try { const t = localStorage.getItem("jarvis.theme"); if (t) setTheme(t); } catch (e) {}

  // ---------------------------------------------------------------- nav glow follows the active tab
  function navGlow() {
    const b = q("#nav > button.active"), g = q(".nav-glow");
    if (b && g) g.style.top = b.offsetTop + "px";
  }
  addEventListener("resize", navGlow);

  // ---------------------------------------------------------------- typewriter subtitle
  // the raw stream is kept whole and cleaned as a whole: a tool tag split across chunks
  // ('<jar' + 'vis>{"tool": ...}</jarvis>') must never leak into the subtitle
  let typeTimer = null, typeRaw = "", typeBuf = "", typeShown = 0, fadeTimer = null;
  function cleanSpoken(s) {
    return s.replace(/<jarvis>[\s\S]*?(<\/jarvis>|$)/g, " ")
      .replace(/\{\s*"tool"[\s\S]*?(\}|$)/g, " ")          // bare JSON tool calls without the tag
      .replace(/<(?:j(?:a(?:r(?:v(?:i(?:s)?)?)?)?)?)?$/, "") // an opening tag still arriving
      .replace(/<\/?jarvis>/g, " ")
      .replace(/\s+/g, " ");
  }
  function typeRender() {
    const el = q("#subtitle"); if (!el) return;
    const start = Math.max(0, typeShown - 260);
    el.innerHTML = (start ? "…" : "") + esc(typeBuf.slice(start, typeShown)) + (typeShown < typeBuf.length ? '<span class="cur"></span>' : "");
  }
  function typeStep() {
    if (typeShown < typeBuf.length) { typeShown = Math.min(typeBuf.length, typeShown + 1 + (typeBuf.length - typeShown > 120 ? 2 : 0)); typeRender(); }
    else { clearInterval(typeTimer); typeTimer = null; typeRender(); }
  }
  function type(text, append = false) {
    const el = q("#subtitle"); if (!el) return;
    if (append) typeRaw += String(text || ""); else { typeRaw = String(text || ""); typeShown = 0; }
    typeBuf = cleanSpoken(typeRaw);
    typeShown = Math.min(typeShown, typeBuf.length);
    el.style.opacity = 1;
    if (!typeTimer) typeTimer = setInterval(typeStep, 28);
    clearTimeout(fadeTimer); fadeTimer = setTimeout(() => (el.style.opacity = 0.35), 12000);
  }

  // ---------------------------------------------------------------- sparklines (CPU / RAM / GPU history)
  const hist = { cpu: [], ram: [], gpu: [] };
  function pushStats(s) {
    hist.cpu.push(s.cpu || 0); hist.ram.push(s.ram || 0); hist.gpu.push(s.gpu ? s.gpu.util : 0);
    for (const k in hist) if (hist[k].length > 90) hist[k].shift();
    drawSpark();
  }
  function drawSpark() {
    const c = q("#spark"); if (!c || !c.offsetWidth) return;
    const dpr = window.devicePixelRatio || 1, w = c.offsetWidth, h = 46;
    if (c.width !== Math.round(w * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
    const x = c.getContext("2d"); x.setTransform(dpr, 0, 0, dpr, 0, 0); x.clearRect(0, 0, w, h);
    const acc = getComputedStyle(document.body).getPropertyValue("--acc").trim(), hot = getComputedStyle(document.body).getPropertyValue("--hot").trim();
    x.strokeStyle = "rgba(127,166,189,.12)"; x.lineWidth = 1;
    for (let i = 1; i < 4; i++) { x.beginPath(); x.moveTo(0, (h / 4) * i); x.lineTo(w, (h / 4) * i); x.stroke(); }
    const line = (arr, col, fill) => {
      if (arr.length < 2) return;
      const step = w / 89;
      x.beginPath();
      arr.forEach((v, i) => { const px = w - (arr.length - 1 - i) * step, py = h - 3 - (v / 100) * (h - 6); i ? x.lineTo(px, py) : x.moveTo(px, py); });
      x.strokeStyle = col; x.lineWidth = 1.6; x.shadowColor = col; x.shadowBlur = 6; x.stroke(); x.shadowBlur = 0;
      if (fill) { x.lineTo(w, h); x.lineTo(w - (arr.length - 1) * step, h); x.closePath(); const g = x.createLinearGradient(0, 0, 0, h); g.addColorStop(0, col + "55"); g.addColorStop(1, col + "00"); x.fillStyle = g; x.fill(); }
    };
    line(hist.ram, "#7c9cb3", false); line(hist.gpu, hot, false); line(hist.cpu, acc, true);
  }

  // ---------------------------------------------------------------- microphone oscilloscope in the command bar
  let micLvl = 0, micSm = 0;
  const wave = q("#bar-wave");
  function drawWave(t) {
    requestAnimationFrame(drawWave);
    if (!wave || document.hidden) return;
    const dpr = window.devicePixelRatio || 1, w = wave.offsetWidth, h = wave.offsetHeight;
    if (wave.width !== Math.round(w * dpr)) { wave.width = Math.round(w * dpr); wave.height = Math.round(h * dpr); }
    const x = wave.getContext("2d"); x.setTransform(dpr, 0, 0, dpr, 0, 0); x.clearRect(0, 0, w, h);
    micSm += (micLvl - micSm) * 0.25;
    const amp = 2 + micSm * h * 0.9, acc = getComputedStyle(document.body).getPropertyValue("--acc").trim();
    for (let k = 0; k < 3; k++) {
      x.beginPath();
      for (let i = 0; i <= w; i += 2) {
        const p = i / w, env = Math.sin(p * Math.PI);
        const y = h / 2 + Math.sin(p * (10 + k * 4) + t / (180 - k * 40)) * amp * env * (1 - k * 0.28) * Math.sin(t / 300 + k);
        i ? x.lineTo(i, y) : x.moveTo(i, y);
      }
      x.strokeStyle = k ? acc + "66" : acc; x.lineWidth = k ? 1 : 1.8; x.shadowColor = acc; x.shadowBlur = k ? 0 : 8; x.stroke();
    }
    x.shadowBlur = 0;
  }
  requestAnimationFrame(drawWave);
  function mic(v) { micLvl = Math.min(1, v * 1.6); }

  // ---------------------------------------------------------------- weather icon by WMO code
  function weatherIcon(code) {
    const id = code == null ? "w-partly" : code === 0 ? "w-sun" : code <= 2 ? "w-partly" : code === 3 ? "w-cloud" : code <= 48 ? "w-fog"
      : code <= 67 || (code >= 80 && code <= 82) ? "w-rain" : code <= 77 || code === 85 || code === 86 ? "w-snow" : "w-storm";
    const u = q("#w-icon use"); if (u) u.setAttribute("href", "#" + id);
  }

  // ---------------------------------------------------------------- clock seconds ring + greeting
  function clockTick(n) {
    const r = q("#sec-ring"); if (r) r.style.strokeDashoffset = 163.4 * (1 - (n.getSeconds() + n.getMilliseconds() / 1000) / 60);
    const g = q("#h-greet");
    if (g) { const h = n.getHours(); const part = h < 5 ? "Доброй ночи" : h < 12 ? "Доброе утро" : h < 18 ? "Добрый день" : "Добрый вечер";
      const name = (typeof S !== "undefined" && S.config && (S.config.user.name || S.config.user.address)) || "сэр"; g.textContent = `${part}, ${name}.`; }
  }

  // ---------------------------------------------------------------- reactor chips (engine info + latency)
  function chips(st) {
    const c = st && st.config; if (!c) return;
    const set = (id, t) => { const e = q(id); if (e) e.textContent = t; };
    set("#chip-stt", "STT " + ((c.stt.engine || "gigaam") === "gigaam" ? "GigaAM v3" : "Whisper"));
    set("#chip-tts", "TTS " + ({ silero: "Silero", xtts: "XTTS", edge: "Edge", sapi: "SAPI" }[c.voice.engine] || c.voice.engine));
    const p = (typeof S !== "undefined" && S.providers && S.providers[c.ai.provider]) || {};
    set("#chip-ai", "AI " + (p.title || c.ai.provider).replace("Мост: ", "").slice(0, 18));
  }
  function latency(ms) { const e = q("#chip-lat"); if (e && ms) e.textContent = `${ms} ms`; }

  // ---------------------------------------------------------------- boot sequence (once per app start)
  function boot(st) {
    const el = q("#boot"); if (!el) return;
    let seen = false; try { seen = sessionStorage.getItem("jarvis.booted"); } catch (e) {}
    if (seen) { el.remove(); return; }
    try { sessionStorage.setItem("jarvis.booted", "1"); } catch (e) {}
    const c = st.config, p = (S.providers || {})[c.ai.provider] || {};
    const lines = [
      ["Ядро J.A.R.V.I.S.", "OK"],
      ["Распознавание речи · " + ((c.stt.engine || "gigaam") === "gigaam" ? "GigaAM v3" : "Whisper"), st.stt.model ? "OK" : "ЗАГРУЗКА"],
      ["Синтез речи · " + ({ silero: "Silero", xtts: "XTTS", edge: "Edge" }[c.voice.engine] || c.voice.engine), "OK"],
      ["Нейросеть · " + (p.title || c.ai.provider), "OK"],
      ["Планировщик и напоминания", "OK"],
      ["Управление ПК · " + (st.apps || "приложения"), "OK"],
      ["Протоколы безопасности", "OK"],
    ];
    const box = q("#boot-lines"), bar = q("#boot-bar");
    let i = 0, finished = false;
    const finish = () => {
      if (finished) return; finished = true;
      box.innerHTML += '<div class="boot-online">ONLINE</div>';
      setTimeout(() => { el.classList.add("done"); if (window.reactor) { reactor.boot = 0; reactor.pulse(); } sound("ok"); setTimeout(() => el.remove(), 900); navGlow(); }, 650);
    };
    el.addEventListener("click", () => { clearInterval(tm); finish(); });
    const tm = setInterval(() => {
      if (i >= lines.length) { clearInterval(tm); finish(); return; }
      const [a, b] = lines[i++];
      box.insertAdjacentHTML("beforeend", `<div><span>${esc(a)}</span><em class="${b === "OK" ? "" : "warn"}">${esc(b)}</em></div>`);
      bar.style.width = (i / lines.length) * 100 + "%";
      sound("hover");
    }, 230);
  }

  function setSounds(on) { soundsOn = on !== false; }
  return { sound, setTheme, navGlow, type, pushStats, drawSpark, mic, weatherIcon, clockTick, chips, latency, boot, setSounds, THEMES };
})();
