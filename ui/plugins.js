// Tab "Плагины": a list on the left and a form like "Команды" on the right — name, trigger phrases, description,
// the code in a Python editor, a test run. The AI is an optional helper above the code, not the main path.
let PLG = { items: [], sel: null, cur: null, ed: null, dirty: false, lastError: "", lmap: null, tpl: null };

const PLG_TPL = [["blank", "Пустой"], ["web", "Данные из интернета"], ["open", "Открыть сайты"], ["counter", "Счётчик"], ["chain", "Цепочка команд"]];
const PLG_API = [
  ["api.get_json", 'data = api.get_json("https://")', "GET-запрос, ответ в JSON (dict / list)"],
  ["api.get", 'html = api.get("https://")', "GET-запрос, ответ текстом"],
  ["api.post_json", 'r = api.post_json("https://", {"key": "value"})', "POST с JSON"],
  ["api.say", 'api.say("")', "сказать сразу, не дожидаясь конца функции"],
  ["api.open", 'api.open("https://")', "открыть сайт, файл или папку"],
  ["api.notify", 'api.notify("Заголовок", "текст")', "уведомление Windows"],
  ["api.clipboard", "api.clipboard(text)", "положить текст в буфер обмена"],
  ["api.type", 'api.type("")', "напечатать текст в активное окно"],
  ["api.hotkey", 'api.hotkey("ctrl+s")', "нажать сочетание клавиш"],
  ["api.run", 'api.run("открой телеграм")', "любая команда Джарвису, как голосом"],
  ["api.store", 'api.store["key"] = value', "словарь, сохраняется между запусками"],
  ["args", 'args.get("слово", "")', "часть фразы из {слова} в триггере"],
];

// ---------------------------------------------------------------- list
async function pluginsLoad() {
  let r;
  try { r = await rpc("plugins"); } catch (e) { return; }
  PLG.items = r.items;
  PLG.permTitles = r.perm_titles || {};
  let dl = $("#plugins-dl");
  if (!dl) { dl = document.createElement("datalist"); dl.id = "plugins-dl"; document.body.appendChild(dl); }
  dl.innerHTML = r.items.map((p) => `<option value="${esc(p.name)}">`).join("");
  pluginRenderList();
}
function pluginRenderList() {
  const box = $("#plg-list"); if (!box) return;
  const q = ($("#plg-q").value || "").trim().toLowerCase();
  $("#plg-q").classList.toggle("hidden", PLG.items.length < 6 && !q);
  const items = PLG.items.filter((p) => !q || (p.name + " " + p.file + " " + (p.triggers || []).join(" ")).toLowerCase().includes(q));
  const sub = (p) => p.error ? "ошибка в файле" : (p.missing || []).length ? "ждёт разрешения" : p.description || (p.triggers || []).filter((t) => !t.startsWith("^")).slice(0, 2).join(" · ") || p.file;
  box.innerHTML = items.map((p) => `<div class="li ${PLG.sel === p.file ? "sel" : ""} ${p.enabled ? "" : "off"} ${p.error ? "has-err" : ""}" onclick="pluginOpen('${esc(p.file)}')">
      <div class="ln"><b>${p.error ? I("warn", "lead bad") : (p.missing || []).length ? I("key", "lead hot") : ""}${esc(p.name)}</b><small>${esc(sub(p))}</small></div>
      <button class="mini" title="Запустить" onclick="event.stopPropagation();pluginQuickRun('${esc(p.file)}')" ${p.error || !p.enabled ? "disabled" : ""}>${I("play")}</button>
      <label class="switch" title="Включён" onclick="event.stopPropagation()"><input type="checkbox" ${p.enabled ? "checked" : ""} onchange="pluginToggle('${esc(p.file)}',this.checked)"><span></span></label></div>`).join("")
    + (PLG.cur && PLG.cur.isNew ? `<div class="li sel"><div class="ln"><b>${esc(PLG.cur.name || "Новый плагин")}</b><small>не сохранён</small></div></div>` : "")
    || `<div class="empty">${q ? "Ничего не найдено" : "Плагинов нет"}</div>`;
}
$("#plg-q").addEventListener("input", pluginRenderList);
async function pluginToggle(file, on) {
  await rpc("plugin_toggle", { file, on });
  const p = PLG.items.find((x) => x.file === file); if (p) p.enabled = on;
  if (PLG.cur && PLG.cur.file === file) { PLG.cur.enabled = on; const s = $("#pe-en"); if (s) s.checked = on; }
  pluginRenderList();
}
async function pluginQuickRun(file) {
  const p = PLG.items.find((x) => x.file === file);
  const r = await rpc("plugin_test", { file, text: ((p && p.triggers) || []).find((t) => !t.startsWith("^") && !t.includes("{")) || "" });
  if (r.need_perms) { pluginOpen(file); toast("Сначала разрешите: " + r.need_perms.map((x) => PLG.permTitles[x] || x).join(", "), p ? p.name : file, true); return; }
  toast(r.ok ? String(r.result ?? "выполнен, ничего не вернул") : String(r.error || "").trim().split("\n").slice(-1)[0], p ? p.name : file, !r.ok, r.ok ? 4500 : 8000);
}

// ---------------------------------------------------------------- editor
function pluginLeave() {
  return !PLG.dirty || confirm("Изменения в плагине не сохранены. Выйти без сохранения?");
}
async function pluginOpen(file, force) {
  if (!force && PLG.cur && PLG.cur.file === file) return;
  if (!force && !pluginLeave()) return;
  const r = await rpc("plugin_open", { file });
  PLG.sel = file; PLG.lmap = null; PLG.lastError = "";
  const it = PLG.items.find((x) => x.file === file) || {};
  PLG.cur = { file, name: r.name || file, description: r.description || "", triggers: r.triggers || [], body: r.body,
              raw: !r.parsed, enabled: r.enabled, error: r.error, isNew: false, needs: it.needs || [], granted: it.granted || [] };
  pluginRender();
}
async function pluginTemplates() {
  if (!PLG.tpl) { try { PLG.tpl = await rpc("plugin_templates"); } catch (e) { PLG.tpl = { blank: "def run(text, args, api):\n    return \"Готово.\"\n" }; } }
  return PLG.tpl;
}
async function pluginNew(from) {
  if (!pluginLeave()) return;
  go("plugins");
  const tpl = await pluginTemplates();
  PLG.sel = null; PLG.lmap = null; PLG.lastError = "";
  PLG.cur = from ? { ...from, triggers: [...from.triggers], file: "", isNew: true, error: null }
    : { file: "", name: "Новый плагин", description: "", triggers: [], body: tpl.blank, raw: false, enabled: true, isNew: true, tplKey: "blank",
        needs: [], granted: [] };
  pluginRender();
  if (from) pluginDirty();
  setTimeout(() => { const n = $("#pe-name"); if (n && !from) { n.focus(); n.select(); } }, 50);
}
function pluginDup() { pluginCollect(); const c = PLG.cur; PLG.dirty = false; pluginNew({ ...c, name: c.name + " (копия)" }); }

function pluginRender() {
  const c = PLG.cur, ed = $("#plg-editor");
  PLG.dirty = false;
  pluginRenderList();
  ed.innerHTML = `
    ${c.error ? `<div class="plg-err">${I("warn", "lead")}${esc(c.error)}</div>` : ""}
    ${c.raw ? `<div class="plg-note">Файл не удалось разобрать на поля (синтаксическая ошибка или вычисляемые значения), поэтому код показан целиком. Исправьте и сохраните — форма появится снова.</div>` : ""}
    <div class="row"><div class="field" style="flex:1"><label>Название</label><input id="pe-name" value="${esc(c.name)}" ${c.raw ? "disabled" : ""}></div>
      <label class="switch" style="margin-top:14px"><input type="checkbox" id="pe-en" ${c.enabled ? "checked" : ""}><span></span>Включён</label></div>
    <div class="field ${c.raw ? "hidden" : ""}"><label>Фразы-триггеры (Enter — добавить). <code>{слово}</code> ловит часть фразы, строка с <code>^</code> — регулярное выражение</label><div id="pe-trig"></div></div>
    <div class="field ${c.raw ? "hidden" : ""}"><label>Описание (необязательно)</label><input id="pe-desc" value="${esc(c.description)}" placeholder="Что делает, одной фразой: видно в списке плагинов"></div>
    <div class="field"><label>Разрешения <span class="dim">— что плагину можно; найдено по коду</span></label><div class="perms" id="pe-perms"></div></div>
    ${c.isNew ? `<div class="field"><label>Заготовка кода</label><div class="tpl-row">${PLG_TPL.map(([k, t]) => `<button class="btn small ${c.tplKey === k ? "on" : ""}" data-tpl="${k}">${esc(t)}</button>`).join("")}</div></div>` : ""}
    <div class="field">
      <div class="code-head"><label>Код${c.file ? ` <span class="dim">· ${esc(c.file)}</span>` : ""}</label><span class="unsaved-tag" id="pe-dirty">не сохранено</span><span class="sp"></span>
        <button class="btn small" id="pe-ai-btn" onclick="pluginAiToggle()" title="Попросить ИИ написать или поправить код">${I("chip", "lead")}ИИ</button></div>
      <div class="ai-row hidden" id="pe-ai">
        <div class="row"><input id="pe-ai-req" placeholder="Что сделать с кодом: «говори курс юаня», «отвечай короче», «добавь фразу …»">
          <select id="pe-ai-via" title="Кто пишет код"></select>
          <button class="btn" id="pe-ai-go" onclick="pluginAi(false)">Выполнить</button>
          <button class="btn" id="pe-ai-fix" onclick="pluginAi(true)" disabled title="Отдать ИИ ошибку последней проверки">${I("wrench", "lead")}Исправить ошибку</button></div>
        <div id="pe-ai-note" class="ai-note"></div></div>
      <div class="api-chips">${PLG_API.map(([n, , d], i) => `<button data-api="${i}" title="${esc(d)}">${esc(n)}</button>`).join("")}</div>
      <div id="pe-code"></div>
      <div class="ced-help">Tab / Shift+Tab — отступ, Ctrl+/ — закомментировать, Ctrl+S — сохранить. Что вернёт <code>run</code> строкой, Джарвис скажет вслух.</div>
    </div>
    <div class="field"><label>Проверка</label>
      <div class="row"><input id="pe-try" placeholder="фраза, как её скажут после «Джарвис»: ${esc((c.triggers || []).find((t) => !t.startsWith("^")) || "")}">
        <button class="btn" onclick="pluginTest()">${I("play", "lead")}Проверить</button></div>
      <div id="pe-out" class="plg-out hidden"></div></div>
    <div class="ed-btns"><button class="primary" onclick="pluginSave()">Сохранить</button>
      ${c.isNew ? "" : `<button class="btn" onclick="pluginDup()">${I("copy", "lead")}Дублировать</button><button class="btn" onclick="pluginExport()" title="Сохранить файл плагина, чтобы отправить другу">${I("export", "lead")}Экспорт</button><button class="btn danger" onclick="pluginDel()">${I("trash", "lead")}Удалить</button>`}</div>`;
  if (!c.raw) chipsInput($("#pe-trig"), c.triggers, "например: курс валют  /  погода в {город}", pluginDirty);
  PLG.ed = codeEditor($("#pe-code"), c.body, { onSave: pluginSave, onChange: () => { pluginDirty(); pluginNeedsSoon(); } });
  pluginPermsRender(c.isNew ? c.needs : c.granted);
  if (c.isNew) pluginNeedsSoon();
  $$("#plg-editor input:not(#pe-en):not(#pe-try):not(#pe-ai-req)").forEach((el) => el.addEventListener("input", pluginDirty));
  $("#pe-name").addEventListener("input", () => { if (c.isNew) { c.name = $("#pe-name").value; pluginRenderList(); } });
  $("#pe-en").addEventListener("change", (e) => { c.enabled = e.target.checked; if (!c.isNew) pluginToggle(c.file, c.enabled); });
  $("#pe-try").addEventListener("keydown", (e) => { if (e.key === "Enter") pluginTest(); });
  $("#pe-ai-req").addEventListener("keydown", (e) => { if (e.key === "Enter") pluginAi(false); });
  $$("#plg-editor [data-api]").forEach((b) => b.onclick = () => PLG.ed.insert(PLG_API[+b.dataset.api][1]));
  $$("#plg-editor [data-tpl]").forEach((b) => b.onclick = () => pluginTemplate(b.dataset.tpl));
}
function pluginDirty() {
  PLG.dirty = true;
  const t = $("#pe-dirty"); if (t) t.classList.add("on");
}

// ---------------------------------------------------------------- permissions (found in the code, granted by the user)
function pluginPermsRender(checked) {
  const c = PLG.cur, box = $("#pe-perms"); if (!box) return;
  const on = new Set(checked || []);
  box.innerHTML = (c.needs || []).map((p) => `<label class="perm ${on.has(p) ? "on" : ""}"><input type="checkbox" data-perm="${p}" ${on.has(p) ? "checked" : ""}>
      ${esc(PLG.permTitles[p] || p)}</label>`).join("") || '<span class="dim">ничего особенного: только отвечать голосом, уведомления и свои данные</span>';
  $$("#pe-perms [data-perm]").forEach((cb) => cb.onchange = () => { cb.parentElement.classList.toggle("on", cb.checked); pluginDirty(); });
}
function pluginPermsChecked() { return $$("#pe-perms [data-perm]:checked").map((cb) => cb.dataset.perm); }
function pluginNeedsSoon() {
  clearTimeout(PLG.needsT);
  PLG.needsT = setTimeout(async () => {
    const c = PLG.cur; if (!c || !PLG.ed) return;
    let n; try { n = await rpc("plugin_needs", { source: PLG.ed.get() }); } catch (e) { return; }
    const before = new Set(c.needs || []), checked = new Set(pluginPermsChecked());
    // what the code newly uses is offered checked: the user is the one typing it
    n.forEach((p) => { if (!before.has(p)) checked.add(p); });
    c.needs = n;
    pluginPermsRender([...checked].filter((p) => n.includes(p)));
  }, 900);
}
async function pluginExport() {
  const folder = await winApi("pick_folder");
  if (!folder) return;
  const path = await rpc("plugin_export", { file: PLG.cur.file, folder });
  toast(path, "Плагин сохранён");
}

// ---------------------------------------------------------------- import: look first, install after
async function pluginImport() {
  if (!pluginLeave()) return;
  const path = await winApi("pick_file", "py");
  if (!path) return;
  const r = await rpc("plugin_inspect", { path });
  go("plugins");
  PLG.cur = null; PLG.sel = null; PLG.dirty = false; pluginRenderList();
  const risky = r.needs.filter((p) => p === "system" || p === "run");
  $("#plg-editor").innerHTML = `<div class="imp">
    <div class="imp-h">${I("import", "lead")}Установка плагина из файла</div>
    <div class="imp-name">${esc(r.name)}</div>${r.description ? `<div class="dim">${esc(r.description)}</div>` : ""}
    ${r.error ? `<div class="plg-err">${I("warn", "lead")}${esc(r.error)}</div>` : ""}
    <div class="field"><label>Срабатывает на фразы</label><div>${r.triggers.map((t) => `<span class="chip-s">${esc(t)}</span>`).join("") || '<span class="dim">нет</span>'}</div></div>
    <div class="field"><label>Что умеет (по коду, ${r.lines} строк)</label><div class="perms">${r.needs.map((p) => `<span class="perm ${p === "system" || p === "run" ? "risk" : ""}">${esc(PLG.permTitles[p] || p)}</span>`).join("") || '<span class="dim">ничего особенного: только отвечать голосом</span>'}</div>
      ${r.imports.length ? `<div class="dim imp-mods">Модули: ${esc(r.imports.join(", "))}</div>` : ""}</div>
    ${risky.length ? `<div class="plg-note">Плагин может ${risky.includes("system") ? "работать с файлами и системой" : ""}${risky.length > 1 ? " и " : ""}${risky.includes("run") ? "давать Джарвису любые команды" : ""}. Устанавливайте, только если доверяете автору, и посмотрите код ниже.</div>` : ""}
    ${r.exists ? `<div class="plg-note">Плагин с таким именем файла уже есть: этот установится рядом, под другим именем.</div>` : ""}
    <details class="imp-code"><summary>Посмотреть код</summary><pre>${esc(r.source)}</pre></details>
    <div class="ed-btns">${r.error ? "" : `<button class="primary" id="imp-go">Установить</button><span class="dim">разрешения спрошу при первом запуске</span>`}
      <button class="btn" onclick="$('#plg-editor').innerHTML='<div class=&quot;empty&quot;>Отменено</div>'">Отмена</button></div></div>`;
  const b = $("#imp-go");
  if (b) b.onclick = async () => {
    const file = await rpc("plugin_import", { path, perms: [] });
    toast(`«${r.name}» установлен`, "Плагины");
    await pluginsLoad();
    pluginOpen(file, true);
  };
}
async function pluginTemplate(key) {
  const tpl = await pluginTemplates(), c = PLG.cur;
  const cur = PLG.ed.get();
  if (cur.trim() && cur !== tpl[c.tplKey] && !confirm("Заменить код заготовкой?")) return;
  c.tplKey = key;
  PLG.ed.set(tpl[key]);
  $$("#plg-editor [data-tpl]").forEach((b) => b.classList.toggle("on", b.dataset.tpl === key));
}
function pluginCollect() {
  const c = PLG.cur;
  if (!c.raw) { c.name = $("#pe-name").value.trim(); c.description = $("#pe-desc").value.trim(); }
  c.body = PLG.ed.get();
  return c;
}
async function pluginSave() {
  const c = pluginCollect();
  if (!c.raw && !c.triggers.length) { toast("Добавьте хотя бы одну фразу-триггер", "Плагин", true); $("#pe-trig input").focus(); return false; }
  let r;
  try {
    r = await rpc("plugin_store", { file: c.file, name: c.name, description: c.description, triggers: c.triggers, body: c.body, raw: c.raw,
                                    perms: pluginPermsChecked() });
    c.granted = pluginPermsChecked();
  } catch (e) {
    const m = /строке (\d+)/.exec(e.message); if (m) PLG.ed.mark(+m[1]);
    return false;
  }
  const wasNew = c.isNew, wasRaw = c.raw, rerender = wasNew || (c.error || null) !== (r.error || null);
  if (wasNew && !c.enabled) await rpc("plugin_toggle", { file: r.file, on: false });
  c.file = r.file; c.isNew = false; c.error = r.error; PLG.sel = r.file; PLG.dirty = false;
  const t = $("#pe-dirty"); if (t) t.classList.remove("on");
  toast(r.error ? "Сохранено, но: " + r.error : "Сохранено, уже работает", c.name, !!r.error);
  await pluginsLoad();
  const keepAi = !$("#pe-ai").classList.contains("hidden"), tryText = $("#pe-try").value;
  if (wasRaw) await pluginOpen(r.file, true);         // the file now splits into the form
  else if (rerender) pluginRender();                  // file name, error banner, Duplicate / Delete buttons
  if (wasRaw || rerender) { $("#pe-try").value = tryText; if (keepAi) pluginAiToggle(); }
  PLG.lmap = wasRaw ? null : r.lmap;                  // valid for the code as it is in the editor now
  return true;
}
// a traceback line of this plugin's file -> the editor line (the form owns NAME / DESCRIPTION / TRIGGERS)
function pluginErrLine(err) {
  const file = PLG.cur.file.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const all = [...String(err).matchAll(new RegExp(`${file}", line (\\d+)`, "g"))];
  if (!all.length) return 0;
  const n = +all[all.length - 1][1];
  return PLG.lmap ? PLG.lmap[n - 1] || 0 : n;
}
async function pluginTest() {
  if (!(await pluginSave())) return;
  const out = $("#pe-out");
  out.classList.remove("hidden", "ok", "bad"); out.textContent = "запускаю…";
  const r = await rpc("plugin_test", { file: PLG.cur.file, text: $("#pe-try").value.trim().toLowerCase() });
  if (r.need_perms) {
    out.classList.add("bad");
    out.innerHTML = `<div class="po-h">${I("key", "lead")}Плагину нужно разрешение: ${esc(r.need_perms.map((p) => PLG.permTitles[p] || p).join(", "))}</div>
      <div class="row" style="margin:8px 0 0"><button class="primary small" id="po-grant">Разрешить и проверить</button><span class="dim">или отметьте галочки в «Разрешениях» выше</span></div>`;
    $("#po-grant").onclick = async () => {
      const all = [...new Set([...(PLG.cur.granted || []), ...r.need_perms])];
      PLG.cur.granted = await rpc("plugin_grant", { file: PLG.cur.file, perms: all });
      pluginPermsRender(PLG.cur.granted);
      pluginsLoad();
      pluginTest();
    };
    return;
  }
  PLG.lastError = r.ok ? "" : r.error || "";
  $("#pe-ai-fix").disabled = r.ok;
  out.classList.add(r.ok ? "ok" : "bad");
  if (r.ok) {
    const fx = (r.effects || []).map((e) => `<li>${esc(e[0])}: ${esc(e.slice(1).join(" · "))}</li>`).join("");
    const args = Object.keys(r.args || {}).length ? `<div class="po-k">args</div><div>${esc(JSON.stringify(r.args, null, 0))}</div>` : "";
    out.innerHTML = `<div class="po-h">${I("check", "lead")}Выполнен за ${r.ms} мс</div><div class="po-k">ответ</div><div>${esc(r.result ?? "(ничего не вернул, Джарвис промолчит)")}</div>${args}
      ${fx ? `<div class="po-k">действия</div><ul>${fx}</ul>` : ""}`;
    PLG.ed.mark(0);
  } else {
    const ln = pluginErrLine(r.error);
    const last = String(r.error || "").trim().split("\n").slice(-1)[0];
    out.innerHTML = `<div class="po-h">${I("warn", "lead")}${esc(last)}${ln ? ` <span class="dim">· строка ${ln}</span>` : ""}</div>
      <details><summary>Полный текст ошибки</summary><pre>${esc(r.error)}</pre></details>`;
    if (ln) PLG.ed.mark(ln);
  }
}
async function pluginDel() {
  const c = PLG.cur;
  if (!confirm(`Удалить плагин «${c.name}»? Файл уйдёт в data\\plugins\\.deleted, его можно вернуть.`)) return;
  await rpc("plugin_delete", { file: c.file });
  PLG.sel = null; PLG.cur = null; PLG.dirty = false;
  $("#plg-editor").innerHTML = '<div class="empty">Удалено</div>'; pluginsLoad();
}

// ---------------------------------------------------------------- AI helper (optional)
let AI_AGENTS = null;
async function pluginAiToggle() {
  const box = $("#pe-ai");
  box.classList.toggle("hidden");
  $("#pe-ai-btn").classList.toggle("on", !box.classList.contains("hidden"));
  if (box.classList.contains("hidden")) return;
  if (!AI_AGENTS) { try { AI_AGENTS = await rpc("plugin_ai_agents"); } catch (e) { AI_AGENTS = [{ id: "api", title: "ИИ из настроек" }]; } }
  const last = (() => { try { return localStorage.getItem("jarvis.plgai") || "api"; } catch (e) { return "api"; } })();
  const sel = $("#pe-ai-via");
  sel.innerHTML = AI_AGENTS.map((a) => `<option value="${esc(a.id)}" ${a.id === last ? "selected" : ""}>${esc(a.title)}</option>`).join("");
  sel.onchange = () => { try { localStorage.setItem("jarvis.plgai", sel.value); } catch (x) {} };
  $("#pe-ai-fix").disabled = !PLG.lastError;
  $("#pe-ai-req").focus();
}
async function pluginAi(fix) {
  const c = pluginCollect();
  const req = $("#pe-ai-req").value.trim();
  if (!req && !fix) { $("#pe-ai-req").focus(); return; }
  const note = $("#pe-ai-note"), btns = [$("#pe-ai-go"), $("#pe-ai-fix")];
  note.className = "ai-note busy"; note.textContent = "Пишу код… через мост это может занять пару минут.";
  btns.forEach((b) => (b.disabled = true));
  try {
    const r = await rpc("plugin_ai", { request: req, via: $("#pe-ai-via").value, error: fix ? PLG.lastError : "", file: c.file || null,
      form: { name: c.name, description: c.description, triggers: c.triggers, body: c.raw ? "" : c.body }, source: c.raw ? c.body : "" });
    const p = r.parts;
    if (p && p.parsed && !c.raw) {
      // the form keeps what the user typed; empty fields take the AI's suggestion
      if (!c.name || c.name === "Новый плагин") { c.name = p.name || c.name; $("#pe-name").value = c.name; }
      if (!c.description) { c.description = p.description || ""; $("#pe-desc").value = c.description; }
      if (!c.triggers.length && p.triggers.length) { c.triggers.push(...p.triggers); chipsInput($("#pe-trig"), c.triggers, "например: курс валют  /  погода в {город}", pluginDirty); }
      PLG.ed.set(p.body);
    } else PLG.ed.set(r.code);
    pluginDirty(); pluginRenderList();
    note.className = "ai-note" + (r.error ? " bad" : "");
    note.textContent = (r.error ? r.error + "\n" : "") + (r.note || "Готово.") + "\nПросмотрите код, затем «Сохранить» или «Проверить».";
    $("#pe-ai-req").value = "";
  } catch (e) {
    note.className = "ai-note bad"; note.textContent = "ИИ не ответил: " + e.message;
  } finally { btns[0].disabled = false; btns[1].disabled = !PLG.lastError; }
}

tabLoaders.plugins = () => { pluginsLoad(); };
setTimeout(pluginsLoad, 3000);
