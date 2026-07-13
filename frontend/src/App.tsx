import { useCallback, useEffect, useRef, useState } from "react";
import Sidebar from "./components/Sidebar";
import Composer from "./components/Composer";
import Message from "./components/Message";
import MemoryView from "./components/MemoryView";
import TasksView from "./components/TasksView";
import TodayView from "./components/TodayView";
import SettingsView from "./components/SettingsView";
import {
  type Chat,
  type ChatEvent,
  type Confirmation,
  type Folder,
  type Health,
  type Msg,
  type ToolRun,
  type ToolRunEvent,
  ApiError,
  createChat,
  createFolder as createFolderApi,
  deleteChat,
  deleteFolder as deleteFolderApi,
  deleteMessage as apiDeleteMessage,
  editMessage,
  exportChatMarkdown,
  getHealth,
  getMessages,
  listChats,
  listConfirmations,
  listFolders,
  listToolRuns,
  renameChat,
  runCommand,
  resolveConfirmation,
  setPinned,
  moveChat,
  streamChat,
  updateFolder as updateFolderApi,
} from "./api";

type UIMsg = Msg & { local?: boolean; streaming?: boolean };

const DRAFTS_KEY = "sb_drafts";
const THEME_KEY = "sb_theme";

function loadDrafts(): Record<number, string> {
  try { return JSON.parse(localStorage.getItem(DRAFTS_KEY) || "{}"); } catch { return {}; }
}
function saveDrafts(d: Record<number, string>) {
  localStorage.setItem(DRAFTS_KEY, JSON.stringify(d));
}
function loadTheme(): "dark" | "light" {
  return (localStorage.getItem(THEME_KEY) as "dark" | "light") || "dark";
}

export default function App() {
  const [chats, setChats] = useState<Chat[]>([]);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [messages, setMessages] = useState<UIMsg[]>([]);
  const [input, setInput] = useState("");
  const [drafts, setDrafts] = useState<Record<number, string>>(loadDrafts);
  const [busy, setBusy] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [confirmations, setConfirmations] = useState<Confirmation[]>([]);
  const [toolRuns, setToolRuns] = useState<ToolRun[]>([]);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">(loadTheme);
  const [view, setView] = useState<"chat" | "memory" | "today" | "settings" | "tasks">("chat");
  const [messageNotice, setMessageNotice] = useState<{ kind: "info" | "error"; text: string } | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const currentIdRef = useRef<number | null>(null);
  currentIdRef.current = currentId;

  // apply theme class to <html>
  useEffect(() => {
    document.documentElement.classList.toggle("light", theme === "light");
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  useEffect(() => {
    if (!messageNotice) return;
    const timeout = window.setTimeout(() => setMessageNotice(null), 5000);
    return () => window.clearTimeout(timeout);
  }, [messageNotice]);

  const refreshHealth = useCallback(async () => {
    try { setHealth(await getHealth()); } catch { setHealth(null); }
  }, []);

  const refreshChats = useCallback(async () => {
    const list = await listChats();
    setChats(list);
  }, []);

  const refreshFolders = useCallback(async () => {
    setFolders(await listFolders());
  }, []);

  const loadMessages = useCallback(async (id: number) => {
    const msgs = await getMessages(id);
    setMessages(msgs);
  }, []);

  const refreshConfirmations = useCallback(async (id: number) => {
    setConfirmations(await listConfirmations(id));
  }, []);

  const refreshToolRuns = useCallback(async (id: number) => {
    try {
      const runs = await listToolRuns(id);
      if (currentIdRef.current === id) setToolRuns(runs);
    } catch {
      // Keep live events visible if an older server has not exposed audit history yet.
    }
  }, []);

  // ref to always read latest drafts without re-creating selectChat on every draft change
  const draftsRef = useRef(drafts);
  draftsRef.current = drafts;

  const selectChat = useCallback(
    (id: number) => {
      currentIdRef.current = id;
      setCurrentId(id);
      setInput(draftsRef.current[id] ?? "");
      setToolRuns([]);
      loadMessages(id);
      refreshConfirmations(id);
      refreshToolRuns(id);
    },
    [loadMessages, refreshConfirmations, refreshToolRuns]
  );

  // persist current chat draft to localStorage (debounced, no per-keystroke re-render)
  useEffect(() => {
    if (currentId == null) return;
    const t = setTimeout(() => {
      setDrafts((prev) => {
        const next = { ...prev, [currentId]: input };
        saveDrafts(next);
        return next;
      });
    }, 400);
    return () => clearTimeout(t);
  }, [input, currentId]);

  // initial load
  useEffect(() => {
    (async () => {
      await Promise.all([refreshChats(), refreshFolders()]);
      let list = await listChats();
      if (list.length === 0) {
        const c = await createChat();
        list = [c];
        setChats(list);
      }
      selectChat(list[0].id);
      refreshHealth();
    })();
  }, [selectChat, refreshHealth, refreshChats, refreshFolders]);

  // autoscroll
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const newChat = useCallback(async (folderId?: number) => {
    const c = await createChat(undefined, folderId);
    await refreshChats();
    selectChat(c.id);
  }, [refreshChats, selectChat]);

  const removeChat = useCallback(
    async (id: number) => {
      await deleteChat(id);
      const list = (await listChats()).filter((c) => c.id !== id);
      setChats(list);
      if (id === currentId) {
        if (list.length > 0) selectChat(list[0].id);
        else {
          const c = await createChat();
          setChats([c]);
          selectChat(c.id);
        }
      }
    },
    [currentId, selectChat]
  );

  const rename = useCallback(async (id: number, title: string) => {
    const updated = await renameChat(id, title);
    setChats((prev) => prev.map((c) => (c.id === id ? updated : c)));
  }, []);

  const togglePin = useCallback(async (id: number, pinned: boolean) => {
    const updated = await setPinned(id, pinned);
    setChats((prev) => [...prev].sort(byOrder).map((c) => (c.id === id ? updated : c)));
    setChats((prev) => [...prev].sort(byOrder));
  }, []);

  const moveToFolder = useCallback(async (chatId: number, folderId: number | null) => {
    const updated = await moveChat(chatId, folderId);
    setChats((prev) => [...prev].sort(byOrder).map((c) => (c.id === chatId ? updated : c)));
  }, []);

  // ---- folder ops ----
  const createFolderCb = useCallback(async (name: string, description?: string) => {
    await createFolderApi(name, description);
    await refreshFolders();
  }, [refreshFolders]);

  const renameFolderCb = useCallback(async (id: number, name: string, description?: string) => {
    await updateFolderApi(id, { name, description });
    await refreshFolders();
  }, [refreshFolders]);

  const removeFolder = useCallback(async (id: number) => {
    await deleteFolderApi(id);
    await Promise.all([refreshFolders(), refreshChats()]);
  }, [refreshFolders, refreshChats]);

  // ---- message ops ----
  const handleDeleteMessage = useCallback(async (msgId: number) => {
    if (currentId == null) return;
    const removeFromList = () => setMessages((m) => m.filter((x) => x.id !== msgId));
    try {
      await apiDeleteMessage(currentId, msgId);
      removeFromList();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        const deleteMemories = window.confirm(
          "У сообщения есть связанные воспоминания.\n\n" +
          "ОК — удалить сообщение и связанные воспоминания.\n" +
          "Отмена — удалить только сообщение, сохранив воспоминания."
        );
        try {
          await apiDeleteMessage(currentId, msgId, deleteMemories ? "delete" : "keep");
          removeFromList();
        } catch (retryError) {
          setMessageNotice({ kind: "error", text: `Не удалось удалить сообщение: ${(retryError as Error).message}` });
        }
      } else {
        setMessageNotice({ kind: "error", text: `Не удалось удалить сообщение: ${(error as Error).message}` });
      }
    }
  }, [currentId]);

  const applyToolRunEvent = useCallback((chatId: number, event: ToolRunEvent) => {
    const eventRunId = typeof event.tool_run_id === "number" ? event.tool_run_id : null;
    const toolName = event.tool_name ?? event.name ?? "tool";
    const result = event.result ?? event.error ?? null;
    const terminalStatus = typeof event.status === "string"
      ? event.status
      : event.type === "tool_error" || toolResultHasError(result)
        ? "failed"
        : "succeeded";

    setToolRuns((runs) => {
      const index = eventRunId === null
        ? runs.findIndex((run) => run.tool_name === toolName && run.status === "running")
        : runs.findIndex((run) => run.id === eventRunId);

      if (event.type === "tool_started") {
        const started: ToolRun = {
          id: eventRunId ?? -Date.now(),
          chat_id: chatId,
          message_id: null,
          tool_name: toolName,
          arguments: event.arguments ?? {},
          result: null,
          policy_decision: event.policy_decision ?? event.risk ?? null,
          risk: event.risk ?? null,
          status: "running",
          created_at: new Date().toISOString(),
          finished_at: null,
        };
        if (index === -1) return [started, ...runs];
        const next = [...runs];
        next[index] = { ...next[index], ...started, id: next[index].id };
        return next;
      }

      if (index === -1) {
        return [{
          id: eventRunId ?? -Date.now(),
          chat_id: chatId,
          message_id: null,
          tool_name: toolName,
          arguments: event.arguments ?? {},
          result,
          policy_decision: event.policy_decision ?? event.risk ?? null,
          risk: event.risk ?? null,
          status: terminalStatus,
          created_at: new Date().toISOString(),
          finished_at: new Date().toISOString(),
        }, ...runs];
      }

      const next = [...runs];
      next[index] = {
        ...next[index],
        result,
        status: terminalStatus,
        finished_at: new Date().toISOString(),
      };
      return next;
    });
  }, []);

  const handleChatEvent = useCallback((chatId: number, event: ChatEvent) => {
    if (chatId !== currentIdRef.current) return;
    if (event.type === "confirmation_required") {
      setConfirmations((items) => [event.confirmation, ...items]);
      setToolRuns((runs) => {
        const index = typeof event.tool_run_id === "number"
          ? runs.findIndex((run) => run.id === event.tool_run_id)
          : runs.findIndex((run) => (
            run.tool_name === event.confirmation.tool_name && run.status === "running"
          ));
        if (index === -1) return runs;
        const next = [...runs];
        next[index] = { ...next[index], status: "pending_confirmation" };
        return next;
      });
      return;
    }
    applyToolRunEvent(chatId, event);
  }, [applyToolRunEvent]);

  const regenerateFrom = useCallback(async (text: string) => {
    if (currentId == null) return;
    const placeholderId = -Date.now() - 9;
    const placeholder: UIMsg = {
      id: placeholderId, chat_id: currentId, role: "assistant",
      content: "", created_at: "", edited_at: null, local: true, streaming: true,
    };
    setMessages((m) => [...m, placeholder]);
    setBusy(true);
    setStreaming(true);
    const controller = new AbortController();
    abortRef.current = controller;
    const onEvent = (event: ChatEvent) => handleChatEvent(currentId, event);
    try {
      const done = await streamChat(currentId, text, (tok) => {
        setMessages((m) =>
          m.map((x) => (x.id === placeholderId ? { ...x, content: x.content + tok } : x))
        );
      }, controller.signal, onEvent);
      if (done) {
        setMessages((m) => m.filter((x) => x.id !== placeholderId).concat(done.assistant_message));
      } else {
        setMessages((m) => m.map((x) => (x.id === placeholderId ? { ...x, streaming: false } : x)));
      }
    } catch (e) {
      setMessages((m) => m.map((x) =>
        x.id === placeholderId ? { ...x, content: "Ошибка: " + (e as Error).message, streaming: false } : x
      ));
    } finally {
      setBusy(false);
      setStreaming(false);
      abortRef.current = null;
      refreshHealth();
      refreshToolRuns(currentId);
    }
  }, [currentId, handleChatEvent, refreshHealth, refreshToolRuns]);

  // edit a user message then regenerate the assistant reply that followed it
  const handleEditAndRegenerate = useCallback(async (msgId: number, content: string) => {
    if (currentId == null || !content.trim()) return;
    try {
      const { message: updated, memory_recheck_count } = await editMessage(currentId, msgId, content);
      // replace the edited message and drop everything after it (the old answer + rest)
      setMessages((m) => {
        const idx = m.findIndex((x) => x.id === msgId);
        if (idx === -1) return m;
        return [...m.slice(0, idx + 1)].map((x) => (x.id === msgId ? { ...updated } : x));
      });
      if (memory_recheck_count && memory_recheck_count > 0) {
        setMessageNotice({
          kind: "info",
          text: "Связанные воспоминания помечены для повторного извлечения.",
        });
      }
      // re-send as a fresh turn (the edited user msg is already persisted)
      await regenerateFrom(content);
    } catch (error) {
      setMessageNotice({ kind: "error", text: `Не удалось изменить сообщение: ${(error as Error).message}` });
    }
  }, [currentId, regenerateFrom]);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || busy || currentId == null) return;
    setInput("");

    if (text.startsWith("/")) {
      const userBubble: UIMsg = {
        id: -Date.now(), chat_id: currentId, role: "user",
        content: text, created_at: new Date().toISOString(), edited_at: null, local: true,
      };
      setMessages((m) => [...m, userBubble]);
      setBusy(true);
      try {
        const res = await runCommand(text, currentId);
        for (const event of res.tool_events ?? []) {
          handleChatEvent(currentId, event);
        }
        if (res.confirmation && !(res.tool_events ?? []).some(
          (event) => event.type === "confirmation_required"
        )) {
          setConfirmations((items) => [res.confirmation!, ...items]);
        }
        const out: UIMsg = {
          id: -Date.now() - 1, chat_id: currentId,
          role: res.error ? "system" : "assistant",
          content: res.is_command ? res.text ?? "(пусто)" : "(не команда)",
          created_at: new Date().toISOString(), edited_at: null, local: true,
        };
        setMessages((m) => [...m, out]);
      } catch (e) {
        setMessages((m) => [...m, {
          id: -Date.now() - 2, chat_id: currentId, role: "system",
          content: "Ошибка: " + (e as Error).message, created_at: "", edited_at: null, local: true,
        }]);
      } finally {
        setBusy(false);
        refreshHealth();
        refreshConfirmations(currentId);
        refreshToolRuns(currentId);
      }
      return;
    }

    const placeholderId = -Date.now() - 3;
    const userBubble: UIMsg = {
      id: -Date.now(), chat_id: currentId, role: "user",
      content: text, created_at: new Date().toISOString(), edited_at: null, local: true,
    };
    const placeholder: UIMsg = {
      id: placeholderId, chat_id: currentId, role: "assistant",
      content: "", created_at: "", edited_at: null, local: true, streaming: true,
    };
    setMessages((m) => [...m, userBubble, placeholder]);
    setBusy(true);
    setStreaming(true);
    const controller = new AbortController();
    abortRef.current = controller;
    const onEvent = (event: ChatEvent) => handleChatEvent(currentId, event);
    try {
      const done = await streamChat(currentId, text, (tok) => {
        setMessages((m) =>
          m.map((x) => (x.id === placeholderId ? { ...x, content: x.content + tok } : x))
        );
      }, controller.signal, onEvent);
      if (done) {
        setMessages((m) => {
          const cleaned = m.filter((x) => x.id !== placeholderId && x.id !== userBubble.id);
          return [...cleaned, done.user_message, done.assistant_message];
        });
        if (done.title) {
          setChats((prev) => [...prev].sort(byOrder).map((c) => (c.id === currentId ? { ...c, title: done.title } : c)));
          setChats((prev) => [...prev].sort(byOrder));
        }
      } else {
        setMessages((m) => m.map((x) => (x.id === placeholderId ? { ...x, streaming: false } : x)));
      }
    } catch (e) {
      setMessages((m) => m.map((x) =>
        x.id === placeholderId ? { ...x, content: "Ошибка: " + (e as Error).message, streaming: false } : x
      ));
    } finally {
      setBusy(false);
      setStreaming(false);
      abortRef.current = null;
      refreshHealth();
      refreshToolRuns(currentId);
    }
  }, [
    input,
    busy,
    currentId,
    handleChatEvent,
    refreshConfirmations,
    refreshHealth,
    refreshToolRuns,
  ]);

  const stop = useCallback(() => { abortRef.current?.abort(); }, []);

  const resolvePendingConfirmation = useCallback(async (
    id: number,
    decision: "approve" | "reject"
  ) => {
    const response = await resolveConfirmation(id, decision);
    setConfirmations((items) => items.filter((item) => item.id !== id));
    if (response.result.error) {
      setMessages((items) => [...items, {
        id: -Date.now(), chat_id: currentId ?? 0, role: "system",
        content: `Действие не выполнено: ${String(response.result.error)}`,
        created_at: new Date().toISOString(), edited_at: null, local: true,
      }]);
    }
    if (currentId !== null) await refreshToolRuns(currentId);
  }, [currentId, refreshToolRuns]);

  const handleExportMd = useCallback(async () => {
    if (currentId == null) return;
    const md = await exportChatMarkdown(currentId);
    const title = chats.find((c) => c.id === currentId)?.title || "chat";
    const blob = new Blob([md], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${title.replace(/[^\w\u0400-\u04FFа-яА-Я-]+/g, "_")}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }, [currentId, chats]);

  const handleExportPdf = useCallback(() => { window.print(); }, []);

  const currentChat = chats.find((c) => c.id === currentId);

  return (
    <div className={"layout" + (sidebarOpen ? " sidebar-open" : "")}>
      <Sidebar
        chats={chats}
        folders={folders}
        currentId={currentId}
        health={health}
        theme={theme}
        onSelect={(id) => { selectChat(id); setView("chat"); setSidebarOpen(false); }}
        onNew={(folderId) => { newChat(folderId); setView("chat"); setSidebarOpen(false); }}
        onDelete={removeChat}
        onRename={rename}
        onTogglePin={togglePin}
        onMove={moveToFolder}
        onCreateFolder={createFolderCb}
        onRenameFolder={renameFolderCb}
        onDeleteFolder={removeFolder}
        onThemeChange={setTheme}
        onOpenMemory={() => { setView("memory"); setSidebarOpen(false); }}
        onOpenToday={() => { setView("today"); setSidebarOpen(false); }}
        onOpenSettings={() => { setView("settings"); setSidebarOpen(false); }}
        onOpenTasks={() => { setView("tasks"); setSidebarOpen(false); }}
        onClose={() => setSidebarOpen(false)}
      />
      {sidebarOpen && <div className="overlay" onClick={() => setSidebarOpen(false)} />}
      {view === "memory" ? (
        <main className="chat">
          <MemoryView onClose={() => setView("chat")} />
        </main>
      ) : view === "tasks" ? (
        <main className="chat">
          <TasksView onClose={() => setView("chat")} />
        </main>
      ) : view === "today" ? (
        <main className="chat">
          <TodayView onClose={() => setView("chat")} />
        </main>
      ) : view === "settings" ? (
        <main className="chat">
          <SettingsView onClose={() => setView("chat")} />
        </main>
      ) : (
      <main className="chat">
        <header className="chat-topbar">
          <button
            className="menu-btn"
            title="Чаты"
            onClick={() => setSidebarOpen((v) => !v)}
            aria-label="Открыть список чатов"
          >☰</button>
          <span className="topbar-title">{currentChat?.title ?? "Second Brain"}</span>
          <div className="topbar-actions no-print">
            <button className="icon-btn" title="Экспорт в Markdown" onClick={handleExportMd}>⤓ MD</button>
            <button className="icon-btn" title="Печать / сохранить в PDF" onClick={handleExportPdf}>🖨 PDF</button>
          </div>
        </header>
        <div className="messages" ref={scrollRef}>
          {messageNotice && (
            <div className={`message-notice ${messageNotice.kind}`} role="status">
              {messageNotice.text}
            </div>
          )}
          {messages.length === 0 && (
            <div className="empty">Напишите что-нибудь — и я это запомню. Команды начинаются с /</div>
          )}
          {messages.map((m) => (
            <Message
              key={m.id}
              msg={m}
              canEdit={m.role === "user"}
              onDelete={handleDeleteMessage}
              onEditAndRegenerate={handleEditAndRegenerate}
            />
          ))}
          {toolRuns.length > 0 && (
            <section className="tool-runs" aria-label="История вызовов инструментов">
              <div className="tool-runs-title">Инструменты <span>{toolRuns.length}</span></div>
              {toolRuns.map((run) => (
                <article className={`tool-run-card status-${toolRunStatusClass(run.status)}`} key={run.id}>
                  <div className="tool-run-head">
                    <code>{run.tool_name}</code>
                    <span className="tool-run-status">{toolRunStatusLabel(run.status)}</span>
                  </div>
                  {(run.policy_decision || run.risk) && (
                    <div className="tool-run-policy">
                      {run.policy_decision && <span>Политика: {run.policy_decision}</span>}
                      {run.risk && run.risk !== run.policy_decision && <span>Риск: {run.risk}</span>}
                    </div>
                  )}
                  {hasToolData(run.arguments) && (
                    <details className="tool-run-details">
                      <summary>Аргументы</summary>
                      <pre>{formatToolData(run.arguments)}</pre>
                    </details>
                  )}
                  {hasToolData(run.result) && (
                    <details className="tool-run-details" open={toolResultHasError(run.result)}>
                      <summary>{toolResultHasError(run.result) ? "Ошибка" : "Результат"}</summary>
                      <pre>{formatToolData(run.result)}</pre>
                    </details>
                  )}
                </article>
              ))}
            </section>
          )}
          {confirmations.map((confirmation) => (
            <section className="confirmation-card" key={confirmation.id}>
              <div className="confirmation-title">Требуется подтверждение</div>
              <div className="confirmation-copy">
                <code>{confirmation.tool_name}</code> · {confirmation.risk}
              </div>
              <pre>{JSON.stringify(confirmation.arguments, null, 2)}</pre>
              <div className="confirmation-actions">
                <button onClick={() => resolvePendingConfirmation(confirmation.id, "approve")}>Подтвердить</button>
                <button className="danger" onClick={() => resolvePendingConfirmation(confirmation.id, "reject")}>Отклонить</button>
              </div>
            </section>
          ))}
        </div>
        <Composer
          value={input}
          onChange={setInput}
          onSend={send}
          onStop={stop}
          busy={busy}
          streaming={streaming}
        />
      </main>
      )}
    </div>
  );
}

// ---- helpers ----
function byOrder(a: Chat, b: Chat): number {
  if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
  return b.updated_at.localeCompare(a.updated_at);
}

function isToolRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function hasToolData(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  if (typeof value === "string") return value.trim().length > 0;
  if (Array.isArray(value)) return value.length > 0;
  return !isToolRecord(value) || Object.keys(value).length > 0;
}

function toolResultHasError(value: unknown): boolean {
  return isToolRecord(value) && Object.prototype.hasOwnProperty.call(value, "error");
}

function formatToolData(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function toolRunStatusClass(status: string): string {
  return ["running", "pending_confirmation", "succeeded", "failed", "rejected"].includes(status)
    ? status
    : "unknown";
}

function toolRunStatusLabel(status: string): string {
  switch (status) {
    case "running": return "Выполняется";
    case "pending_confirmation": return "Ждёт подтверждения";
    case "succeeded": return "Готово";
    case "failed": return "Ошибка";
    case "rejected": return "Отклонено";
    default: return status || "Неизвестно";
  }
}
