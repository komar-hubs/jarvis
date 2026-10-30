// Settings → «Помощь в делах» → «Голос в играх» (jarvis/gamevoice.py): a phrase said in a game presses keys.
let GV = { st: null, t: null };

async function gamePanel() {
  const box = sPanel("sec-help", "game-panel", "end");
  if (!box) return;
  try { GV.st = await rpc("game_state"); } catch (e) { return; }
  const st = GV.st;
  const opt = (v, t, cur) => `<option value="${v}" ${String(v) === String(cur) ? "selected" : ""}>${t}</option>`;
  box.innerHTML = `<h3><span>${I("bolt", "lead")}Голос в играх</span><span class="h3-meta">${st.active ? "активен: " + esc(st.exe || "") : "без ИИ и интернета"}</span></h3>
    <p class="hint">Пока игра на экране, фразы из списка нажимают клавиши и кнопки мыши сразу, без «Джарвис». Срабатывает только фраза целиком, поэтому обычная речь ничего не нажимает. Придуманные слова («люмос») лучше записать с голоса: Джарвис запомнит, как именно их слышит. Голосом: «научи заклинание люмос на клавишу один». Игры с античитом могут не принимать программные нажатия.</p>
    <div class="row"><span class="dim">Работает</span><select id="gv-mode" style="flex:none">${opt("auto", "когда игра на экране", st.mode)}${opt("on", "всегда", st.mode)}${opt("off", "выключено", st.mode)}</select>
      <span class="dim">Конец фразы</span><select id="gv-end" style="flex:none">${[250, 350, 450, 600].map((v) => opt(v, v + " мс", st.end_ms)).join("")}</select></div>
    <div class="gv-head dim"><span>Фразы (через запятую)</span><span>Клавиши</span><span>Держать, мс</span><span></span></div>
    <div id="gv-list">${st.binds.map(gvRow).join("") || '<div class="empty">Фраз пока нет</div>'}</div>
    <div class="row"><button class="btn" id="gv-add">${I("plus", "lead")}Фраза</button>
      <button class="btn" data-preset="spells">Пример: заклинания</button><button class="btn" data-preset="shooter">Пример: шутер</button>
      <span class="dim">клавиши: q, shift+w, f5, space, lmb / rmb (кнопки мыши), q:500 — держать 0,5 с, «e, 150, lmb» — по очереди</span></div>
    <div id="gv-err" class="warn-t"></div>`;
  $("#gv-mode").onchange = (e) => gvSave({ mode: e.target.value });
  $("#gv-end").onchange = (e) => gvSave({ end_ms: +e.target.value });
  $("#gv-add").onclick = () => { $("#gv-list .empty")?.remove(); $("#gv-list").insertAdjacentHTML("beforeend", gvRow({ id: "", phrases: [], keys: "", hold: 60 })); gvBind(); };
  box.querySelectorAll("[data-preset]").forEach((b) => b.onclick = async () => { GV.st = await rpc("game_preset", { name: b.dataset.preset }); gamePanel(); });
  gvBind();
}

function gvRow(b) {
  return `<div class="gv-row" data-id="${esc(b.id || "")}">
    <input class="gv-ph" value="${esc((b.phrases || []).join(", "))}" placeholder="люмос, лумос" spellcheck="false">
    <input class="gv-keys" value="${esc(b.keys || "")}" placeholder="1" spellcheck="false">
    <input class="gv-hold" type="number" min="20" max="5000" step="10" value="${b.hold || 60}">
    <span class="gv-btns"><button class="btn small" data-learn title="Скажите фразу после сигнала: Джарвис запомнит, как её слышит">${I("mic", "lead")}С голоса</button>
      <button class="btn small danger" data-del title="Удалить">${I("x")}</button></span></div>`;
}

function gvBind() {
  $$("#gv-list .gv-row").forEach((row) => {
    row.querySelectorAll("input").forEach((i) => i.oninput = () => { clearTimeout(GV.t); GV.t = setTimeout(() => gvSave({}), 700); });
    row.querySelector("[data-del]").onclick = () => { row.remove(); gvSave({}); };
    row.querySelector("[data-learn]").onclick = async () => {
      if (!row.dataset.id) { await gvSave({}); return toast("Сначала впишите клавишу, потом записывайте с голоса", "Голос в играх"); }
      await rpc("game_learn", { id: row.dataset.id });
      toast("Скажите фразу (10 секунд)", "Голос в играх");
    };
  });
}

function gvCollect() {
  return $$("#gv-list .gv-row").map((r) => ({ id: r.dataset.id, keys: r.querySelector(".gv-keys").value.trim(),
    hold: +r.querySelector(".gv-hold").value || 60,
    phrases: r.querySelector(".gv-ph").value.split(",").map((s) => s.trim()).filter(Boolean) }));
}

async function gvSave(extra) {
  const err = $("#gv-err");
  try {
    const st = await rpc("game_save", { binds: gvCollect().filter((b) => b.keys || b.phrases.length), ...extra });
    err.textContent = "";
    // fresh ids for new rows, without re-rendering what is being typed
    const rows = $$("#gv-list .gv-row").filter((r) => r.querySelector(".gv-keys").value.trim() || r.querySelector(".gv-ph").value.trim());
    rows.forEach((r, i) => { if (st.binds[i]) r.dataset.id = st.binds[i].id; });
    GV.st = st;
  } catch (e) { err.textContent = e.message; }
}
