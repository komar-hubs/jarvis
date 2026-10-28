// "Сегодня" tab: focus mode, time per program, habits, clipboard history, messengers; plus the party effect.
const CAT_COLOR = { "Игры": "#ff5d6c", "Браузер": "#4fd8ff", "Общение": "#9b86ff", "Разработка": "#3ff0a2", "Творчество": "#ffb347",
  "Медиа": "#ff8ad8", "Работа": "#e4ecf2", "Система": "#7fa6bd", "Другое": "#5b7184" };
let HUB = null, hubTimer = null;

function fmtHM(sec) {
  const m = Math.round(sec / 60), h = Math.floor(m / 60);
  return h ? `${h} ч ${m % 60} мин` : `${m} мин`;
}

async function hubLoad() {
  try { HUB = await rpc("hub"); } catch (e) { return; }
  S.focus = HUB.focus;
  hubRender();
}
function hubChanged(ev) {
  if (ev.what === "messengers" && HUB) { HUB.messengers = ev.counts || {}; hubMsgs(); return; }
  if (S.tab === "hub") { clearTimeout(hubChanged.t); hubChanged.t = setTimeout(hubLoad, 150); }
  else if (ev.what === "dnd") hubLoad();
}

function hubRender() {
  if (!HUB) return;
  hubFocus();
  // time per program: one stacked bar by category + top programs
  const u = HUB.usage, tot = Math.max(1, u.total);
  $("#usage-total").textContent = u.total ? fmtHM(u.total) + (HUB.yesterday ? ` · вчера ${fmtHM(HUB.yesterday)}` : "") : "";
  $("#cat-bar").innerHTML = u.cats.map((c) => `<i style="flex:${c.sec};background:${CAT_COLOR[c.cat] || "#5b7184"}" title="${esc(c.cat)} ${fmtHM(c.sec)}"></i>`).join("") || '<i style="flex:1;opacity:.15;background:var(--acc)"></i>';
  $("#cat-legend").innerHTML = u.cats.slice(0, 6).map((c) => `<span><b style="background:${CAT_COLOR[c.cat] || "#5b7184"}"></b>${esc(c.cat)} ${Math.round(c.sec * 100 / tot)}%</span>`).join("");
  const max = Math.max(1, ...u.apps.map((a) => a.sec));
  $("#usage-list").innerHTML = u.apps.slice(0, 9).map((a, i) => `<div class="ua" style="animation-delay:${i * 0.04}s">
    <span class="ua-n">${esc(a.name)}</span><i><b style="width:${(a.sec / max) * 100}%;background:${CAT_COLOR[a.cat] || "#5b7184"}"></b></i><span class="ua-t">${fmtHM(a.sec)}</span></div>`).join("")
    || '<div class="empty">Считаю… статистика появится через пару минут работы</div>';
  // habits with a 7-day strip
  $("#habits").innerHTML = HUB.habits.map((h) => `<div class="habit ${h.today ? "done" : ""}">
    <button class="hb-plus" onclick="hubHabit(${esc(JSON.stringify(h.name))}, 1)" title="Отметить">${I(h.today ? "check" : "plus")}</button>
    <div class="hb-name">${esc(h.name)}${h.today > 1 ? ` <small>×${h.today}</small>` : ""}${h.streak > 1 ? ` <span class="streak" title="дней подряд">${I("flame")}${h.streak}</span>` : ""}</div>
    <div class="hb-week">${h.week.map((v, i) => `<i class="${v ? "on" : ""} ${i === 6 ? "today" : ""}"></i>`).join("")}</div>
    <button class="ib" onclick="hubHabit(${esc(JSON.stringify(h.name))}, -1)" title="Убрать отметку">−</button>
    <button class="ib del" onclick="hubHabitDel(${esc(JSON.stringify(h.name))})" title="Удалить привычку">${I("x")}</button></div>`).join("") || '<div class="empty">нет привычек</div>';
  // clipboard
  $("#clip-list").innerHTML = HUB.clipboard.map((c, i) => `<div class="clip" onclick="hubClip(${i}, this)" title="Скопировать снова">
    <span class="clip-t">${esc(c.text.length > 160 ? c.text.slice(0, 160) + "…" : c.text)}</span><span class="clip-m">${esc(c.t)} · ${esc(c.app || "")}</span></div>`).join("")
    || '<div class="empty">Скопируйте что-нибудь — появится здесь</div>';
  hubMsgs();
  $("#hub-active").textContent = `Без перерыва за ПК: ${HUB.active_min} мин.` + (HUB.recording ? " · идёт запись экрана" : "");
  $("#hub-sub").textContent = new Date().toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
}
function hubMsgs() {
  const m = (HUB && HUB.messengers) || {};
  const ent = Object.entries(m);
  $("#hub-msgs").innerHTML = ent.length ? ent.map(([app, n]) => `<div class="ht"><span>${I("chat", "lead")}${esc(app)}</span><span class="tm">${n}</span></div>`).join("")
    : '<div class="empty">непрочитанных не вижу</div>';
}

// ---------------------------------------------------------------- focus
function hubFocus(minutes) {
  if (typeof minutes === "number") { rpc("focus_start", { minutes, task: ($("#focus-task-in") || {}).value || "" }); return; }
  const f = S.focus;
  const panel = $("#focus-panel");
  const badge = $("#badge-focus");
  if (badge) { badge.textContent = f ? "●" : ""; badge.classList.toggle("on", !!f); }
  document.body.classList.toggle("focusing", !!f);
  if (!panel) return;
  panel.classList.toggle("active", !!f);
  $("#focus-ctl").classList.toggle("hidden", !!f);
  $("#focus-stop").classList.toggle("hidden", !f);
  clearInterval(hubTimer);
  if (!f) { $("#focus-time").textContent = "45:00"; $("#focus-task").textContent = "готов к работе"; $("#fr-fg").style.strokeDashoffset = 0; return; }
  $("#focus-task").textContent = f.task || "фокус";
  $("#focus-dist").textContent = f.distractions ? `отвлечений: ${f.distractions}` : "ни одного отвлечения";
  const draw = () => {
    const left = Math.max(0, f.end - Date.now() / 1000);
    $("#focus-time").textContent = fmtLeft(left);
    $("#fr-fg").style.strokeDashoffset = 326.7 * (1 - left / (f.minutes * 60));
  };
  draw(); hubTimer = setInterval(draw, 1000);
}

// ---------------------------------------------------------------- habits, clipboard, misc
async function hubHabit(name, d) { await rpc("habit_add", { name, delta: d }); if (d > 0) FX.sound("toast"); }
async function hubHabitDel(name) { if (confirm(`Удалить привычку «${name}»? История останется.`)) rpc("habit_remove", { name }); }
async function hubHabitNew() { const n = prompt("Новая привычка (например: медитация)"); if (n && n.trim()) { await rpc("habit_add", { name: n.trim(), delta: 0 }); hubLoad(); } }
async function hubClip(i, el) { await rpc("clip_copy", { i }); el.classList.add("copied"); setTimeout(() => el.classList.remove("copied"), 700); toast("Снова в буфере обмена", "Копирование", false, 1800); }
async function hubDnd() { const on = HUB && HUB.dnd_until * 1000 > Date.now(); await rpc("dnd", { minutes: on ? 0 : 120 }); hubLoad(); }
async function hubNet() {
  $("#hub-net").innerHTML = '<div class="dim ai-busy">проверяю соединение… (около 15 секунд)</div>';
  try {
    const r = await rpc("netcheck");
    const t = new Date().toTimeString().slice(0, 5);
    const verdict = r.ping == null && r.mbps == null ? "нет соединения" : (r.mbps ?? 0) >= 50 ? "отлично" : (r.mbps ?? 0) >= 10 ? "нормально" : "медленно";
    $("#hub-net").innerHTML = `<div class="ht"><span>Пинг</span><span class="tm">${r.ping ?? "—"} мс</span></div>
      <div class="ht"><span>Загрузка</span><span class="tm">${r.mbps ?? "—"} Мбит/с</span></div>
      ${r.loss ? '<div class="ht"><span>Потери пакетов</span><span class="tm">есть</span></div>' : ""}
      <div class="hint" style="margin:8px 0 0">${verdict} · проверено в ${t}</div>`;
  } catch (e) { $("#hub-net").innerHTML = '<div class="bad">не удалось проверить</div>'; }
}
tabLoaders.hub = hubLoad;
setTimeout(() => hubFocus(), 1500);  // focus badge after a restart
setInterval(() => { if (S.tab === "hub") hubLoad(); }, 30000);

// ---------------------------------------------------------------- party protocol (and birthday fireworks)
function party(seconds = 10, birthday = false) {
  const cv = document.createElement("canvas");
  cv.className = "party-cv";
  document.body.appendChild(cv);
  const x = cv.getContext("2d");
  const W = (cv.width = innerWidth), H = (cv.height = innerHeight);
  const cols = ["#4fd8ff", "#ff4b5c", "#ffb347", "#3ff0a2", "#9b86ff", "#ff8ad8", "#fff"];
  const parts = [];
  const burst = (bx, by, n = 70) => { for (let i = 0; i < n; i++) { const a = Math.random() * 6.283, v = 2 + Math.random() * 6; parts.push({ x: bx, y: by, vx: Math.cos(a) * v, vy: Math.sin(a) * v - 2, c: cols[i % cols.length], life: 1, s: 2 + Math.random() * 3, rot: Math.random() * 6 }); } };
  for (let i = 0; i < 120; i++) parts.push({ x: Math.random() * W, y: -Math.random() * H, vx: (Math.random() - 0.5) * 2, vy: 2 + Math.random() * 3, c: cols[i % cols.length], life: 2, s: 3 + Math.random() * 4, rot: Math.random() * 6, conf: true });
  document.body.classList.add("party");
  if (birthday) { const b = document.createElement("div"); b.className = "party-banner"; b.textContent = "С ДНЁМ РОЖДЕНИЯ!"; document.body.appendChild(b); setTimeout(() => b.remove(), seconds * 1000); }
  const end = performance.now() + seconds * 1000;
  let lastBurst = 0, th = 0;
  const themes = Object.keys(FX.THEMES);
  function frame(t) {
    x.clearRect(0, 0, W, H);
    if (t - lastBurst > 650 && t < end - 800) { lastBurst = t; burst(W * (0.15 + Math.random() * 0.7), H * (0.15 + Math.random() * 0.4)); FX.setTheme(themes[th++ % themes.length]); }
    for (const p of parts) {
      p.x += p.vx; p.y += p.vy; p.vy += p.conf ? 0.02 : 0.12; p.rot += 0.1;
      p.life -= p.conf ? 0.002 : 0.014;
      if (p.life <= 0) continue;
      x.globalAlpha = Math.min(1, p.life); x.fillStyle = p.c;
      x.save(); x.translate(p.x, p.y); x.rotate(p.rot); x.fillRect(-p.s / 2, -p.s / 4, p.s, p.s / 2); x.restore();
    }
    if (window.reactor) reactor.setVoice(0.4 + 0.5 * Math.abs(Math.sin(t / 180)));
    if (t < end) requestAnimationFrame(frame);
    else { cv.remove(); document.body.classList.remove("party"); FX.setTheme((S.config && S.config.theme) || "jarvis"); if (window.reactor) reactor.setVoice(0); }
  }
  requestAnimationFrame(frame);
}
