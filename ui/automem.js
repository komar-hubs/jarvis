// Settings → «Помощь в делах» → «Память и автоматика»: screen memory (jarvis/screenmem.py), "ушёл / вернулся"
// by the phone (jarvis/phone.py presence_tick), macros (jarvis/macros.py) and the "сделай за меня" agent.
async function memPanel() {
  const box = sPanel("sec-help", "mem-panel", "end");
  if (!box) return;
  let sm, macros;
  try { [sm, macros] = await Promise.all([rpc("screenmem_state"), rpc("macros")]); } catch (e) { return; }
  const c = S.config || {}, ph = c.phone || {}, ag = c.agent || {};
  const sw = (id, on, label) => `<label class="switch"><input type="checkbox" id="${id}" ${on ? "checked" : ""}><span></span>${label}</label>`;
  box.innerHTML = `<h3><span>${I("eye", "lead")}Память и автоматика</span><span class="h3-meta">без запросов к ИИ</span></h3>
    <div class="mt-sec"><b>Память экрана</b><span class="dim">Раз в минуту, пока вы за ПК, Джарвис запоминает текст активного окна (распознавание прямо на ПК). Игры и фильмы во весь экран, менеджеры паролей, банки и приватные окна пропускаются. «Джарвис, где я видел …», «что было на экране в 15:00».</span>
      <div class="row">${sw("mem-on", sm.enabled, "Запоминать")}
        <span class="dim">хранить</span><select id="mem-days" style="width:110px;flex:none">${[1, 3, 7, 14, 30].map((d) => `<option value="${d}" ${d === sm.days ? "selected" : ""}>${d} дн.</option>`).join("")}</select>
        <span class="dim">${sm.count} снимков · ${sm.mb} МБ</span>
        <button class="btn small" id="mem-wipe">${I("trash", "lead")}Стереть всё</button></div>
      <div class="row"><input id="mem-q" placeholder="Что вы видели? Например: видеокарта, договор, рецепт" spellcheck="false">
        <button class="btn" id="mem-go">${I("search", "lead")}Найти</button></div>
      <div id="mem-res"></div></div>
    <div class="mt-sec"><b>Ушёл — вернулся</b><span class="dim">Телефон пропал из домашней Wi-Fi больше чем на 3 минуты, а ПК никто не трогает — музыка на паузу и блокировка. Вернулись — «С возвращением» и что пришло, пока вас не было. Фильм или игра во весь экран не прерываются.</span>
      <div class="row">${sw("pr-on", ph.presence !== false, "Следить")}${sw("pr-lock", ph.presence_lock !== false, "Блокировать ПК, когда ухожу")}</div></div>
    <div class="mt-sec"><b>Макросы</b><span class="dim">«Джарвис, запиши макрос» → делаете действия → «стоп запись» → название. Потом скажите название, и Джарвис повторит.</span>
      <div class="mac-list">${macros.length ? macros.map((n) => `<div class="row"><span style="flex:1">${esc(n)}</span>
        <button class="btn small" data-mplay="${esc(n)}">${I("play", "lead")}Выполнить</button>
        <button class="btn small" data-mdel="${esc(n)}">${I("trash", "lead")}</button></div>`).join("") : '<div class="empty">Макросов пока нет</div>'}</div></div>
    <div class="mt-sec"><b>«Сделай за меня»</b><span class="dim">«Джарвис, сделай за меня: …» — ИИ сам выполняет задачу в программах по шагам. Пароли, оплату, отправку и удаление он не делает без вашего «да». Тратит запросы только когда вы просите.</span>
      <div class="row"><span class="dim">шагов не больше</span><select id="ag-steps" style="width:90px;flex:none">${[6, 8, 12, 16, 20].map((d) => `<option ${d === (ag.max_steps || 12) ? "selected" : ""}>${d}</option>`).join("")}</select>
        ${sw("ag-show", ag.show_steps !== false, "Показывать маркером, куда нажимает")}</div></div>`;
  $("#mem-on").onchange = (e) => rpc("screenmem_set", { enabled: e.target.checked });
  $("#mem-days").onchange = (e) => rpc("screenmem_set", { days: +e.target.value });
  $("#mem-wipe").onclick = () => { if (confirm("Стереть всё, что Джарвис запомнил с экрана?")) rpc("screenmem_wipe").then(memPanel); };
  const search = async () => {
    const q = $("#mem-q").value.trim(); if (!q) return;
    const res = await rpc("screenmem_search", { q }).catch(() => []);
    $("#mem-res").innerHTML = res.length ? res.map((r) => `<div class="mem-hit" data-sid="${r.id}"><b>${esc(r.when)}</b> · ${esc(r.exe || "")}
      <span class="dim">${esc((r.title || "").slice(0, 90))}</span><div class="mem-snip">${esc(r.snippet || "").replace(/\[/g, "<mark>").replace(/\]/g, "</mark>")}</div></div>`).join("")
      : '<div class="empty">Ничего не нашлось</div>';
    document.querySelectorAll(".mem-hit").forEach((el) => el.onclick = () => rpc("screenmem_open", { id: +el.dataset.sid }));
  };
  $("#mem-go").onclick = search;
  $("#mem-q").onkeydown = (e) => { if (e.key === "Enter") search(); };
  $("#pr-on").onchange = (e) => saveConfig({ phone: { presence: e.target.checked } });
  $("#pr-lock").onchange = (e) => saveConfig({ phone: { presence_lock: e.target.checked } });
  $("#ag-steps").onchange = (e) => saveConfig({ agent: { max_steps: +e.target.value } });
  $("#ag-show").onchange = (e) => saveConfig({ agent: { show_steps: e.target.checked } });
  box.querySelectorAll("[data-mplay]").forEach((b) => b.onclick = () => { rpc("macro_play", { name: b.dataset.mplay }); toast("Выполняю макрос — «стоп», чтобы прервать", "Макрос"); });
  box.querySelectorAll("[data-mdel]").forEach((b) => b.onclick = () => { if (confirm(`Удалить макрос «${b.dataset.mdel}»?`)) rpc("macro_delete", { name: b.dataset.mdel }).then(memPanel); });
}
