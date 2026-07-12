import { useCallback, useEffect, useRef, useState } from "react";
import Sidebar from "./components/Sidebar";
import Composer from "./components/Composer";
import Message from "./components/Message";
import MemoryView from "./components/MemoryView";
import TasksView from "./components/TasksView";
import {
  type Chat,
  type ChatEvent,
  type Confirmation,
  type Folder,
  type Health,
  type Msg,
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
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">(loadTheme);
  const [view, setView] = useState<"chat" | "memory" | "tasks">("chat");
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // apply theme class to <html>
  useEffect(() => {
    document.documentElement.classList.toggle("light", theme === "light");
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

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

  // ref to always read latest drafts without re-creating selectChat on every draft change
  const draftsRef = useRef(drafts);
  draftsRef.current = drafts;

  const selectChat = useCallback(
    (id: number) => {
      setCurrentId(id);
      setInput(draftsRef.current[id] ?? "");
      loadMessages(id);
      refreshConfirmations(id);
    },
    [loadMessages, refreshConfirmations]
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
    await apiDeleteMessage(currentId, msgId);
    setMessages((m) => m.filter((x) => x.id !== msgId));
  }, [currentId]);

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
    const onEvent = (event: ChatEvent) => {
      if (event.type === "confirmation_required") {
        setConfirmations((items) => [event.confirmation, ...items]);
      }
    };
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
    }
  }, [currentId, refreshHealth]);

  // edit a user message then regenerate the assistant reply that followed it
  const handleEditAndRegenerate = useCallback(async (msgId: number, content: string) => {
    if (currentId == null || !content.trim()) return;
    const updated = await editMessage(currentId, msgId, content);
    // replace the edited message and drop everything after it (the old answer + rest)
    setMessages((m) => {
      const idx = m.findIndex((x) => x.id === msgId);
      if (idx === -1) return m;
      return [...m.slice(0, idx + 1)].map((x) => (x.id === msgId ? { ...updated } : x));
    });
    // re-send as a fresh turn (the edited user msg is already persisted)
    await regenerateFrom(content);
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
    const onEvent = (event: ChatEvent) => {
      if (event.type === "confirmation_required") {
        setConfirmations((items) => [event.confirmation, ...items]);
      }
    };
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
    }
  }, [input, busy, currentId, refreshHealth]);

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
  }, [currentId]);

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
