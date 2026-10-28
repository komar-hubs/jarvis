// First-run setup wizard: profile -> voice -> microphone -> AI provider -> bridges -> done.
const W = { step: 0, det: null, prov: null, voice: null };
const STEPS = ["Знакомство", "Голос", "Микрофон", "Интеллект", "Мосты", "Готово"];

async function setupOpen() {
  $("#setup").classList.remove("hidden");
  W.step = 0;
  W.prov = S.config.ai.provider;
  W.voice = S.config.voice.engine;
  setupDraw();
  W.det = await rpc("setup_detect");
  if (!S.config.setup_done) {
    W.voice = "silero";
    // Claude CLI bridge costs no VRAM; local Ollama only when no bridge is installed
    if (W.det.bridges.claude && W.det.bridges.claude.found) W.prov = "claude_code";
    else if (W.det.bridges.antigravity && W.det.bridges.antigravity.found) W.prov = "antigravity";  // Gemini without API keys
    else if (W.det.ollama.running && W.det.ollama.models.length) W.prov = "ollama";
  }
  setupDraw();
}
function setupClose() { $("#setup").classList.add("hidden"); }

function setupDraw() {
  $("#setup-steps").innerHTML = STEPS.map((s, i) => `<div class="${i === W.step ? "cur" : i < W.step ? "done" : ""}">${i < W.step ? I("check", "lead") : ""}${s}</div>`).join("");
  $("#setup-back").style.visibility = W.step ? "visible" : "hidden";
  $("#setup-next").textContent = W.step === STEPS.length - 1 ? "Запустить Джарвиса" : "Далее";
  const body = $("#setup-body");
  const c = S.config, d = W.det;
  if (W.step === 0) {
    body.innerHTML = `<h1>J.A.R.V.I.S.</h1><p>Just A Rather Very Intelligent System. Я ваш персональный голосовой ассистент: управляю компьютером,
      веду задачи и напоминания, отвечаю на вопросы через ИИ и работаю в фоне. Давайте познакомимся.</p>
      <div class="sgrid" style="margin-top:18px">
        <label>Как вас зовут?</label><input id="w-name" value="${esc(c.user.name)}" placeholder="Тони">
        <label>Как к вам обращаться?</label><input id="w-addr" value="${esc(c.user.address)}" list="addr-l2"><datalist id="addr-l2"><option value="сэр"><option value="мэм"><option value="босс"></datalist>
        <label>Ваш город</label><input id="wz-city" value="${esc(c.location.city)}" placeholder="Москва">
        <label>Автозапуск с Windows</label><label class="switch"><input type="checkbox" id="w-auto" ${c.autostart ? "checked" : ""}><span></span>запускать Джарвиса при входе в систему</label>
      </div>`;
  } else if (W.step === 1) {
    const x = d ? d.xtts : false, cu = d ? d.cuda : false;
    body.innerHTML = `<h1>Голос</h1><p>Silero — быстрый и чистый русский голос, работает без интернета на любом ПК. XTTS умеет копировать тембр по образцу, но медленнее и капризнее.</p>
      <div class="cards">
        <div class="card ${W.voice === "silero" ? "sel" : ""}" data-v="silero"><b>Silero</b><small>Мужской голос «Айдар», ответ за доли секунды, офлайн. Тембр и темп меняются в настройках.</small><span class="tag">рекомендуется</span></div>
        <div class="card ${W.voice === "xtts" ? "sel" : ""} ${x ? "" : "dis"}" data-v="xtts"><b>Клон голоса (XTTS)</b><small>Копирует тембр образца, нужна NVIDIA. Экспериментально. ${x ? (cu ? "" : "Без NVIDIA будет очень медленно.") : "Не установлен: install_xtts.bat"}</small></div>
        <div class="card ${W.voice === "edge" ? "sel" : ""}" data-v="edge"><b>Edge Neural</b><small>Быстрый качественный голос Microsoft, нужен интернет. Дмитрий или мультиязычные голоса с акцентом.</small></div>
        <div class="card ${W.voice === "sapi" ? "sel" : ""}" data-v="sapi"><b>Системный</b><small>Голос Windows, офлайн. Звучит роботизированно.</small></div>
      </div>
      <div class="row"><button class="primary" onclick="setupVoiceTest()">${I("play", "lead")}Прослушать</button><span style="color:var(--dim);font-size:14px">Первая загрузка XTTS ~30-60 секунд, пока говорит резервный голос.</span></div>`;
    $$(".card[data-v]", body).forEach((el) => el.onclick = () => { W.voice = el.dataset.v; setupDraw(); });
  } else if (W.step === 2) {
    const mics = d ? d.mics : [];
    body.innerHTML = `<h1>Микрофон</h1><p>Джарвис слушает в фоне и реагирует, только когда фраза начинается со слова «Джарвис». Распознавание — GigaAM от Сбера: точнее всех для русского и быстро работает даже без видеокарты.</p>
      <div class="sgrid">
        <label>Микрофон</label><select id="w-mic"><option value="">По умолчанию системы</option>${mics.map((m) => `<option value="${m.id}" ${c.stt.mic === m.id ? "selected" : ""}>${esc(m.name)}</option>`).join("")}</select>
        <label>Уровень сигнала</label><div class="meter"><i class="mic-meter"></i></div>
        <label>Движок</label><select id="w-model">
          <option value="gigaam" ${(c.stt.engine || "gigaam") === "gigaam" ? "selected" : ""}>GigaAM v3 — рекомендуется</option>
          <option value="whisper" ${c.stt.engine === "whisper" ? "selected" : ""}>Whisper — если говорите не только по-русски${d && !d.cuda ? " (медленно без видеокарты)" : ""}</option></select>
        <label>Проверка</label><div class="inline"><button class="btn" onclick="setupMicTest()">${I("mic", "lead")}Сказать фразу</button><span id="setup-heard" style="color:var(--cyan2)"></span></div>
      </div>
      <p>Модель распознавания (~250 МБ) скачается при первом запуске.</p>`;
    $("#w-mic").onchange = (e) => saveConfig({ stt: { mic: e.target.value === "" ? null : +e.target.value } });
    $("#w-model").onchange = (e) => saveConfig({ stt: { engine: e.target.value } });
  } else if (W.step === 3) {
    const groups = [["Локально (бесплатно, приватно)", ["ollama", "lmstudio", "llamacpp"]], ["Облачные API", ["openai", "anthropic", "gemini", "openrouter", "groq", "deepseek", "mistral", "xai", "together", "yandex", "custom"]], ["Мосты к агентам", ["claude_code", "codex", "antigravity", "gemini_cli"]]];
    const found = (id) => {
      if (!d) return "";
      if (id === "ollama") return d.ollama.running ? `найден, моделей: ${d.ollama.models.length}` : "";
      if (id === "lmstudio") return d.lmstudio ? "найден" : "";
      if (id === "groq" || id === "gemini" || id === "openrouter") return "бесплатный ключ";
      const ag = S.providers[id] && S.providers[id].agent;
      if (ag && d.bridges[ag]) return d.bridges[ag].found ? "установлен" : "";
      return "";
    };
    const pv = { ...S.providers[W.prov], ...(c.ai.providers[W.prov] || {}) };
    body.innerHTML = `<h1>Интеллект</h1><p>Выберите «мозг» для ИИ-чата. Потом можно переключать в любой момент. Команды ПК работают и без ИИ.</p>
      ${groups.map(([g, ids]) => `<h3 class="mt">${g}</h3><div class="cards">${ids.map((id) => { const f = found(id); return `<div class="card ${W.prov === id ? "sel" : ""}" data-p="${id}"><b>${esc(S.providers[id].title)}</b><small>${esc(S.providers[id].base_url || "через CLI")}</small>${f ? `<span class="tag">${f}</span>` : ""}</div>`; }).join("")}</div>`).join("")}
      <div class="sgrid" style="margin-top:14px" id="w-pf">
        ${pv.kind !== "bridge" ? `<label>Адрес</label><input id="w-url" value="${esc(pv.base_url || "")}">
        ${pv.key ? `<label>API ключ</label><input id="w-key" type="password" placeholder="${pv.api_key_set ? "сохранён" : "вставьте ключ"}">` : ""}
        <label>Модель</label>${W.prov === "ollama" && d && d.ollama.models.length ? `<select id="w-mod">${d.ollama.models.map((m) => `<option ${m === pv.model ? "selected" : ""}>${esc(m)}</option>`).join("")}</select>` : `<input id="w-mod" value="${esc(pv.model || "")}" placeholder="например gpt-…, claude-…, llama…">`}` : `<div class="full" style="color:var(--dim)">Чат пойдёт через установленный агент. Ответы медленнее, но это полноценный кодинг-агент.</div>`}
        <div class="full row"><button class="primary" onclick="setupAITest()">Проверить</button><span id="w-ai-res"></span></div>
      </div>
      ${W.prov === "ollama" ? (() => {
        const vram = d && d.gpu ? d.gpu.mem_total : 0;   // MiB
        const small = !vram || vram < 9000;
        const model = small ? "qwen3.5:4b" : "qwen3.5:9b";
        const warn = small ? `<p style="color:var(--amber)">Видеопамяти ${vram ? Math.round(vram / 1024) + " ГБ" : "мало"}: берите модель до 4B (<code>${model}</code>), иначе ПК будет тормозить. Быстрее и умнее — бесплатный ключ Groq или Gemini.</p>` : "";
        const has = d && d.ollama.running && (d.ollama.models || []).length;
        const inst = has ? "" : `<div class="ollama-box"><p>Бесплатный ИИ прямо на вашем ПК: без ключей, без интернета, без денег. Джарвис сам поставит Ollama и скачает модель <code>${model}</code> (~${small ? 3 : 6} ГБ).</p>
          <div class="row"><button class="primary" onclick="ollamaSetup('${model}', this)">Установить локальный ИИ в один клик</button><span class="dim" id="ollama-res"></span></div></div>`;
        return warn + inst;
      })() : ""}
      ${["groq", "gemini", "openrouter"].includes(W.prov) ? `<p>Бесплатный ключ: ${{ groq: "console.groq.com → API Keys", gemini: "aistudio.google.com → Get API key", openrouter: "openrouter.ai → Keys (модели с пометкой :free)" }[W.prov]}. После ввода ключа нажмите «Проверить» — модель подберётся сама.</p>` : ""}`;
    $$(".card[data-p]", body).forEach((el) => el.onclick = async () => { await setupSaveAI(); W.prov = el.dataset.p; await saveConfig({ ai: { provider: W.prov } }); setupDraw(); });
  } else if (W.step === 4) {
    const b = d ? d.bridges : {};
    body.innerHTML = `<h1>Мосты</h1><p>Джарвис умеет передавать задачи ИИ-агентам для программирования и озвучивать результат: «Джарвис, поручи Кодексу написать тесты».
      Агенты, в свою очередь, могут говорить голосом Джарвиса через MCP.</p>
      <div class="cards">${Object.entries(b).map(([k, a]) => `<div class="card ${a.found ? "sel" : ""}"><b>${esc(a.title)}</b><small>${a.found ? esc(a.path) : "не установлен: " + esc(a.install)}</small>${a.found ? `<span class="tag">найден</span>` : ""}</div>`).join("")}</div>
      <p>Подключить MCP и выбрать рабочие папки можно во вкладке «Мосты».</p>`;
  } else {
    body.innerHTML = `<h1>Всё готово</h1><p>Джарвис будет работать в фоне — закрытие окна сворачивает его в трей (иконка у часов).</p>
      <div class="help" style="margin-top:12px">
        <div class="panel"><h3>Попробуйте сказать</h3><ul><li>Джарвис, открой ютуб</li><li>Джарвис, загугли погоду на выходные</li><li>Джарвис, таймер на 5 минут</li><li>Джарвис, добавь задачу купить хлеб завтра</li><li>Джарвис, как дела?</li></ul></div>
        <div class="panel"><h3>Горячие клавиши</h3><ul><li><code>Ctrl+Alt+J</code> — слушать без слова «Джарвис»</li><li><code>Ctrl+Alt+Space</code> — показать / скрыть окно</li><li><code>Esc</code> — прервать речь</li></ul></div>
      </div>`;
  }
}

async function setupSaveStep() {
  if (W.step === 0) {
    const patch = { user: { name: $("#w-name").value.trim(), address: $("#w-addr").value.trim() || "сэр" } };
    await saveConfig(patch);
    const city = $("#wz-city").value.trim();
    if (city && city !== S.config.location.city) { try { await rpc("geocode", { city }); } catch (e) {} }
    if ($("#w-auto").checked !== !!S.config.autostart) await saveConfig({ autostart: $("#w-auto").checked });
  } else if (W.step === 1) {
    await saveConfig({ voice: { engine: W.voice } });
  } else if (W.step === 3) {
    await setupSaveAI();
  }
}
async function setupSaveAI() {
  const url = $("#w-url"), key = $("#w-key"), mod = $("#w-mod");
  if (!url && !mod) return;
  const p = {};
  if (url) p.base_url = url.value.trim();
  if (key) p.api_key = key.value.trim();
  if (mod) p.model = mod.value.trim();
  await saveConfig({ ai: { provider: W.prov, providers: { [W.prov]: p } } });
  // key entered but no model: pick a good one from the provider's list automatically
  if (mod && !p.model && (p.api_key || !S.providers[W.prov].key)) {
    try {
      const ms = await rpc("models", { pid: W.prov });
      const pref = [/llama-3\.3-70b/, /gemini-[\d.]+-flash(?!.*(lite|image|tts|live))/, /gpt-[\d.]+-mini/, /qwen.*(instruct|32b)/, /llama.*70b/, /./];
      const pick = pref.map((rx) => ms.find((m) => rx.test(m))).find(Boolean);
      if (pick) { mod.value = pick; await saveConfig({ ai: { providers: { [W.prov]: { model: pick } } } }); }
    } catch (e) {}
  }
}
// one click: winget Ollama + the model; progress comes as a background job ("В фоне", tab «Сегодня»)
async function ollamaSetup(model, btn) {
  if (btn) btn.disabled = true;
  const res = $("#ollama-res");
  const m = await rpc("ollama_setup", { model });
  if (res) res.textContent = `ставлю Ollama и качаю ${m} — это фоновая задача, можно идти дальше`;
  toast(`Ставлю Ollama и модель ${m}. Скажу, когда будет готово.`, "Локальный ИИ", false, 7000);
}
async function setupVoiceTest() { await saveConfig({ voice: { engine: W.voice } }); rpc("voice_test"); }
async function setupMicTest() { $("#setup-heard").textContent = "говорите…"; rpc("listen", { target: "test" }); }
async function setupAITest() {
  await setupSaveAI();
  $("#w-ai-res").textContent = "проверяю…";
  try { const r = await rpc("test_provider", { pid: W.prov }); $("#w-ai-res").innerHTML = `<span style="color:var(--green)">✓ ${esc(r.text)} · ${r.ms} мс</span>`; }
  catch (e) { $("#w-ai-res").innerHTML = `<span style="color:var(--red)">✕ ${esc(e.message)}</span>`; }
}

$("#setup-next").onclick = async () => {
  await setupSaveStep();
  if (W.step === STEPS.length - 1) {
    await rpc("setup_finish", { data: {} });
    S.config.setup_done = true;
    setupClose(); chatHeadInit();
    return;
  }
  W.step++; setupDraw();
};
$("#setup-back").onclick = async () => { await setupSaveStep(); W.step = Math.max(0, W.step - 1); setupDraw(); };
