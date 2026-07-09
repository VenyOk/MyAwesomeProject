import { fmtMoscow, type Msg } from "../api";

type Props = { msg: Msg & { local?: boolean; streaming?: boolean } };

export default function Message({ msg }: Props) {
  const isUser = msg.role === "user";
  const isSystem = msg.role === "system";
  const role = "role-" + msg.role;

  return (
    <div className={"message " + (isUser ? "user" : isSystem ? "system" : "assistant")}>
      <div className="avatar">{isUser ? "Вы" : isSystem ? "•" : "◎"}</div>
      <div className="content">
        <div className="meta">
          <span className="author">{isUser ? "Вы" : isSystem ? "Система" : "Мозг"}</span>
          {msg.created_at && <span className="time">{fmtMoscow(msg.created_at)} МСК</span>}
        </div>
        <div className={"bubble " + role}>
          {msg.content}
          {msg.streaming && <span className="cursor">▋</span>}
        </div>
      </div>
    </div>
  );
}
