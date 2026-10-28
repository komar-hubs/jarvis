// "Команды" → "Автоматизации": when something happens on the PC (and the conditions hold), run steps.
let AU = { items: [], triggers: {}, defaults: {}, examples: [], history: [], sel: null, cur: null };
const DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

// ---------------------------------------------------------------- segment Commands | Automations
function cmdSeg(which) {
  $$("#cmd-seg button").forEach((b) => b.classList.toggle("on", b.dataset.seg === which));
  $$("#tab-commands .seg-cmd").forEach((e) => e.classList.toggle("hidden", which !== "cmd"));
  $$("#tab-commands .seg-auto").forEach((e) => e.classList.toggle("hidden", which !== "auto"));
  try { localStorage.setItem("jarvis.cmdseg", which); } catch (e) {}
  if (which === "auto") autoLoad();
}
$$("#cmd-seg button").forEach((b) => b.onclick = () => cmdSeg(b.dataset.seg));
const _cmdLoader = tabLoaders.commands;
tabLoaders.commands = () => {
  _cmdLoader();
  let seg = "cmd"; try { seg = localStorage.getItem("jarvis.cmdseg") || "cmd"; } catch (e) {}
  cmdSeg(seg);
};

// ---------------------------------------------------------------- list
async function autoLoad() {
  try { Object.assign(AU, await rpc("automations")); } catch (e) { return; }
  autoRenderList();
  autoRenderHist();
  if (AU.sel && !AU.cur) { const a = AU.items.find((x) => x.id === AU.sel); if (a) autoEdit(a.id); }
}
function autoSummary(a) {
  const t = a.trigger || {}, d = AU.triggers[t.type];
  if (!d) return "?";
  const main = (d.fields[0] || [])[0];
  let v = main ? t[main] : "";
  if (t.type === "power") v = t.state === "plug" ? "подключили" : "отключили";
  if (t.type === "interval") v = `${t.minutes || 60} мин`;
  if (t.type === "battery") v = `${t.below || 20}%`;
  return d.title + (v ? ": " + v : "");
}
function autoRenderList() {
  const box = $("#auto-list");
  box.innerHTML = AU.items.map((a) => `<div class="li ${a.id === AU.sel ? "sel" : ""} ${a.enabled === false ? "off" : ""}" onclick="autoEdit('${a.id}')">
      <div class="ln"><b>${esc(a.name)}</b><small>${esc(autoSummary(a))}${a.runs ? ` · ${a.runs}×` : ""}</small></div>
      <button class="mini" title="Выполнить сейчас (проверка)" onclick="event.stopPropagation();rpc('automation_run',{id:'${a.id}'})">${I("play")}</button>
      <label class="switch" onclick="event.stopPropagation()"><input type="checkbox" ${a.enabled !== false ? "checked" : ""} onchange="rpc('automation_toggle',{id:'${a.id}',on:this.checked})"><span></span></label></div>`).join("")
    + (AU.cur && !AU.cur.id ? `<div class="li sel"><div class="ln"><b>${esc(AU.cur.name || "Новая автоматизация")}</b><small>не сохранена</small></div></div>` : "")
    || '<div class="empty">Автоматизаций нет</div>';
}
function autoRenderHist() {
  $("#auto-hist").innerHTML = AU.history.slice(0, 12).map((h) => `<div class="ah"><span class="ah-t">${esc(h.at)}</span><span>${esc(h.name)}${h.manual ? ' <small class="dim">проверка</small>' : ""}</span></div>`).join("")
    || '<div class="empty">Пока не срабатывали</div>';
}

// ---------------------------------------------------------------- editor
function autoNew(example) {
  const ex = example ? JSON.parse(JSON.stringify(example)) : null;
  AU.sel = null;
  AU.cur = ex ? { ...ex, enabled: true, cond: ex.cond || {}, cooldown: ex.cooldown ?? 1 }
    : { name: "", enabled: true, trigger: { type: "app_start", app: "" }, cond: {}, reply: "", actions: [], cooldown: 1 };
  autoRender();
}
function autoEdit(id) {
  const a = AU.items.find((x) => x.id === id);
  if (!a) return;
  AU.sel = id;
  AU.cur = JSON.parse(JSON.stringify(a));
  AU.cur.cond = AU.cur.cond || {};
  autoRender();
}
function autoDup() { const c = autoCollect(); autoNew({ ...c, id: undefined, name: c.name + " (копия)" }); }

function autoField(t, [k, label, kind]) {
  const v = t[k] ?? (AU.defaults[t.type] || {})[k] ?? "";
  if (kind.startsWith("select:")) {
    const opts = kind.slice(7).split("|").map((o) => o.split("="));
    return `<div class="field"><label>${esc(label)}</label><select data-tk="${k}">${opts.map(([val, n]) => `<option value="${val}" ${String(v) === val ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></div>`;
  }
  if (kind === "folder") return `<div class="field grow"><label>${esc(label)}</label><div class="inline"><input data-tk="${k}" value="${esc(v)}"><button class="btn" title="Выбрать папку" onclick="autoPickFolder('${k}')">${I("folder")}</button></div></div>`;
  return `<div class="field ${kind === "text" ? "grow" : ""}"><label>${esc(label)}</label><input data-tk="${k}" type="${kind === "number" ? "number" : kind === "time" ? "time" : "text"}" value="${esc(v)}" ${kind === "time" ? 'style="color-scheme:dark"' : ""} ${kind === "text" && (k === "app") ? 'list="procs-dl"' : ""}></div>`;
}
function autoTrigFields() {
  const t = AU.cur.trigger, d = AU.triggers[t.type] || { fields: [], vars: "" };
  $("#ae-tf").innerHTML = d.fields.map((f) => autoField(t, f)).join("");
  $("#ae-vars").innerHTML = d.vars ? `Переменные для шагов: <code>${esc(d.vars)}</code>` : "";
}
function autoRender() {
  const a = AU.cur, c = a.cond;
  autoRenderList();
  $("#auto-editor").innerHTML = `
    ${!a.id ? `<div class="field"><label>Взять готовый пример</label><select id="ae-ex"><option value="">— с нуля —</option>${AU.examples.map((e, i) => `<option value="${i}">${esc(e.name)}</option>`).join("")}</select></div>` : ""}
    <div class="row"><div class="field" style="flex:1"><label>Название</label><input id="ae-name" value="${esc(a.name)}" placeholder="например: Игра запустилась — тишина"></div>
      <label class="switch" style="margin-top:14px"><input type="checkbox" id="ae-en" ${a.enabled !== false ? "checked" : ""}><span></span>Включена</label></div>
    <div class="ae-block"><h3>Когда</h3>
      <div class="row ae-trig"><div class="field"><label>Событие</label><select id="ae-type">${Object.entries(AU.triggers).map(([k, d]) => `<option value="${k}" ${k === a.trigger.type ? "selected" : ""}>${esc(d.title)}</option>`).join("")}</select></div>
        <div class="ae-tf" id="ae-tf"></div></div>
      <div class="dim ae-vars" id="ae-vars"></div></div>
    <div class="ae-block"><h3>Условия <span class="h3-meta">необязательно</span></h3>
      <div class="field"><label>Дни недели (ни один не выбран — все)</label><div class="days" id="ae-days">${DAYS.map((d, i) => `<button class="${(c.days || []).includes(i) ? "on" : ""}" data-d="${i}">${d}</button>`).join("")}</div></div>
      <div class="row"><div class="field"><label>Время с</label><input id="ae-from" type="time" value="${esc(c.from || "")}" style="color-scheme:dark"></div>
        <div class="field"><label>до</label><input id="ae-to" type="time" value="${esc(c.to || "")}" style="color-scheme:dark"></div>
        <div class="field"><label>Питание</label><select id="ae-power"><option value="">любое</option><option value="ac" ${c.power === "ac" ? "selected" : ""}>от сети</option><option value="battery" ${c.power === "battery" ? "selected" : ""}>от батареи</option></select></div></div>
      <div class="row"><div class="field grow"><label>Только если запущена программа</label><input id="ae-run" value="${esc(c.running || "")}" placeholder="например: obs64" list="procs-dl"></div>
        <div class="field grow"><label>Только если НЕ запущена</label><input id="ae-norun" value="${esc(c.not_running || "")}" placeholder="например: zoom" list="procs-dl"></div></div></div>
    <div class="ae-block"><h3>Что сделать</h3>
      <div class="field"><label>Ответ Джарвиса (необязательно, можно с переменными)</label><input id="ae-reply" value="${esc(a.reply || "")}" placeholder="Подключён диск {drive}."></div>
      <div class="steps" id="ae-steps"></div>
      <div class="add-step"><select id="ae-add">${stepTypesOptions()}</select><button class="btn" onclick="stepAdd('#ae-steps','#ae-add')">+ Добавить шаг</button></div></div>
    <div class="ae-block"><h3>Поведение</h3>
      <div class="row"><label class="switch"><input type="checkbox" id="ae-ask" ${a.ask ? "checked" : ""}><span></span>Спросить перед запуском</label>
        <label class="switch"><input type="checkbox" id="ae-ann" ${a.announce ? "checked" : ""}><span></span>Показать уведомление</label>
        <label class="switch"><input type="checkbox" id="ae-once" ${a.once ? "checked" : ""}><span></span>Один раз, потом выключить</label></div>
      <div class="row"><span class="dim">Не чаще чем раз в</span><input id="ae-cd" type="number" min="0" value="${esc(a.cooldown ?? 1)}" style="width:80px"><span class="dim">минут</span></div></div>
    <div class="ed-btns"><button class="primary" onclick="autoSave()">Сохранить</button>
      ${a.id ? `<button class="btn" onclick="autoRun()">${I("play", "lead")}Выполнить сейчас</button><button class="btn" onclick="autoDup()">${I("copy", "lead")}Дублировать</button><button class="btn danger" onclick="autoDel()">${I("trash", "lead")}Удалить</button>` : ""}</div>
    ${a.id && a.runs ? `<div class="dim ae-stat">Сработала ${a.runs} раз, последний — ${new Date(a.last_run * 1000).toLocaleString("ru-RU")}</div>` : ""}`;
  autoTrigFields();
  renderSteps(a, "#ae-steps");
  procsDatalist();
  $("#ae-type").onchange = (e) => { autoCollect(); a.trigger = { type: e.target.value, ...(AU.defaults[e.target.value] || {}) }; autoTrigFields(); };
  $$("#ae-days button").forEach((b) => b.onclick = () => b.classList.toggle("on"));
  const ex = $("#ae-ex"); if (ex) ex.onchange = () => { if (ex.value !== "") autoNew(AU.examples[+ex.value]); };
  $("#ae-name").addEventListener("input", () => { if (!a.id) { a.name = $("#ae-name").value; autoRenderList(); } });
}
async function procsDatalist() {
  // running programs as suggestions: the trigger needs the process name (cs2, obs64), not the window title
  let dl = $("#procs-dl");
  if (!dl) { dl = document.createElement("datalist"); dl.id = "procs-dl"; document.body.appendChild(dl); }
  try { dl.innerHTML = (await rpc("processes")).map((p) => `<option value="${esc(p)}">`).join(""); } catch (e) {}
}
async function autoPickFolder(k) { const p = await winApi("pick_folder"); if (p) $(`#ae-tf [data-tk="${k}"]`).value = p; }
function autoCollect() {
  const a = AU.cur;
  a.name = $("#ae-name").value.trim();
  a.enabled = $("#ae-en").checked;
  $$("#ae-tf [data-tk]").forEach((el) => (a.trigger[el.dataset.tk] = el.type === "number" ? +el.value : el.value.trim()));
  a.cond = { days: $$("#ae-days button.on").map((b) => +b.dataset.d), from: $("#ae-from").value, to: $("#ae-to").value,
             power: $("#ae-power").value, running: $("#ae-run").value.trim(), not_running: $("#ae-norun").value.trim() };
  a.reply = $("#ae-reply").value;
  a.ask = $("#ae-ask").checked; a.announce = $("#ae-ann").checked; a.once = $("#ae-once").checked;
  a.cooldown = Math.max(0, +$("#ae-cd").value || 0);
  return a;
}
async function autoSave() {
  const a = autoCollect();
  if (!a.actions.length && !a.reply.trim()) { toast("Добавьте шаг или ответ Джарвиса", "Автоматизация", true); return; }
  const saved = await rpc("automation_save", { auto: a });
  AU.sel = saved.id; AU.cur = null;
  toast("Сохранено: " + saved.name, "Автоматизация");
  await autoLoad();
  autoEdit(saved.id);
}
async function autoRun() { await autoSave(); await rpc("automation_run", { id: AU.sel }); toast("Выполняю шаги (условия пропущены)", "Проверка"); }
async function autoDel() {
  if (!confirm(`Удалить автоматизацию «${AU.cur.name}»?`)) return;
  await rpc("automation_delete", { id: AU.cur.id });
  AU.sel = null; AU.cur = null;
  $("#auto-editor").innerHTML = '<div class="empty">Удалено</div>';
  autoLoad();
}
