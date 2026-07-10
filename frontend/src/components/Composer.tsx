import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { COMMANDS } from "../commands";

type Props = {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  onStop: () => void;
  busy: boolean;
  streaming: boolean;
};

type Mode = "edit" | "preview";

export default function Composer({ value, onChange, onSend, onStop, busy, streaming }: Props) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [active, setActive] = useState(0);
  const [closed, setClosed] = useState(false);
  const [mode, setMode] = useState<Mode>("edit");

  const slash = value.startsWith("/");
  const typed = slash ? value.slice(1) : "";
  const typingCommand = slash && !typed.includes(" ");

  const filtered = useMemo(() => {
    if (!typingCommand) return [];
    const q = typed.toLowerCase();
    return COMMANDS.filter((c) => c.name.startsWith(q));
  }, [typingCommand, typed]);

  const menuOpen = !closed && filtered.length > 0;

  useEffect(() => { setActive(0); setClosed(false); }, [typed]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }, [value, mode]);

  const pick = (name: string) => {
    onChange("/" + name + " ");
    setClosed(true);
    requestAnimationFrame(() => {
      const el = ref.current;
      if (el) {
        el.focus();
        const pos = el.value.length;
        el.setSelectionRange(pos, pos);
      }
    });
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (menuOpen) {
      if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => (i + 1) % filtered.length); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => (i - 1 + filtered.length) % filtered.length); return; }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); pick(filtered[active].name); return; }
      if (e.key === "Escape") { e.preventDefault(); setClosed(true); return; }
    }
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSend(); }
  };

  return (
    <div className="composer no-print">
      {menuOpen && (
        <div className="cmd-menu">
          {filtered.map((c, i) => (
            <div
              key={c.name}
              className={"cmd-item" + (i === active ? " active" : "")}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => { e.preventDefault(); pick(c.name); }}
            >
              <span className="cmd-name">/{c.name}</span>
              {c.args && <span className="cmd-args">{c.args}</span>}
              <span className="cmd-desc">{c.desc}</span>
            </div>
          ))}
        </div>
      )}
      <div className="composer-row">
        <div className="composer-field">
          <div className="composer-tabs">
            <button className={"tab" + (mode === "edit" ? " active" : "")} onClick={() => setMode("edit")}>Текст</button>
            <button className={"tab" + (mode === "preview" ? " active" : "")} onClick={() => setMode("preview")}>Превью</button>
          </div>
          {mode === "edit" ? (
            <textarea
              ref={ref}
              className="composer-input"
              rows={1}
              placeholder="Напишите сообщение или /help…"
              value={value}
              onChange={(e) => onChange(e.target.value)}
              onKeyDown={onKeyDown}
            />
          ) : (
            <div className="composer-preview markdown">
              {value.trim() ? (
                <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
                  {value}
                </ReactMarkdown>
              ) : (
                <span className="preview-empty">Превью пусто — введите markdown-текст…</span>
              )}
            </div>
          )}
        </div>
        <button className="send-btn" onClick={onSend} disabled={busy || !value.trim()}>
          Отправить
        </button>
        {streaming && (
          <button className="stop-btn" onClick={onStop}>⏹ Стоп</button>
        )}
      </div>
    </div>
  );
}
