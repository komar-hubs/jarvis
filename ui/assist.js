// Mail tab, the "Jarvis speaks first" card, chain progress, the PC sentinel and the action journal on "Сегодня".

// ================================================================ events from the core
const _onEventBase = onEvent;
onEvent = function (ev) {
  switch (ev.type) {
    case "initiative": initiativeShow(ev.event); if (S.tab === "hub") guardLoad(); return;
    case "journal": if (S.tab === "hub") journalLoad(); return;
    case "chain": chainShow(ev); return;
    case "mail": mailBadge(); if (S.tab === "mail") mailLoad(false); return;
    case "mail_draft": toast(`Кому: ${ev.to}\n${ev.subject}`, "Черновик письма", false, 9000); return;
    case "mail_compose": go("mail"); setTimeout(() => mailCompose(ev.to, ev.subject || ""), 250); return;
    case "watchers": case "bg_job": if (S.tab === "hub") watchLoad(); if (ev.type === "bg_job") bgToast(ev.job); return;
    case "people": if (S.tab === "mail" && $("#people-view")) peopleShow(); return;
    case "plugins": pluginsLoad(); return;
    case "plugin_open": go("plugins"); setTimeout(() => pluginOpen(ev.file, true), 300); return;
    case "plugin_error": toast(String(ev.error || "").split("\n").filter(Boolean).slice(-1)[0] || "ошибка", "Плагин " + ev.file, true, 8000); return;
  }
  _onEventBase(ev);
};

// ================================================================ initiative card
// When Jarvis speaks first with a question ("Закрыть её?"), the same question waits here with buttons.
function initiativeShow(e) {
  if (!e) return;
  let el = $("#ini-card");
  if (e.state === "done" || e.state === "dismissed") { if (el && +el.dataset.id === e.id) el.remove(); return; }
  if (!e.ask || e.state !== "said") return;
  if (!el) { el = document.createElement("div"); el.id = "ini-card"; document.body.appendChild(el); }
  el.dataset.id = e.id;
  el.className = "ini-card p" + e.priority;
  el.innerHTML = `<div class="ini-ico">${I(e.kind === "sentinel" ? "shield" : e.title === "Почта" ? "mail" : "diamond")}</div>
    <div class="ini-body"><div class="ini-t">${esc(e.title)} · ${esc(e.at)}</div><div class="ini-x">${esc(e.text)}</div>
    <div class="ini-b"><button class="primary" onclick="initiativeAnswer(${e.id}, true)">Да</button>
    <button class="btn" onclick="initiativeAnswer(${e.id}, false)">Нет</button></div>
    <div class="ini-h">или ответьте голосом «да» / «нет» — без имени</div></div>`;
  FX.sound("toast");
  clearTimeout(initiativeShow.t);
  initiativeShow.t = setTimeout(() => { const c = $("#ini-card"); if (c && +c.dataset.id === e.id) c.remove(); }, 120000);
}
async function initiativeAnswer(id, yes) {
  const c = $("#ini-card"); if (c) c.remove();
  await rpc("event_answer", { id, yes });
}

// ================================================================ chain progress under the reactor
function chainShow(ev) {
  const box = $("#chain-strip");
  if (!box) return;
  const steps = ev.steps || [];
  box.innerHTML = steps.map((s, i) => {
    const st = i + 1 < ev.at ? "done" : i + 1 === ev.at ? "cur" : "";
    return `<span class="cs ${st}"><b>${i + 1}</b>${esc(s)}</span>`;
  }).join('<i class="cs-arrow">›</i>');
  box.classList.add("on");
  clearTimeout(chainShow.t);
  if (ev.at > steps.length) chainShow.t = setTimeout(() => box.classList.remove("on"), 5000);
}

// ================================================================ sentinel and journal (tab "Сегодня")
const PRIO_CLS = { 1: "p1", 2: "p2", 3: "p3", 4: "p4" };
async function guardLoad() {
  let evs = [];
  try { evs = await rpc("events"); } catch (e) { return; }
  const box = $("#guard-events");
  if (!box) return;
  box.innerHTML = evs.slice(0, 12).map((e) => `<div class="ge ${PRIO_CLS[e.priority] || ""}">
      <i></i><span class="ge-t">${esc(e.at)}</span><span class="ge-x"><b>${esc(e.title)}</b> ${esc(e.text)}</span></div>`).join("")
    || '<div class="empty">Всё спокойно — ничего странного не замечено</div>';
}
async function guardAutostart() {
  const box = $("#guard-auto");
  if (!box.classList.contains("hidden")) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  box.innerHTML = '<div class="empty">загружаю…</div>';
  const r = await rpc("sentinel");
  box.innerHTML = r.autostart.map((a) => `<div class="ga"><span class="ga-n" title="${esc(a.cmd)}">${esc(a.name)}</span>
      <span class="ga-w">${esc(a.where)}</span>${/jarvis/i.test(a.name) ? "" : `<button class="ib del" title="Убрать из автозагрузки" onclick="guardAutoRemove('${esc(a.id).replace(/\\/g, "\\\\").replace(/'/g, "\\'")}', this)">${I("x")}</button>`}</div>`).join("")
    + (r.removed.length ? `<div class="sub-h">Убранные (можно вернуть)</div>` + r.removed.map((a) => `<div class="ga off"><span class="ga-n">${esc(a.name)}</span>
      <button class="ib" title="Вернуть" onclick="guardAutoRestore('${esc(a.name).replace(/'/g, "\\'")}')">${I("undo")}</button></div>`).join("") : "");
}
async function guardAutoRemove(id, btn) {
  if (!confirm("Убрать эту программу из автозагрузки? Её можно будет вернуть.")) return;
  const msg = await rpc("autostart_remove", { id });
  toast(msg, "Автозагрузка");
  $("#guard-auto").classList.add("hidden"); guardAutostart();
}
async function guardAutoRestore(name) { toast(await rpc("autostart_restore", { name }), "Автозагрузка"); $("#guard-auto").classList.add("hidden"); guardAutostart(); }

async function journalLoad() {
  let js = [];
  try { js = await rpc("journal"); } catch (e) { return; }
  const box = $("#journal-list");
  if (!box) return;
  box.innerHTML = js.slice(0, 12).map((g) => `<div class="jr ${g.undone ? "undone" : ""}">
      <span class="jr-t">${esc(g.at.slice(0, 5))}</span>
      <div class="jr-b"><div class="jr-q">«${esc(g.text.replace(/^\W*(джарвис|jarvis)\W*/i, ""))}»</div><div class="jr-a">${g.items.map(esc).join(" · ")}</div></div>
      ${g.undoable ? `<button class="ib" title="Отменить" onclick="journalUndo(${g.id})">${I("undo")}</button>` : g.undone ? '<span class="jr-u">отменено</span>' : ""}</div>`).join("")
    || '<div class="empty">Пока ничего — скажите «Джарвис, …»</div>';
}
async function journalUndo(id) { await rpc("journal_undo", { id }); journalLoad(); }

// ================================================================ watching and background jobs
const WATCH_ICO = { youtube: "play", twitch: "live", page: "globe", process: "gear", download: "download", media: "moon" };
async function watchLoad() {
  let ws = [], js = [];
  try { [ws, js] = await Promise.all([rpc("watches"), rpc("bg_jobs")]); } catch (e) { return; }
  const wl = $("#watch-list"), bl = $("#bg-list");
  if (!wl) return;
  wl.innerHTML = ws.map((w) => `<div class="wt"><span class="wt-i k-${w.kind}">${I(WATCH_ICO[w.kind] || "eye")}</span>
      <span class="wt-t">${esc(w.title)}${w.error ? ` <small class="bad" title="${esc(w.error)}">нет связи</small>` : ""}</span>
      <button class="ib del" title="Больше не следить" onclick="rpc('watch_delete',{id:'${w.id}'}).then(watchLoad)">${I("x")}</button></div>`).join("")
    || '<div class="empty">Ни за чем не слежу</div>';
  bl.innerHTML = js.slice(0, 6).map((j) => `<div class="bj s-${j.state}"><span class="bj-t">${esc(j.at)}</span>
      <div class="bj-b"><div>${esc(j.title)}</div>${j.state === "running" ? `<i class="bj-bar"><b style="width:${j.progress || 8}%"></b></i>` : `<small>${esc(j.result || (j.state === "queued" ? "в очереди" : ""))}</small>`}</div></div>`).join("");
}
function bgToast(j) {
  if (!j || !j.user || j.state === "running") return;
  if (j.state === "queued") toast(j.title, "В фоне", false, 3000);
}

const _hubLoadBase = hubLoad;
hubLoad = async function () { await _hubLoadBase(); guardLoad(); journalLoad(); watchLoad(); };

// ================================================================ people (Почта → Контакты)
async function peopleShow() {
  const r = await rpc("people");
  const roleName = (id) => Object.entries(r.roles).filter(([, pid]) => pid === id).map(([k]) => k);
  $("#mail-view").innerHTML = `<div class="mv-head" id="people-view"><div class="mv-s">Контакты и роли</div>
      <div class="mv-f">Как вы называете людей голосом. «Почту шефу» найдёт того, у кого роль «руководитель». Поправка
      «нет, под шефом я имею в виду Антона» меняет это навсегда.</div></div>
    <div class="people">${r.people.map((p) => `<div class="pp" data-id="${p.id}">
      <input class="pp-n" value="${esc(p.name)}" placeholder="Имя">
      <input class="pp-e" value="${esc((p.emails || []).join(", "))}" placeholder="почта">
      <input class="pp-r" value="${esc((p.roles || []).join(", "))}" placeholder="роли: руководитель, коллега…" list="pp-roles">
      <button class="btn small" onclick="personSave('${p.id}')">Сохранить</button>
      <button class="ib del" title="Удалить" onclick="personDel('${p.id}')">${I("x")}</button>
      ${roleName(p.id).length ? `<span class="pp-main">основной: ${roleName(p.id).map(esc).join(", ")}</span>` : ""}</div>`).join("") || '<div class="empty">Пока никого. Добавьте ниже или скажите «мой шеф — Антон».</div>'}</div>
    <datalist id="pp-roles">${r.known_roles.map((x) => `<option value="${esc(x)}">`).join("")}</datalist>
    <div class="pp new"><input class="pp-n" placeholder="Имя (Антон)"><input class="pp-e" placeholder="anton@firm.ru">
      <input class="pp-r" placeholder="руководитель" list="pp-roles"><button class="primary small" onclick="personSave(null)">Добавить</button></div>`;
}
async function personSave(id) {
  const row = id ? $(`.pp[data-id="${id}"]`) : $(".pp.new");
  const split = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);
  await rpc("person_save", { person: { id, name: $(".pp-n", row).value, emails: split($(".pp-e", row).value), roles: split($(".pp-r", row).value) } });
  toast("Сохранено", "Контакты"); peopleShow();
}
async function personDel(id) { if (confirm("Удалить контакт?")) { await rpc("person_delete", { id }); peopleShow(); } }
tabLoaders.hub = hubLoad;

// ================================================================ mail
let MAIL = { items: [], accounts: [], cur: null };
function mailWhen(iso) {
  if (!iso) return "";
  const d = parseLocal(iso), n = new Date();
  return d.toDateString() === n.toDateString() ? `${pad2(d.getHours())}:${pad2(d.getMinutes())}` : `${d.getDate()} ${MON[d.getMonth()].slice(0, 3)}`;
}
async function mailBadge() {
  try {
    const r = await rpc("mail_inbox", { fresh: false });
    const b = $("#badge-mail"); b.textContent = r.unread || ""; b.classList.toggle("on", (r.unread || 0) > 0);
  } catch (e) {}
}
async function mailLoad(fresh) {
  const list = $("#mail-list");
  if (fresh) list.classList.add("loading");
  let r;
  try { r = await rpc("mail_inbox", { fresh: !!fresh }); } catch (e) { list.classList.remove("loading"); return; }
  list.classList.remove("loading");
  MAIL.items = r.items; MAIL.accounts = r.accounts;
  const b = $("#badge-mail"); b.textContent = r.unread || ""; b.classList.toggle("on", (r.unread || 0) > 0);
  $("#mail-sub").textContent = r.accounts.length ? r.accounts.map((a) => a.address).join(" · ") + (r.unread ? ` · непрочитанных ${r.unread}` : "") : "";
  if (!r.accounts.length) { list.innerHTML = '<div class="empty">Ящик не подключён</div>'; mailAccounts(); return; }
  const errs = r.accounts.filter((a) => a.error);
  mailRenderList(errs.length ? `<div class="mail-err">${I("warn", "lead")}${errs.map((a) => esc(a.address) + ": " + esc(a.error)).join("<br>")}</div>` : "");
}
function mailRenderList(head = "") {
  const q = ($("#mail-q").value || "").toLowerCase();
  const items = MAIL.items.filter((h) => !q || (h.from_name + " " + h.from + " " + h.subject).toLowerCase().includes(q));
  $("#mail-list").innerHTML = head + (items.map((h) => `<div class="ml ${h.unseen ? "new" : ""} ${h.bulk ? "bulk" : ""} ${MAIL.cur && MAIL.cur.uid === h.uid ? "sel" : ""}"
      onclick="mailOpen('${h.acc}','${h.uid}')"><div class="ml-top"><b>${esc(h.from_name || h.from)}</b><span>${mailWhen(h.date)}</span></div>
      <div class="ml-s">${esc(h.subject)}</div></div>`).join("") || '<div class="empty">Писем нет</div>');
}
$("#mail-q").addEventListener("input", () => mailRenderList());
async function mailOpen(acc, uid) {
  $("#mail-view").innerHTML = '<div class="empty">открываю…</div>';
  const m = await rpc("mail_read", { acc, uid });
  MAIL.cur = m;
  const h = MAIL.items.find((x) => x.uid === uid); if (h) h.unseen = false;
  mailRenderList();
  $("#mail-view").innerHTML = `<div class="mv-head"><div class="mv-s">${esc(m.subject)}</div>
      <div class="mv-f"><b>${esc(m.from_name)}</b> &lt;${esc(m.from)}&gt; · ${esc(m.date)}</div>
      ${m.attachments.length ? `<div class="mv-att">${I("clip", "lead")}${m.attachments.map(esc).join(", ")}</div>` : ""}
      <div class="mv-btns"><button class="primary small" onclick="mailReply()">${I("reply", "lead")}Ответить</button>
      <button class="btn small" onclick="rpc('execute',{text:'прочитай это письмо'})">${I("speaker", "lead")}Прочитать вслух</button>
      <button class="btn small danger" onclick="mailDelete()">${I("trash", "lead")}В корзину</button></div></div>
    <div class="mv-body">${mdLite(m.text || "(пустое письмо)")}</div>`;
}
function mailCompose(to = "", subject = "", body = "", replyUid = null) {
  MAIL.replyUid = replyUid;
  $("#mail-view").innerHTML = `<div class="mv-head"><div class="mv-s">${replyUid ? "Ответ" : "Новое письмо"}</div></div>
    <div class="mc"><input id="mc-to" placeholder="Кому: адрес" value="${esc(to)}"><input id="mc-subj" placeholder="Тема" value="${esc(subject)}">
    <textarea id="mc-body" rows="12" placeholder="Текст письма… или скажите: «Джарвис, напиши письмо Ивану, что встреча переносится»">${esc(body)}</textarea>
    <div class="row"><button class="primary" onclick="mailSend()">Отправить</button>
    <button class="btn" onclick="mailPolish()" title="ИИ поправит орфографию и стиль">${I("pen", "lead")}Поправить текст</button>
    <span id="mc-res" class="dim"></span></div></div>`;
  setTimeout(() => $(to ? "#mc-body" : "#mc-to").focus(), 50);
}
function mailReply() {
  const m = MAIL.cur; if (!m) return;
  mailCompose(m.from, /^re:/i.test(m.subject) ? m.subject : "Re: " + m.subject, "", m.uid);
}
async function mailSend() {
  const to = $("#mc-to").value.trim(), subject = $("#mc-subj").value.trim() || "Без темы", body = $("#mc-body").value;
  if (!to || !body.trim()) { toast("Нужны адрес и текст", "Почта", true); return; }
  $("#mc-res").textContent = "отправляю…";
  try {
    await rpc("mail_send", { to, subject, body, reply_uid: MAIL.replyUid });
    toast("Письмо отправлено: " + to, "Почта");
    $("#mail-view").innerHTML = '<div class="empty">Отправлено</div>';
  } catch (e) { $("#mc-res").textContent = "не отправлено"; }
}
async function mailPolish() {
  const ta = $("#mc-body"); if (!ta.value.trim()) return;
  $("#mc-res").textContent = "улучшаю…";
  try {
    const r = await rpc("polish_text", { text: ta.value, kind: "email" });
    if (r) ta.value = r;
    $("#mc-res").textContent = "";
  } catch (e) { $("#mc-res").textContent = ""; }
}
async function mailDelete() {
  const m = MAIL.cur; if (!m || !confirm(`Переместить «${m.subject}» в корзину?`)) return;
  await rpc("mail_delete", { acc: m.acc, uid: m.uid });
  MAIL.cur = null; $("#mail-view").innerHTML = '<div class="empty">Письмо в корзине</div>'; mailLoad(true);
}

// ---- mailboxes
const MAIL_HELP = {
  "gmail.com": "Gmail: включите двухэтапную аутентификацию и создайте пароль приложения на myaccount.google.com/apppasswords.",
  "yandex.ru": "Яндекс: Почта → Настройки → Почтовые программы → разрешить IMAP; пароль приложения — id.yandex.ru → Безопасность → Пароли приложений.",
  "ya.ru": "Яндекс: Почта → Настройки → Почтовые программы → разрешить IMAP; пароль приложения — id.yandex.ru → Безопасность → Пароли приложений.",
  "mail.ru": "Mail.ru: Настройки → Безопасность → Пароли для внешних приложений → создать (с доступом IMAP/SMTP).",
  "outlook.com": "Outlook: нужен пароль приложения (account.microsoft.com → Безопасность). Некоторые аккаунты Microsoft пускают только по OAuth.",
};
function mailAccounts() {
  const accs = MAIL.accounts || [];
  const m = S.config.mail || {};
  $("#mail-view").innerHTML = `<div class="mv-head"><div class="mv-s">Почтовые ящики</div>
    <div class="mv-f">Джарвис читает и отправляет письма по IMAP/SMTP. Нужен <b>пароль приложения</b>, не обычный пароль.</div></div>
    <div class="acc-list">${accs.map((a) => `<div class="acc"><b>${esc(a.address)}</b><span class="${a.error ? "bad" : "ok"}">${a.error ? I("warn", "lead") + esc(a.error) : I("check", "lead") + "подключён"}</span>
      <button class="btn small" onclick="mailTest('${a.id}', this)">Проверить</button><button class="btn small danger" onclick="mailAccDel('${a.id}')">Удалить</button></div>`).join("") || '<div class="empty">пока нет ни одного</div>'}</div>
    <div class="acc-form"><h3>Добавить ящик</h3>
      <div class="sgrid"><label>Адрес</label><input id="ma-addr" placeholder="you@gmail.com">
      <label>Пароль приложения</label><input id="ma-pass" type="password" placeholder="xxxx xxxx xxxx xxxx">
      <label>Имя отправителя</label><input id="ma-name" placeholder="${esc(S.config.user.name || "")}" value="${esc(S.config.user.name || "")}">
      <label>Подпись</label><input id="ma-sig" placeholder="необязательно">
      <div class="full hint" id="ma-help">Серверы для Gmail, Яндекса, Mail.ru, Rambler, Outlook и iCloud подставлю сам.</div>
      <label>IMAP сервер</label><div class="inline"><input id="ma-imap" placeholder="авто"><input id="ma-iport" placeholder="993" style="max-width:80px"></div>
      <label>SMTP сервер</label><div class="inline"><input id="ma-smtp" placeholder="авто"><input id="ma-sport" placeholder="465" style="max-width:80px"></div>
      <div class="full row"><button class="primary" onclick="mailAccSave()">Сохранить и проверить</button><span id="ma-res" class="dim"></span></div></div></div>
    <div class="acc-form"><h3>Поведение</h3><div class="sgrid">
      <label>Проверять каждые</label><div class="inline"><input id="ma-every" type="number" min="1" max="60" value="${m.check_min || 3}" style="max-width:90px"><span class="dim">минут</span></div>
      <label>Важные отправители</label><input id="ma-vip" value="${esc((m.vip || []).join(", "))}" placeholder="boss@work.ru, Мама, Иван — о них скажу сразу">
      <label>Контакты</label><textarea id="ma-contacts" rows="3" placeholder="мама = mama@mail.ru&#10;Иван = ivan@yandex.ru">${esc(Object.entries(m.contacts || {}).map(([k, v]) => `${k} = ${v}`).join("\n"))}</textarea>
      <div class="full hint">Новое письмо от важного отправителя, от того, кому вы писали, или со словом «срочно» — скажу вслух и предложу прочитать. Рассылки только посчитаю.</div>
    </div></div>`;
  $("#ma-addr").addEventListener("input", (e) => { const d = e.target.value.split("@")[1] || ""; $("#ma-help").textContent = MAIL_HELP[d.toLowerCase()] || "Серверы для Gmail, Яндекса, Mail.ru, Rambler, Outlook и iCloud подставлю сам."; });
  const save = () => {
    const contacts = {};
    $("#ma-contacts").value.split("\n").forEach((l) => { const [k, v] = l.split("="); if (k && v && v.includes("@")) contacts[k.trim()] = v.trim(); });
    saveConfig({ mail: { check_min: Math.max(1, +$("#ma-every").value || 3), vip: $("#ma-vip").value.split(",").map((x) => x.trim()).filter(Boolean), contacts: null } })
      .then(() => saveConfig({ mail: { contacts } })).then(() => toast("Сохранено", "Почта"));
  };
  ["#ma-every", "#ma-vip", "#ma-contacts"].forEach((id) => $(id).addEventListener("change", save));
}
async function mailAccSave() {
  const acc = { address: $("#ma-addr").value.trim(), password: $("#ma-pass").value.trim(), name: $("#ma-name").value.trim(),
    signature: $("#ma-sig").value.trim(), imap_host: $("#ma-imap").value.trim(), imap_port: +$("#ma-iport").value || 0,
    smtp_host: $("#ma-smtp").value.trim(), smtp_port: +$("#ma-sport").value || 0 };
  if (!acc.address || !acc.password) { toast("Нужны адрес и пароль приложения", "Почта", true); return; }
  $("#ma-res").textContent = "подключаюсь…";
  try {
    const a = await rpc("mail_account_save", { acc });
    const t = await rpc("mail_test", { id: a.id });
    $("#ma-res").innerHTML = `<span style="color:var(--green)">✓ работает, непрочитанных: ${t.unseen}</span>`;
    S.config = await rpc("config_set", { patch: {} });
    setTimeout(() => mailLoad(true), 600);
  } catch (e) { $("#ma-res").innerHTML = `<span style="color:var(--red)">✕ ${esc(e.message)}</span>`; }
}
async function mailTest(id, btn) { btn.textContent = "…"; try { const t = await rpc("mail_test", { id }); btn.textContent = `✓ ${t.ms} мс`; } catch (e) { btn.textContent = "✕"; } }
async function mailAccDel(id) { if (!confirm("Удалить ящик из Джарвиса? Письма на сервере останутся.")) return; await rpc("mail_account_delete", { id }); MAIL.accounts = MAIL.accounts.filter((a) => a.id !== id); mailAccounts(); mailLoad(false); }

tabLoaders.mail = () => { mailLoad(false); };
setTimeout(mailBadge, 4000);
setInterval(mailBadge, 5 * 60 * 1000);
