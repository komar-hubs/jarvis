// «Моды»: add-ons the user switches on or off (jarvis/mods.py). Each card says what the mod adds, what is inside and
// what changes without it; the phone remote follows a switch at once.
async function modsLoad() {
  const box = $("#mods-list");
  if (!box) return;
  let list = [];
  try { list = await rpc("mods"); } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message || e)}</div>`; return; }
  modsRender(list);
}

function modsRender(list) {
  const on = list.filter((m) => m.on).length;
  $("#mods-count").textContent = `${on} из ${list.length} включено`;
  $("#mods-list").innerHTML = list.map((m) => `
    <div class="panel mod-card ${m.on ? "on" : ""}" data-mod="${esc(m.id)}">
      <div class="mod-hd">
        <div class="mod-ic">${I(m.icon || "puzzle")}</div>
        <div class="mod-tt"><b>${esc(m.title)}</b><span class="mod-where">${esc(m.where)}${m.default ? " · по умолчанию включён" : ""}</span></div>
        <label class="switch" title="${m.on ? "Выключить" : "Включить"}"><input type="checkbox" data-mod-sw="${esc(m.id)}" ${m.on ? "checked" : ""}><span></span></label>
      </div>
      <p class="mod-what">${esc(m.what)}</p>
      <ul class="mod-in">${(m.inside || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
      <div class="mod-off">${m.on ? I("check", "lead") + "Работает" : I("x", "lead") + esc(m.off || "Выключен")}</div>
    </div>`).join("") + `
    <div class="panel mod-card mod-soon">
      <div class="mod-hd"><div class="mod-ic">${I("plus")}</div><div class="mod-tt"><b>Новые моды</b><span class="mod-where">скоро</span></div></div>
      <p class="mod-what">Сюда будут добавляться новые дополнения. Каждое можно включить или выключить, ничего не удаляя.</p>
    </div>`;
  $$("#mods-list [data-mod-sw]").forEach((cb) => cb.addEventListener("change", async () => {
    cb.disabled = true;
    try {
      const fresh = await rpc("mod_set", { id: cb.dataset.modSw, on: cb.checked });
      const m = fresh.find((x) => x.id === cb.dataset.modSw);
      toast(`${m.title}: ${m.on ? "включён" : "выключен"}. Пульт на телефоне обновится сам.`, "Моды");
      modsRender(fresh);
    } catch (e) { cb.checked = !cb.checked; cb.disabled = false; }   // rpc() has shown the error
  }));
}

tabLoaders.mods = modsLoad;
