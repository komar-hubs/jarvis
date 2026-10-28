// Settings → backups and PC upkeep: program updates (winget), sorting Downloads, "turn off after the video".
const MT = { updates: null };
function fmtSize(n) { return n > 2 ** 20 ? (n / 2 ** 20).toFixed(1) + " МБ" : Math.max(1, Math.round(n / 1024)) + " КБ"; }

// a panel with this id inside a Settings section, created once: (section, id, where: "first" | "after:<n>" | "end")
function sPanel(secId, id, where) {
  const sec = $("#" + secId); if (!sec) return null;
  let el = $("#" + id);
  if (el) return el;
  el = document.createElement("div"); el.id = id; el.className = "panel";
  if (where === "first") sec.prepend(el);
  else if (where && where.startsWith("after:")) { const ref = sec.children[+where.slice(6)]; ref ? ref.after(el) : sec.appendChild(el); }
  else sec.appendChild(el);
  return el;
}

async function maintLoad() {
  if (!$("#settings")) return;
  let b;
  try { b = await rpc("backups"); } catch (e) { return; }
  const bg = (S.config && S.config.background) || {};
  const last = b.last ? new Date(b.last * 1000).toLocaleString("ru-RU") : "ещё не делалась";
  // Обслуживание: PC upkeep, then backups, then Jarvis's own updates (already there)
  const svc = sPanel("sec-service", "svc-panel", "first");
  const bk = sPanel("sec-service", "bk-panel", "after:0");
  sPanel("sec-help", "phone-panel", "after:0");          // Помощь в делах: after «Забота и продуктивность»
  if (!svc || !bk) return;
  bk.innerHTML = `<h3><span>${I("box", "lead")}Резервные копии</span><span class="h3-meta">последняя: ${esc(last)}</span></h3>
    <p class="hint">В копию входят настройки, команды, автоматизации, интеграции, плагины с их данными, заметки, задачи, напоминания, привычки, контакты и чат. Модели и кэш не входят: они скачаются сами. <b class="warn-t">В копии ваши ключи ИИ и пароли почты: не отправляйте её другим.</b> Чтобы поделиться плагином, используйте экспорт плагина.</p>
    <div class="row"><button class="primary" onclick="backupMake()">${I("box", "lead")}Сделать копию сейчас</button>
      <button class="btn" onclick="backupPick()">${I("import", "lead")}Восстановить из файла…</button>
      <button class="btn" onclick="rpc('backup_open_folder')">${I("folder", "lead")}Открыть папку</button>
      <button class="btn" onclick="backupFolder()">Сменить папку</button></div>
    <div class="row"><label class="switch"><input type="checkbox" id="bk-auto" ${b.auto ? "checked" : ""}><span></span>Автоматически раз в неделю</label>
      <span class="dim">хранить последних</span><input id="bk-keep" type="number" min="1" max="50" value="${b.keep}" style="width:70px;flex:none"></div>
    <div class="dim bk-folder">Папка: ${esc(b.folder)}</div>
    <div class="bk-list">${b.items.slice(0, 8).map((x) => `<div class="bk"><span class="bk-n">${esc(x.at)}</span>
      <span class="bk-tag">${x.safety ? "перед восстановлением" : x.auto ? "авто" : "вручную"}</span><span class="dim">${fmtSize(x.size)}</span>
      <button class="btn small" onclick='backupRestore(${JSON.stringify(x.path)})'>Восстановить</button></div>`).join("") || '<div class="empty">Копий пока нет</div>'}</div>`;
  svc.innerHTML = `<h3><span>${I("wrench", "lead")}Обслуживание ПК</span></h3>
    <div class="mt-sec"><b>Обновления программ</b><span class="dim">через winget, встроенный в Windows. Некоторые программы попросят разрешение администратора.</span>
      <div class="row"><button class="btn" id="up-check" onclick="updatesCheck()">${I("refresh", "lead")}Проверить обновления</button><span class="dim">«Джарвис, обнови все программы»</span></div>
      <div id="up-list"></div></div>
    <div class="mt-sec"><b>Папка «Загрузки»</b><span class="dim">Раскладываю по папкам: Документы, Картинки, Видео, Музыка, Архивы, Установщики, Торренты, Код. Файлы младше 10 минут и неизвестные типы не трогаю.</span>
      <div class="row"><button class="btn" onclick="rpc('downloads_sort')">${I("folder", "lead")}Разложить сейчас</button>
        <button class="btn" onclick="rpc('downloads_sort',{undo:true})">${I("undo", "lead")}Вернуть как было</button>
        <label class="switch"><input type="checkbox" id="dl-auto" ${bg.sort_downloads ? "checked" : ""}><span></span>Раскладывать сам раз в несколько часов</label></div></div>
    <div class="mt-sec"><b>Выключить, когда досмотрю</b><span class="dim">Жду, пока звук затихнет и вы не будете трогать мышь пару минут, предупреждаю голосом и выключаю через минуту. Работает на самом ПК: интернет и ИИ не нужны.</span>
      <div class="row" id="media-row"></div></div>`;
  $("#bk-auto").onchange = (e) => rpc("backup_set", { auto: e.target.checked });
  $("#bk-keep").onchange = (e) => rpc("backup_set", { keep: +e.target.value || 5 });
  $("#dl-auto").onchange = (e) => saveConfig({ background: { sort_downloads: e.target.checked } });
  if (MT.updates) updatesRender();
  mediaRow();
  personaPanel();
  phonePanel();
  overlaysPanel();
}
async function mediaRow() {
  const row = $("#media-row"); if (!row) return;
  let ws = [];
  try { ws = await rpc("watches"); } catch (e) {}
  const w = ws.find((x) => x.kind === "media");
  row.innerHTML = w ? `<span class="ok-t">${I("check", "lead")}Жду: ${esc(w.title)}</span><button class="btn danger small" onclick="mediaCancel()">Отменить</button>`
    : `<button class="btn" onclick="mediaStart('shutdown')">Выключить ПК после видео</button><button class="btn" onclick="mediaStart('sleep')">Усыпить ПК после видео</button>
       <button class="btn" onclick="mediaStart('pause')">Только выключить звук</button><span class="dim">голосом: «выключи комп, когда досмотрю»</span>`;
}
async function mediaStart(act) { toast(await rpc("watch_media", { act }), "После видео"); mediaRow(); }
async function mediaCancel() { await rpc("watch_media_cancel"); toast("Отменено", "После видео"); mediaRow(); }

// ---------------------------------------------------------------- phone through KDE Connect
async function phonePanel() {
  const box = $("#phone-panel"); if (!box) return;
  let r; try { r = await rpc("phone"); } catch (e) { return; }
  const s = r.settings, dev = r.devices.find((d) => d.id === r.device);
  const sw = (k, label) => `<label class="switch"><input type="checkbox" data-ph="${k}" ${s[k] ? "checked" : ""}><span></span>${label}</label>`;
  if (!r.installed) {
    box.innerHTML = `<h3><span>${I("chat", "lead")}Телефон</span><span class="h3-meta">KDE Connect не установлен</span></h3>
      <p class="hint">Уведомления и звонки Android прямо на ПК, «найди телефон», ссылки и буфер на телефон, SMS, фото с камеры телефона. Всё идёт по вашей Wi-Fi, без облака и аккаунтов. iPhone так не умеет.</p>
      <ol class="ph-steps"><li>Установите KDE Connect на ПК кнопкой ниже (Windows может спросить разрешение).</li>
        <li>Поставьте приложение <b>KDE Connect</b> на телефон из Google Play.</li>
        <li>На телефоне выберите этот ПК → «Запросить сопряжение», на ПК подтвердите.</li></ol>
      <div class="row"><button class="primary" onclick="rpc('phone_install')">${I("download", "lead")}Установить KDE Connect</button></div>`;
    return;
  }
  box.innerHTML = `<h3><span>${I("chat", "lead")}Телефон</span><span class="h3-meta ${dev ? "ok-t" : ""}">${dev ? "на связи: " + esc(dev.name) : r.devices.length ? "телефон не в сети" : "нет сопряжённого телефона"}</span></h3>
    ${!dev ? `<div class="ph-ip"><b>Телефон не видит ПК?</b> Так бывает из-за VPN (Radmin, WireGuard) или настроек роутера: устройства не находят друг друга сами. Подключите по адресу:
      <ol class="ph-steps"><li>Телефон и ПК в одной Wi-Fi, VPN на телефоне выключен.</li>
        <li>На телефоне: KDE Connect → ⋮ → «Добавить устройство по IP» → <code>${esc((r.pc_ips || [])[0] || "IP этого ПК")}</code></li>
        <li>Или наоборот: впишите IP телефона (Настройки телефона → О телефоне → IP-адрес) и нажмите «Подключить».</li></ol>
      <div class="row"><input id="ph-ipin" placeholder="IP телефона, например 192.168.1.105" value="${esc(r.phone_ip || "")}" style="max-width:260px">
        <button class="btn" id="ph-ipgo">Подключить по IP</button><span class="dim" id="ph-ipres"></span></div></div>` : ""}
    ${r.devices.length > 1 ? `<div class="sgrid"><label>Телефон</label><select id="ph-dev">${r.devices.map((d) => `<option value="${esc(d.id)}" ${d.id === r.device ? "selected" : ""}>${esc(d.name)}${d.reachable ? "" : " (не в сети)"}</option>`).join("")}</select></div>` : ""}
    <div class="row"><button class="btn" ${dev ? "" : "disabled"} onclick="phoneAct('ring')">${I("bell", "lead")}Найти телефон</button>
      <button class="btn" ${dev ? "" : "disabled"} onclick="phoneAct('clipboard')">${I("export", "lead")}Буфер на телефон</button>
      <button class="btn" ${dev ? "" : "disabled"} onclick="phoneAct('photo')">${I("eye", "lead")}Фото с телефона</button>
      <button class="btn small" ${dev ? "" : "disabled"} onclick="rpc('phone_find',{on:false}).then((r)=>toast(r,'Телефон'))">${I("check", "lead")}Нашёл</button></div>
    <p class="hint">«Найти телефон» звонит каждые 8 секунд до двух минут, пока не скажете «нашёл». Если на телефоне приходит только тихое уведомление: Настройки телефона → Приложения → KDE Connect → разрешите «Всплывающие окна в фоне», «Экран блокировки» и «Полноэкранные уведомления», а в KDE Connect → «Найти телефон» выберите громкую мелодию.</p>
    <div class="ph-sw">${sw("enabled", "Следить за телефоном")}${sw("calls", "Говорить, кто звонит")}${sw("announce", "Озвучивать уведомления")}${sw("filter", "Отсеивать неважное: рекламу, загрузки, системные")}</div>
    <div class="sgrid ph-rules"><label>Мессенджеры</label>${ruleSel("ph-mrule", r.rule_titles, s.messenger_rule)}
      <label>Остальные приложения</label>${ruleSel("ph-drule", r.rule_titles, s.default_rule)}</div>
    <div class="field"><label>Правила для приложений <span class="dim">— как принимать уведомления от каждого</span></label>
      <div class="ph-apps">${r.apps.map((a) => `<div class="ph-app ${(s.rules || {})[a] ? "set" : ""}"><span>${esc(a)}</span>${ruleSel("", { auto: "как для всех", ...r.rule_titles }, (s.rules || {})[a] || "auto", a)}</div>`).join("")}</div>
      <div class="row ph-add"><input id="ph-newapp" placeholder="другое приложение: как оно называется на телефоне" list="ph-apps-dl">
        <datalist id="ph-apps-dl">${r.recent.map((x) => x.app).filter((v, i, a) => a.indexOf(v) === i).map((a) => `<option value="${esc(a)}">`).join("")}</datalist>
        ${ruleSel("ph-newrule", r.rule_titles, "ask")}<button class="btn" id="ph-addapp">Добавить</button></div></div>
    <p class="hint">Всплывающие окна KDE Connect на ПК отключены: говорит только Джарвис. «Спросить, прочитать ли» — скажу, откуда сообщение, и прочитаю текст, только если ответите «да». Голосом: «не говори про сообщения из Telegram», «читай сообщения из Max», «спрашивай про сообщения из WhatsApp».</p>
    <div class="field"><label>Контакты для звонков и SMS (по строке: имя = номер) <span class="dim">— «позвони маме», «смс маме что…»</span></label><textarea id="ph-contacts" rows="3" placeholder="мама = +79001234567">${esc(Object.entries(s.contacts || {}).map(([k, v]) => `${k} = ${v}`).join("\n"))}</textarea></div>
    ${r.error ? `<div class="dim ph-err">${esc(r.error)}</div>` : ""}
    <div class="ph-recent">${r.recent.map((x) => `<div class="ph-n ${x.call ? "call" : ""} ${x.rule === "silent" ? "muted" : ""}"><span class="ah-t">${esc(x.at)}</span><b>${esc(x.app)}</b>
      <span class="ph-tag">${x.call ? "звонок" : x.rule === "silent" ? (x.why || "молча") : esc((r.rule_titles[x.rule] || "").toLowerCase())}</span>
      <span>${x.rule === "silent" ? "" : esc(x.text)}</span></div>`).join("") || '<div class="empty">Уведомлений с телефона пока не было</div>'}</div>`;
  $$("#phone-panel [data-ph]").forEach((c) => c.onchange = () => rpc("phone_set", { [c.dataset.ph]: c.checked }));
  const ipgo = $("#ph-ipgo");
  if (ipgo) ipgo.onclick = async () => {
    $("#ph-ipres").textContent = "перезапускаю KDE Connect и стучусь на телефон… секунд 15";
    ipgo.disabled = true;
    try { const r2 = await rpc("phone_ip", { ip: $("#ph-ipin").value }); $("#ph-ipres").textContent = r2.devices.length ? "телефон найден" : "пока не видно: откройте KDE Connect на телефоне"; }
    catch (e) { $("#ph-ipres").textContent = ""; }
    ipgo.disabled = false; phonePanel();
  };
  const devSel = $("#ph-dev"); if (devSel) devSel.onchange = () => rpc("phone_set", { device: devSel.value }).then(phonePanel);
  $("#ph-mrule").onchange = (e) => rpc("phone_set", { messenger_rule: e.target.value });
  $("#ph-drule").onchange = (e) => rpc("phone_set", { default_rule: e.target.value });
  $$("#phone-panel [data-app]").forEach((sel) => sel.onchange = () => { sel.closest(".ph-app").classList.toggle("set", sel.value !== "auto"); rpc("phone_rule", { app: sel.dataset.app, rule: sel.value }); });
  $("#ph-addapp").onclick = async () => {
    const app = $("#ph-newapp").value.trim(); if (!app) { $("#ph-newapp").focus(); return; }
    await rpc("phone_rule", { app, rule: $("#ph-newrule").value });
    toast(`${app}: ${r.rule_titles[$("#ph-newrule").value].toLowerCase()}`, "Телефон"); phonePanel();
  };
  $("#ph-contacts").onchange = (e) => {
    const book = {};
    e.target.value.split("\n").forEach((l) => { const [k, v] = l.split("="); if (k && v && /\d{5,}/.test(v.replace(/\D/g, ""))) book[k.trim()] = v.trim(); });
    rpc("phone_set", { contacts: book }).then(() => toast("Сохранено", "Контакты для SMS"));
  };
}
function ruleSel(id, titles, cur, app) {
  return `<select ${id ? `id="${id}"` : ""} ${app ? `data-app="${esc(app)}"` : ""}>${Object.entries(titles).map(([k, t]) => `<option value="${k}" ${k === cur ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>`;
}
async function phoneAct(action) {
  try { const r = await rpc("phone_action", { action }); toast(r, "Телефон"); } catch (e) {}
}

// ---------------------------------------------------------------- persona: Jarvis or Friday (top of «Личность и голос»)
async function personaPanel() {
  const box = $("#persona-block"); if (!box || S.tab !== "settings") return;
  let r; try { r = await rpc("persona"); } catch (e) { return; }
  const cur = r.current.id;
  box.innerHTML = `
    <div class="persona-cards">
      <button class="pcard ${cur === "jarvis" ? "sel" : ""}" data-p="jarvis"><b>J.A.R.V.I.S.</b><span>Джарвис · мужской голос, британская ирония, обращается «сэр»</span></button>
      <button class="pcard friday ${cur === "friday" ? "sel" : ""}" data-p="friday"><b>F.R.I.D.A.Y.</b><span>Пятница · женский голос, тёплая и живая, обращается «босс»</span></button>
      <button class="pcard ultron ${cur === "ultron" ? "sel" : ""}" data-p="ultron"><b>ULTRON</b><span>Альтрон · низкий голос, холодная ирония, обращается «человек», красный интерфейс</span></button></div>
    <p class="hint">Голосом: «позови Пятницу», «позови Альтрона», «верни Джарвиса». Включённая личность отзывается и на своё имя, и на «Джарвис».</p>`;
  $$("#persona-block .pcard").forEach((b) => b.onclick = async () => { if (b.dataset.p !== cur) { await rpc("persona_set", { id: b.dataset.p }); } });
}

// ---------------------------------------------------------------- backups
async function backupMake() {
  toast("Делаю копию…", "Резервная копия", false, 2500);
  const r = await rpc("backup_make");
  toast(`${r.name} · ${fmtSize(r.size)}`, "Копия готова");
  maintLoad();
}
async function backupFolder() { const p = await winApi("pick_folder"); if (p) { await rpc("backup_set", { folder: p }); maintLoad(); } }
async function backupPick() { const p = await winApi("pick_file", "zip"); if (p) backupRestore(p); }
async function backupRestore(path) {
  const m = await rpc("backup_inspect", { path });
  const w = m.what || {};
  const msg = `Восстановить копию от ${new Date(m.created).toLocaleString("ru-RU")}` + (m.pc ? ` (ПК ${m.pc})` : "") +
    `?\n\nВнутри: плагинов ${w.plugins || 0}, ${w.notes ? "заметки, " : ""}${w.commands ? "команды, " : ""}${w.automations ? "автоматизации, " : ""}всего файлов ${w.files}.\n\n` +
    "Текущие настройки и данные заменятся. Перед этим я сам сохраню копию текущего состояния. Джарвис перезапустится.";
  if (!confirm(msg)) return;
  const r = await rpc("backup_restore", { path });
  toast("Восстановлено. Перезапускаюсь… Прежнее состояние сохранено в копии «перед восстановлением».", "Резервная копия", false, 8000);
}

// ---------------------------------------------------------------- winget
async function updatesCheck() {
  const b = $("#up-check"); b.disabled = true; b.lastChild.textContent = "Проверяю… до минуты";
  try { MT.updates = await rpc("updates_check"); } catch (e) { MT.updates = null; }
  b.disabled = false; b.lastChild.textContent = "Проверить снова";
  updatesRender();
}
function updatesRender() {
  const box = $("#up-list"); if (!box) return;
  const u = MT.updates || [];
  box.innerHTML = !u.length ? '<div class="empty">Все программы обновлены</div>' : `
    <div class="up-list">${u.map((x, i) => `<label class="up"><input type="checkbox" checked data-up="${i}"><span class="up-n">${esc(x.name)}</span>
      <span class="dim">${esc(x.version)} → <b>${esc(x.available)}</b></span></label>`).join("")}</div>
    <div class="row"><button class="primary" onclick="updatesInstall()">Обновить выбранные (${u.length})</button><span class="dim">идёт в фоне, по одной программе</span></div>`;
}
async function updatesInstall() {
  const ids = $$("#up-list [data-up]:checked").map((c) => MT.updates[+c.dataset.up].id);
  if (!ids.length) return;
  await rpc("updates_install", { ids });   // the "В фоне" toast and the progress on «Сегодня» come from the job itself
  MT.updates = null; updatesRender();
}

const _settingsLoader = tabLoaders.settings;
tabLoaders.settings = async () => { await _settingsLoader(); maintLoad(); };
