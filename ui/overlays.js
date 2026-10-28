// Settings → «Быстрая панель» (jarvis/quick.py, ui/quick.html): open it, change its hotkey.
function overlaysPanel() {
  const el = sPanel("sec-main", "ovl-panel", "end");
  if (!el) return;
  const q = (S.config && S.config.quick) || {};
  el.innerHTML = `<h3><span>${I("grid", "lead")}Быстрая панель</span></h3>
    <p class="hint">Полноэкранный HUD поверх любой программы или игры: ваши протоколы и плагины, поиск окон и приложений, музыка, громкость, таймеры, задачи, загрузка ПК, режимы Джарвиса, телефон, буфер обмена. Цифры 1–9 жмут протоколы. Игры в эксклюзивном полноэкранном режиме панель перекрыть не может: включите в игре «оконный без рамки».</p>
    <div class="row"><button class="primary" onclick="rpc('quick_show')">${I("grid", "lead")}Открыть</button>
      <span class="dim">клавиши</span><input id="ovl-hk" value="${esc(q.hotkey || "ctrl+alt+q")}" style="width:150px;flex:none" spellcheck="false">
      <span class="dim">меняется после перезапуска · свои команды закрепляются в «Команды» → «В быстрой панели»</span></div>`;
  $("#ovl-hk").onchange = (e) => { const v = e.target.value.trim().toLowerCase(); if (v) saveConfig({ quick: { hotkey: v } }); };
}
