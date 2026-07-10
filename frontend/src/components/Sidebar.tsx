import { useEffect, useMemo, useRef, useState } from "react";
import type { Chat, Folder, Health } from "../api";
import { searchMessages, type Msg } from "../api";

type Props = {
  chats: Chat[];
  folders: Folder[];
  currentId: number | null;
  health: Health | null;
  theme: "dark" | "light";
  onSelect: (id: number) => void;
  onNew: (folderId?: number) => void;
  onDelete: (id: number) => void;
  onRename: (id: number, title: string) => void;
  onTogglePin: (id: number, pinned: boolean) => void;
  onMove: (chatId: number, folderId: number | null) => void;
  onCreateFolder: (name: string, description?: string) => Promise<void>;
  onRenameFolder: (id: number, name: string, description?: string) => Promise<void>;
  onDeleteFolder: (id: number) => Promise<void>;
  onThemeChange: (t: "dark" | "light") => void;
  onClose: () => void;
};

export default function Sidebar(props: Props) {
  const {
    chats, folders, currentId, health, theme,
    onSelect, onNew, onDelete, onRename, onTogglePin, onMove,
    onCreateFolder, onRenameFolder, onDeleteFolder, onThemeChange, onClose,
  } = props;

  const [editingId, setEditingId] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [search, setSearch] = useState("");
  const [hits, setHits] = useState<Msg[] | null>(null);
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [folderName, setFolderName] = useState("");
  const [folderDesc, setFolderDesc] = useState("");
  const [editingFolder, setEditingFolder] = useState<number | null>(null);
  const [editFolderName, setEditFolderName] = useState("");
  const [editFolderDesc, setEditFolderDesc] = useState("");
  const [menuFor, setMenuFor] = useState<number | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { if (editingId != null) inputRef.current?.select(); }, [editingId]);

  // debounce search across messages
  useEffect(() => {
    const q = search.trim();
    if (!q) { setHits(null); return; }
    const t = setTimeout(async () => {
      try {
        const res = await searchMessages(q);
        setHits(res.messages);
      } catch { setHits([]); }
    }, 250);
    return () => clearTimeout(t);
  }, [search]);

  const startEdit = (c: Chat) => { setEditingId(c.id); setDraft(c.title); };
  const commit = () => {
    if (editingId != null && draft.trim()) onRename(editingId, draft.trim());
    setEditingId(null);
  };

  const createFolder = async () => {
    if (!folderName.trim()) { setCreatingFolder(false); return; }
    await onCreateFolder(folderName.trim(), folderDesc.trim());
    setFolderName(""); setFolderDesc(""); setCreatingFolder(false);
  };

  const saveFolder = async () => {
    if (editingFolder != null) {
      await onRenameFolder(editingFolder, editFolderName.trim() || "Папка", editFolderDesc.trim());
    }
    setEditingFolder(null);
  };

  // grouping
  const titleFilter = search.trim().toLowerCase();
  const filtered = useMemo(() => {
    let list = chats;
    if (titleFilter && !hits) {
      list = chats.filter((c) => c.title.toLowerCase().includes(titleFilter));
    }
    return list;
  }, [chats, titleFilter, hits]);

  const pinned = filtered.filter((c) => c.pinned);
  const noFolder = filtered.filter((c) => !c.pinned && c.folder_id == null);

  const renderChat = (c: Chat) => (
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
          {c.pinned && <span className="pin-mark" title="Закреплён">📌</span>}
          <span className="chat-title">{c.title}</span>
          <button
            className="chat-more"
            title="Действия"
            onClick={(e) => { e.stopPropagation(); setMenuFor(menuFor === c.id ? null : c.id); }}
          >⋯</button>
          {menuFor === c.id && (
            <div className="chat-menu" onClick={(e) => e.stopPropagation()}>
              <button onClick={() => { onTogglePin(c.id, !c.pinned); setMenuFor(null); }}>
                {c.pinned ? "Открепить" : "Закрепить"}
              </button>
              <button onClick={() => { startEdit(c); setMenuFor(null); }}>Переименовать</button>
              <div className="chat-menu-sub">В папку:</div>
              <button onClick={() => { onMove(c.id, null); setMenuFor(null); }}>— Без папки</button>
              {folders.map((f) => (
                <button key={f.id} onClick={() => { onMove(c.id, f.id); setMenuFor(null); }}>
                  {f.name}
                </button>
              ))}
              <button className="danger" onClick={() => { onDelete(c.id); setMenuFor(null); }}>Удалить чат</button>
            </div>
          )}
        </>
      )}
    </div>
  );

  const chatIdsInHits = hits ? new Set(hits.map((h) => h.chat_id)) : null;

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="logo">◎</span>
        <span>Second Brain</span>
        <button className="brand-close" onClick={onClose} title="Закрыть" aria-label="Закрыть">×</button>
      </div>

      <div className="search-wrap">
        <input
          className="search-input"
          placeholder="Поиск по чатам и сообщениям…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <button className="new-chat" onClick={() => onNew()}>+ Новый чат</button>

      <div className="chat-list">
        {/* search results override the list */}
        {hits ? (
          <>
            <div className="section-label">Найдено: {hits.length}</div>
            {hits.map((h) => (
              <div key={h.id} className="search-hit" onClick={() => onSelect(h.chat_id)}>
                <div className="search-hit-preview">
                  {h.content.slice(0, 70)}{h.content.length > 70 ? "…" : ""}
                </div>
                <div className="search-hit-meta">
                  {chats.find((c) => c.id === h.chat_id)?.title ?? `Чат #${h.chat_id}`}
                </div>
              </div>
            ))}
          </>
        ) : (
          <>
            {pinned.length > 0 && (
              <>
                <div className="section-label">📌 Закреплённые</div>
                {pinned.map(renderChat)}
              </>
            )}
            {folders.map((f) => {
              const items = filtered.filter((c) => !c.pinned && c.folder_id === f.id);
              if (items.length === 0 && titleFilter) return null;
              return (
                <div key={f.id} className="folder-group">
                  <div className="folder-head">
                    <span
                      className="folder-name"
                      onDoubleClick={() => {
                        setEditingFolder(f.id);
                        setEditFolderName(f.name);
                        setEditFolderDesc(f.description);
                      }}
                    >📂 {f.name}</span>
                    <button
                      className="folder-add"
                      title="Чат в эту папку"
                      onClick={() => onNew(f.id)}
                    >+</button>
                  </div>
                  {editingFolder === f.id ? (
                    <div className="folder-edit">
                      <input value={editFolderName} onChange={(e) => setEditFolderName(e.target.value)} placeholder="Название" />
                      <textarea value={editFolderDesc} onChange={(e) => setEditFolderDesc(e.target.value)} placeholder="Описание (общий контекст)" rows={2} />
                      <div className="folder-edit-row">
                        <button onClick={saveFolder}>OK</button>
                        <button onClick={() => setEditingFolder(null)}>Отмена</button>
                        <button className="danger" onClick={() => { onDeleteFolder(f.id); setEditingFolder(null); }}>Удалить</button>
                      </div>
                    </div>
                  ) : (
                    f.description && <div className="folder-desc">{f.description}</div>
                  )}
                  {items.map(renderChat)}
                </div>
              );
            })}
            {noFolder.length > 0 && (
              <>
                {folders.length > 0 && <div className="section-label">Чаты</div>}
                {noFolder.map(renderChat)}
              </>
            )}
            {creatingFolder && (
              <div className="folder-edit">
                <input value={folderName} onChange={(e) => setFolderName(e.target.value)} placeholder="Название папки" autoFocus />
                <textarea value={folderDesc} onChange={(e) => setFolderDesc(e.target.value)} placeholder="Описание — общий контекст для чатов" rows={2} />
                <div className="folder-edit-row">
                  <button onClick={createFolder}>Создать</button>
                  <button onClick={() => setCreatingFolder(false)}>Отмена</button>
                </div>
              </div>
            )}
          </>
        )}
      </div>

      <button className="add-folder-btn" onClick={() => setCreatingFolder(true)}>+ Новая папка</button>

      <button
        className="theme-toggle"
        onClick={() => onThemeChange(theme === "dark" ? "light" : "dark")}
        title="Переключить тему"
      >
        {theme === "dark" ? "☀ Светлая тема" : "🌙 Тёмная тема"}
      </button>

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
