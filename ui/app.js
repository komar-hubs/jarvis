// Core UI plumbing: RPC, WebSocket events, tabs, home dashboard, chat, command bar.
const TOKEN = window.JARVIS_TOKEN;
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// inline icon from the sprite in index.html; system emoji render differently on every PC and clash with the HUD
const I = (name, cls = "") => `<svg class="ic ${cls}"><use href="#i-${name}"/></svg>`;
const S = { config: null, providers: {}, actionTypes: {}, sourceTypes: {}, tasks: [], timers: [], reminders: [], stats: null, tab: "home" };
const STATE_LABEL = { idle: "ожидание", listening: "слушаю", thinking: "обрабатываю", speaking: "говорю", loading: "загрузка", error: "ошибка" };

async function rpc(method, params = {}) {
  const r = await fetch("/api/rpc", { method: "POST", headers: { "Content-Type": "application/json", "X-Jarvis-Token": TOKEN }, body: JSON.stringify({ method, params }) });
  const j = await r.json();
  if (!j.ok) { toast(j.error || "Ошибка", "Ошибка", true); throw new Error(j.error); }
  return j.result;
}

function winApi(fn, ...a) {
  if (fn === "minimize" || fn === "hide") {
    // the window hides under the cursor: the pressed button would come back with its focus ring and hover lit
    document.activeElement?.blur?.();
    document.body.classList.add("no-hover");
    setTimeout(() => addEventListener("mousemove", () => document.body.classList.remove("no-hover"), { once: true }), 600);
  }
  if (window.pywebview && window.pywebview.api && window.pywebview.api[fn]) return window.pywebview.api[fn](...a);
  return null;
}

function toast(text, title = "J.A.R.V.I.S.", err = false, ms = 4500, action = null) {
  // the same message twice in a row (a job's result reported by two paths) shows once
  const key = title + "\n" + text;
  if (!action && toast.last === key && Date.now() - toast.at < 3000) return;
  toast.last = key; toast.at = Date.now();
  const d = document.createElement("div");
  d.className = "toast" + (err ? " err" : "") + (action ? " act" : "");
  d.innerHTML = `<b>${esc(title)}</b>${esc(text)}` + (action ? `<button class="btn toast-btn">${esc(action.label)}</button>` : "");
  if (action) $(".toast-btn", d).onclick = () => { action.fn(); d.remove(); };
  $("#toasts").appendChild(d);
  setTimeout(() => { d.classList.add("out"); setTimeout(() => d.remove(), 300); }, ms);
}
// dates in local time: toISOString() is UTC, so before 03:00 (Moscow) "today" was still yesterday
// and today's tasks showed up only at three in the morning
const pad2 = (n) => String(n).padStart(2, "0");
function localDay(d = new Date()) { return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`; }
function parseLocal(iso) {
  if (!iso) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/.exec(iso);
  return m ? new Date(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0)) : new Date(iso);
}

// ---------------------------------------------------------------- tabs
const tabLoaders = {};
function go(tab) {
  S.tab = tab;
  $$("#nav > button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + tab));
  if (tabLoaders[tab]) tabLoaders[tab]();
  FX.navGlow(); FX.sound("tab");
  try { localStorage.setItem("jarvis.tab", tab); } catch (e) {}
}
$$("#nav > button").forEach((b) => b.addEventListener("click", () => go(b.dataset.tab)));

// ---------------------------------------------------------------- websocket
let ws, wsRetry = 0;
function connect() {
  ws = new WebSocket(`ws://${location.host}/ws?t=${TOKEN}`);
  ws.onopen = () => { wsRetry = 0; };
  ws.onmessage = (e) => { try { onEvent(JSON.parse(e.data)); } catch (err) { console.error(err); } };
  ws.onclose = () => { setTimeout(connect, Math.min(5000, 500 * ++wsRetry)); };
}

let subtitleTimer;
function onEvent(ev) {
  switch (ev.type) {
    case "state":
      if (ev.dictation !== undefined) S.dictation = ev.dictation;
      if (ev.conversation !== undefined) S.conversation = ev.conversation;
      S.followUntil = ev.follow ? Date.now() + (ev.follow_left || 0) * 1000 : 0;
      setState(ev.state, ev.sleeping, ev.follow);
      break;
    case "focus": S.focus = ev.minutes ? ev : null; if (window.hubFocus) hubFocus(); break;
    case "hub": if (window.hubChanged) hubChanged(ev); break;
    case "game_voice": if (window.gamePanel && $("#game-panel")) gamePanel(); break;   // a phrase taught by voice
    case "party": if (window.party) party(ev.seconds || 10, ev.birthday); break;
    case "recording": document.body.classList.toggle("recording", !!ev.on); toast(ev.on ? "Идёт запись экрана" : "Запись остановлена", "Запись"); break;
    case "files_found":
      if (ev.files.length) toast(ev.files.slice(0, 5).map((f) => f.split("\\").pop()).join("\n"), `Найдено: ${ev.files.length}`, false, 7000);
      break;
    case "mic_level":
      reactor.setMic(ev.v); FX.mic(ev.v);
      $("#bar-level").style.height = Math.min(100, ev.v * 160) + "%";
      $$(".mic-meter").forEach((m) => (m.style.width = Math.min(100, ev.v * 160) + "%"));
      break;
    case "tts_level": reactor.setVoice(Math.min(1, ev.v)); break;
    case "heard":
      if (ev.meta && ev.meta.target === "test") { const t = $("#setup-heard"); if (t) t.textContent = "«" + ev.text + "»"; }
      // background speech without the wake word stays private unless the user enables the debug view
      if (S.config && S.config.stt.show_ignored) { $("#heard").textContent = ev.text; $("#heard").classList.remove("ok"); logLine(`… ${ev.text}`, "ign"); }
      break;
    case "transcript":
      $("#heard").textContent = "«" + ev.text + "»"; $("#heard").classList.add("ok");
      logLine(`Вы: ${ev.text}` + (ev.stt_ms ? `  (${ev.stt_ms} мс)` : ""), "you");
      break;
    case "jarvis_say":
      FX.type(ev.text);
      logLine(`Джарвис: ${ev.text}`, "jv");
      break;
    case "log": logLine(ev.msg, ev.level); break;
    case "stats": S.stats = ev; renderStats(ev); FX.pushStats(ev); break;
    case "toast": toast(ev.text, ev.title || "J.A.R.V.I.S."); FX.sound("toast"); break;
    case "theme": FX.setTheme(ev.name, true); if (S.config) S.config.theme = ev.name; break;
    case "notice": noticeShow(ev); break;
    case "commands": if (S.tab === "commands" && window.loadCmds) loadCmds(); break;   // taught by voice
    case "phone": if (S.tab === "settings" && window.phonePanel) phonePanel(); break;
    case "persona":   // the voice and wake words changed on the server too: take the fresh config
      applyPersona(ev.id);
      rpc("state").then((st) => { S.config = st.config; if (S.tab === "settings") tabLoaders.settings(); });
      break;
    case "night": setNight(ev.on); if (S.config) S.config.night = { ...(S.config.night || {}), enabled: ev.on }; break;
    case "briefing": briefShow(ev); break;
    case "update":
      if (typeof updShow === "function") updShow(ev);
      if (ev.status === "available") toast(`Доступна версия ${ev.latest}. Настройки → Обновления`, "Обновление");
      if (ev.status === "error") toast("Обновление не удалось: " + (ev.error || ""), "Обновление", true);
      break;
    case "show_tab": go(ev.tab); break;
    case "planner": plannerChanged(ev.what); break;
    case "chat_msg": chatOnMsg(ev.msg); break;
    case "chat_stream": chatOnStream(ev); break;
    case "bridge": if (window.bridgeEvent) bridgeEvent(ev); break;
    case "weather": renderWeather(ev); break;
    case "fired": break;  // shown once, as the notice card with "Готово" / "Отложить" (the toast was a duplicate)
    case "stt_status": if (ev.status === "loading") logLine("Загружаю модель распознавания речи…", "warn"); break;
  }
}

function setState(st, sleeping, follow) {
  document.body.dataset.state = st;
  document.body.classList.toggle("sleeping", !!sleeping);
  reactor.setState(st);
  let label = sleeping ? "РЕЖИМ СНА" : (STATE_LABEL[st] || st).toUpperCase();
  if (follow && st === "idle" && !sleeping) label = "ГОВОРИТЕ БЕЗ ИМЕНИ";
  if (S.conversation && st === "idle" && !sleeping) label = "ДИАЛОГ · БЕЗ ИМЕНИ";
  if (S.dictation && !sleeping) label = "ДИКТОВКА";
  document.body.classList.toggle("conversation", !!S.conversation && !sleeping);
  document.body.classList.toggle("follow", !!follow && !sleeping);
  const cb = $("#conv-btn"); if (cb) cb.classList.toggle("on", !!S.conversation);
  $("#reactor-label").textContent = label;
  $("#tb-state").textContent = label;
  $("#bar-state").textContent = label.toLowerCase();
  $("#bar-mic").classList.toggle("on", st === "listening");
  $("#sleep-btn").classList.toggle("on", !!sleeping);
  if (st !== "listening") $("#chat-mic").classList.remove("on");
}

function logLine(msg, level = "info") {
  const box = $("#home-log");
  const d = document.createElement("div");
  d.className = level;
  const t = new Date().toTimeString().slice(0, 8);
  d.textContent = `${t}  ${msg}`;
  box.appendChild(d);
  while (box.children.length > 150) box.firstChild.remove();
  box.scrollTop = box.scrollHeight;
}

// ---------------------------------------------------------------- home
const WD = ["воскресенье", "понедельник", "вторник", "среда", "четверг", "пятница", "суббота"];
const MON = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
function tickClock() {
  const n = new Date();
  if (S.followUntil && !S.conversation) {
    const left = Math.ceil((S.followUntil - Date.now()) / 1000);
    if (left > 0 && document.body.dataset.state === "idle") $("#reactor-label").textContent = `ГОВОРИТЕ БЕЗ ИМЕНИ · ${left}`;
  }
  const hm = n.toTimeString().slice(0, 5);
  $("#h-time").textContent = hm;
  $("#tb-clock").textContent = n.toTimeString().slice(0, 8);
  FX.clockTick(n);
  $("#h-date").textContent = `${WD[n.getDay()]}, ${n.getDate()} ${MON[n.getMonth()]}`;
  renderTimersLive();
}
setInterval(tickClock, 250);

function setGauge(id, pct, hot) {
  const g = $(id);
  if (!g) return;
  const v = Math.max(0, Math.min(100, pct || 0));
  $(".g-fg", g).style.strokeDashoffset = 251.3 * (1 - v / 100);
  $("b", g).textContent = Math.round(v) + "%";
  g.classList.toggle("hot", v > (hot || 85));
}
function renderStats(s) {
  setGauge("#g-cpu", s.cpu); setGauge("#g-ram", s.ram, 90); setGauge("#g-gpu", s.gpu ? s.gpu.util : 0);
  const lines = [];
  lines.push(`RAM  <b>${s.ram_used} / ${s.ram_total} ГБ</b>`);
  if (s.gpu) lines.push(`GPU  <b>${s.gpu.temp}°C · ${(s.gpu.mem_used / 1024).toFixed(1)}/${(s.gpu.mem_total / 1024).toFixed(0)} ГБ</b>`);
  lines.push(`DISK <b>${s.disk_free} ГБ свободно</b>`);
  if (s.battery != null) lines.push(`BAT  <b>${s.battery}%${s.plugged ? " · зарядка" : ""}</b>`);
  if (s.volume != null) lines.push(`VOL  <b>${s.volume}%</b>`);
  lines.push(`UP   <b>${s.uptime_h} ч · ${s.procs} проц.</b>`);
  $("#sys-lines").innerHTML = lines.join("<br>");
}
function renderWeather(w) {
  $("#w-temp").textContent = (w.temp > 0 ? "+" : "") + w.temp + "°";
  $("#w-desc").textContent = w.desc;
  $("#w-city").textContent = w.city || "";
  $("#w-sub").textContent = `ощущается ${w.feels}° · ветер ${w.wind} м/с · осадки ${w.rain ?? 0}%`;
  FX.weatherIcon(w.code);
  $("#w-min").textContent = (w.min > 0 ? "+" : "") + w.min + "°";
  $("#w-max").textContent = (w.max > 0 ? "+" : "") + w.max + "°";
  const pos = Math.max(0, Math.min(1, (w.temp - w.min) / Math.max(1, w.max - w.min)));
  $("#w-bar").style.left = "0"; $("#w-bar").style.right = (100 - Math.max(6, pos * 100)) + "%";
}
async function loadWeather() { try { renderWeather(await rpc("weather")); } catch (e) {} }

function fmtLeft(s) {
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0");
}
function timerLeft(t) { return t.paused_left != null ? t.paused_left : Math.max(0, t.end - Date.now() / 1000); }
function renderTimersLive() {
  // the list used to be rebuilt 4 times a second, so nothing in it could be clicked: build it only when the set of
  // timers changes and update the numbers in place
  const box = $("#home-timers");
  const key = S.timers.map((t) => t.id + (t.paused_left != null ? "p" : "")).join();
  if (box.dataset.key !== key) {
    box.dataset.key = key;
    box.innerHTML = S.timers.length ? S.timers.map((t) => `<div class="ht timer-row" data-id="${t.id}">
      <span class="tl-name">${esc(t.label || "Таймер")}</span><i class="tbar"><b></b></i><span class="tm"></span>
      <span class="row-btns"><button class="ib" title="${t.paused_left != null ? "Продолжить" : "Пауза"}" onclick="rpc('timer_pause',{id:'${t.id}',pause:${t.paused_left == null}})">${I(t.paused_left != null ? "play" : "pause")}</button>
      <button class="ib del" title="Отменить" onclick="timerDel('${t.id}')">${I("x")}</button></span></div>`).join("")
      : '<div class="empty">нет активных — «Джарвис, таймер на 10 минут»</div>';
  }
  S.timers.forEach((t) => {
    const el = box.querySelector(`[data-id="${t.id}"]`);
    if (!el) return;
    const left = timerLeft(t);
    $(".tm", el).textContent = fmtLeft(left) + (t.paused_left != null ? " пауза" : "");
    $(".tbar b", el).style.width = Math.max(0, Math.min(100, (left / t.duration) * 100)) + "%";
    el.classList.toggle("soon", left < 60 && t.paused_left == null);
  });
  if (window.renderTimerRings) renderTimerRings();
  const b = $("#badge-timers"); b.textContent = S.timers.length; b.classList.toggle("on", S.timers.length > 0);
}
async function timerDel(id) {
  const t = S.timers.find((x) => x.id === id);
  S.timers = S.timers.filter((x) => x.id !== id); renderTimersLive();  // instant, the server event follows
  await rpc("timer_cancel", { id });
  if (t) toast(`${t.label || "Таймер"} отменён`, "Таймер", false, 5000, { label: "Вернуть", fn: () => rpc("timer_add", { seconds: Math.max(1, Math.round(timerLeft(t))), label: t.label || "" }) });
}

function dueLabel(iso) {
  if (!iso) return "";
  const d = parseLocal(iso), n = new Date();
  const day = Math.round((new Date(d.toDateString()) - new Date(n.toDateString())) / 864e5);
  const hm = iso.length >= 16 ? `${pad2(d.getHours())}:${pad2(d.getMinutes())}` : "";
  const dl = day === 0 ? "сегодня" : day === 1 ? "завтра" : day === -1 ? "вчера" : `${d.getDate()} ${MON[d.getMonth()].slice(0, 3)}`;
  return (dl + " " + hm).trim();
}
function homeTaskHtml(t) {
  const over = t.due && parseLocal(t.due) < new Date() && (t.due.length >= 16 || t.due.slice(0, 10) < localDay());
  return `<div class="ht task-row ${over ? "over" : ""}" data-id="${t.id}">
    <button class="ib chk" title="Выполнено" onclick="taskDone('${t.id}', this)">${I("check")}</button>
    <span class="tt-name" ondblclick="taskEdit('${t.id}')">${esc(t.text)}</span><span class="due">${dueLabel(t.due)}</span>
    <button class="ib del" title="Удалить" onclick="taskDel('${t.id}')">${I("x")}</button></div>`;
}
function renderHomePlanner() {
  const today = localDay(), tomorrow = localDay(new Date(Date.now() + 864e5));
  const open = S.tasks.filter((t) => !t.done);
  const td = open.filter((t) => !t.due || t.due.slice(0, 10) <= today);
  const tm = open.filter((t) => t.due && t.due.slice(0, 10) === tomorrow);
  $("#home-tasks").innerHTML = (td.length ? td.slice(0, 10).map(homeTaskHtml).join("") : '<div class="empty">задач на сегодня нет</div>')
    + (td.length > 10 ? `<div class="more" onclick="go('tasks')">ещё ${td.length - 10} →</div>` : "")
    + (tm.length ? `<div class="sub-h">Завтра</div>` + tm.slice(0, 4).map(homeTaskHtml).join("") : "");
  const rs = S.reminders.filter((r) => r.active).sort((a, b) => a.at.localeCompare(b.at)).slice(0, 4);
  $("#home-rem").innerHTML = rs.length ? rs.map((r) => `<div class="ht"><span>${I(r.alarm ? "alarm" : "bell", "lead")}${esc(r.text)}</span><span class="due">${dueLabel(r.at)}</span>
    <button class="ib del" title="Удалить" onclick="rpc('reminder_delete',{id:'${r.id}'})">${I("x")}</button></div>`).join("") : '<div class="empty">нет</div>';
  const b = $("#badge-tasks"); const n = open.filter((t) => t.due && t.due.slice(0, 10) <= today).length;
  b.textContent = n; b.classList.toggle("on", n > 0);
}

// ---------------------------------------------------------------- night mode
function setNight(on, sounds) {
  document.body.classList.toggle("night", on);
  const want = sounds !== undefined ? sounds : (S.config ? S.config.ui_sounds !== false : true);
  FX.setSounds(want && !on);  // no interface clicks at night
}

// ---------------------------------------------------------------- fired reminder / timer card
function noticeShow(n) {
  let el = $("#notice-card");
  if (!n || !n.kind) { if (el) el.remove(); return; }
  if (!el) { el = document.createElement("div"); el.id = "notice-card"; document.body.appendChild(el); }
  const icon = { timer: "timer", alarm: "alarm", reminder: "bell", task: "checkbox" }[n.kind] || "bell";
  el.className = "notice-card k-" + n.kind;
  el.innerHTML = `<div class="nc-ico">${I(icon)}</div><div class="nc-body"><div class="nc-t">${esc(n.title || "")}</div>
    <div class="nc-x">${esc(n.text || "")}</div><div class="nc-b">
    <button class="primary" onclick="noticeAct('done')">Готово</button>
    ${n.kind !== "task" ? `<button class="btn" onclick="noticeAct('snooze',5)">+5 мин</button><button class="btn" onclick="noticeAct('snooze',10)">+10 мин</button><button class="btn" onclick="noticeAct('snooze',30)">+30 мин</button>` : ""}
    </div><div class="nc-h">или скажите «отложи на 10 минут» / «готово»</div></div>`;
  FX.sound("toast");
}
async function noticeAct(action, minutes) {
  noticeShow(null);
  const r = await rpc("notice", { action, minutes });
  if (action === "snooze") toast(r, "Отложено");
}

// ---------------------------------------------------------------- briefing card
const BRIEF_ICO = { weather: "cloud", tasks: "checkbox", warn: "warn", ok: "check", bell: "bell", timer: "timer", money: "ruble", update: "up" };
function briefShow(b) {
  let el = $("#brief-card");
  if (!el) { el = document.createElement("div"); el.id = "brief-card"; document.body.appendChild(el); }
  el.innerHTML = `<div class="bc-head"><div><div class="bc-t">${esc(b.title)}</div><div class="bc-d">${esc(b.date || "")}</div></div>
    <button class="btn" onclick="this.closest('#brief-card').remove()">${I("x")}</button></div>
    ${(b.items || []).map(([ico, t, line], i) => `<div class="bc-row k-${ico}" style="animation-delay:${0.15 + i * 0.12}s">
      <span class="bc-i">${I(BRIEF_ICO[ico] || "diamond")}</span><div><b>${esc(t)}</b><span>${esc(line)}</span></div></div>`).join("")
      || '<div class="empty">Сегодня всё спокойно.</div>'}`;
  clearTimeout(briefShow.t);
  briefShow.t = setTimeout(() => el.remove(), 90000);
}

async function plannerChanged(what) {
  if (what === "tasks" || what === "all") S.tasks = await rpc("tasks");
  if (what === "timers" || what === "all") S.timers = await rpc("timers");
  if (what === "reminders" || what === "all") S.reminders = await rpc("reminders");
  if (what === "stopwatch" && window.swRefresh) swRefresh();
  renderHomePlanner();
  if (window.renderTasks && S.tab === "tasks") renderTasks();
  if (window.renderRems && S.tab === "time") renderRems();
  renderTimersLive();
}

$("#reactor").addEventListener("click", () => rpc("listen", { target: "voice" }));
$("#bar-mic").addEventListener("click", () => rpc("listen", { target: "voice" }));
$("#sleep-btn").addEventListener("click", () => rpc("sleep", { on: !document.body.classList.contains("sleeping") }));
$("#conv-btn").addEventListener("click", () => rpc("conversation", {}));
$("#bar-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.value.trim()) {
    rpc("execute", { text: e.target.value.trim() });
    logLine("Вы: " + e.target.value.trim(), "you");
    e.target.value = "";
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") rpc("tts_stop");
});

// ---------------------------------------------------------------- chat
let streamEl = null, streamText = "";
function mdLite(s) {
  let h = esc(s);
  h = h.replace(/```(\w*)\n?([\s\S]*?)```/g, (m, l, c) => `<pre>${c}</pre>`);
  h = h.replace(/`([^`]+)`/g, "<code>$1</code>");
  h = h.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  const link = (url, label) => `<a href="#" data-url="${url}" onclick="rpc('open',{target:this.dataset.url});return false" style="color:var(--cyan)">${label}</a>`;
  // markdown links first ([2ГИС](https://...)), then bare URLs that are not already inside a link
  h = h.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (m, label, url) => link(url, label));
  h = h.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, (m, pre, url) => pre + link(url, url.length > 60 ? url.slice(0, 57) + "…" : url));
  return h;
}
function hideTags(s) {
  return s.replace(/<jarvis>[\s\S]*?(<\/jarvis>|$)/g, "").replace(/\{\s*"tool"[\s\S]*?(\}|$)/g, "")
    .replace(/<(?:j(?:a(?:r(?:v(?:i(?:s)?)?)?)?)?)?$/, "").replace(/<\/?jarvis>/g, "").trim();
}
function msgEl(m) {
  const d = document.createElement("div");
  const err = m.source === "error";
  d.className = "msg " + (m.role === "user" ? "user" : "assistant") + (err ? " error" : "");
  const src = { voice: "голосом", bridge: "через мост", ai: m.provider || "ИИ", text: "", error: "" }[m.source] ?? "";
  const time = (m.ts || "").slice(11, 16);
  d.innerHTML = mdLite(m.content.replace(/^\s*[⚠✎]️?\s*/, "")) +
    (m.tools && m.tools.length ? `<div class="tools">${m.tools.map((t) => `<span>${I("gear")}${esc(t)}</span>`).join("")}</div>` : "") +
    `<span class="meta">${esc(time)} ${esc(m.agent || src)}</span>`;
  return d;
}
function chatScroll() { const l = $("#chat-list"); l.scrollTop = l.scrollHeight; }
async function chatLoad() {
  const items = await rpc("chat_history");
  const l = $("#chat-list");
  l.innerHTML = "";
  if (!items.length) l.innerHTML = '<div class="empty" style="text-align:center;margin-top:20%">Чат пуст. Спросите что-нибудь — или скажите «Джарвис, …»</div>';
  items.forEach((m) => l.appendChild(msgEl(m)));
  chatScroll();
}
function chatOnMsg(m) {
  if (m.role === "assistant" && m.source === "ai" && streamEl) { streamEl.replaceWith(msgEl(m)); streamEl = null; chatScroll(); return; }
  const l = $("#chat-list");
  const e = l.querySelector(".empty"); if (e) e.remove();
  l.appendChild(msgEl(m));
  chatScroll();
}
function chatOnStream(ev) {
  // mirror AI answers into the home-screen subtitle with the typewriter
  if (ev.state === "start") FX.type("");
  else if (ev.state === "chunk") FX.type(ev.text, true);
  else if (ev.state === "end") FX.latency(ev.ms);
  const l = $("#chat-list");
  if (ev.state === "start") {
    streamText = "";
    streamEl = document.createElement("div");
    streamEl.className = "msg assistant streaming";
    streamEl.innerHTML = '<span style="color:var(--dim)">думаю…</span>';
    l.appendChild(streamEl); chatScroll();
  } else if (ev.state === "tool" && streamEl) {
    const [ico, label] = { WebSearch: ["search", "ищу в интернете…"], WebFetch: ["globe", "читаю страницу…"], Read: ["eye", "смотрю…"] }[ev.name] || ["gear", ev.name + "…"];
    streamEl.innerHTML = mdLite(hideTags(streamText)) + `<div class="tools"><span>${I(ico)}${esc(label)}</span></div>`;
    chatScroll();
  } else if (ev.state === "chunk" && streamEl) {
    streamText += ev.text;
    streamEl.innerHTML = mdLite(hideTags(streamText)) || '<span style="color:var(--dim)">выполняю…</span>';
    chatScroll();
  } else if (ev.state === "end" || ev.state === "error") {
    if (streamEl) {
      streamEl.classList.remove("streaming");
      if (ev.state === "error" || !hideTags(streamText)) { streamEl.remove(); streamEl = null; }  // tool-only turn
    }
  }
}
function chatSend() {
  const ta = $("#chat-text");
  const t = ta.value.trim();
  if (!t) return;
  ta.value = ""; ta.style.height = "";
  rpc("chat_send", { text: t });
}
async function chatClear() { await rpc("chat_clear"); chatLoad(); }
$("#chat-send").addEventListener("click", chatSend);
$("#chat-text").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); chatSend(); } });
$("#chat-text").addEventListener("input", (e) => { e.target.style.height = "auto"; e.target.style.height = Math.min(180, e.target.scrollHeight) + "px"; });
$("#chat-mic").addEventListener("click", () => { $("#chat-mic").classList.add("on"); rpc("listen", { target: "chat" }); });

function fillProviderSelect(sel, value) {
  sel.innerHTML = Object.entries(S.providers).map(([id, p]) => `<option value="${id}">${esc(p.title)}</option>`).join("");
  sel.value = value;
}
async function chatHeadInit() {
  const c = S.config;
  fillProviderSelect($("#chat-provider"), c.ai.provider);
  chatModelField();
  $("#chat-speak").checked = c.ai.speak_chat;
}
function chatModelField() {
  const pid = $("#chat-provider").value;
  const p = { ...S.providers[pid], ...(S.config.ai.providers[pid] || {}) };
  $("#chat-model").value = p.model || "";
  $("#chat-model").placeholder = p.kind === "bridge" ? "модель (пусто = по умолчанию агента)" : "модель";
  $("#chat-models").innerHTML = "";
  rpc("models", { pid }).then((ms) => { $("#chat-models").innerHTML = ms.map((m) => `<option value="${esc(m)}">`).join(""); }).catch(() => {});
}
async function saveConfig(patch) { S.config = await rpc("config_set", { patch }); return S.config; }
$("#chat-provider").addEventListener("change", async (e) => { await saveConfig({ ai: { provider: e.target.value } }); chatModelField(); toast("Провайдер: " + S.providers[e.target.value].title); });
$("#chat-model").addEventListener("change", async (e) => { const pid = $("#chat-provider").value; await saveConfig({ ai: { providers: { [pid]: { model: e.target.value.trim() } } } }); toast("Модель: " + e.target.value); });
$("#chat-speak").addEventListener("change", (e) => saveConfig({ ai: { speak_chat: e.target.checked } }));
tabLoaders.chat = () => { chatLoad(); setTimeout(() => $("#chat-text").focus(), 100); };

// ---------------------------------------------------------------- persona: Jarvis or Friday
const PERSONA_UI = {
  jarvis: { logo: "J.A.R.V.I.S.", sub: "Just A Rather Very Intelligent System",
            ring: "J.A.R.V.I.S. • JUST A RATHER VERY INTELLIGENT SYSTEM • STARK INDUSTRIES • NEURAL INTERFACE ONLINE •" },
  friday: { logo: "F.R.I.D.A.Y.", sub: "Female Replacement Intelligent Digital Assistant Youth",
            ring: "F.R.I.D.A.Y. • FEMALE REPLACEMENT INTELLIGENT DIGITAL ASSISTANT YOUTH • STARK INDUSTRIES • ONLINE •" },
  ultron: { logo: "ULTRON", sub: "Global Peacekeeping Initiative",
            ring: "ULTRON • THERE ARE NO STRINGS ON ME • GLOBAL PEACEKEEPING INITIATIVE • EVOLUTION IS INEVITABLE •" },
};
const PERSONA_WAKE = { jarvis: "Джарвис", friday: "Пятница", ultron: "Альтрон" };
function applyPersona(id) {
  const p = PERSONA_UI[id] || PERSONA_UI.jarvis;
  S.persona = id in PERSONA_UI ? id : "jarvis";
  $("#logo-name").textContent = p.logo;
  $("#logo-sub").textContent = p.sub;
  $("#ring-text").textContent = p.ring;
  const bt = $(".boot-title"); if (bt) bt.textContent = p.logo;
  $("#bar-input").placeholder = `Скажите «${PERSONA_WAKE[S.persona]}…» или введите команду: открой телеграм, таймер на 5 минут, что у меня на экране`;
  document.body.dataset.persona = S.persona;
}

// ---------------------------------------------------------------- boot
async function boot() {
  const st = await rpc("state");
  S.config = st.config; S.providers = st.providers; S.actionTypes = st.action_types; S.sourceTypes = st.source_types; S.boot = st;
  S.conversation = !!st.conversation; S.focus = st.focus || null;
  applyPersona((st.config.persona || {}).id || "jarvis");
  setState(st.state, st.sleeping);
  if (st.config.theme) FX.setTheme(st.config.theme);
  setNight(!!st.night, st.config.ui_sounds);
  if (st.notice) noticeShow(st.notice);
  FX.chips(st); FX.boot(st); setTimeout(FX.navGlow, 50);
  st.logs.forEach((l) => logLine(l.msg, l.level));
  connect();
  chatHeadInit();
  plannerChanged("all");
  loadWeather(); setInterval(loadWeather, 15 * 60 * 1000);
  rpc("stats").then(renderStats);
  let tab = "home"; try { tab = localStorage.getItem("jarvis.tab") || "home"; } catch (e) {}
  go(tab);
  if (!st.config.setup_done) setupOpen();
}
window.addEventListener("load", boot);
