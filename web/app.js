const COMMANDS = [
  ["help", "показать команды"],
  ["save", "сохранить: /save <текст>"],
  ["search", "поиск: /search <запрос>"],
  ["recent", "недавние: /recent [n]"],
  ["tags", "список тегов"],
  ["tag", "тег: /tag <id> <тег>"],
  ["forget", "удалить: /forget <id>"],
  ["summary", "резюме памяти"],
  ["context", "что вспоминается"],
  ["export", "экспорт JSON"],
  ["clear", "очистить диалог"],
  ["think", "/think on|off"],
  ["wipe", "удалить всё: /wipe everything"],
  ["status", "статус приложения"],
];

const messagesEl = document.getElementById("messages");
const inputEl = document.getElementById("input");
const sendEl = document.getElementById("send");
const hintEl = document.getElementById("cmd-hint");
const statusEl = document.getElementById("status");

function addMessage(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  const author = document.createElement("div");
  author.className = "author";
  author.textContent = role === "user" ? "Вы" : role === "assistant" ? "Мозг" : "Система";
  const body = document.createElement("div");
  body.textContent = text;
  div.appendChild(author);
  div.appendChild(body);
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return body;
}

function autoresize() {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(inputEl.scrollHeight, 160) + "px";
}

function updateHint() {
  const v = inputEl.value.trim();
  if (!v.startsWith("/")) { hintEl.hidden = true; return; }
  const name = v.slice(1).split(/\s/)[0].toLowerCase();
  const matches = COMMANDS.filter(([c]) => !name || c.startsWith(name)).slice(0, 5);
  if (!matches.length) { hintEl.hidden = true; return; }
  hintEl.hidden = false;
  hintEl.innerHTML = matches.map(([c, d]) => `<b>/${c}</b> — ${d}`).join("<br>");
}

async function runCommand(line) {
  addMessage("user", line);
  setBusy(true);
  try {
    const res = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ input: line }),
    });
    const data = await res.json();
    if (data.is_command) {
      addMessage(data.error ? "system" : "assistant", data.text || "(пусто)");
    } else {
      addMessage("system", "Это не команда.");
    }
  } catch (e) {
    addMessage("system", "Ошибка команды: " + e.message);
  } finally {
    setBusy(false);
    refreshStatus();
  }
}

async function runChat(message) {
  addMessage("user", message);
  const body = addMessage("assistant", "");
  setBusy(true);
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let acc = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop();
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data:")) continue;
        const payload = JSON.parse(line.slice(5).trim());
        if (payload.token) { acc += payload.token; body.textContent = acc; messagesEl.scrollTop = messagesEl.scrollHeight; }
        if (payload.done) { /* final */ }
      }
    }
    if (!acc) body.textContent = "(пустой ответ)";
  } catch (e) {
    body.textContent = "Ошибка: " + e.message;
  } finally {
    setBusy(false);
    refreshStatus();
  }
}

function setBusy(busy) {
  sendEl.disabled = busy;
  inputEl.disabled = busy;
  if (!busy) inputEl.focus();
}

function send() {
  const text = inputEl.value.trim();
  if (!text) return;
  inputEl.value = "";
  autoresize();
  updateHint();
  if (text.startsWith("/")) runCommand(text);
  else runChat(text);
}

async function refreshStatus() {
  try {
    const res = await fetch("/api/health");
    const d = await res.json();
    statusEl.innerHTML =
      `Модель: ${d.model}<br>` +
      `Загружена: ${d.model_loaded ? "да" : "нет"}<br>` +
      `Воспоминаний: ${d.memories}<br>` +
      `Индекс: ${d.index_size}`;
  } catch {
    statusEl.textContent = "нет соединения с сервером";
  }
}

sendEl.addEventListener("click", send);
inputEl.addEventListener("input", () => { autoresize(); updateHint(); });
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});
document.getElementById("btn-recent").addEventListener("click", () => runCommand("/recent 10"));
document.getElementById("btn-new").addEventListener("click", () => runCommand("/clear"));

refreshStatus();
autoresize();
inputEl.focus();
