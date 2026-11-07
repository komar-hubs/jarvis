// Settings → «Приватность» (jarvis/features.py): every background feature — what it does, why, where its data goes —
// with a switch, so nobody has to wonder what an assistant with a microphone does behind their back. The switch of a
// feature lives only here; other panels show a chip with its state that leads to its row (featChip).
let FT = { rows: [] };

// a chip for a panel: "● включено · Приватность", clickable
function featChip(id) {
  return `<a href="#" class="fchip" data-fchip="${esc(id)}" title="Переключатель — в разделе «Приватность»"><i></i><span>…</span></a>`;
}

function featChipsFill() {
  $$("[data-fchip]").forEach((a) => {
    const r = FT.rows.find((x) => x.id === a.dataset.fchip);
    if (!r) return;
    a.classList.toggle("on", !!r.on);
    a.querySelector("span").textContent = (r.on ? "включено" : "выключено") + " · Приватность";
    a.onclick = (e) => { e.preventDefault(); featGoto(r.id); };
  });
}

function featGoto(id) {
  if (S.tab !== "settings") { go("settings"); setTimeout(() => featGoto(id), 450); return; }   // a chip on another tab
  const row = $(`#feat-panel tr[data-row="${id}"]`);
  if (!row) return;
  if (window.setSearchClear) setSearchClear();
  row.scrollIntoView({ block: "center", behavior: "smooth" });
  row.classList.remove("flash"); void row.offsetWidth; row.classList.add("flash");
}

async function featuresPanel() {
  const box = $("#feat-panel");
  if (!box) return;
  try { FT.rows = await rpc("features"); } catch (e) { return; }
  const rows = FT.rows, sw = rows.filter((r) => r.switch), on = sw.filter((r) => r.on).length;
  box.innerHTML = `<h3><span>${I("shield", "lead")}Что делает Джарвис и как это выключить</span><span class="h3-meta">включено ${on} из ${sw.length}</span></h3>
    <p class="hint">Всё, что Джарвис делает сам, без команды. Распознавание речи, память экрана и статистика остаются на этом компьютере. В интернет уходят только ваши запросы к облачному ИИ, если вы его выбрали, и проверка номера новой версии. Любую строку можно выключить.</p>
    <div class="ft-wrap"><table class="ft">
      <thead><tr><th>Функция</th><th>Что делает</th><th>Зачем</th><th>Данные</th><th></th></tr></thead>
      <tbody>${rows.map(ftRow).join("")}</tbody></table></div>`;
  box.querySelectorAll("input[data-ft]").forEach((i) => i.onchange = async () => {
    i.disabled = true;
    try { await rpc("feature_set", { id: i.dataset.ft, on: i.checked }); } catch (e) { i.checked = !i.checked; toast(e.message, "Приватность", true); }
    featuresPanel();
  });
  featChipsFill();
}

function ftRow(r) {
  const ctl = r.switch
    ? `<label class="switch" title="${r.on ? "Выключить" : "Включить"}"><input type="checkbox" data-ft="${esc(r.id)}" ${r.on ? "checked" : ""}><span></span></label>`
    : `<span class="ft-req">${esc(r.badge || "по запросу")}</span>`;
  return `<tr data-row="${esc(r.id)}" class="${r.switch && !r.on ? "off" : ""}"><td class="ft-t">${esc(r.title)}</td><td>${esc(r.what)}${r.note ? `<div class="dim">${esc(r.note)}</div>` : ""}</td>
    <td>${esc(r.why)}</td><td class="dim">${esc(r.data)}</td><td class="ft-c">${ctl}</td></tr>`;
}
