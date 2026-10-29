// Settings → «Быстрая панель» (jarvis/quick.py, ui/quick.html): open it, change its hotkey.
function overlaysPanel() {
  const el = sPanel("sec-main", "ovl-panel", "end");
  if (!el) return;
  const q = (S.config && S.config.quick) || {};
  el.innerHTML = `<h3><span>${I("grid", "lead")}Быстрая панель</span></h3>
    <p class="hint">Полноэкранный HUD поверх любой программы или игры: ваши протоколы и плагины, поиск окон и приложений, музыка, громкость, таймеры, задачи, загрузка ПК, режимы Джарвиса, телефон, буфер обмена. Цифры 1–9 жмут протоколы. Игры в эксклюзивном полноэкранном режиме панель перекрыть не может: включите в игре «оконный без рамки».</p>
    <div class="row"><button class="primary" onclick="rpc('quick_show')">${I("grid", "lead")}Открыть</button>
      <span class="dim">клавиши</span><input id="ovl-hk" value="${esc(q.hotkey || "ctrl+alt+q")}" style="width:150px;flex:none" spellcheck="false">
      <span class="dim">меняется после перезапуска · свои команды закрепляются в «Команды» → «В быстрой панели»</span></div>
    <h4 style="margin:14px 0 4px">Загрузка костюма</h4>
    <p class="hint">Кинематографичная загрузка на весь экран: проверка систем этого ПК, погода, дела на сегодня и приветствие голосом. Без запросов к ИИ. В любой момент: «протокол загрузки». Esc или клик пропускают.</p>
    <div class="row"><select id="boot-mode" style="flex:none">
        <option value="startup">при включении ПК и первом запуске за день</option>
        <option value="always">при каждом запуске Джарвиса</option>
        <option value="off">выключена</option></select>
      <button onclick="rpc('boot_play')">${I("play", "lead")}Показать</button></div>`;
  $("#ovl-hk").onchange = (e) => { const v = e.target.value.trim().toLowerCase(); if (v) saveConfig({ quick: { hotkey: v } }); };
  $("#boot-mode").value = ((S.config && S.config.boot) || {}).mode || (S.boot && S.boot.perf && S.boot.perf.lite ? "off" : "startup");
  $("#boot-mode").onchange = (e) => saveConfig({ boot: { mode: e.target.value } });
}
