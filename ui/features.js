// Settings → «Что делает Джарвис» (jarvis/features.py): every background feature — what it does, why, where its data
// goes — with a switch, so nobody has to wonder what an assistant with a microphone does behind their back.
let FT = { open: false };
try { FT.open = localStorage.getItem("feat-open") === "1"; } catch (e) {}

async function featuresPanel() {
  const box = $("#feat-panel");
  if (!box) return;
  let rows;
  try { rows = await rpc("features"); } catch (e) { return; }
  const sw = rows.filter((r) => r.switch), on = sw.filter((r) => r.on).length;
  const shown = FT.open ? rows : rows.slice(0, 7);
  box.innerHTML = `<h3><span>${I("shield", "lead")}Что делает Джарвис и как это выключить</span><span class="h3-meta">включено ${on} из ${sw.length}</span></h3>
    <p class="hint">Всё, что Джарвис делает сам, без команды. Распознавание речи, память экрана и статистика остаются на этом компьютере. В интернет уходят только ваши запросы к облачному ИИ, если вы его выбрали, и проверка номера новой версии. Любую строку можно выключить.</p>
    <div class="ft-wrap"><table class="ft">
      <thead><tr><th>Функция</th><th>Что делает</th><th>Зачем</th><th>Данные</th><th></th></tr></thead>
      <tbody>${shown.map(ftRow).join("")}</tbody></table></div>
    ${rows.length > 7 ? `<div class="row"><button class="btn" id="ft-more">${FT.open ? "Свернуть" : `Показать все (${rows.length})`}</button></div>` : ""}`;
  box.querySelectorAll("input[data-ft]").forEach((i) => i.onchange = async () => {
    i.disabled = true;
    try { await rpc("feature_set", { id: i.dataset.ft, on: i.checked }); } catch (e) { i.checked = !i.checked; toast(e.message, "Настройки", true); }
    featuresPanel();
  });
  const more = $("#ft-more");
  if (more) more.onclick = () => { FT.open = !FT.open; try { localStorage.setItem("feat-open", FT.open ? "1" : "0"); } catch (e) {} featuresPanel(); };
}

function ftRow(r) {
  const ctl = r.switch
    ? `<label class="switch" title="${r.on ? "Выключить" : "Включить"}"><input type="checkbox" data-ft="${esc(r.id)}" ${r.on ? "checked" : ""}><span></span></label>`
    : `<span class="ft-req">${esc(r.badge || "по запросу")}</span>`;
  return `<tr class="${r.switch && !r.on ? "off" : ""}"><td class="ft-t">${esc(r.title)}</td><td>${esc(r.what)}${r.note ? `<div class="dim">${esc(r.note)}</div>` : ""}</td>
    <td>${esc(r.why)}</td><td class="dim">${esc(r.data)}</td><td class="ft-c">${ctl}</td></tr>`;
}
