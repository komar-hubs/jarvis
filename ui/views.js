// Tabs: tasks, time, commands, sources, bridges, settings, help.

// ================================================================ TASKS
async function taskAdd() {
  const text = $("#t-text").value.trim();
  if (!text) return;
  const due = $("#t-due").value || null;
  await rpc("task_add", { text, due, priority: +$("#t-prio").value, parse: !due });
  $("#t-text").value = ""; $("#t-due").value = "";
}
$("#t-text").addEventListener("keydown", (e) => { if (e.key === "Enter") taskAdd(); });
async function taskToggle(id, done) { await rpc("task_update", { id, done }); }
async function taskDone(id, btn) {
  const row = btn && btn.closest(".ht, .task");
  if (row) row.classList.add("vanish");
  setTimeout(() => rpc("task_update", { id, done: true }), row ? 260 : 0);
  if (window.FX && FX.sound) FX.sound("toast");
}
async function taskDel(id) {
  const el = document.querySelector(`[data-id="${id}"]`);
  if (el) el.classList.add("vanish");
  S.tasks = S.tasks.filter((t) => t.id !== id);
  setTimeout(() => { renderHomePlanner(); if (S.tab === "tasks") renderTasks(); }, 240);
  const gone = await rpc("task_delete", { id });
  if (gone) toast(gone.text, "Задача удалена", false, 6000, { label: "Отменить", fn: () => rpc("task_restore", { task: gone }) });
}
async function tasksClearDone() {
  const n = await rpc("tasks_clear_done");
  toast(n ? `Убрано выполненных: ${n}` : "Выполненных нет", "Задачи");
}
async function taskEdit(id) {
  const t = S.tasks.find((x) => x.id === id);
  const v = prompt("Изменить задачу", t.text);
  if (v && v.trim()) rpc("task_update", { id, text: v.trim() });
}
async function taskDue(id, val) { await rpc("task_update", { id, due: val || null }); }
function taskHtml(t) {
  const tags = (t.tags || []).map((g) => `<span class="tag">${esc(g)}</span>`).join("");
  return `<div class="task ${t.done ? "done" : ""}" data-id="${t.id}">
    <div class="prio p${t.priority}" title="Приоритет — клик, чтобы сменить" onclick="taskPrio('${t.id}')"></div>
    <input type="checkbox" ${t.done ? "checked" : ""} onchange="taskToggle('${t.id}', this.checked)">
    <div class="tt" ondblclick="taskEdit('${t.id}')">${esc(t.text)}${tags}<small>${t.due ? I("time", "lead") + dueLabel(t.due) : ""}${t.notes && t.notes !== t.text ? " · " + esc(t.notes) : ""}</small></div>
    <input type="datetime-local" value="${t.due ? t.due.slice(0, 16) : ""}" onchange="taskDue('${t.id}', this.value)" class="due-pick" title="Срок">
    <button class="x" onclick="taskDel('${t.id}')" title="Удалить">${I("x")}</button></div>`;
}
async function taskPrio(id) {
  const t = S.tasks.find((x) => x.id === id);
  if (t) rpc("task_update", { id, priority: (t.priority % 3) + 1 });
}
function renderTasks() {
  const now = new Date(), today = localDay(now);
  const tm = localDay(new Date(now.getTime() + 864e5));
  const open = S.tasks.filter((t) => !t.done);
  const isOver = (t) => t.due && parseLocal(t.due) < now && (t.due.length >= 16 || t.due.slice(0, 10) < today);
  const done = S.tasks.filter((t) => t.done).sort((a, b) => (b.done_at || "").localeCompare(a.done_at || ""));
  const groups = [
    ["Просрочено", "over", open.filter(isOver)],
    ["Сегодня", "", open.filter((t) => t.due && t.due.slice(0, 10) === today && !isOver(t))],
    ["Завтра", "", open.filter((t) => t.due && t.due.slice(0, 10) === tm)],
    ["Позже", "", open.filter((t) => t.due && t.due.slice(0, 10) > tm)],
    ["Без срока", "", open.filter((t) => !t.due)],
    ["Выполнено", "donecol", done.slice(0, 20)],
  ];
  $("#task-cols").innerHTML = groups.filter((g) => g[2].length || g[0] === "Сегодня").map(([n, cls, items]) =>
    `<div class="panel tcol ${cls}"><h3>${n} <span class="cnt">${n === "Выполнено" ? done.length : items.length}</span>${n === "Выполнено" ? '<button class="mini" onclick="tasksClearDone()" title="Убрать все выполненные">Очистить</button>' : ""}</h3>${items.map(taskHtml).join("") || '<div class="empty">пусто</div>'}</div>`).join("");
}
tabLoaders.tasks = renderTasks;

// ================================================================ TIME
async function timerQuick(sec) { await rpc("timer_add", { seconds: sec }); toast("Таймер запущен: " + fmtLeft(sec)); }
async function timerAdd() {
  const p = $("#tm-phrase").value.trim();
  if (!p) return;
  const sec = /^\d+$/.test(p) ? +p * 60 : null;
  await rpc("timer_add", sec ? { seconds: sec, label: $("#tm-label").value } : { phrase: p, label: $("#tm-label").value });
  $("#tm-phrase").value = ""; $("#tm-label").value = "";
}
$("#tm-phrase").addEventListener("keydown", (e) => { if (e.key === "Enter") timerAdd(); });
function renderTimerRings() {
  const box = $("#timers");
  if (!box || S.tab !== "time") return;
  if (!S.timers.length) { box.innerHTML = '<div class="empty">Нет таймеров. «Джарвис, таймер на 10 минут»</div>'; return; }
  const ids = S.timers.map((t) => t.id + (t.paused_left != null ? "p" : "")).join();  // pause flips the button too
  if (box.dataset.ids !== ids) {
    box.dataset.ids = ids;
    box.innerHTML = S.timers.map((t) => `<div class="timer" data-id="${t.id}">
      <svg viewBox="0 0 110 110"><circle cx="55" cy="55" r="48" stroke="rgba(255,179,71,.12)"/><circle class="ring" cx="55" cy="55" r="48"/></svg>
      <div class="tv"></div><div class="tl">${esc(t.label || "")}</div>
      <div class="tb"><button onclick="rpc('timer_pause',{id:'${t.id}',pause:${t.paused_left == null}})">${t.paused_left == null ? "Пауза" : "Пуск"}</button>
      <button class="danger" onclick="timerDel('${t.id}')" title="Отменить">${I("x")}</button></div></div>`).join("");
  }
  S.timers.forEach((t) => {
    const el = box.querySelector(`[data-id="${t.id}"]`);
    if (!el) return;
    const left = timerLeft(t);
    el.querySelector(".tv").textContent = fmtLeft(left);
    el.querySelector(".ring").style.strokeDashoffset = 301.6 * (1 - left / t.duration);
  });
}
let swState = { value: 0, running: false, laps: [], at: 0 };
async function sw(action) { swState = { ...(await rpc("stopwatch", { action })), at: performance.now() }; swDraw(); }
async function swRefresh() { swState = { ...(await rpc("stopwatch", { action: "get" })), at: performance.now() }; swDraw(); }
function swFmt(v) { const m = Math.floor(v / 60), s = v % 60; return `${String(m).padStart(2, "0")}:${s.toFixed(1).padStart(4, "0")}`; }
function swDraw() {
  $("#sw-laps").innerHTML = swState.laps.map((l, i) => `<div>Круг ${i + 1}: ${swFmt(l)}</div>`).reverse().join("");
  $("#sw-start").textContent = swState.running ? "Идёт…" : "Старт";
}
setInterval(() => {
  if (S.tab !== "time") return;
  const v = swState.value + (swState.running ? (performance.now() - swState.at) / 1000 : 0);
  $("#sw").textContent = swFmt(v);
}, 100);
const REP = { daily: "каждый день", weekdays: "по будням", weekly: "каждую неделю", hourly: "каждый час" };
async function remAdd() {
  const text = $("#r-text").value.trim(), at = $("#r-at").value, phrase = $("#r-phrase").value.trim();
  if (!at && !phrase) return toast("Укажите время", "Напоминание", true);
  await rpc("reminder_add", { text, at: at ? at + ":00" : null, phrase: at ? null : phrase, repeat: $("#r-repeat").value, alarm: $("#r-alarm").checked });
  ["#r-text", "#r-at", "#r-phrase"].forEach((s) => ($(s).value = ""));
}
function renderRems() {
  $("#rems").innerHTML = S.reminders.length ? S.reminders.map((r) => `<div class="rem ${r.active ? "" : "off"}">
    <span class="rem-i">${I(r.alarm ? "alarm" : "bell")}</span><span class="rt">${esc(r.text)}</span>
    ${r.repeat ? `<span class="rr">${REP[r.repeat] || r.repeat}</span>` : ""}
    <span class="ra">${dueLabel(r.at)}</span>
    <label class="switch"><input type="checkbox" ${r.active ? "checked" : ""} onchange="rpc('reminder_update',{id:'${r.id}',active:this.checked})"><span></span></label>
    <button class="btn danger" onclick="rpc('reminder_delete',{id:'${r.id}'})" title="Удалить">${I("x")}</button></div>`).join("") : '<div class="empty">Нет напоминаний. «Джарвис, напомни через час выпить воды»</div>';
}
tabLoaders.time = () => { renderRems(); $("#timers").dataset.ids = ""; renderTimerRings(); swRefresh(); };

// ================================================================ chips helper
function chipsInput(container, values, placeholder, onChange) {
  // a value starting with ^ is a regular expression (plugins): keep its case and let it contain commas
  const isRx = (v) => v.trim().startsWith("^");
  const norm = (v) => isRx(v) ? v.trim() : v.trim().toLowerCase();
  const draw = () => {
    container.innerHTML = values.map((v, i) => `<span class="chip ${isRx(v) ? "rx" : ""}">${esc(v)}<button data-i="${i}" title="Убрать">${I("x")}</button></span>`).join("") + `<input placeholder="${esc(placeholder)}">`;
    $$("button", container).forEach((b) => b.onclick = () => { values.splice(+b.dataset.i, 1); draw(); onChange && onChange(values); });
    const inp = $("input", container);
    inp.onkeydown = (e) => {
      if ((e.key === "Enter" || (e.key === "," && !isRx(inp.value))) && inp.value.trim()) { e.preventDefault(); values.push(norm(inp.value)); draw(); $("input", container).focus(); onChange && onChange(values); }
      if (e.key === "Backspace" && !inp.value && values.length) { values.pop(); draw(); $("input", container).focus(); onChange && onChange(values); }
    };
    inp.onblur = () => { if (inp.value.trim()) { values.push(norm(inp.value)); draw(); onChange && onChange(values); } };
  };
  container.classList.add("chips");
  draw();
}

// ================================================================ COMMANDS
let cmds = [], cmdSel = null;
const MODES = {
  window: [["minimize", "свернуть"], ["maximize", "развернуть"], ["close", "закрыть"], ["focus", "фокус"], ["hide", "скрыть"], ["restore", "восстановить"]],
  bridge: [["claude", "Claude Code"], ["codex", "Codex"], ["antigravity", "Antigravity"], ["gemini", "Gemini CLI"]],
  method: [["GET", "GET"], ["POST", "POST"], ["PUT", "PUT"], ["DELETE", "DELETE"]],
  message: [["telegram", "Telegram"], ["discord", "Discord"], ["whatsapp", "WhatsApp"]],
  if: [["contains", "текст содержит"], ["equals", "текст равен"], ["answer_yes", "ответ — «да»"], ["empty", "текст пустой"], ["not_empty", "текст не пустой"],
       ["time", "время между"], ["days", "день недели"], ["running", "запущена программа"], ["not_running", "не запущена программа"],
       ["battery_below", "заряд меньше, %"], ["online", "есть интернет"]],
};
const IF_PH = { contains: "{answer}  или  {result}", equals: "{answer}", answer_yes: "{answer}", empty: "{query}", not_empty: "{query}",
  time: "23:00-07:00", days: "будни  /  выходные  /  пн, ср, пт", running: "steam", not_running: "steam", battery_below: "20", online: "" };
const LOGIC = ["if", "else", "end", "repeat", "ask", "set", "stop"];
const BODY_PH = { message: "Текст сообщения, можно {result}", plugin: "фраза для плагина (необязательно)", http: 'тело запроса, например {"text":"{query}"}',
  if: "с чем сравнить (для «содержит» и «равен»): музыка", ask: "переменная для ответа: answer → потом {answer}", set: "значение, можно {result} или {answer}" };
const PH = {
  open: "https://youtube.com  или  C:\\Games\\game.exe  или  D:\\Папка", app: "telegram, steam, chrome…", close: "discord",
  youtube: "AC/DC Thunderstruck  или  {query}", music: "Кино Группа крови  или  {query}",
  say: "Фраза, можно {result}", wait: "2", search: "погода {query}", hotkey: "ctrl+shift+esc", type: "Текст для ввода",
  window: "часть заголовка окна", volume: "40  /  +10  /  -10  /  mute", media: "play / next / prev", shell: "ipconfig",
  http: "https://api.example.com/x", source: "название интеграции", ai: "Придумай тост на день рождения", bridge: "Задача агенту, {query}",
  notify: "Текст уведомления", timer: "25 минут", click: "960, 540", clipboard: "текст", run: "название другой команды",
  voice: "Любая фраза, как Джарвису: открой телеграм и напиши Ване привет", message: "Кому: Ваня / шеф / мама",
  plugin: "название плагина", click_text: "Отправить  /  вторая ссылка  /  кнопка Войти",
  repeat: "3", ask: "Какую музыку включить?", set: "имя переменной: count", stop: "что сказать напоследок (необязательно)",
};
async function loadCmds() { cmds = await rpc("commands"); renderCmdList(); if (cmdSel) cmdEdit(cmdSel); }
function renderCmdList() {
  $("#cmd-list").innerHTML = cmds.map((c) => `<div class="li ${c.id === cmdSel ? "sel" : ""} ${c.enabled ? "" : "off"}" onclick="cmdEdit('${c.id}')">
    <div class="ln"><b>${esc(c.name)}</b><small>${esc((c.triggers || []).slice(0, 2).join(" · "))} · ${c.actions.length} шаг.</small></div>
    <button class="mini" title="Запустить" onclick="event.stopPropagation();rpc('command_run',{id:'${c.id}'})">${I("play")}</button></div>`).join("") || '<div class="empty">Команд нет</div>';
}
function cmdNew() {
  const c = { id: "new" + Date.now(), name: "Новая команда", triggers: [], reply: "", actions: [{ type: "open", value: "" }], enabled: true, _new: true };
  cmds.push(c); cmdSel = c.id; renderCmdList(); cmdEdit(c.id);
}
function cmdEdit(id) {
  cmdSel = id; renderCmdList();
  const c = cmds.find((x) => x.id === id);
  if (!c) return;
  const ed = $("#cmd-editor");
  ed.innerHTML = `
    <div class="row"><div class="field" style="flex:1"><label>Название</label><input id="ce-name" value="${esc(c.name)}"></div>
      <label class="switch" style="margin-top:14px"><input type="checkbox" id="ce-en" ${c.enabled ? "checked" : ""}><span></span>Включена</label>
      <label class="switch" style="margin-top:14px" title="Кнопка на быстрой панели (Ctrl+Alt+Q) поверх любых окон и игр"><input type="checkbox" id="ce-quick" ${c.quick ? "checked" : ""}><span></span>В быстрой панели</label></div>
    <div class="field"><label>Фразы-триггеры (Enter — добавить). Название тоже срабатывает.</label><div id="ce-trig"></div></div>
    <div class="field"><label>Ответ Джарвиса (необязательно)</label><input id="ce-reply" value="${esc(c.reply || "")}" placeholder="Как пожелаете, сэр. Запускаю протокол."></div>
    <div class="field"><label>Цепочка действий</label><div class="steps" id="ce-steps"></div></div>
    <div class="add-step"><select id="ce-add">${Object.entries(S.actionTypes).map(([k, v]) => `<option value="${k}">${esc(v.title)}</option>`).join("")}</select>
      <button class="btn" onclick="stepAdd()">+ Добавить шаг</button></div>
    <div class="ed-btns"><button class="primary" onclick="cmdSave()">Сохранить</button><button class="btn" onclick="cmdSave(true)">Сохранить и запустить</button>
      <button class="btn" onclick="cmdDup()">Дублировать</button><button class="btn danger" onclick="cmdDel()">Удалить</button></div>`;
  chipsInput($("#ce-trig"), c.triggers, "например: рабочий режим  /  включи {query}");
  renderSteps(c);
}
async function appsDatalist() {
  // exact Start-menu names as suggestions for "app" steps: a typed name that matches nothing never launches
  if ($("#apps-dl")) return;
  const dl = document.createElement("datalist"); dl.id = "apps-dl"; document.body.appendChild(dl);
  try { dl.innerHTML = (await rpc("apps")).map((a) => `<option value="${esc(a.name)}">`).join(""); } catch (e) {}
}
// the step editor is shared by Commands (#ce-steps) and Automations (#ae-steps): each container edits its owner's .actions
const STEP_OWNERS = {};
function renderSteps(c, box = "#ce-steps") {
  STEP_OWNERS[box] = c;
  appsDatalist();
  // nesting of "Если / Повторить … Конец": steps inside a block are indented
  let lvl = 0;
  const depth = c.actions.map((a) => {
    if (a.type === "end" || a.type === "else") lvl = Math.max(0, lvl - 1);
    const d = lvl;
    if (a.type === "if" || a.type === "repeat" || a.type === "else") lvl++;
    return d;
  });
  $(box).innerHTML = c.actions.map((a, i) => {
    const t = S.actionTypes[a.type] || { fields: ["value"] };
    let f = `<select data-i="${i}" data-k="type">${Object.entries(S.actionTypes).map(([k, v]) => `<option value="${k}" ${k === a.type ? "selected" : ""}>${esc(v.title)}</option>`).join("")}</select>`;
    if (t.fields.includes("mode")) {
      const opts = MODES[a.type] || [];
      f += `<select data-i="${i}" data-k="mode">${opts.map(([v, n]) => `<option value="${v}" ${a.mode === v ? "selected" : ""}>${n}</option>`).join("")}</select>`;
    }
    if (t.fields.includes("method")) f += `<select data-i="${i}" data-k="method">${MODES.method.map(([v]) => `<option ${a.method === v ? "selected" : ""}>${v}</option>`).join("")}</select>`;
    if (t.fields.includes("value")) {
      const wide = !(t.fields.includes("mode") || t.fields.includes("method"));
      f += ["say", "ai", "bridge", "shell", "type", "voice"].includes(a.type)
        ? `<textarea data-i="${i}" data-k="value" rows="2" class="${wide ? "" : "wide"}" placeholder="${esc(PH[a.type] || "")}">${esc(a.value || "")}</textarea>`
        : `<div class="inline ${wide ? "" : "wide"}"><input data-i="${i}" data-k="value" value="${esc(a.value || "")}" placeholder="${esc(a.type === "if" ? IF_PH[a.mode || "contains"] || "" : PH[a.type] || "")}" ${a.type === "app" || a.type === "close" || (a.type === "if" && /running/.test(a.mode || "")) ? 'list="apps-dl"' : a.type === "plugin" ? 'list="plugins-dl"' : ""}>
           ${a.type === "open" || a.type === "app" ? `<button class="btn" title="выбрать файл (.exe, ярлык)" onclick="pickInto('${box}',${i})">${I("folder")}</button>` : ""}</div>`;
    }
    if (t.fields.includes("body")) f += ["if", "ask", "set"].includes(a.type)
      ? (a.type === "if" && !["contains", "equals"].includes(a.mode || "contains") ? "" : `<input class="wide" data-i="${i}" data-k="body" value="${esc(a.body || "")}" placeholder="${esc(BODY_PH[a.type] || "")}">`)
      : `<textarea class="wide" data-i="${i}" data-k="body" rows="2" placeholder='${esc(BODY_PH[a.type] || "")}'>${esc(a.body || "")}</textarea>`;
    if (t.fields.includes("speak")) f += `<label class="switch"><input type="checkbox" data-i="${i}" data-k="speak" ${a.speak !== false ? "checked" : ""}><span></span>Озвучить ответ</label>`;
    return `<div class="step ${LOGIC.includes(a.type) ? "logic lg-" + a.type : ""}" style="margin-left:${depth[i] * 28}px"><div class="sn">${i + 1}</div><div class="sf">${f}</div>
      <div class="sb"><button onclick="stepMove('${box}',${i},-1)" title="Выше">${I("up")}</button><button onclick="stepMove('${box}',${i},1)" title="Ниже">${I("up", "flip")}</button><button onclick="stepDel('${box}',${i})" title="Убрать шаг">${I("x")}</button></div></div>`;
  }).join("") || '<div class="empty">Нет шагов</div>';
  $$(`${box} [data-k]`).forEach((el) => el.addEventListener("change", () => {
    const a = c.actions[+el.dataset.i];
    a[el.dataset.k] = el.type === "checkbox" ? el.checked : el.value;
    if (el.dataset.k === "type") { if (MODES[a.type]) a.mode = MODES[a.type][0][0]; renderSteps(c, box); }
    else if (el.dataset.k === "mode" && a.type === "if") renderSteps(c, box);   // other placeholder / no second field
  }));
}
function stepTypesOptions() { return Object.entries(S.actionTypes).map(([k, v]) => `<option value="${k}">${esc(v.title)}</option>`).join(""); }
function curCmd() { return cmds.find((x) => x.id === cmdSel); }
function stepAdd(box = "#ce-steps", sel = "#ce-add") { const c = STEP_OWNERS[box]; const type = $(sel).value; c.actions.push({ type, value: "", ...(MODES[type] ? { mode: MODES[type][0][0] } : {}) }); renderSteps(c, box); }
function stepDel(box, i) { const c = STEP_OWNERS[box]; c.actions.splice(i, 1); renderSteps(c, box); }
function stepMove(box, i, d) { const c = STEP_OWNERS[box]; const j = i + d; if (j < 0 || j >= c.actions.length) return; [c.actions[i], c.actions[j]] = [c.actions[j], c.actions[i]]; renderSteps(c, box); }
async function pickInto(box, i) {
  const p = await winApi("pick_file");
  if (p) { const c = STEP_OWNERS[box]; c.actions[i].value = p; renderSteps(c, box); }
}
async function cmdSave(run) {
  const c = curCmd();
  c.name = $("#ce-name").value.trim() || "Без названия";
  c.reply = $("#ce-reply").value;
  c.enabled = $("#ce-en").checked;
  c.quick = $("#ce-quick").checked;
  const isNew = c._new; delete c._new;
  if (isNew) delete c.id;
  const saved = await rpc("command_save", { cmd: c });
  cmdSel = saved.id;
  toast("Команда сохранена: " + saved.name);
  await loadCmds();
  if (run) rpc("command_run", { id: saved.id });
}
function cmdDup() { const c = JSON.parse(JSON.stringify(curCmd())); c.id = "new" + Date.now(); c._new = true; c.name += " (копия)"; cmds.push(c); cmdEdit(c.id); }
async function cmdDel() {
  const c = curCmd();
  if (!confirm(`Удалить команду «${c.name}»?`)) return;
  if (!c._new) await rpc("command_delete", { id: c.id });
  cmdSel = null; $("#cmd-editor").innerHTML = '<div class="empty">Удалено</div>';
  loadCmds();
}
tabLoaders.commands = loadCmds;

// ================================================================ SOURCES
let srcs = [], srcSel = null;
const SRC_FIELDS = {
  http_json: [["url", "URL (API, возвращающий JSON)"], ["path", "Путь к полю: current.temperature_2m или items[0].name"], ["headers", "Заголовки (по строке: Authorization: Bearer xxx)", "ta"]],
  http_text: [["url", "URL страницы"], ["regex", "Regex для выборки (необязательно, группа 1)"], ["headers", "Заголовки", "ta"]],
  command: [["command", "Команда (cmd)"]],
  file: [["path_file", "Путь к файлу"]],
  window: [], clipboard: [],
  process: [["process", "Имя процесса, например steam.exe"]],
  rss: [["url", "URL RSS ленты"], ["limit", "Сколько заголовков"]],
};
async function loadSrcs() { srcs = await rpc("sources"); renderSrcList(); if (srcSel) srcEdit(srcSel); }
function renderSrcList() {
  $("#src-list").innerHTML = srcs.map((s) => `<div class="li ${s.id === srcSel ? "sel" : ""}" onclick="srcEdit('${s.id}')">
    <div class="ln"><b>${esc(s.name)}</b><small>${esc(S.sourceTypes[s.type] || s.type).split(" (")[0]}</small></div>
    <button class="mini" title="Озвучить" onclick="event.stopPropagation();srcTest('${s.id}',true)">${I("speaker")}</button></div>`).join("");
}
function srcNew() { const s = { id: "new" + Date.now(), name: "Новая интеграция", type: "http_json", triggers: [], template: "{value}", _new: true }; srcs.push(s); srcEdit(s.id); }
function srcEdit(id) {
  srcSel = id; renderSrcList();
  const s = srcs.find((x) => x.id === id);
  const fields = SRC_FIELDS[s.type] || [];
  $("#src-editor").innerHTML = `
    <div class="row"><div class="field" style="flex:1"><label>Название</label><input id="se-name" value="${esc(s.name)}"></div>
    <div class="field" style="flex:1"><label>Тип</label><select id="se-type">${Object.entries(S.sourceTypes).map(([k, v]) => `<option value="${k}" ${k === s.type ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></div></div>
    ${fields.map(([k, lbl, ta]) => `<div class="field"><label>${esc(lbl)}</label>${ta ? `<textarea data-k="${k}" rows="2">${esc(s[k] || "")}</textarea>` : `<input data-k="${k}" value="${esc(s[k] ?? "")}">`}</div>`).join("")}
    <div class="field"><label>Фраза ответа ({value} — полученные данные)</label><input id="se-tpl" value="${esc(s.template || "{value}")}"></div>
    <div class="field"><label>Голосовые триггеры</label><div id="se-trig"></div></div>
    <div class="ed-btns"><button class="primary" onclick="srcSave()">Сохранить</button><button class="btn" onclick="srcTest(null)">Тест</button>
      <button class="btn" onclick="srcTest(null,true)">Тест голосом</button><button class="btn danger" onclick="srcDel()">Удалить</button></div>
    <div class="test-out" id="se-out"></div>`;
  chipsInput($("#se-trig"), s.triggers, "например: сколько стоит биткоин");
  $("#se-type").onchange = (e) => { srcCollect(); s.type = e.target.value; srcEdit(id); };
}
function srcCollect() {
  const s = srcs.find((x) => x.id === srcSel);
  s.name = $("#se-name").value.trim(); s.template = $("#se-tpl").value;
  $$("#src-editor [data-k]").forEach((el) => (s[el.dataset.k] = el.value));
  return s;
}
async function srcSave() {
  const s = srcCollect();
  const isNew = s._new; delete s._new; if (isNew) delete s.id;
  const saved = await rpc("source_save", { src: s });
  srcSel = saved.id; toast("Интеграция сохранена"); loadSrcs();
}
async function srcTest(id, speak = false) {
  const s = id ? srcs.find((x) => x.id === id) : srcCollect();
  const out = $("#se-out");
  if (out) out.textContent = "запрос…";
  try {
    const r = await rpc("source_test", { src: s, speak });
    if (out) out.textContent = `Значение: ${r.value}\nФраза: ${r.phrase}`;
  } catch (e) { if (out) out.textContent = "Ошибка: " + e.message; }
}
async function srcDel() {
  const s = srcs.find((x) => x.id === srcSel);
  if (!confirm(`Удалить «${s.name}»?`)) return;
  if (!s._new) await rpc("source_delete", { id: s.id });
  srcSel = null; $("#src-editor").innerHTML = '<div class="empty">Удалено</div>'; loadSrcs();
}
tabLoaders.sources = loadSrcs;

// ================================================================ BRIDGES
async function loadBridges() {
  const b = await rpc("bridges");
  $("#bridge-cards").innerHTML = Object.entries(b.agents).map(([k, a]) => `<div class="panel bc">
    <h3>${esc(a.title)} ${a.busy ? '<span style="color:var(--amber)">● работает</span>' : ""}</h3>
    <div class="st ${a.found ? "ok" : "no"}">${a.found ? I("check", "lead") + "найден: " + esc(a.path) : I("x", "lead") + "не найден. Установка: " + esc(a.install)}</div>
    <div class="inline"><input id="bw-${k}" value="${esc(a.workdir)}" placeholder="Рабочая папка (по умолчанию домашняя)"><button class="btn" onclick="bridgePick('${k}')" title="Выбрать папку">${I("folder")}</button></div>
    <div class="row" style="margin-top:8px"><label class="switch"><input type="checkbox" ${a.allow_edits ? "checked" : ""} onchange="bridgeSet('${k}',{allow_edits:this.checked})"><span></span>Разрешить менять файлы</label>
      <button class="btn small" ${a.found ? "" : "disabled"} onclick="bridgeMcp('${k}')">Подключить Jarvis MCP</button>
      <button class="btn small" onclick="rpc('open',{target:'${a.site}'})">Сайт</button></div>
    <textarea id="bp-${k}" placeholder="Задача для ${esc(a.title)}…"></textarea>
    <div class="row"><button class="primary small" ${a.found ? "" : "disabled"} onclick="bridgeAsk('${k}')">Отправить</button>
      <button class="btn small danger" onclick="rpc('bridge_stop',{agent:'${k}'})">Остановить</button></div></div>`).join("");
  Object.keys(b.agents).forEach((k) => $("#bw-" + k).addEventListener("change", (e) => bridgeSet(k, { workdir: e.target.value })));
  const cfg = S.config;
  $("#api-info").innerHTML = `<div class="kv"><b>HTTP API:</b> POST http://127.0.0.1:${cfg.port}/api/rpc<br>
    <b>Заголовок:</b> X-Jarvis-Token — токен хранится в data/config.json<br>
    <b>Тело:</b> {"method":"say","params":{"text":"Привет"}} · методы: say, execute, task_add, reminder_add, timer_add, notify, stats, open…<br><br>
    <b>MCP сервер (stdio):</b> ${esc(b.mcp.command)} ${esc(b.mcp.args.join(" "))}<br>
    <b>JSON для mcp_config / settings.json:</b></div><pre class="console">${esc(JSON.stringify(b.mcp.json, null, 2))}</pre>`;
}
async function bridgePick(k) { const p = await winApi("pick_folder"); if (p) { $("#bw-" + k).value = p; bridgeSet(k, { workdir: p }); } }
async function bridgeSet(agent, kw) { await rpc("bridge_set", { agent, ...kw }); toast("Сохранено"); }
async function bridgeAsk(k) { const t = $("#bp-" + k).value.trim(); if (!t) return; $("#bridge-out").textContent = ""; await rpc("bridge_ask", { agent: k, prompt: t }); }
async function bridgeMcp(k) { toast("Регистрирую MCP…"); const r = await rpc("bridge_mcp", { agent: k }); $("#bridge-out").textContent = r; toast("Готово. Перезапустите агента."); }
function bridgeEvent(ev) {
  const o = $("#bridge-out");
  if (ev.state === "start") o.textContent = `> ${ev.agent}: ${ev.prompt}\n`;
  else if (ev.state === "line") o.textContent += ev.line + "\n";
  else if (ev.state === "done") { o.textContent += "\n— готово —\n"; if (S.tab === "bridges") loadBridges(); }
  o.scrollTop = o.scrollHeight;
}
tabLoaders.bridges = loadBridges;

// ================================================================ SETTINGS
let voices = [];
async function loadSettings() {
  const c = S.config;
  const ex = c.extras || {};
  if (!voices.length) voices = await rpc("voices");
  const mics = await rpc("mics");
  const st = await rpc("state");
  S.boot = st;
  const prov = c.ai.provider;
  const pv = { ...S.providers[prov], ...(c.ai.providers[prov] || {}) };
  const sel = (id, opts, val) => `<select id="${id}">${opts.map(([v, n]) => `<option value="${esc(v)}" ${String(v) === String(val) ? "selected" : ""}>${esc(n)}</option>`).join("")}</select>`;
  const sw_ = (id, val, lbl = "") => `<label class="switch"><input type="checkbox" id="${id}" ${val ? "checked" : ""}><span></span>${lbl}</label>`;
  const whose = { friday: "Пятницы", ultron: "Альтрона" }[(c.persona || {}).id] || "Джарвиса";
  // sections in a fixed logical order; panels added later by maint.js go into #sec-help / #sec-service
  $("#settings").innerHTML = `
  <section class="s-sec"><h3 class="s-h">Основное и голос</h3><div class="s-grid" id="sec-main">
  <div class="panel"><h3>Профиль</h3><div class="sgrid">
    <label>Ваше имя</label><input id="s-name" value="${esc(c.user.name)}" placeholder="Тони">
    <label>Обращение</label><input id="s-addr" value="${esc(c.user.address)}" list="addr-l"><datalist id="addr-l"><option value="сэр"><option value="мэм"><option value="босс"><option value="мистер Старк"></datalist>
    <label>Город (погода)</label><div class="inline"><input id="s-city" value="${esc(c.location.city)}"><button class="btn" onclick="setCity()">Найти</button></div>
    <label>Приветствие при запуске</label>${sw_("s-greet", c.greeting)}
    <label>Автозапуск с Windows</label>${sw_("s-auto", c.autostart)}
    <label>Запускать свёрнутым</label>${sw_("s-hidden", c.start_hidden)}
    <label>Утренний брифинг</label><div class="inline"><input id="s-brief" type="time" value="${esc(c.briefing_time || "")}" style="color-scheme:dark;max-width:130px"><span style="color:var(--dim);font-size:13px">погода, задачи и напоминания — при первом появлении за ПК</span></div>
    <label>Тема</label><div class="themes" id="s-themes">${Object.entries(FX.THEMES).map(([k, t]) => `<div class="theme-sw ${(c.theme || "jarvis") === k ? "sel" : ""}" data-th="${k}" title="${esc(t.name)}" style="color:${t.acc};background:radial-gradient(circle at 35% 35%, #fff 0 8%, ${t.acc} 30%, ${t.hot} 75%, #000 100%)"><span>${esc(t.name)}</span></div>`).join("")}</div>
    <label style="margin-top:14px">Звуки интерфейса</label>${sw_("s-uisnd", c.ui_sounds !== false, "тихие щелчки и сигналы")}
    <label>HUD-плашка</label>${sw_("s-overlay", c.overlay !== false, "показывать поверх окон, когда я слушаю и говорю")}
    <label>Поисковик</label>${sel("s-search", [["https://www.google.com/search?q={q}", "Google"], ["https://yandex.ru/search/?text={q}", "Яндекс"], ["https://duckduckgo.com/?q={q}", "DuckDuckGo"], ["https://www.bing.com/search?q={q}", "Bing"]], c.search_url)}
    <label>Музыка голосом</label>${sel("s-music", [["youtube", "YouTube: сразу играет, дальше похожие"], ["ytmusic", "YouTube Music: сразу играет, дальше похожие"], ["yandex", "Яндекс Музыка: только открыть поиск"]], c.music_service || "youtube")}
  </div></div>

  <div class="panel" id="voice-panel"><h3>Личность и голос</h3>
    <div id="persona-block"></div>
    <div class="sgrid">
    <div class="full s-sub">Голос ${whose} <span class="dim">— у каждой личности свой, запоминается при переключении</span></div>
    <label>Движок</label>${sel("s-engine", [["silero", "Silero — быстрый русский голос (рекомендуется)"], ["elevenlabs", "ElevenLabs — облачный голос премиум-качества (тратит кредиты)"], ["xtts", "XTTS — клон британского голоса (GPU, экспериментально)"], ["edge", "Edge Neural (онлайн)"], ["sapi", "Windows SAPI (офлайн)"]], c.voice.engine)}
    <label data-eng="silero">Голос Silero</label>${sel("s-spk", [["aidar", "Айдар — мужской, низкий"], ["eugene", "Евгений — мужской"], ["baya", "Бая — женский, спокойный"], ["kseniya", "Ксения — женский"], ["xenia", "Ксения 2 — женский, звонкий"]], c.voice.silero_speaker || "aidar")}
    <label data-eng="silero">Темп / тон Silero</label><div class="inline" data-eng="silero">${sel("s-srate", [["x-slow", "очень медленно"], ["slow", "медленно"], ["medium", "обычно"], ["fast", "быстро"]], c.voice.silero_rate || "medium")}${sel("s-spitch", [["x-low", "очень низкий"], ["low", "низкий"], ["medium", "обычный"], ["high", "высокий"]], c.voice.silero_pitch || "medium")}</div>
    <label data-eng="xtts">XTTS статус</label><span data-eng="xtts" style="font-size:14px;color:${st.xtts.ready ? "var(--green)" : "var(--amber)"}">${st.xtts.installed ? (st.xtts.ready ? "✓ загружен" : (st.xtts.error ? "✕ " + esc(st.xtts.error) : "загружается / не активен")) : "не установлен — запустите install_xtts.bat"}</span>
    <label data-eng="xtts">Образец голоса</label><div class="inline" data-eng="xtts"><button class="btn" onclick="refPick()">Свой WAV…</button><button class="btn" onclick="refGen('en-GB-RyanNeural')">Британский: Ryan</button><button class="btn" onclick="refGen('en-GB-ThomasNeural')">Thomas</button></div>
    <label data-eng="elevenlabs">API ключ ElevenLabs</label><input data-eng="elevenlabs" id="s-elkey" type="password" placeholder="${c.voice.eleven_key_set ? "•••••••• (сохранён)" : "вставьте ключ с elevenlabs.io → Developers → API Keys"}">
    <label data-eng="elevenlabs">Голос ElevenLabs</label><div class="inline" data-eng="elevenlabs">${sel("s-elvoice", [["onwK4e9ZLuTAKqWW03F9", "Daniel — британец, низкий (как Джарвис)"], ["JBFqnCBsd6RMkjVDRZzb", "George — британец, тёплый"], ["nPczCjzI2devNBz1zQrb", "Brian — глубокий"], ["pNInz6obpgDQGcFmaJgB", "Adam — мужской"]].concat(["onwK4e9ZLuTAKqWW03F9", "JBFqnCBsd6RMkjVDRZzb", "nPczCjzI2devNBz1zQrb", "pNInz6obpgDQGcFmaJgB"].includes(c.voice.eleven_voice) ? [] : [[c.voice.eleven_voice, "Свой: " + c.voice.eleven_voice]]), c.voice.eleven_voice)}<input id="s-elvid" placeholder="или ID своего голоса" style="max-width:200px"></div>
    <label data-eng="elevenlabs">Модель</label>${sel("s-elmodel", [["eleven_flash_v2_5", "Flash v2.5 — быстрый, ~0.5 кредита/символ"], ["eleven_multilingual_v2", "Multilingual v2 — богаче интонации, 1 кредит/символ"]], c.voice.eleven_model || "eleven_flash_v2_5")}
    <label data-eng="elevenlabs">Скорость</label><input data-eng="elevenlabs" id="s-elspeed" type="range" min="70" max="120" value="${Math.round((c.voice.eleven_speed || 1) * 100)}">
    <label data-eng="elevenlabs">Кредиты</label><span data-eng="elevenlabs" id="s-elinfo" style="font-size:14px;color:var(--dim)">…</span>
    <label data-eng="edge">Голос Edge</label>${sel("s-edge", voices.map((v) => [v.id, v.title]), c.voice.edge_voice)}
    <label data-eng="edge">Скорость Edge</label><input data-eng="edge" id="s-rate" type="range" min="-30" max="40" value="${parseInt(c.voice.edge_rate)}">
    <label data-eng="edge">Высота Edge</label><input data-eng="edge" id="s-pitch" type="range" min="-30" max="20" value="${parseInt(c.voice.edge_pitch)}">
    <label data-eng="xtts">Скорость XTTS</label><input data-eng="xtts" id="s-xspeed" type="range" min="80" max="140" value="${Math.round(c.voice.xtts_speed * 100)}">
    <label>Громкость</label><input id="s-vol" type="range" min="0" max="100" value="${Math.round(c.voice.volume * 100)}">
    <label>HUD-эффект голоса</label>${sw_("s-fx", c.voice.effect)}
    <label>Озвучка включена</label>${sw_("s-ven", c.voice.enabled)}
    <div class="full row"><button class="primary" onclick="rpc('voice_test')">Проверить голос</button><button class="btn" data-eng="xtts" onclick="rpc('xtts_reload')">Перезапустить XTTS</button></div>
  </div></div>

  <div class="panel"><h3>Ночной режим и звук</h3><div class="sgrid">
    <label>Ночной режим</label>${sw_("s-night", c.night && c.night.enabled, "тёмный тёплый интерфейс, тихий голос, без щелчков")}
    <label>Громкость ночью</label><div class="inline"><input id="s-nvol" type="range" min="15" max="90" value="${Math.round(((c.night && c.night.volume) || 0.45) * 100)}"><span id="s-nvol-v" style="width:50px">${Math.round(((c.night && c.night.volume) || 0.45) * 100)}%</span></div>
    <label>По расписанию</label><div class="inline">${sw_("s-nauto", c.night && c.night.auto)}<input id="s-nfrom" type="time" value="${esc((c.night && c.night.from) || "23:00")}" style="color-scheme:dark;max-width:120px"><span style="color:var(--dim)">—</span><input id="s-nto" type="time" value="${esc((c.night && c.night.to) || "08:00")}" style="color-scheme:dark;max-width:120px"></div>
    <label>Приглушать музыку</label><div class="inline">${sw_("s-duck", c.duck_media !== false, "пока говорит Джарвис")}<select id="s-ducklvl">${[[0.3, "до 30%"], [0.5, "до 50%"], [0.7, "до 70%"]].map(([v, n]) => `<option value="${v}" ${Math.abs((c.duck_level || 0.5) - v) < 0.01 ? "selected" : ""}>${n}</option>`).join("")}</select></div>
    <label>Уведомления Windows</label>${sw_("s-wintoast", c.windows_notifications, "дублировать напоминания в центр уведомлений Windows")}
  </div></div>

  </div></section>

  <section class="s-sec"><h3 class="s-h">Речь и интеллект</h3><div class="s-grid" id="sec-ai">
  <div class="panel"><h3>Распознавание речи</h3><div class="sgrid">
    <label>Микрофон</label>${sel("s-mic", [["", "По умолчанию системы"], ...mics.map((m) => [m.id, m.name])], c.stt.mic ?? "")}
    <label>Уровень</label><div class="meter"><i class="mic-meter"></i></div>
    <label>Проверка</label><div class="inline"><button class="btn" onclick="micTest()">Проверить микрофон</button><button class="btn" onclick="micBoost()" title="снять «выключен» и поставить громкость микрофона в Windows на 100%">Громкость микрофона 100%</button></div>
    <label></label><span id="mic-test-res" style="font-size:13px;color:var(--dim)">нажмите и говорите 3 секунды</span>
    <label>Движок</label>${sel("s-stteng", [["gigaam", "GigaAM v3 — точный русский, быстро на любом ПК"], ["whisper", "Whisper — многоязычный (лучше с видеокартой)"]], c.stt.engine || "gigaam")}
    <label data-stt="whisper">Модель Whisper</label>${sel("s-model", [["large-v3-turbo", "large-v3-turbo — быстро и точно (GPU)"], ["large-v3", "large-v3 — максимум точности"], ["medium", "medium"], ["small", "small — для слабых ПК"], ["base", "base"], ["tiny", "tiny — самый быстрый"]], c.stt.model)}
    <label data-stt="whisper">Устройство</label>${sel("s-dev", [["auto", "Авто"], ["cuda", "NVIDIA GPU"], ["cpu", "Процессор"]], c.stt.device)}
    <label>Сейчас</label><span style="font-size:14px">${esc(st.stt.model || "—")} на ${esc((st.stt.device || "—").toUpperCase())}${st.stt.mic_error ? ' · <span style="color:var(--red)">' + esc(st.stt.mic_error) + "</span>" : ""}</span>
    <label>Слова активации</label><div id="s-wake"></div>
    <label>Слушать постоянно</label>${sw_("s-always", c.stt.always_listen, "фоновое прослушивание")}
    <label>Без имени после ответа</label><div class="inline"><input id="s-follow" type="number" min="0" max="60" value="${c.stt.follow_up_sec}" style="width:80px"><span style="color:var(--dim);font-size:13px">сек.: после ответа следующую фразу можно сказать без «Джарвис» (0 — выключить)</span></div>
    <label>Режим разговора</label><div class="inline"><input id="s-convidle" type="number" min="20" max="900" value="${c.stt.conversation_idle_sec || 120}" style="width:80px"><span style="color:var(--dim);font-size:13px">сек. тишины — и режим выключится. Включить: «Джарвис, давай поговорим» или кнопка с двумя облачками слева внизу</span></div>
    <label>Пауза конца фразы</label><div class="inline"><input id="s-sil" type="range" min="350" max="1400" step="50" value="${c.stt.silence_ms}"><span id="s-sil-v" style="width:60px">${c.stt.silence_ms} мс</span></div>
    <label>Фильтр шума (VAD)</label>${sel("s-vad", [[0, "0 — чувствительно"], [1, "1"], [2, "2 — оптимально"], [3, "3 — строго"]], c.stt.vad)}
    <label>Звук активации</label>${sw_("s-chime", c.stt.chime)}
    <label>Отладка</label>${sw_("s-ign", c.stt.show_ignored, "показывать в журнале всё услышанное")}
    <label>Горячие клавиши</label><span style="font-size:14px"><code>${esc(c.stt.ptt_hotkey)}</code> слушать · <code>${esc(c.stt.show_hotkey)}</code> окно · <code>${esc((c.quick || {}).hotkey || "ctrl+alt+q")}</code> быстрая панель</span>
  </div></div>

  <div class="panel"><h3>Искусственный интеллект</h3><div class="sgrid">
    <label>Провайдер</label><select id="s-prov"></select>
    <label>Адрес API</label><input id="s-url" value="${esc(pv.base_url || "")}" ${pv.kind === "bridge" ? "disabled" : ""}>
    <label>API ключ</label><input id="s-key" type="password" placeholder="${pv.api_key_set ? "•••••••• (сохранён)" : pv.key ? "вставьте ключ" : "не требуется"}" ${pv.kind === "bridge" ? "disabled" : ""}>
    <label>Модель</label><div class="inline"><input id="s-mod" value="${esc(pv.model || "")}" list="s-mods" placeholder="${pv.kind === "bridge" ? "пусто = модель агента по умолчанию" : ""}"><datalist id="s-mods"></datalist><button class="btn" onclick="fetchModels()">Список</button></div>
    <div class="full row"><button class="primary" onclick="saveAI(true)">Сохранить и проверить</button><span id="s-ai-res" style="font-size:14px;color:var(--dim)"></span></div>
    <div class="full row"><button class="btn" onclick="ollamaSetup('', this)" title="Ollama: ИИ на этом ПК, без ключей и интернета">Бесплатный локальный ИИ в один клик</button><span class="dim" id="ollama-res">без ключей и интернета · Ollama</span></div>
    <label>Температура</label><input id="s-temp" type="range" min="0" max="120" value="${Math.round(c.ai.temperature * 100)}">
    <label>Память диалога</label><input id="s-hist" type="number" min="2" max="60" value="${c.ai.history}">
    <label>ИИ управляет ПК</label>${sw_("s-tools", c.ai.agent_tools, "инструменты")}
    <label>ИИ: консоль</label>${sw_("s-shell", c.ai.allow_shell, "разрешить консольные команды")}
    <label>ИИ: интернет и зрение</label>${sw_("s-web", c.ai.web_tools !== false, "поиск в сети и «что на экране» (мост Claude)")}
    <label>Уровень юмора</label><div class="inline"><input id="s-humor" type="range" min="0" max="100" step="5" value="${c.ai.humor ?? 60}"><span id="s-humor-v" style="width:60px">${c.ai.humor ?? 60}%</span></div>
    <label>Характер</label><textarea id="s-persona" rows="2" placeholder="Дополнительные инструкции: например, отвечай с юмором">${esc(c.ai.persona_extra || "")}</textarea>
  </div></div>

  <div class="panel"><h3>Инициатива и сторож</h3><div class="sgrid">
    <label>Говорить первым</label>${sel("s-ini", [[0, "никогда — только отвечать"], [1, "только о критичном"], [2, "о важном (рекомендуется)"], [3, "обо всём, что заметил"]], (c.initiative || {}).level ?? 2)}
    <label>Сторож системы</label>${sw_("s-sent", !c.sentinel || c.sentinel.enabled !== false, "автозагрузка, неподписанные программы, майнеры, зависания, hosts, Защитник, сеть")}
    <label>Зрение для мыши</label>${sw_("s-mvis", !c.mouse || c.mouse.vision !== false, "искать иконки без подписи по скриншоту (модель с картинками)")}
    <label>Фоновый режим</label>${sw_("s-bg", !c.background || c.background.enabled !== false, "сам освобождаю память и чищу временные файлы при нехватке — без мыши и клавиатуры")}
    <label>Мессенджер</label><div class="inline">${sel("s-msgapp", [["telegram", "Telegram"], ["discord", "Discord"], ["whatsapp", "WhatsApp"], ["vk", "ВКонтакте (в браузере)"]], (c.messenger || {}).default || "telegram")}${sw_("s-msgconf", !c.messenger || c.messenger.confirm !== false, "спрашивать «Отправить?»")}</div>
    <label>Разбор почты</label>${sw_("s-bgmail", c.background && c.background.mail_sort, "раз в полчаса убирать старые рассылки в папку Newsletters")}
    <div class="full hint">Во время игр и видео на весь экран, в режиме фокуса, «не беспокой» и когда вас нет за ПК я жду удобного момента. С вопросом («Закрыть её?») можно ответить просто «да».</div>
  </div></div>

  <div class="panel"><h3>Память ассистента</h3><p class="hint">«Запомни, что я люблю кофе без сахара». ИИ учитывает эти факты.</p><div id="s-notes"></div></div>
  </div></section>

  <section class="s-sec"><h3 class="s-h">Помощь в делах</h3><div class="s-grid" id="sec-help">
  <div class="panel"><h3>Забота и продуктивность</h3><div class="sgrid">
    <label>Сторож ПК</label>${sw_("s-watch", ex.watchdog !== false, "предупреждать о нехватке памяти, перегреве, месте на диске")}
    <label>Мессенджеры</label>${sw_("s-msg", ex.messengers !== false, "говорить о новых сообщениях Telegram / Discord / WhatsApp")}
    <label>Статистика программ</label>${sw_("s-usage", ex.usage_tracking !== false, "считать время в программах (только на этом ПК)")}
    <label>История копирования</label>${sw_("s-clip", ex.clipboard_history !== false, "25 последних текстов, только в памяти; пароли не сохраняются")}
    <label>Перерыв</label><div class="inline"><input id="s-break" type="number" min="0" max="300" value="${ex.break_min ?? 90}" style="width:80px"><span style="color:var(--dim);font-size:13px">мин. за ПК без паузы — предложу размяться (0 — выключить)</span></div>
    <label>Вода</label><div class="inline"><input id="s-water" type="number" min="0" max="300" value="${ex.water_min || 0}" style="width:80px"><span style="color:var(--dim);font-size:13px">напоминать пить воду каждые N минут (0 — выключить)</span></div>
    <label>Фокус: свернуть</label><input id="s-fapps" value="${esc((ex.focus_apps || []).join(", "))}" placeholder="telegram, discord">
    <label>Фокус: отвлечения</label><input id="s-fdist" value="${esc((ex.distractions || []).join(", "))}" placeholder="youtube, vk.com, twitch">
  </div></div>

  <div class="panel"><h3>Псевдонимы приложений</h3>
    <p class="hint">Как вы называете программу голосом → название из меню Пуск или путь к .exe. Например: «танки» → «World of Tanks».</p>
    <div id="s-aliases"></div>
    <div class="row"><input id="al-k" placeholder="как говорите"><input id="al-v" placeholder="приложение или путь"><button class="btn" onclick="aliasAdd()">Добавить</button></div>
    <div class="row"><input id="app-q" placeholder="поиск по найденным приложениям…"><button class="btn" onclick="rpc('apps_rebuild');toast('Пересканирую меню Пуск…')">Пересканировать</button></div>
    <div id="app-list" style="max-height:180px;overflow:auto;font-size:14px;color:var(--dim)"></div></div>

  </div></section>

  <section class="s-sec"><h3 class="s-h">Обслуживание</h3><div class="s-grid" id="sec-service">
  <div class="panel" id="upd-panel"><h3>Обновления Джарвиса</h3><div class="sgrid">
    <label>Версия</label><span style="font-size:14px">${esc(st.version || "dev")}</span>
    <label>Статус</label><span id="upd-status" style="font-size:14px;color:var(--dim)">проверяю…</span>
    <div class="full row"><button class="btn" onclick="updCheck()">Проверить</button><button class="primary" id="upd-go" onclick="updApply()" disabled>Обновить</button><div class="meter" style="flex:1;align-self:center"><i id="upd-bar"></i></div></div>
    <label>Автопроверка</label>${sw_("s-updauto", !c.update || c.update.auto_check !== false, "при запуске Джарвиса")}
    <div class="full hint" id="upd-notes" style="white-space:pre-line"></div>
    <p class="full hint">Ваши настройки, ключи, чат и задачи при обновлении не трогаются. Старая версия сохраняется в data/backup. Голосом: «Джарвис, обновись».</p>
  </div></div>
  </div></section>`;

  fillProviderSelect($("#s-prov"), prov);
  $("#s-prov").onchange = async (e) => { await saveConfig({ ai: { provider: e.target.value } }); chatHeadInit(); await loadSettings(); if (window.maintLoad) maintLoad(); };
  chipsInput($("#s-wake"), [...c.stt.wake_words], "добавить слово", (v) => saveConfig({ stt: { wake_words: v } }));
  const bind = (id, fn, ev = "change") => $(id).addEventListener(ev, (e) => fn(e.target));
  bind("#s-name", (t) => saveConfig({ user: { name: t.value } }));
  bind("#s-addr", (t) => saveConfig({ user: { address: t.value } }));
  bind("#s-greet", (t) => saveConfig({ greeting: t.checked }));
  bind("#s-auto", (t) => saveConfig({ autostart: t.checked }));
  bind("#s-updauto", (t) => saveConfig({ update: { auto_check: t.checked } }));
  bind("#s-night", (t) => rpc("night", { on: t.checked }));
  bind("#s-nvol", (t) => { $("#s-nvol-v").textContent = t.value + "%"; }, "input");
  bind("#s-nvol", (t) => saveConfig({ night: { volume: t.value / 100 } }));
  bind("#s-nauto", (t) => saveConfig({ night: { auto: t.checked } }));
  bind("#s-nfrom", (t) => saveConfig({ night: { from: t.value } }));
  bind("#s-nto", (t) => saveConfig({ night: { to: t.value } }));
  bind("#s-duck", (t) => saveConfig({ duck_media: t.checked }));
  bind("#s-ducklvl", (t) => saveConfig({ duck_level: parseFloat(t.value) }));
  bind("#s-wintoast", (t) => saveConfig({ windows_notifications: t.checked }));
  updCheck();
  bind("#s-hidden", (t) => saveConfig({ start_hidden: t.checked }));
  bind("#s-search", (t) => saveConfig({ search_url: t.value }));
  bind("#s-music", (t) => saveConfig({ music_service: t.value }));
  const engRows = (eng) => $$("#settings [data-eng]").forEach((el) => (el.style.display = el.dataset.eng === eng ? "" : "none"));
  engRows(c.voice.engine);
  $("#s-edge").dataset.eng = "edge"; engRows(c.voice.engine);
  $("#s-spk").dataset.eng = "silero"; $("#s-elmodel").dataset.eng = "elevenlabs"; engRows(c.voice.engine);
  const elInfo = () => rpc("eleven_info").then((r) => { const el = $("#s-elinfo"); if (!el) return;
    el.textContent = r && r.ok ? `осталось ${r.left.toLocaleString("ru")} из ${r.limit.toLocaleString("ru")} (${r.tier})` : "✕ " + ((r && r.error) || "нет ответа");
    el.style.color = r && r.ok ? "var(--green)" : "var(--amber)"; }).catch(() => {});
  if (c.voice.engine === "elevenlabs") elInfo();
  bind("#s-engine", (t) => { engRows(t.value); saveConfig({ voice: { engine: t.value } }).then(() => { toast("Движок голоса: " + t.value); if (t.value === "elevenlabs") elInfo(); }); });
  bind("#s-elkey", (t) => { const k = t.value.trim(); if (!k) return; saveConfig({ voice: { eleven_key: k } }).then(() => { t.value = ""; t.placeholder = "•••••••• (сохранён)"; toast("Ключ ElevenLabs сохранён"); elInfo(); rpc("voice_test"); }); });
  bind("#s-elvoice", (t) => saveConfig({ voice: { eleven_voice: t.value } }).then(() => rpc("voice_test")));
  bind("#s-elvid", (t) => { const v = t.value.trim(); if (v) saveConfig({ voice: { eleven_voice: v } }).then(() => { toast("Голос: " + v); rpc("voice_test"); }); });
  bind("#s-elmodel", (t) => saveConfig({ voice: { eleven_model: t.value } }).then(() => rpc("voice_test")));
  bind("#s-elspeed", (t) => saveConfig({ voice: { eleven_speed: t.value / 100 } }));
  bind("#s-edge", (t) => saveConfig({ voice: { edge_voice: t.value } }));
  bind("#s-spk", (t) => saveConfig({ voice: { silero_speaker: t.value } }).then(() => rpc("voice_test")));
  bind("#s-srate", (t) => saveConfig({ voice: { silero_rate: t.value } }).then(() => rpc("voice_test")));
  bind("#s-spitch", (t) => saveConfig({ voice: { silero_pitch: t.value } }).then(() => rpc("voice_test")));
  const sttRows = (eng) => { $("#s-model").dataset.stt = "whisper"; $("#s-dev").dataset.stt = "whisper"; $$("#settings [data-stt]").forEach((el) => (el.style.display = eng === "whisper" ? "" : "none")); };
  sttRows(c.stt.engine || "gigaam");
  bind("#s-stteng", (t) => { sttRows(t.value); saveConfig({ stt: { engine: t.value } }).then(() => toast("Переключаю распознавание…")); });
  bind("#s-rate", (t) => saveConfig({ voice: { edge_rate: (t.value >= 0 ? "+" : "") + t.value + "%" } }));
  bind("#s-pitch", (t) => saveConfig({ voice: { edge_pitch: (t.value >= 0 ? "+" : "") + t.value + "Hz" } }));
  bind("#s-xspeed", (t) => saveConfig({ voice: { xtts_speed: t.value / 100 } }));
  bind("#s-vol", (t) => saveConfig({ voice: { volume: t.value / 100 } }));
  bind("#s-fx", (t) => saveConfig({ voice: { effect: t.checked } }));
  bind("#s-ven", (t) => saveConfig({ voice: { enabled: t.checked } }));
  bind("#s-mic", (t) => saveConfig({ stt: { mic: t.value === "" ? null : +t.value } }).then(() => toast("Микрофон переключён")));
  bind("#s-model", (t) => saveConfig({ stt: { model: t.value } }).then(() => toast("Загружаю модель " + t.value + "…")));
  bind("#s-dev", (t) => saveConfig({ stt: { device: t.value } }));
  bind("#s-always", (t) => saveConfig({ stt: { always_listen: t.checked } }));
  bind("#s-follow", (t) => saveConfig({ stt: { follow_up_sec: +t.value } }));
  bind("#s-convidle", (t) => saveConfig({ stt: { conversation_idle_sec: +t.value } }));
  bind("#s-humor", (t) => { $("#s-humor-v").textContent = t.value + "%"; }, "input");
  bind("#s-humor", (t) => saveConfig({ ai: { humor: +t.value } }));
  bind("#s-watch", (t) => saveConfig({ extras: { watchdog: t.checked } }));
  bind("#s-ini", (t) => saveConfig({ initiative: { level: +t.value } }));
  bind("#s-sent", (t) => saveConfig({ sentinel: { enabled: t.checked } }));
  bind("#s-mvis", (t) => saveConfig({ mouse: { vision: t.checked } }));
  bind("#s-bg", (t) => saveConfig({ background: { enabled: t.checked } }));
  bind("#s-msgapp", (t) => saveConfig({ messenger: { default: t.value } }));
  bind("#s-msgconf", (t) => saveConfig({ messenger: { confirm: t.checked } }));
  bind("#s-bgmail", (t) => saveConfig({ background: { mail_sort: t.checked } }));
  bind("#s-msg", (t) => saveConfig({ extras: { messengers: t.checked } }));
  bind("#s-usage", (t) => saveConfig({ extras: { usage_tracking: t.checked } }));
  bind("#s-clip", (t) => saveConfig({ extras: { clipboard_history: t.checked } }));
  bind("#s-break", (t) => saveConfig({ extras: { break_min: +t.value } }));
  bind("#s-water", (t) => saveConfig({ extras: { water_min: +t.value } }));
  const listOf = (v) => v.split(",").map((x) => x.trim().toLowerCase()).filter(Boolean);
  bind("#s-fapps", (t) => saveConfig({ extras: { focus_apps: listOf(t.value) } }));
  bind("#s-fdist", (t) => saveConfig({ extras: { distractions: listOf(t.value) } }));
  bind("#s-sil", (t) => { $("#s-sil-v").textContent = t.value + " мс"; saveConfig({ stt: { silence_ms: +t.value } }); });
  bind("#s-vad", (t) => saveConfig({ stt: { vad: +t.value } }).then(() => toast("Применится после перезапуска")));
  bind("#s-chime", (t) => saveConfig({ stt: { chime: t.checked } }));
  bind("#s-ign", (t) => saveConfig({ stt: { show_ignored: t.checked } }));
  bind("#s-temp", (t) => saveConfig({ ai: { temperature: t.value / 100 } }));
  bind("#s-hist", (t) => saveConfig({ ai: { history: +t.value } }));
  bind("#s-tools", (t) => saveConfig({ ai: { agent_tools: t.checked } }));
  bind("#s-shell", (t) => saveConfig({ ai: { allow_shell: t.checked } }));
  bind("#s-web", (t) => saveConfig({ ai: { web_tools: t.checked } }));
  bind("#s-overlay", (t) => saveConfig({ overlay: t.checked }));
  bind("#s-uisnd", (t) => { FX.setSounds(t.checked); saveConfig({ ui_sounds: t.checked }); });
  $$("#s-themes .theme-sw").forEach((el) => el.addEventListener("click", async () => {
    $$("#s-themes .theme-sw").forEach((x) => x.classList.toggle("sel", x === el));
    await rpc("set_theme", { name: el.dataset.th });
  }));
  bind("#s-brief", (t) => saveConfig({ briefing_time: t.value }).then(() => toast(t.value ? "Брифинг каждый день в " + t.value : "Брифинг выключен")));
  bind("#s-persona", (t) => saveConfig({ ai: { persona_extra: t.value } }));
  bind("#app-q", (t) => appSearch(t.value), "input");
  renderAliases(); renderNotes(); appSearch("");
}
// ---------------------------------------------------------------- microphone check
async function micTest() {
  const el = $("#mic-test-res");
  el.textContent = "говорите что-нибудь… (3 сек)"; el.style.color = "var(--acc)";
  const r = await rpc("mic_test");
  const w = r.windows ? ` · в Windows: ${Math.round(r.windows.level * 100)}%${r.windows.muted ? ", выключен" : ""}` : "";
  el.textContent = `${r.verdict} (уровень ${Math.round(r.loud || 0)}, шум ${Math.round(r.noise || 0)}, усиление ×${r.gain ?? 1})${w}`;
  el.style.color = (r.loud || 0) >= 600 ? "var(--green)" : (r.loud || 0) >= 150 ? "var(--amber)" : "var(--red)";
}
async function micBoost() {
  const n = await rpc("mic_boost");
  toast(n ? `Громкость «${n}» — 100%` : "Микрофон не найден");
}

// ---------------------------------------------------------------- one-button updates
function updShow(ev) {
  const st = $("#upd-status"), go = $("#upd-go"), bar = $("#upd-bar"), notes = $("#upd-notes");
  if (!st) return;
  const txt = {
    available: () => `доступна версия ${ev.latest}`, latest: () => "у вас последняя версия",
    downloading: () => `скачиваю… ${ev.progress || 0}%`, installing: () => "устанавливаю…",
    deps: () => "доустанавливаю пакеты (может занять пару минут)…", done: () => "готово, перезапуск…",
    error: () => "ошибка: " + (ev.error || ""),
  }[ev.status];
  if (txt) st.textContent = txt();
  st.style.color = ev.status === "error" ? "var(--red)" : ev.status === "available" ? "var(--green)" : "var(--dim)";
  if (go) go.disabled = ev.status !== "available";
  if (bar) bar.style.width = (["downloading", "installing", "deps", "done"].includes(ev.status) ? ev.progress ?? 100 : 0) + "%";
  if (notes && ev.notes !== undefined) notes.textContent = ev.status === "available" && ev.notes ? "Что нового:\n" + ev.notes : "";
}
async function updCheck() {
  const st = $("#upd-status"); if (st) st.textContent = "проверяю…";
  const r = await rpc("update_check");
  if (r.error) updShow({ status: "error", error: r.error });
  else updShow({ ...r, status: r.available ? "available" : "latest" });
}
async function updApply() {
  $("#upd-go").disabled = true;
  updShow({ status: "downloading", progress: 0 });
  await rpc("update_apply");
}

async function setCity() { try { const g = await rpc("geocode", { city: $("#s-city").value }); S.config.location = g; toast(`Город: ${g.city}`); loadWeather(); } catch (e) {} }
async function refPick() { const p = await winApi("pick_file", "wav"); if (p) { await rpc("ref_voice", { path: p }); toast("Образец установлен, XTTS перезагружается"); } }
async function refGen(v) { toast("Генерирую образец " + v + "…"); await rpc("ref_voice", { edge_voice: v }); toast("Готово. XTTS перезагружается (~30 с)"); }
async function fetchModels() {
  await saveAI(false);
  try { const ms = await rpc("models", { pid: $("#s-prov").value }); $("#s-mods").innerHTML = ms.map((m) => `<option value="${esc(m)}">`).join(""); toast(`Моделей: ${ms.length}. Выберите в поле «Модель»`); } catch (e) {}
}
async function saveAI(test) {
  const pid = $("#s-prov").value;
  const p = { base_url: $("#s-url").value.trim(), model: $("#s-mod").value.trim(), api_key: $("#s-key").value.trim() };
  await saveConfig({ ai: { providers: { [pid]: p } } });
  chatHeadInit();
  if (!test) return;
  $("#s-ai-res").textContent = "проверяю…";
  try { const r = await rpc("test_provider", { pid }); $("#s-ai-res").innerHTML = `<span style="color:var(--green)">${I("check", "lead")}${esc(r.text)} (${r.ms} мс)</span>`; }
  catch (e) { $("#s-ai-res").innerHTML = `<span style="color:var(--red)">${I("x", "lead")}${esc(e.message)}</span>`; }
}
function renderAliases() {
  const a = S.config.aliases || {};
  $("#s-aliases").innerHTML = Object.entries(a).map(([k, v]) => `<div class="note-row"><span><b>${esc(k)}</b> → ${esc(v)}</span><button class="btn danger" onclick="aliasDel('${esc(k)}')" title="Удалить">${I("x")}</button></div>`).join("") || '<div class="empty">нет</div>';
}
async function aliasAdd() {
  const k = $("#al-k").value.trim().toLowerCase(), v = $("#al-v").value.trim();
  if (!k || !v) return;
  const a = { ...(S.config.aliases || {}), [k]: v };
  await rpc("config_set", { patch: { aliases: a } }); S.config.aliases = a;
  $("#al-k").value = $("#al-v").value = ""; renderAliases();
}
async function aliasDel(k) {
  const a = { ...S.config.aliases }; delete a[k];
  await rpc("config_set", { patch: { aliases: null } });  // dict patches merge, so reset first
  S.config = await rpc("config_set", { patch: { aliases: a } });
  renderAliases();
}
async function appSearch(q) {
  const apps = await rpc("apps", { q });
  $("#app-list").innerHTML = apps.slice(0, 120).map((a) => `<div style="cursor:pointer" title="${esc(a.path)}" onclick="$('#al-v').value='${esc(a.name).replace(/'/g, "\\'")}'">${esc(a.name)}</div>`).join("");
}
async function renderNotes() {
  const notes = await rpc("notes");
  $("#s-notes").innerHTML = notes.map((n, i) => `<div class="note-row"><span>${esc(n.text)}</span><button class="btn danger" onclick="noteDel(${i})" title="Удалить">${I("x")}</button></div>`).join("") || '<div class="empty">пусто</div>';
}
async function noteDel(i) { const n = await rpc("notes"); n.splice(i, 1); await rpc("notes_set", { notes: n }); renderNotes(); }
tabLoaders.settings = loadSettings;

// ================================================================ HELP
const HELP = [
  ["Активация", ["Фраза должна начинаться с «Джарвис»: «Джарвис, открой телеграм»", "Просто «Джарвис» — ответит «Да, сэр?» и выслушает следующую фразу", "Ctrl+Alt+J или клик по реактору — слушать без имени", "«Джарвис, стоп» — замолчать (можно перебить на полуслове)", "«Джарвис, спи» / «Джарвис, проснись»", "Esc в окне — прервать речь"]],
  ["Без «Джарвис»", ["После ответа 10 секунд можно говорить без имени: «а завтра?», «сделай громче» (реактор пишет «ГОВОРИТЕ БЕЗ ИМЕНИ»)", "давай поговорим / режим разговора — слушаю всё без имени, пока не скажете «конец разговора» или не помолчите 2 минуты", "кнопка с двумя облачками слева внизу — то же самое", "фразы, сказанные явно не мне (разговор, видео), я пропускаю молча"]],
  ["Игры голосом", ["давай сыграем в города / в слова / в викторину / в загадки", "сыграем в 20 вопросов / угадай число / правду или ложь / крокодила / квест", "хватит — подведу итог; конец разговора — выйти"]],
  ["Фокус и продуктивность (вкладка «Сегодня»)", ["режим фокуса на 45 минут / фокус над отчётом / сосредоточься", "сколько осталось фокуса / выключи фокус", "сколько я сегодня сидел за компьютером / статистика дня / сколько я играл / сколько я провёл в Chrome", "не беспокой (на 2 часа) / можешь беспокоить", "напомню размяться после 90 минут за ПК (Настройки → Забота)"]],
  ["Привычки", ["я выпил воды / выпил 2 стакана воды / сколько воды я выпил", "сделал зарядку / почитал / погулял", "добавь привычку медитация → отметь привычку медитация", "мои привычки — серии дней подряд"]],
  ["История копирования", ["что я копировал — последние 25 текстов (на вкладке «Сегодня» — клик копирует)", "вставь предыдущее / вставь третье из буфера", "очисти историю копирования"]],
  ["Забота о ПК", ["почему тормозит компьютер — найду виновника и предложу закрыть", "проверь интернет / какой пинг", "раздели экран / окно по центру", "запиши экран → стоп запись (видео в Видео\\Jarvis)", "режим производительности / энергосбережения / сбалансированный", "игровой режим / режим кино", "есть новые сообщения? — сам скажу, когда напишут в Telegram или Discord"]],
  ["Характер", ["уровень юмора 80 / какой у тебя уровень юмора", "включи броню / позови пятницу / ты лучше Алисы? / самоуничтожение", "протокол вечеринка", "мой день рождения 24 октября — поздравлю"]],
  ["Задачи: уборка", ["галочка на главной — выполнено, крестик — удалить (с кнопкой «Отменить»)", "очисти выполненные задачи / удали все задачи (с подтверждением)", "клик по цветной полоске задачи — сменить приоритет", "выполненные старше двух недель убираются сами"]],
  ["Зрение и интернет", ["что у меня на экране / посмотри на экран", "что за ошибка на экране / переведи что на экране", "какой курс биткоина / кто выиграл вчера — ищет в сети сам", "найди файл отчёт / где папка проекты"]],
  ["Выделенный текст (в любой программе)", ["переведи выделенное (на английский) — заменит текст переводом", "исправь выделенное — орфография и пунктуация", "улучши / сократи выделенное / сделай деловым это", "объясни выделенное / перескажи выделенное — ответит голосом"]],
  ["Медиа", ["что сейчас играет / какая это песня", "пауза / следующий трек / громче", "сделай дискорд тише / громкость браузера 30 / приглуши спотифай"]],
  ["Руки не нужны", ["скопируй / вставь / вырежи / выдели всё / отмени / сохрани", "листай вниз / прокрути вверх сильно / кликни / кликни дважды", "окно влево / окно вправо / скрин области / история буфера", "обнови страницу / открой закрытую вкладку / увеличь масштаб / переключи раскладку"]],
  ["Цепочки команд", ["сначала открой ютуб, потом найди там котиков и сделай громче — шаги идут строго по порядку, под реактором видно, какой сейчас", "открой блокнот и напиши привет — подожду, пока окно откроется, и напечатаю", "открой блокнот, напиши стих про осень и сохрани — то, что не умею сам, ИИ делает прямо на своём шаге", "«найди рецепт пиццы и пасты» — одна команда: режу только перед новым действием"]],
  ["Позже и по расписанию", ["через 10 минут выключи звук", "через час выключи компьютер", "в 18:30 открой почту", "команды ждут в списке напоминаний и переживают перезапуск"]],
  ["Отмена и исправления", ["отмени / отмени последнее — верну громкость, удалю таймер или напоминание, закрою открытую вкладку, сотру напечатанное", "отмени последние 3 действия / отмени всё", "не котиков, а собачек — переделаю последнюю команду", "не на 5, а на 10 минут / я имел в виду Рутуб", "измени таймер на 10 минут / добавь к таймеру 5 минут / перенеси будильник на 8", "переименуй задачу на … / перенеси задачу на завтра", "что ты сделал / повтори последнее действие", "вкладка «Сегодня» → «Что я сделал» — отмена кнопкой"]],
  ["Поиск, как сказали бы человеку", ["найди в ютубе котиков / найди котиков на ютубе — ищу «котики», а не «котиков»", "найди видео с котиками / покажи смешные видео про котов — YouTube", "найди картинки смешных собак / найди новости про космос", "найди в стиме …, поищи на авито велосипед, найди на озоне …, на маркете, в вк, на кинопоиске, в википедии, на карте аптеки", "открой ютуб и найди там котиков — «там» = сайт с прошлого шага"]],
  ["Почта", ["вкладка «Почта» → Ящики: Gmail, Яндекс, Mail.ru, Outlook… (нужен пароль приложения)", "проверь почту / есть новые письма / сколько непрочитанных", "прочитай последнее письмо / что пишет Иван / найди письма от банка", "ответь ему, что буду в пять — ИИ оформит ответ, я спрошу «Отправить?»", "напиши письмо Ивану, что встреча переносится / напиши письмо на ivan собака mail точка ru", "удали это письмо / отметь все письма прочитанными / открой почту", "о важных письмах скажу сам и предложу прочитать"]],
  ["Мессенджеры", ["напиши Ване привет — Telegram по умолчанию (Настройки → Инициатива и сторож)", "открой телеграм и напиши Ване привет", "скинь в дискорд Пете что я опаздываю / напиши в ватсап маме что скоро буду", "напиши шефу что отчёт готов — «шеф» из контактов", "перед Enter спрошу «Отправить?» — можно выключить"]],
  ["Свои команды и плагины", ["вкладка «Команды» → шаг «Фраза Джарвису»: любая фраза, как голосом, например «открой телеграм и напиши Ване привет»", "шаги «Сообщение в мессенджер», «Клик по надписи», «Запустить плагин»", "плагины: вкладка «Плагины» или папка data\\plugins — свои функции на Python", "новый плагин: название, фразы, заготовка кода — и «Сохранить»; код можно попросить у ИИ кнопкой «ИИ» над редактором", "создай плагин погода на даче / мои плагины / выключи плагин …", "пример: «брось d20», «брось 3d6» — плагин «Кости для настолок»",
    "плагин от друга: «Плагины» → Импорт; разрешения (интернет, клавиатура, файлы…) спрошу при первом запуске", "поделиться своим: открыть плагин → Экспорт"]],
  ["Научить голосом", ["когда я говорю доброе утро, открой почту и скажи погоду — новая команда", "научись новой команде — спрошу фразу и что делать",
    "добавь к команде доброе утро ещё включи музыку", "что ты делаешь, когда я говорю доброе утро", "забудь команду доброе утро"]],
  ["Телефон (KDE Connect, Android)", ["скажу, кто звонит, и прочитаю уведомления — Настройки → Телефон", "найди телефон / где мой телефон",
    "отправь ссылку на телефон (из буфера) / отправь на телефон купить хлеб", "напиши смс маме что я скоро буду — спрошу «Отправить?»",
    "сфоткай телефоном / что на телефоне / не читай уведомления с телефона"]],
  ["Личность", ["позови Пятницу / позови Альтрона / верни Джарвиса", "кто ты — каждая личность ответит по-своему",
    "голос каждой личности — Настройки → Личность и голос (запоминается отдельно)"]],
  ["Автоматизации (вкладка «Команды» → «Автоматизации»)", ["сами срабатывают на события: запустилась или закрылась программа, время, каждые N минут, вы отошли или вернулись, зарядка, батарея, наушники, Wi-Fi, окно, нагрузка процессора, новый файл в папке, флешка",
    "условия: дни недели, время, запущена / не запущена программа, питание", "действия — те же шаги, что в командах; можно спросить перед запуском",
    "мои автоматизации / выключи автоматизацию … / запусти автоматизацию …"]],
  ["Заметки (своя вкладка)", ["запиши в заметки купить фильтр для воды / создай заметку → «Слушаю»", "допиши в заметку ещё про краску", "прочитай заметку про ремонт / найди в заметках пароль от вайфая", "прочитай последнюю заметку / удали последнюю заметку"]],
  ["Обслуживание ПК", ["выключи комп, когда досмотрю / усыпи компьютер после фильма / выключи звук, когда закончится видео — «отмени выключение», чтобы передумать",
    "разбери загрузки / всегда раскладывай загрузки / отмени сортировку загрузок / удали старые установщики",
    "какие программы можно обновить / обнови все программы (через winget)", "сделай резервную копию / открой папку бэкапов — и Настройки → Резервные копии"]],
  ["Отслеживание", ["скажи, когда выйдет новое видео у Робзи / следи за каналом Куплинов", "скажи, когда Куплинов начнёт стрим (Twitch)", "скажи, когда скачается файл / когда закончится загрузка", "скажи, когда закроется блендер", "скажи, когда на сайте shop.ru появится «в наличии»", "за чем ты следишь / перестань следить за Робзи"]],
  ["Люди и роли", ["мой шеф — Антон / мой начальник Антон anton@firm.ru / мама — это Наташа", "почта Антона anton собака firm точка ru", "почту шефу — найду руководителя, открою форму письма и спрошу текст", "нет, под шефом я имею в виду Антона, а не Игоря — запомню навсегда и переделаю черновик", "кто мой шеф / мои контакты / Почта → Контакты"]],
  ["Сленг и коротко", ["врубай музон, вырубай звук, чекни почту, скинь, тг / дс / вк, комп, инет", "таймер 5 минут, громкость 30, ютуб котики, погода завтра, почту маме", "телега / стим / ютуб — просто название, и я открою"]],
  ["Фоновый режим", ["скачай файл из буфера / скачай https://… — в Загрузки, без браузера", "почисти временные файлы / очисти кэш / освободи память", "разбери почту / всегда сортируй почту — рассылки в папку Newsletters", "что ты делаешь в фоне", "мышь и клавиатура остаются вашими: всё через сеть и систему, не через экран"]],
  ["Сторож и инициатива", ["что случилось? / что ты заметил / всё ли в порядке с компьютером", "что в автозагрузке / убери Discord из автозагрузки / верни из автозагрузки …", "говори только о важном / сообщай обо всём / не говори первым", "выключи сторожа / следи за компьютером", "сам скажу: новая программа в автозагрузке, неподписанный файл из Temp или Загрузок, майнер, зависшее окно, растущая память, изменённый hosts, выключенный Защитник, пропал интернет"]],
  ["Математика и перевод величин", ["сколько будет 25 плюс 17 / посчитай 12 умножить на 8 / раздели 144 на 12", "возведи 12 в квадрат / извлеки корень из 81 / синус 30 градусов / округли 3,7", "сколько процентов составляет 30 от 120 / 20 процентов от 5000 / скидка 20 процентов от 5000", "сколько метров в 3 милях / переведи 10 килограмм в фунты / 100 градусов Фаренгейта в Цельсий", "сколько минут в 3 днях / сколько байт в 5 мегабайтах / сколько секунд в сутках", "реши уравнение 2x + 5 = 15 / случайное число от 1 до 100 / число пи"]],
  ["Деньги, новости, карты", ["переведи 100 евро в рубли / сколько будет 50 долларов в рублях / курс биткоина", "расскажи последние новости / что нового в мире технологий / новости спорта", "покажи пробки / как добраться до Красной площади / сколько ехать до работы", "запомни адрес работы улица Ленина 5 / найди ближайшее кафе / во сколько закрывается Ашан"]],
  ["Погода подробнее", ["будет ли сегодня дождь / нужен ли зонт", "сколько градусов в Сочи", "какая погода в Казани на выходных / погода в субботу / погода на неделю"]],
  ["Планы, заметки, покупки", ["что у меня запланировано на сегодня / какие планы на завтра", "добавь встречу с Иваном на 15:00 / перенеси встречу на час позже / очисти календарь на сегодня", "удали последнее напоминание / запусти помодоро / сколько дней до нового года", "отметь первую задачу как выполненную / покажи мой список дел", "запиши в заметки: … / создай заметку / прочитай последнюю заметку", "добавь молоко и хлеб в список покупок / что в списке покупок / удали молоко из списка / очисти список покупок"]],
  ["Файлы (в открытом проводнике)", ["создай папку Проекты / переименуй этот файл в Итоги / заархивируй эту папку", "удали этот файл (в корзину, с подтверждением) / удали пустые папки / найди дубликаты", "проверь размер файла / покажи свойства / сколько свободного места на диске С", "сохрани этот текст в файл Отчёт / прочитай файл заметки / запиши мою речь в файл", "добавь ссылку в прочитать позже / открой закладки"]],
  ["Музыка", ["включи рок / включи расслабляющую музыку / включи песню Nirvana", "включи радио / включи радио рекорд / поставь мой плейлист", "перемешай треки / включи повтор / выключи плеер / сделай музыку тише на фоне", "найди клип … / включи фильм Интерстеллар / открой Яндекс Музыку"]],
  ["Видео и вкладки", ["полный экран / видео на весь экран — в YouTube и других плеерах; выйди из полноэкранного", "пауза / продолжи / останови видео — работает с YouTube, Spotify, Яндекс Музыкой, плеерами", "перемотай вперёд на 30 секунд / назад на минуту / перемотай на 1:20 / на пятую минуту / включи сначала", "скорость полтора / ускорь / замедли / сколько осталось до конца видео", "следующее видео / предыдущий трек / включи субтитры", "закрой вкладку / закрой все вкладки в гугле"]],
  ["Отмена и «отложи»", ["отмени таймер на 5 минут / удали таймер чай / отмени все таймеры", "удали напоминание про чайник / на 19:30 / удали все напоминания", "выключи будильник / отмени будильник на 7:30", "когда сработало: «отложи на 10 минут» или «готово» — даже без «Джарвис»"]],
  ["Режимы и голос", ["ночной режим / сделай потемнее — тихий голос и тёмный интерфейс; дневной режим", "смени голос на женский / говори голосом Евгения / смени голос", "смени тему на зелёную / поставь красную тему / режим стелс", "брифинг — утром утренний, вечером вечерняя сводка; итоги дня / что на завтра"]],
  ["Обновления", ["обновись / обнови себя — скачает и поставит новую версию, сам перезапустится", "проверь обновления / какая у тебя версия", "или Настройки → Обновления → «Обновить»"]],
  ["Мышь и экран", ["нажми на «Войти» / кликни по кнопке Отправить / правой кнопкой по Корзина — ищу настоящие кнопки и ссылки окна, потом текст на всех мониторах", "открой вторую ссылку / включи первое видео / открой последний результат", "кликни по иконке шестерёнки — без подписи найду «глазами» (модель с картинками)", "введи котики в поле поиска / в поле Имя впиши Клим", "прокрути до Комментарии / листай вправо / прокрути до конца", "перетащи отчёт на Корзину / зажми мышь → отпусти мышь", "мышь в правый верхний угол / в центр / чуть левее / вниз на 300 / кликни здесь", "что тут можно нажать? / что у меня на экране?"]],
  ["Диктовка", ["включи диктовку — всё сказанное печатается в активное окно", "«запятая», «точка», «новая строка» — знаки", "конец диктовки — выключить"]],
  ["Настройки Windows", ["открой настройки звука / блютуз / вайфай", "открой обновления / приложения / обои", "открой настройки экрана / питания / микрофона"]],
  ["Программы и сайты", ["открой телеграм / запусти стим / включи дискорд", "закрой хром / убей процесс блокнот", "открой ютуб / открой github.com", "открой папку загрузки / документы", "закрой вкладку / закрой это окно"]],
  ["Поиск", ["найди рецепт пасты карбонара", "загугли курс биткоина", "найди на ютубе обзор RTX", "найди в википедии Никола Тесла"]],
  ["Окна", ["сверни все / сверни телеграм", "разверни хром / переключись на дискорд", "скрой телеграм → покажи скрытые окна", "какие окна открыты", "новая вкладка / переключи окно"]],
  ["Звук и медиа", ["громкость 40 / громче / тише на 20", "выключи звук / включи звук", "пауза / следующий трек / предыдущий", "яркость 70"]],
  ["Таймеры и будильники", ["таймер на 10 минут / таймер на полчаса для пасты", "сколько осталось / отмени таймеры", "засеки время → сколько прошло", "разбуди меня в 7:30 / будильник на 8 каждый день", "напомни через 20 минут проверить духовку", "напомни завтра в 9 позвонить врачу"]],
  ["Задачи", ["добавь задачу купить продукты завтра вечером", "добавь задачу срочно отправить отчёт к 18:00", "какие задачи на сегодня / на завтра", "отметь задачу 2 выполненной", "удали задачу продукты"]],
  ["Информация", ["сколько времени / какое сегодня число", "какая погода / погода в Лондоне завтра", "курс доллара / евро", "статус системы / что ест память", "посчитай 15 умножить на 7", "брифинг / доброе утро", "что в буфере"]],
  ["Система", ["скриншот", "заблокируй компьютер", "выключи компьютер (с подтверждением)", "перезагрузи / спящий режим / отмена выключения", "очисти корзину", "напечатай текст… / нажми контрол шифт эскейп"]],
  ["ИИ и агенты", ["Любой вопрос без команды уходит в ИИ-чат", "ИИ сам может открыть программу, поставить таймер и т.д.", "создай команду «киновечер»: открой кинопоиск и громкость 60 — создаст протокол", "спроси Клода почему падает сборка", "поручи Кодексу написать тесты", "запомни, что мой день рождения 5 мая"]],
  ["Протоколы (вкладка «Команды»)", ["рабочий режим / игровой режим", "протокол вечеринка / режим фокуса", "включи музыку Imagine Dragons", "ютуб котики", "Создавайте свои: фразы + цепочка действий"]],
  ["Пасхалки", ["кто ты / как дела / спасибо", "расскажи шутку / подбрось монетку", "брось кубик / загадай число от 1 до 10", "я дома / привет"]],
];
tabLoaders.help = () => {
  $("#help").innerHTML = HELP.map(([h, items]) => `<div class="panel"><h3>${h}</h3><ul>${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul></div>`).join("");
};
