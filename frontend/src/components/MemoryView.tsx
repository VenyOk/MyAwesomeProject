import { useCallback, useEffect, useState } from "react";
import {
  activateMemory,
  deleteMemory,
  listMemories,
  restoreMemory,
  updateMemory,
  type Memory,
} from "../api";

type Filter = "active" | "candidate" | "deleted" | "all";

const KIND_LABELS: Record<string, string> = {
  fact: "факт",
  preference: "предпочтение",
  decision: "решение",
  idea: "идея",
  person: "человек",
  project: "проект",
};

export default function MemoryView({ onClose }: { onClose: () => void }) {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [filter, setFilter] = useState<Filter>("active");
  const [query, setQuery] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const params: { status?: string; q?: string; limit?: number } = { limit: 200 };
      if (filter === "active") params.status = "active";
      else if (filter === "candidate") params.status = "candidate";
      else if (filter === "deleted") params.status = "deleted";
      if (query.trim()) params.q = query.trim();
      const data = await listMemories(params);
      setMemories(data.memories);
    } catch {
      setMemories([]);
    } finally {
      setLoading(false);
    }
  }, [filter, query]);

  useEffect(() => { refresh(); }, [refresh]);

  const handleActivate = async (id: number) => {
    await activateMemory(id);
    refresh();
  };
  const handleDelete = async (id: number) => {
    if (!confirm("Удалить воспоминание? Его можно восстановить из раздела «Удалённые».")) return;
    await deleteMemory(id);
    refresh();
  };
  const handleRestore = async (id: number) => {
    await restoreMemory(id);
    refresh();
  };
  const startEdit = (m: Memory) => { setEditingId(m.id); setDraft(m.content); };
  const commitEdit = async (id: number) => {
    if (draft.trim()) {
      await updateMemory(id, { content: draft.trim() });
    }
    setEditingId(null);
    refresh();
  };

  return (
    <div className="memory-view">
      <header className="memory-topbar">
        <button className="back-btn" onClick={onClose} title="Назад к чатам">← Чаты</button>
        <span className="memory-title">Память</span>
        <span className="memory-count">{memories.length}</span>
      </header>

      <div className="memory-filters">
        {(["active", "candidate", "deleted", "all"] as Filter[]).map((f) => (
          <button
            key={f}
            className={"mem-filter" + (filter === f ? " active" : "")}
            onClick={() => setFilter(f)}
          >
            {f === "all" ? "Все" : f === "active" ? "Активные" : f === "candidate" ? "Кандидаты" : "Удалённые"}
          </button>
        ))}
      </div>

      <input
        className="memory-search"
        placeholder="Поиск по памяти…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />

      <div className="memory-list">
        {loading && <div className="memory-empty">Загрузка…</div>}
        {!loading && memories.length === 0 && (
          <div className="memory-empty">Пока пусто. Скажите в чате «запомни, что …» — и здесь появится запись.</div>
        )}
        {memories.map((m) => (
          <div key={m.id} className={"memory-card" + (m.status === "candidate" ? " candidate" : "")}>
            <div className="memory-card-head">
              <span className={"mem-kind kind-" + m.kind}>{KIND_LABELS[m.kind] ?? m.kind}</span>
              {m.status === "candidate" && <span className="mem-badge candidate">кандидат</span>}
              {m.status === "deleted" && <span className="mem-badge deleted">удалено</span>}
              <span className="mem-importance" title="важность">важн. {(m.importance ?? 0).toFixed(2)}</span>
              <span className="mem-confidence" title="уверенность">увер. {(m.confidence ?? 0).toFixed(2)}</span>
            </div>

            {editingId === m.id ? (
              <div className="memory-edit">
                <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={3} autoFocus />
                <div className="memory-edit-row">
                  <button onClick={() => commitEdit(m.id)}>Сохранить</button>
                  <button onClick={() => setEditingId(null)}>Отмена</button>
                </div>
              </div>
            ) : (
              <div className="memory-content">{m.content}</div>
            )}

            {m.source_message_id != null && (
              <div className="memory-source">из сообщения #{m.source_message_id}</div>
            )}

            {m.status !== "deleted" && editingId !== m.id && (
              <div className="memory-actions">
                {m.status === "candidate" && (
                  <button onClick={() => handleActivate(m.id)}>✓ Активировать</button>
                )}
                <button onClick={() => startEdit(m)}>✎ Изменить</button>
                <button className="danger" onClick={() => handleDelete(m.id)}>🗑 Удалить</button>
              </div>
            )}
            {m.status === "deleted" && (
              <div className="memory-actions">
                <button onClick={() => handleRestore(m.id)}>↩ Восстановить</button>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
