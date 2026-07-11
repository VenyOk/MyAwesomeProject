import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { fmtMoscow, type Msg } from "../api";

type Props = {
  msg: Msg & { local?: boolean; streaming?: boolean };
  canEdit?: boolean;          // true only for user's own messages
  onDelete?: (id: number) => void;   // only user messages
  onEditAndRegenerate?: (id: number, content: string) => void;  // only user messages
};

export default function Message({ msg, canEdit, onDelete, onEditAndRegenerate }: Props) {
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
    if (!v || !onEditAndRegenerate) { cancelEdit(); return; }
    onEditAndRegenerate(msg.id, v);
    setEditing(false);
  };

  const waiting = msg.streaming && !msg.content;
  const hasContent = !!msg.content;
  // copy: any message; edit+delete: only user's own; nothing while streaming
  const showActions = !editing && hasContent && !msg.streaming && !isSystem;

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
              <button className="primary" onClick={saveEdit}>Сохранить и ответить заново</button>
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

        {/* hover action toolbar */}
        {showActions && (
          <div className="msg-actions">
            <button
              className="icon-action"
              onClick={copy}
              title={copied ? "Скопировано" : "Копировать"}
            >
              {copied ? "✓" : "⧉"}
            </button>
            {canEdit && onEditAndRegenerate && (
              <button className="icon-action" onClick={startEdit} title="Изменить">✎</button>
            )}
            {canEdit && onDelete && (
              <button
                className="icon-action danger"
                onClick={() => onDelete(msg.id)}
                title="Удалить"
              >🗑</button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
