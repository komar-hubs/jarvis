// Tab «Учёба» (jarvis/school.py): what to do now and what can wait, homework, the timetable editor, own blocks.
let SCH = { d: null, saveT: null, showSun: false };
const DOW = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

function schWhen(iso) {
  if (!iso) return "";
  const d = new Date(iso + "T00:00"), t = new Date(); t.setHours(0, 0, 0, 0);
  const k = Math.round((d - t) / 864e5);
  if (k === 0) return "сегодня";
  if (k === 1) return "завтра";
  if (k === 2) return "послезавтра";
  if (k < 0) return "просрочено";
  return `${DOW[(d.getDay() + 6) % 7]}, ${d.getDate()} ${MON[d.getMonth()].slice(0, 3)}`;
}
function schMin(n) { n = Math.round(n); return n < 60 ? `${n} мин` : `${Math.floor(n / 60)} ч${n % 60 ? " " + (n % 60) + " мин" : ""}`; }
// one colour per subject: the same subject has the same colour in the timetable and in the homework
function schHue(s) { let h = 0; for (const c of (s || "")) h = (h * 31 + c.charCodeAt(0)) % 360; return h; }
function schTag(s) { return s ? `<span class="sch-subj" style="--h:${schHue(s)}">${esc(s)}</span>` : ""; }

// the badge on the tab: homework for tomorrow that is not done yet
async function schBadge(r) {
  try { r = r || await rpc("school_get"); } catch (e) { return; }
  const t = new Date(Date.now() + 864e5), tom = `${t.getFullYear()}-${pad2(t.getMonth() + 1)}-${pad2(t.getDate())}`;
  const n = r.hw.filter((x) => !x.done && (x.due || "").slice(0, 10) <= tom).length;
  const b = $("#badge-hw"); if (b) { b.textContent = n; b.classList.toggle("on", n > 0); }
}

async function schoolLoad() {
  let r;
  try { r = await rpc("school_get"); } catch (e) { return; }
  SCH.d = r.data; SCH.r = r;
  schBadge(r);
  SCH.showSun = SCH.showSun || (r.data.days["7"] || []).some(Boolean);
  schPlan(r); schHw(r); schTable(r); schEvents(r); schSettings(r);
}

// ---------------------------------------------------------------- the plan
function schPlan(r) {
  const p = r.plan, box = $("#sch-plan");
  const used = p.free ? Math.min(100, Math.round(p.total / p.free * 100)) : 100;
  const busy = p.busy ? `<div class="sch-busy">${I(p.busy.kind === "lesson" ? "tasks" : "time", "lead")}${p.busy.kind === "lesson" ? "Сейчас урок: " : "Сейчас: "}<b>${esc(p.busy.title)}</b> · до ${p.busy.end} · осталось ${schMin(p.busy.left)}</div>` : "";
  const item = (x, later) => `<div class="task sch-task ${x.days < 0 ? "over" : ""}" data-id="${x.id}">
      <input type="checkbox" data-sch-done="${x.id}" title="Сделано">
      <div class="tt">${x.hw ? schTag(x.subject) + " " + esc(x.text.replace(/^[^:]+:\s*/, "")) : esc(x.text)}
        <small>${esc(x.why)}</small></div>
      <button class="sch-est" data-sch-est="${x.id}" title="Сколько займёт — нажмите, чтобы поправить">${schMin(x.est)}</button>
      ${later ? "" : `<button class="ghost small" data-sch-defer="${x.id}" title="Отложить на завтра (срок не меняется)">${I("time")}</button>`}
    </div>`;
  box.innerHTML = `<h3><span>${I("bolt", "lead")}Что делать сейчас</span><button class="mini" onclick="schoolLoad()" title="Пересчитать">${I("refresh")}</button></h3>
    ${busy}
    <div class="sch-now">${esc(r.plan_text)}</div>
    <div class="sch-free"><div class="sch-bar ${p.short ? "over" : ""}"><i style="width:${used}%"></i></div>
      <span>${p.late ? "уже поздно — пора спать" : `занято ${schMin(p.total)} из ${schMin(p.free)} свободных до ${esc(p.bedtime)}`}${p.short ? ` · <b class="bad">не хватает ${schMin(p.short)}</b>` : ""}</span></div>
    <div class="sch-col-h">Сегодня</div>
    ${p.today.map((x) => item(x)).join("") || '<div class="empty">Срочного нет — можно отдыхать</div>'}
    ${p.later.length ? `<div class="sch-col-h">Можно отложить</div>${p.later.map((x) => item(x, true)).join("")}` : ""}
    <p class="hint">«Джарвис, что мне сейчас делать», «план на сегодня», «что можно отложить», «отложи историю на завтра». Время на дело — по кнопке справа: Джарвис учтёт.</p>`;
  box.querySelectorAll("[data-sch-done]").forEach((c) => c.onchange = () => rpc("hw_done", { id: c.dataset.schDone, done: true }).then(schoolLoad));
  box.querySelectorAll("[data-sch-defer]").forEach((b) => b.onclick = () => rpc("task_defer", { id: b.dataset.schDefer, days: 1 }).then(schoolLoad));
  box.querySelectorAll("[data-sch-est]").forEach((b) => b.onclick = () => {
    const v = prompt("Сколько минут займёт?", parseInt(b.textContent) || 30);
    if (v && +v > 0) rpc("task_est", { id: b.dataset.schEst, est: +v }).then(schoolLoad);
  });
}

// ---------------------------------------------------------------- homework
function schHw(r) {
  const box = $("#sch-hw");
  const groups = {};
  r.hw.forEach((t) => { const k = t.done ? "~done" : (t.due || "").slice(0, 10); (groups[k] = groups[k] || []).push(t); });
  const keys = Object.keys(groups).sort();
  const subjects = r.subjects || [];
  box.innerHTML = `<h3><span>${I("note", "lead")}Домашка</span><span class="h3-meta">${r.hw.filter((t) => !t.done).length} не сделано</span></h3>
    <div class="row sch-add">
      <input id="sch-hw-subj" list="sch-subjects" placeholder="предмет" style="max-width:150px">
      <input id="sch-hw-text" placeholder="что задали: §12, упр. 120…" style="flex:1;min-width:160px">
      <input id="sch-hw-due" type="date" title="К какому дню. Пусто — к следующему уроку по расписанию" style="max-width:150px">
      <button class="btn" id="sch-hw-add">${I("plus", "lead")}Записать</button>
    </div>
    <datalist id="sch-subjects">${subjects.map((s) => `<option value="${esc(s)}">`).join("")}</datalist>
    ${keys.map((k) => `<div class="sch-col-h">${k === "~done" ? "Сделано" : "К уроку " + schWhen(k)}</div>` + groups[k].map((t) => `
      <div class="task ${t.done ? "done" : ""}">
        <input type="checkbox" ${t.done ? "checked" : ""} data-hw-done="${t.id}">
        <div class="tt">${schTag(t.subject)} ${esc((t.text || "").replace(/^[^:]+:\s*/, ""))}${t.notes && t.notes !== "вручную" && t.notes !== "голос" ? `<small>${esc(t.notes)}</small>` : ""}</div>
        <button class="x" data-hw-del="${t.id}" title="Удалить">✕</button>
      </div>`).join("")).join("") || '<div class="empty">Домашки нет. Голосом: «задали по биологии параграф 12», или придёт сама из чата класса.</div>'}`;
  const add = async () => {
    const subject = $("#sch-hw-subj").value.trim(), text = $("#sch-hw-text").value.trim();
    if (!subject || !text) return;
    await rpc("hw_add", { subject, text, due: $("#sch-hw-due").value || null });
    schoolLoad();
  };
  $("#sch-hw-add").onclick = add;
  $("#sch-hw-text").onkeydown = (e) => { if (e.key === "Enter") add(); };
  box.querySelectorAll("[data-hw-done]").forEach((c) => c.onchange = () => rpc("hw_done", { id: c.dataset.hwDone, done: c.checked }).then(schoolLoad));
  box.querySelectorAll("[data-hw-del]").forEach((b) => b.onclick = () => rpc("hw_delete", { id: b.dataset.hwDel }).then(schoolLoad));
}

// ---------------------------------------------------------------- the timetable
function schTable(r) {
  const d = SCH.d, box = $("#sch-tt");
  const cols = SCH.showSun ? 7 : 6;
  const rows = Math.max(6, ...Object.values(d.days).map((x) => x.length), d.bells.length);
  const now = new Date(), today = (now.getDay() + 6) % 7, hm = pad2(now.getHours()) + ":" + pad2(now.getMinutes());
  let html = `<table class="sch-grid"><thead><tr><th class="sch-bell-h">Звонки</th>${DOW.slice(0, cols).map((x, i) => `<th class="${i === today ? "today" : ""}">${x}</th>`).join("")}</tr></thead><tbody>`;
  for (let n = 0; n < rows; n++) {
    const b = d.bells[n] || ["", ""];
    const cur = b[0] && b[0] <= hm && hm < b[1];
    html += `<tr><td class="sch-bell"><b>${n + 1}</b><input data-bell="${n}:0" value="${esc(b[0])}" placeholder="08:00">–<input data-bell="${n}:1" value="${esc(b[1])}" placeholder="08:45"></td>`;
    for (let c = 0; c < cols; c++) {
      const s = (d.days[String(c + 1)] || [])[n] || "";
      html += `<td class="${c === today ? "today" : ""} ${c === today && cur ? "now" : ""}"><input class="sch-cell" list="sch-subjects" data-cell="${c + 1}:${n}" value="${esc(s)}" style="${s ? `--h:${schHue(s)}` : ""}" ${s ? 'data-has="1"' : ""}></td>`;
    }
    html += "</tr>";
  }
  html += "</tbody></table>";
  box.innerHTML = `<h3><span>${I("tasks", "lead")}Расписание уроков</span>
      <span><label class="sch-sun"><input type="checkbox" id="sch-sun" ${SCH.showSun ? "checked" : ""}> воскресенье</label>
      <button class="mini" id="sch-row" title="Добавить урок">${I("plus")}</button></span></h3>
    <div class="sch-grid-wrap">${html}</div>
    <p class="hint">Впишите предметы — домашка сама встанет к следующему уроку, а утром и вечером Джарвис скажет, какие уроки. «Какой сейчас урок», «какие уроки завтра», «когда химия», «сколько до звонка».</p>`;
  box.querySelectorAll("[data-cell]").forEach((inp) => inp.oninput = () => {
    const [day, n] = inp.dataset.cell.split(":").map(Number);
    const arr = d.days[String(day)] = d.days[String(day)] || [];
    while (arr.length <= n) arr.push("");
    arr[n] = inp.value.trim();
    while (arr.length && !arr[arr.length - 1]) arr.pop();
    inp.style.setProperty("--h", schHue(arr[n]));
    inp.toggleAttribute("data-has", !!arr[n]);
    schSave();
  });
  box.querySelectorAll("[data-bell]").forEach((inp) => inp.onchange = () => {
    const [n, k] = inp.dataset.bell.split(":").map(Number);
    while (d.bells.length <= n) d.bells.push(["", ""]);
    d.bells[n][k] = inp.value.trim();
    schSave();
  });
  $("#sch-sun").onchange = (e) => { SCH.showSun = e.target.checked; schTable(r); };
  $("#sch-row").onclick = () => { d.bells.push(["", ""]); schTable(r); };
}

function schSave(reload) {
  clearTimeout(SCH.saveT);
  SCH.saveT = setTimeout(async () => { await rpc("school_save", { data: SCH.d }); if (reload) schoolLoad(); }, 600);
}

// ---------------------------------------------------------------- own blocks: секция, репетитор
function schEvents(r) {
  const d = SCH.d, box = $("#sch-ev");
  box.innerHTML = `<h3><span>${I("time", "lead")}Свои дела по расписанию</span><button class="mini" id="sch-ev-add" title="Добавить">${I("plus")}</button></h3>
    <p class="hint">Секции, кружки, репетитор: в это время Джарвис не планирует домашку.</p>
    ${d.events.map((e, i) => `<div class="sch-ev">
      <input data-ev="${i}:title" value="${esc(e.title)}" placeholder="Секция по футболу">
      <span class="sch-days">${DOW.map((x, k) => `<button class="${(e.days || []).includes(k + 1) ? "on" : ""}" data-evd="${i}:${k + 1}">${x}</button>`).join("")}</span>
      <input type="time" data-ev="${i}:start" value="${esc(e.start)}"> – <input type="time" data-ev="${i}:end" value="${esc(e.end)}">
      <button class="ghost small" data-evx="${i}" title="Удалить">${I("x")}</button></div>`).join("") || '<div class="empty">Пока ничего</div>'}`;
  $("#sch-ev-add").onclick = () => { d.events.push({ title: "", days: [], start: "17:00", end: "18:30" }); schEvents(r); };
  box.querySelectorAll("[data-ev]").forEach((inp) => inp.onchange = () => {
    const [i, k] = inp.dataset.ev.split(":"); d.events[+i][k] = inp.value; schSave(true);
  });
  box.querySelectorAll("[data-evd]").forEach((b) => b.onclick = () => {
    const [i, k] = b.dataset.evd.split(":").map(Number); const e = d.events[i];
    e.days = (e.days || []).includes(k) ? e.days.filter((x) => x !== k) : [...(e.days || []), k].sort();
    b.classList.toggle("on"); schSave(true);
  });
  box.querySelectorAll("[data-evx]").forEach((b) => b.onclick = () => { d.events.splice(+b.dataset.evx, 1); schEvents(r); schSave(true); });
}

// ---------------------------------------------------------------- settings of the tab
function schSettings(r) {
  const d = SCH.d, box = $("#sch-set");
  const subj = [...new Set(Object.values(d.days).flat().filter(Boolean))];
  const seen = (r.chats_seen || []).filter((c) => !(d.chats || []).some((x) => c.toLowerCase().includes(x.toLowerCase())));
  box.innerHTML = `<h3><span>${I("gear", "lead")}Настройки учёбы</span></h3>
    <div class="sgrid">
      <label>Чат класса</label><div><input id="sch-chats" value="${esc((d.chats || []).join(", "))}" placeholder="6Б, 6 «Б» класс">
        ${seen.length ? `<div class="sch-seen">недавно с телефона: ${seen.slice(0, 8).map((c) => `<button class="ghost small" data-chat="${esc(c)}">+ ${esc(c)}</button>`).join("")}</div>` : ""}</div>
      <label>Домашка из чата</label><div>${featChip("homework")}</div>
      <label>Ложусь спать</label><input id="sch-bed" type="time" value="${esc(d.bedtime || "22:30")}" style="max-width:120px">
      <label>Сводка на завтра</label><div class="inline"><input id="sch-eve" type="time" value="${esc(d.evening || "")}" style="max-width:120px"><span class="dim small">вечером скажу, какие завтра уроки и что не сделано; пусто — не говорить</span></div>
    </div>
    <p class="hint">Сообщения учителей из этих чатов (MAX, Telegram, WhatsApp на телефоне) с «д/з», «§», «упр.», «задали» сами попадают в домашку — без ИИ. Если предмет не назван, беру предмет учителя или урок, который только что закончился.</p>
    ${subj.length ? `<div class="sch-col-h">Предметы</div><div class="sch-subjects">${subj.map((s) => {
      const x = d.subjects[s] || {};
      return `<div class="sch-srow">${schTag(s)}<input data-sub="${esc(s)}:est" type="number" min="5" max="240" value="${x.est || 30}" title="минут на домашку" style="width:64px"><span class="dim">мин</span>
        <input data-sub="${esc(s)}:teacher" value="${esc(x.teacher || "")}" placeholder="учитель (как в чате)">
        <input data-sub="${esc(s)}:bring" value="${esc(x.bring || "")}" placeholder="взять: форму, циркуль…"></div>`;
    }).join("")}</div>` : ""}`;
  $("#sch-chats").onchange = (e) => { d.chats = e.target.value.split(",").map((x) => x.trim()).filter(Boolean); schSave(); };
  box.querySelectorAll("[data-chat]").forEach((b) => b.onclick = () => { d.chats = [...(d.chats || []), b.dataset.chat]; schSave(true); });
  $("#sch-bed").onchange = (e) => { d.bedtime = e.target.value; schSave(true); };
  $("#sch-eve").onchange = (e) => { d.evening = e.target.value; schSave(); };
  box.querySelectorAll("[data-sub]").forEach((inp) => inp.onchange = () => {
    const i = inp.dataset.sub.lastIndexOf(":"), s = inp.dataset.sub.slice(0, i), k = inp.dataset.sub.slice(i + 1);
    d.subjects[s] = d.subjects[s] || {};
    d.subjects[s][k] = k === "est" ? +inp.value : inp.value.trim();
    schSave(k === "est");
  });
  if (!FT.rows.length) rpc("features").then((rows) => { FT.rows = rows; featChipsFill(); }).catch(() => {});
  else featChipsFill();
}

tabLoaders.school = schoolLoad;
setTimeout(() => schBadge(), 4000);
