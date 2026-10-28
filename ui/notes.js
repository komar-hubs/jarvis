// Tab "Заметки": a plain notebook. Saves by itself while you type; voice notes ("запиши в заметки …") show up here.
let NT = { items: [], sel: null, timer: null, dirty: false };

function noteTitle(n) {
  const first = (n.text || "").split("\n").map((l) => l.trim()).find(Boolean) || "";
  return n.title || (first.length > 60 ? first.slice(0, 60) + "…" : first) || "Без названия";
}
function noteWhen(iso) {
  if (!iso) return "";
  const d = new Date(iso), n = new Date();
  const hm = `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  return d.toDateString() === n.toDateString() ? "сегодня " + hm : `${d.getDate()} ${MON[d.getMonth()].slice(0, 3)} ${hm}`;
}
async function notesLoad() {
  try { NT.items = await rpc("notebook"); } catch (e) { return; }
  NT.items.sort((a, b) => (!!b.pinned - !!a.pinned) || (b.updated || "").localeCompare(a.updated || ""));
  notesRenderList();
  if (NT.sel && !$("#ne-text")) noteOpen(NT.sel);
}
function notesRenderList() {
  const q = ($("#note-q").value || "").trim().toLowerCase();
  const items = NT.items.filter((n) => !q || (noteTitle(n) + " " + (n.text || "")).toLowerCase().includes(q));
  $("#note-list").innerHTML = items.map((n) => {
    const body = (n.text || "").replace(/\s+/g, " ").trim();
    const prev = n.title ? body : body.slice(noteTitle(n).replace(/…$/, "").length).trim();
    return `<div class="li note-li ${n.id === NT.sel ? "sel" : ""}" onclick="noteOpen('${n.id}')">
      <div class="ln"><b>${n.pinned ? I("pin", "lead pin") : ""}${esc(noteTitle(n))}</b><small>${esc(prev.slice(0, 90) || "пусто")}</small><small class="nw">${esc(noteWhen(n.updated))}</small></div></div>`;
  }).join("") || `<div class="empty">${q ? "Ничего не найдено" : "Заметок пока нет"}</div>`;
}
$("#note-q").addEventListener("input", notesRenderList);

function noteOpen(id) {
  noteFlush();
  const n = NT.items.find((x) => x.id === id);
  if (!n) return;
  NT.sel = id;
  notesRenderList();
  $("#note-ed").innerHTML = `
    <div class="ne-head"><input id="ne-title" value="${esc(n.title || "")}" placeholder="${esc(noteTitle({ text: n.text }) === "Без названия" ? "Заголовок" : noteTitle({ text: n.text }))}">
      <button class="ib ${n.pinned ? "on" : ""}" id="ne-pin" title="${n.pinned ? "Открепить" : "Закрепить сверху"}">${I("pin")}</button>
      <button class="ib" title="Скопировать текст" onclick="noteCopy()">${I("copy")}</button>
      <button class="ib" title="Прочитать вслух" onclick="noteSpeak()">${I("speaker")}</button>
      <button class="ib del" title="Удалить" onclick="noteDel()">${I("trash")}</button></div>
    <textarea id="ne-text" spellcheck="true" placeholder="Пишите… сохраняется само">${esc(n.text || "")}</textarea>
    <div class="ne-foot"><span id="ne-count"></span><span id="ne-state">${esc("изменено " + noteWhen(n.updated))}</span></div>`;
  const ta = $("#ne-text");
  const changed = () => { NT.dirty = true; $("#ne-state").textContent = "…"; clearTimeout(NT.timer); NT.timer = setTimeout(noteFlush, 700); noteCount(); };
  ta.addEventListener("input", changed);
  $("#ne-title").addEventListener("input", changed);
  ta.addEventListener("keydown", (e) => { if (e.key === "s" && e.ctrlKey) { e.preventDefault(); noteFlush(); } });
  $("#ne-pin").onclick = async () => { n.pinned = !n.pinned; await rpc("note_save", { note: { id: n.id, pinned: n.pinned } }); notesLoad(); noteOpenKeep(); };
  noteCount();
}
function noteOpenKeep() { setTimeout(() => { const p = $("#ne-pin"); const n = NT.items.find((x) => x.id === NT.sel); if (p && n) p.classList.toggle("on", !!n.pinned); }, 300); }
function noteCount() {
  const t = ($("#ne-text") || {}).value || "";
  $("#ne-count").textContent = `${t.trim() ? t.trim().split(/\s+/).length : 0} слов · ${t.length} знаков`;
}
async function noteFlush() {
  clearTimeout(NT.timer);
  if (!NT.dirty || !NT.sel || !$("#ne-text")) return;
  NT.dirty = false;
  const note = { id: NT.sel, title: $("#ne-title").value.trim(), text: $("#ne-text").value };
  try {
    const saved = await rpc("note_save", { note });
    const i = NT.items.findIndex((x) => x.id === saved.id);
    if (i >= 0) NT.items[i] = saved;
    const st = $("#ne-state"); if (st) st.textContent = "сохранено";
    notesRenderList();
  } catch (e) { NT.dirty = true; }
}
async function noteNew() {
  go("notes");
  await noteFlush();
  const n = await rpc("note_save", { note: { title: "", text: "" } });
  NT.items.unshift(n);
  NT.sel = n.id;
  noteOpen(n.id);
  $("#ne-text").focus();
}
function noteCopy() { navigator.clipboard.writeText($("#ne-text").value).then(() => toast("Скопировано", "Заметка")); }
function noteSpeak() { const t = $("#ne-text").value.trim(); if (t) rpc("say", { text: t.slice(0, 1500) }); }
async function noteDel() {
  const n = NT.items.find((x) => x.id === NT.sel);
  if (!n || ((n.text || "").trim() && !confirm(`Удалить заметку «${noteTitle(n)}»?`))) return;
  NT.dirty = false;
  await rpc("note_delete", { id: n.id });
  NT.sel = null;
  $("#note-ed").innerHTML = '<div class="empty">Удалено</div>';
  notesLoad();
}
tabLoaders.notes = () => { notesLoad(); };
window.addEventListener("beforeunload", noteFlush);

// events from the core: a voice note was added, "прочитай заметку про …" opens it
const _onEventNotes = onEvent;
onEvent = function (ev) {
  if (ev.type === "notes") { if (S.tab === "notes" && !NT.dirty) notesLoad(); return; }
  if (ev.type === "note_open") { go("notes"); setTimeout(async () => { await notesLoad(); noteOpen(ev.id); }, 250); return; }
  if (ev.type === "automations") { if (S.tab === "commands" && window.autoLoad && !$("#tab-commands .seg-auto").classList.contains("hidden")) autoLoad(); return; }
  if (ev.type === "show_automations") { setTimeout(() => cmdSeg("auto"), 300); return; }
  _onEventNotes(ev);
};
