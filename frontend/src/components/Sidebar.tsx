import { useEffect, useRef, useState } from "react";
import type { Chat, Health } from "../api";

type Props = {
  chats: Chat[];
  currentId: number | null;
  health: Health | null;
  onSelect: (id: number) => void;
  onNew: () => void;
  onDelete: (id: number) => void;
  onRename: (id: number, title: string) => void;
};

export default function Sidebar({ chats, currentId, health, onSelect, onNew, onDelete, onRename }: Props) {
  const [editingId, setEditingId] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editingId != null) inputRef.current?.select();
  }, [editingId]);

  const startEdit = (c: Chat) => {
    setEditingId(c.id);
    setDraft(c.title);
  };
  const commit = () => {
    if (editingId != null && draft.trim()) onRename(editingId, draft.trim());
    setEditingId(null);
  };

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="logo">◎</span>
        <span>Second Brain</span>
      </div>

      <button className="new-chat" onClick={onNew}>+ Новый чат</button>

      <div className="chat-list">
        {chats.map((c) => (
          <div
            key={c.id}
            className={"chat-item" + (c.id === currentId ? " active" : "")}
            onClick={() => editingId !== c.id && onSelect(c.id)}
            onDoubleClick={() => startEdit(c)}
          >
            {editingId === c.id ? (
              <input
                ref={inputRef}
                className="rename-input"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onClick={(e) => e.stopPropagation()}
                onBlur={commit}
                onKeyDown={(e) => {
                  if (e.key === "Enter") commit();
                  if (e.key === "Escape") setEditingId(null);
                }}
              />
            ) : (
              <>
                <span className="chat-title">{c.title}</span>
                <button
                  className="chat-del"
                  title="Удалить чат"
                  onClick={(e) => { e.stopPropagation(); onDelete(c.id); }}
                >×</button>
              </>
            )}
          </div>
        ))}
      </div>

      <div className="status">
        {health ? (
          <>
            <div>Модель: <b>{health.model.replace("google/", "")}</b></div>
            <div>Загружена: {health.model_loaded ? "да" : "нет"}</div>
            <div>Воспоминаний: {health.memories} · Индекс: {health.index_size}</div>
          </>
        ) : (
          <div>нет связи с сервером</div>
        )}
      </div>

      <div className="hint">
        Двойной клик по чату — переименовать. Команды: <code>/save</code>, <code>/search</code>, <code>/recent</code>, <code>/summary</code>.
      </div>
    </aside>
  );
}
