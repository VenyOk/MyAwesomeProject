import { useCallback, useEffect, useRef, useState } from "react";
import Sidebar from "./components/Sidebar";
import Composer from "./components/Composer";
import Message from "./components/Message";
import {
  type Chat,
  type Health,
  type Msg,
  createChat,
  deleteChat,
  getHealth,
  getMessages,
  listChats,
  renameChat,
  runCommand,
  streamChat,
} from "./api";

type UIMsg = Msg & { local?: boolean; streaming?: boolean };


export default function App() {
  const [chats, setChats] = useState<Chat[]>([]);
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [messages, setMessages] = useState<UIMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const refreshHealth = useCallback(async () => {
    try {
      setHealth(await getHealth());
    } catch {
      setHealth(null);
    }
  }, []);

  const loadMessages = useCallback(async (id: number) => {
    const msgs = await getMessages(id);
    setMessages(msgs);
  }, []);

  const selectChat = useCallback(
    (id: number) => {
      setCurrentId(id);
      loadMessages(id);
    },
    [loadMessages]
  );

  // initial load
  useEffect(() => {
    (async () => {
      let list = await listChats();
      if (list.length === 0) {
        const c = await createChat();
        list = [c];
      }
      setChats(list);
      selectChat(list[0].id);
      refreshHealth();
    })();
  }, [selectChat, refreshHealth]);

  // autoscroll
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const newChat = useCallback(async () => {
    const c = await createChat();
    setChats((prev) => [c, ...prev]);
    selectChat(c.id);
  }, [selectChat]);

  const removeChat = useCallback(
    async (id: number) => {
      await deleteChat(id);
      setChats(async (prev) => {
        const next = prev.filter((c) => c.id !== id);
        if (id === currentId) {
          if (next.length > 0) selectChat(next[0].id);
          else {
            const c = await createChat();
            selectChat(c.id);
            return [c];
          }
        }
        return next;
      });
    },
    [currentId, selectChat]
  );

  const rename = useCallback(async (id: number, title: string) => {
    const updated = await renameChat(id, title);
    setChats((prev) => prev.map((c) => (c.id === id ? updated : c)));
  }, []);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || busy || currentId == null) return;
    setInput("");

    if (text.startsWith("/")) {
      const userBubble: UIMsg = {
        id: -Date.now(), chat_id: currentId, role: "user",
        content: text, created_at: new Date().toISOString(), local: true,
      };
      setMessages((m) => [...m, userBubble]);
      setBusy(true);
      try {
        const res = await runCommand(text, currentId);
        const out: UIMsg = {
          id: -Date.now() - 1, chat_id: currentId,
          role: res.error ? "system" : "assistant",
          content: res.is_command ? res.text ?? "(пусто)" : "(не команда)",
          created_at: new Date().toISOString(), local: true,
        };
        setMessages((m) => [...m, out]);
      } catch (e) {
        setMessages((m) => [...m, {
          id: -Date.now() - 2, chat_id: currentId, role: "system",
          content: "Ошибка: " + (e as Error).message, created_at: "", local: true,
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
      content: text, created_at: new Date().toISOString(), local: true,
    };
    const placeholder: UIMsg = {
      id: placeholderId, chat_id: currentId, role: "assistant",
      content: "", created_at: "", local: true, streaming: true,
    };
    setMessages((m) => [...m, userBubble, placeholder]);
    setBusy(true);
    try {
      const done = await streamChat(currentId, text, (tok) => {
        setMessages((m) =>
          m.map((x) => (x.id === placeholderId ? { ...x, content: x.content + tok } : x))
        );
      });
      setMessages((m) => {
        const cleaned = m.filter((x) => x.id !== placeholderId && x.id !== userBubble.id);
        return [...cleaned, done.user_message, done.assistant_message];
      });
      if (done.title) {
        setChats((prev) => prev.map((c) => (c.id === currentId ? { ...c, title: done.title } : c)));
      }
    } catch (e) {
      setMessages((m) =>
        m.map((x) =>
          x.id === placeholderId
            ? { ...x, content: "Ошибка: " + (e as Error).message, streaming: false }
            : x
        )
      );
    } finally {
      setBusy(false);
      refreshHealth();
    }
  }, [input, busy, currentId, refreshHealth]);

  return (
    <div className="layout">
      <Sidebar
        chats={chats}
        currentId={currentId}
        health={health}
        onSelect={selectChat}
        onNew={newChat}
        onDelete={removeChat}
        onRename={rename}
      />
      <main className="chat">
        <div className="messages" ref={scrollRef}>
          {messages.length === 0 && (
            <div className="empty">Напишите что-нибудь — и я это запомню. Команды начинаются с /</div>
          )}
          {messages.map((m) => (
            <Message key={m.id} msg={m} />
          ))}
        </div>
        <Composer
          value={input}
          onChange={setInput}
          onSend={send}
          busy={busy}
        />
      </main>
    </div>
  );
}
