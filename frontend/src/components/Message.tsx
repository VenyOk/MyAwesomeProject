import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { fmtMoscow, type Msg } from "../api";

type Props = {
  msg: Msg & { local?: boolean; streaming?: boolean };
  canEdit?: boolean;
  onDelete?: (id: number) => void;
  onEdit?: (id: number, content: string) => void;
  onEditAndRegenerate?: (id: number, content: string) => void;
};

export default function Message({ msg, canEdit, onDelete, onEdit, onEditAndRegenerate }: Props) {
  const isUser = msg.role === "user";
  const isSystem = msg.role === "system";
  const role = "role-" + msg.role;
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(msg.content);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(msg.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard unavailable */ }
  };

  const startEdit = () => { setDraft(msg.content); setEditing(true); };
  const cancelEdit = () => { setEditing(false); setDraft(msg.content); };
  const saveEdit = () => {
    const v = draft.trim();
    if (!v) { cancelEdit(); return; }
    if (canEdit && onEditAndRegenerate) onEditAndRegenerate(msg.id, v);
    else if (onEdit) onEdit(msg.id, v);
    setEditing(false);
  };

  const waiting = msg.streaming && !msg.content;
  const showActions = !isUser && !isSystem && !!msg.content && !msg.streaming;

  return (
    <div className={"message " + (isUser ? "user" : isSystem ? "system" : "assistant")}>
      <div className="avatar">{isUser ? "Вы" : isSystem ? "•" : "◎"}</div>
      <div className="content">
        <div className="meta">
          <span className="author">{isUser ? "Вы" : isSystem ? "Система" : "Мозг"}</span>
          {msg.created_at && <span className="time">{fmtMoscow(msg.created_at)} МСК</span>}
          {msg.edited_at && <span className="edited-badge" title={"Изменено " + fmtMoscow(msg.edited_at)}>ред.</span>}
        </div>

        {editing ? (
          <div className="edit-area">
            <textarea
              className="edit-textarea"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={Math.min(10, Math.max(2, draft.split("\n").length))}
              autoFocus
            />
            <div className="edit-actions">
              <button onClick={saveEdit}>Сохранить{canEdit ? " и ответить заново" : ""}</button>
              <button onClick={cancelEdit}>Отмена</button>
            </div>
          </div>
        ) : (
          <div className={"bubble " + role}>
            {waiting ? (
              <span className="typing"><span></span><span></span><span></span></span>
            ) : isUser || isSystem ? (
              msg.content
            ) : (
              <div className="markdown">
                <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
                  {msg.content}
                </ReactMarkdown>
              </div>
            )}
            {msg.streaming && !waiting && <span className="cursor">▋</span>}
          </div>
        )}

        {!editing && (
          <div className="actions">
            {showActions && (
              <button className="action-btn" onClick={copy} title="Копировать">
                {copied ? "✓ Скопировано" : "Копировать"}
              </button>
            )}
            {canEdit && onEditAndRegenerate && (
              <button className="action-btn" onClick={startEdit} title="Изменить и заново ответить">Изменить</button>
            )}
            {!canEdit && onEdit && msg.content && (
              <button className="action-btn" onClick={startEdit} title="Изменить">Изменить</button>
            )}
            {onDelete && msg.content && (
              <button className="action-btn danger" onClick={() => onDelete(msg.id)} title="Удалить">Удалить</button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
