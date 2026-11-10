// Settings: the menu of sections (scrolls, follows the scroll), the search over every panel, and the panels of
// features that had only voice commands before: «Ударения», «Только мой голос», «Игровые помощники».
const SETNAV = { pending: null };
// words people type that are not written in the panels: typed word start -> phrases to look for instead
const SET_SYN = {
  "ударен": ["ударения"], "произнош": ["ударения"], "имя": ["имена для вызова"], "микро": ["микрофон"],
  "клип": ["клипы"], "стим": ["steam"], "пароль": ["api ключ", "только мой голос"], "приват": ["что делает джарвис"],
  "обнов": ["обновления джарвиса"], "тем": ["тема"], "ночь": ["ночной режим"], "ии": ["искусственный интеллект"],
  "памят": ["память о вас"], "запомн": ["память о вас"], "забыт": ["память о вас"], "перев": ["переводчик экрана"], "файл": ["поиск по файлам"], "документ": ["поиск по файлам"],
  "расслыш": ["что он сказал"], "пересказ": ["что он сказал"], "смс": ["телефон"], "звонок": ["телефон"], "уведомлен": ["телефон", "уведомления windows"], "громк": ["громкость"],
};
// lists of data inside panels are not settings: app names, notifications, search results
const SET_SKIP = "#app-list, .ph-recent, .ph-apps, #mem-res, .bk-list, #up-list, .mac-list, .st-list, datalist, #gv-list";

function settingsExtras() {
  stressPanel();
  voiceidPanel();
  gameHelpPanel();
  if (window.toolsPanels) toolsPanels();
  setNavInit();
}

// ---------------------------------------------------------------- menu and search
function setNavInit() {
  const tab = $("#tab-settings"), nav = $("#set-nav");
  if (!tab || !nav) return;
  nav.querySelectorAll("a").forEach((a) => a.onclick = (e) => { e.preventDefault(); setGo(a.dataset.sec); });
  $$("#settings .set-trust a[data-sec]").forEach((a) => a.onclick = (e) => { e.preventDefault(); setGo(a.dataset.sec); });
  // the section whose top passed the search bar is the current one (an observer lagged one section behind)
  let ticking = false;
  tab.onscroll = () => {
    if (ticking || S.tab !== "settings") return;
    ticking = true;
    requestAnimationFrame(() => {
      ticking = false;
      const top = tab.getBoundingClientRect().top + 120;
      let cur = null;
      $$("#settings .s-sec:not(.s-hide)").forEach((sec) => { if (sec.getBoundingClientRect().top <= top) cur = sec.id.slice(3); });
      if (tab.scrollTop + tab.clientHeight >= tab.scrollHeight - 4) cur = ($$("#settings .s-sec:not(.s-hide)").pop() || {}).id?.slice(3);
      setNavActive(cur || "main");
    });
  };
  const q = $("#set-q");
  q.oninput = () => setSearch(q.value);
  q.onkeydown = (e) => { if (e.key === "Escape") { q.value = ""; setSearch(""); } };
  if (SETNAV.pending) { const id = SETNAV.pending; SETNAV.pending = null; setTimeout(() => setGo(id), 150); }
}

function setNavActive(id) {
  $$("#set-nav a").forEach((a) => a.classList.toggle("active", a.dataset.sec === id));
}

function setGo(id) {
  setSearchClear();
  const sec = $("#ss-" + id);
  if (!sec) return;
  const tab = $("#tab-settings");
  const top = tab.scrollTop + sec.getBoundingClientRect().top - tab.getBoundingClientRect().top - 64;
  tab.scrollTo({ top, behavior: "smooth" });
  // a hidden or busy page may not animate: then jump
  setTimeout(() => { if (Math.abs(tab.scrollTop - top) > 40 && tab.scrollHeight - tab.clientHeight > top - 40) tab.scrollTop = top; }, 700);
  setNavActive(id);
}

function setSearchClear() {
  const q = $("#set-q");
  if (q && q.value) { q.value = ""; setSearch(""); }
}
window.setSearchClear = setSearchClear;

function _norm(s) { return (s || "").toLowerCase().replace(/ё/g, "е"); }

function _panelText(p, full) {
  const c = p.cloneNode(true);
  c.querySelectorAll(SET_SKIP).forEach((x) => x.remove());
  if (full) return _norm(c.textContent);
  // the names only: the panel's title, the setting labels, sub-headings
  return _norm([...c.querySelectorAll("h3, .sgrid > label, .s-sub, .mt-sec > b, .ft-t, th")].map((x) => x.textContent).join(" | "));
}

function _hit(text, words) {
  return words.every((w) => text.includes(w) || Object.entries(SET_SYN).some(([k, v]) => w.startsWith(k) && v.some((x) => text.includes(x))));
}

function setSearch(raw) {
  const q = _norm(raw).trim();
  const words = q ? q.split(/\s+/) : [];
  const panels = $$("#settings .s-grid > .panel");
  let hits = new Set(panels);
  if (words.length) {
    // names of settings first; the whole text (hints) only when no name matches
    hits = new Set(panels.filter((p) => _hit(_panelText(p, false), words)));
    if (!hits.size) hits = new Set(panels.filter((p) => _hit(_panelText(p, true), words)));
  }
  const feat = $("#feat-panel");
  if (feat) {
    let rows = 0;
    feat.querySelectorAll("tbody tr").forEach((tr) => { const ok = !words.length || _hit(_norm(tr.textContent), words); tr.classList.toggle("s-hide", !ok); rows += ok; });
    if (words.length && rows) hits.add(feat); else if (words.length) hits.delete(feat);
  }
  panels.forEach((p) => p.classList.toggle("s-hide", !hits.has(p)));
  $$("#settings .s-sec").forEach((sec) => sec.classList.toggle("s-hide", !sec.querySelector(".s-grid > .panel:not(.s-hide)")));
  $("#set-q-res").textContent = words.length ? (hits.size ? `найдено: ${hits.size}` : "") : "";
  $("#set-empty").hidden = !(words.length && !hits.size);
  $(".set-trust").classList.toggle("s-hide", !!words.length);
  if (words.length) $("#tab-settings").scrollTo({ top: 0 });
}

// a voice command opens a section ("открой настройки голоса")
function settingsSection(id) {
  if (S.tab !== "settings") { SETNAV.pending = id; go("settings"); return; }
  if ($("#ss-" + id)) setGo(id); else SETNAV.pending = id;
}

// ---------------------------------------------------------------- «Ударения» (jarvis/stress.py)
async function stressPanel() {
  const box = $("#stress-panel");
  if (!box) return;
  let r;
  try { r = await rpc("stress_list"); } catch (e) { return; }
  const show = (m) => { const i = m.indexOf("+"); return i < 0 ? esc(m) : esc(m.slice(0, i)) + `<b class="acc">${esc(m[i + 1] || "")}</b>` + esc(m.slice(i + 2)); };
  box.innerHTML = `<h3><span>${I("dict", "lead")}Ударения</span><span class="h3-meta">ваших: ${r.items.length} · встроенных: ${r.builtin}</span></h3>
    <p class="hint">Голос иногда ставит ударение не туда. Впишите слово и где ударение — буквой («о») или слогом («2»), или прямо со знаком «+»: твор+ог. Голосом: «ударение в слове творог на второй слог», «ты неправильно сказал торты».</p>
    <div class="row"><input id="st-word" placeholder="слово, например творог" style="max-width:220px" spellcheck="false">
      <input id="st-spec" placeholder="на о / на 2 слог / твор+ог" style="max-width:200px" spellcheck="false">
      <button class="btn" id="st-add">${I("plus", "lead")}Запомнить</button></div>
    <div id="st-err" class="warn-t"></div>
    <div class="st-list">${r.items.map((x) => `<span class="st-item"><span>${show(x.marked)}</span>
      <button class="ghost small" data-st-say="${esc(x.word)}" title="Произнести">${I("speaker")}</button>
      <button class="ghost small" data-st-del="${esc(x.word)}" title="Забыть">${I("x")}</button></span>`).join("") || '<span class="empty">Своих ударений пока нет</span>'}</div>`;
  const add = async () => {
    const word = $("#st-word").value.trim(), spec0 = $("#st-spec").value.trim();
    if (!word || !spec0) return;
    const spec = /^\d$/.test(spec0) ? spec0 + " слог" : spec0;
    try { await rpc("stress_set", { word, spec: spec.includes("+") ? spec : "на " + spec.replace(/^на\s+/, "") }); stressPanel(); rpc("stress_say", { word }); }
    catch (e) { $("#st-err").textContent = e.message; }
  };
  $("#st-add").onclick = add;
  $("#st-spec").onkeydown = (e) => { if (e.key === "Enter") add(); };
  box.querySelectorAll("[data-st-say]").forEach((b) => b.onclick = () => rpc("stress_say", { word: b.dataset.stSay }));
  box.querySelectorAll("[data-st-del]").forEach((b) => b.onclick = () => rpc("stress_delete", { word: b.dataset.stDel }).then(stressPanel));
}

// ---------------------------------------------------------------- «Только мой голос» (jarvis/voiceid.py)
// the recording on screen: which phrase, what to read, and whether Jarvis is listening right now
function voiceidStep(ev) {
  const box = $("#voiceid-panel");
  if (!box) return;
  let st = $("#vi-step");
  if (!st) { st = document.createElement("div"); st.id = "vi-step"; st.className = "vi-step"; box.appendChild(st); }
  if (ev.state === "done" || ev.state === "cancel") {
    st.remove();
    if (ev.state === "done") setTimeout(() => { voiceidPanel(); featuresPanel(); }, 1500);
    return;
  }
  st.innerHTML = `<div class="vi-dots">${[1, 2, 3].map((k) => `<i class="${k < ev.n ? "ok" : k === ev.n ? "cur" : ""}"></i>`).join("")}<span>фраза ${ev.n} из 3</span></div>
    <div class="vi-text">«${esc(ev.text)}»</div>
    <div class="vi-state ${ev.state === "listen" ? "on" : ""}">${ev.state === "listen" ? I("mic", "lead") + "Говорите сейчас — прочитайте фразу вслух" : "Дождитесь, пока Джарвис договорит…"}</div>
    <button class="ghost small" id="vi-cancel">Отменить</button>`;
  $("#vi-cancel").onclick = () => rpc("voiceid_cancel");
}

async function voiceidPanel() {
  const box = $("#voiceid-panel");
  if (!box) return;
  let v;
  try { v = await rpc("voiceid_state"); } catch (e) { return; }
  const state = !v.enrolled ? "голос не записан" : v.enabled ? "включено" : "выключено";
  box.innerHTML = `<h3><span>${I("key", "lead")}Только мой голос</span><span class="h3-meta ${v.enrolled && v.enabled ? "ok-t" : ""}">${state}</span></h3>
    <p class="hint">Выключение и перезагрузку ПК, удаление, отправку сообщений, «закрой всё» и «сделай за меня» Джарвис выполняет только по вашему голосу — домашние и гости не смогут. Остальное работает для всех. Хранится только отпечаток голоса (256 чисел), записей нет, всё на этом ПК.</p>
    <div class="row">${v.enrolled
      ? `<button class="btn" id="vi-again">${I("mic", "lead")}Записать заново</button><button class="btn danger" id="vi-forget">${I("trash", "lead")}Забыть мой голос</button>`
      : `<button class="primary" id="vi-again">${I("mic", "lead")}Запомнить мой голос</button>`}
      <span class="dim">${v.enrolled ? `порог сходства ${v.threshold}` : "попрошу сказать три фразы" + (v.model ? "" : " · модель 26 МБ скачается один раз")}</span></div>
    <div class="row"><span class="dim">проверка:</span>${featChip("voice")}</div>`;
  $("#vi-again").onclick = () => rpc("voiceid_enroll");
  const f = $("#vi-forget");
  if (f) f.onclick = () => { if (confirm("Забыть ваш голос? Опасные команды снова будут работать для всех.")) rpc("voiceid_forget").then(() => { voiceidPanel(); featuresPanel(); }); };
  if (window.featChipsFill) featChipsFill();
}

// ---------------------------------------------------------------- «Игровые помощники» (jarvis/gamefx.py, jarvis/steam.py)
async function gameHelpPanel() {
  const box = $("#gamehelp-panel");
  if (!box) return;
  let g;
  try { g = await rpc("gamefx_state"); } catch (e) { return; }
  box.innerHTML = `<h3><span>${I("rec", "lead")}Игровые помощники</span><span class="h3-meta">без лишних запросов к ИИ</span></h3>
    <div class="mt-sec"><b>Клипы</b><span class="dim">«Сохрани момент» — последние 30 секунд через Xbox Game Bar, «начни / останови запись», «покажи клипы».</span>
      <div class="row">${g.recording_ok ? `<span class="ok-t">${I("check", "lead")}Запись последних секунд включена</span>`
        : `<span class="warn-t">${I("warn", "lead")}В Windows выключена запись последних секунд</span><button class="btn" onclick="rpc('gamebar_settings')">Открыть настройки Windows</button>`}
        <button class="btn" onclick="rpc('clips_open')">${I("folder", "lead")}Клипы (${g.clips})</button></div></div>
    <div class="mt-sec"><b>Подсказка поверх игры</b><span class="dim">«Загугли как победить босса», пока игра на экране — короткий ответ ИИ карточкой поверх игры, без браузера. «Скрой подсказку», «открой подробнее».</span></div>
    <div class="mt-sec"><b>Перерывы в игре</b><span class="dim">Тихой карточкой и не чаще раза в час. «Я в марафоне» — молчу до настоящего перерыва${g.marathon ? " (сейчас включено)" : ""}. Как часто — в «Дела» → «Забота и фокус».</span></div>
    <div class="mt-sec"><b>Steam и Discord</b><span class="dim">«Запусти последнюю игру», «сколько у меня часов в …», «что из желаемого на скидке». «Выключи микрофон в дискорде», «выключи звук игры».</span></div>`;
}
