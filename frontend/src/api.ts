export type Chat = {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
};

export type Msg = {
  id: number;
  chat_id: number;
  role: string;
  content: string;
  created_at: string;
};

export type Health = {
  status: string;
  model: string;
  model_loaded: boolean;
  memories: number;
  index_size: number;
  chats: number;
};

const BASE = "/api";

async function jget<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json() as Promise<T>;
}

async function jpost<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json() as Promise<T>;
}

export async function listChats(): Promise<Chat[]> {
  const data = await jget<{ chats: Chat[] }>(`${BASE}/chats`);
  return data.chats;
}

export function createChat(title?: string): Promise<Chat> {
  return jpost<Chat>(`${BASE}/chats`, { title });
}

export async function renameChat(id: number, title: string): Promise<Chat> {
  const r = await fetch(`${BASE}/chats/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function deleteChat(id: number): Promise<void> {
  await fetch(`${BASE}/chats/${id}`, { method: "DELETE" });
}

export async function getMessages(id: number): Promise<Msg[]> {
  const data = await jget<{ messages: Msg[] }>(`${BASE}/chats/${id}/messages`);
  return data.messages;
}

export type CommandResult = {
  is_command: boolean;
  text?: string;
  error?: boolean;
};

export function runCommand(input: string, chatId: number | null): Promise<CommandResult> {
  return jpost<CommandResult>(`${BASE}/command`, { input, chat_id: chatId });
}

export function getHealth(): Promise<Health> {
  return jget<Health>(`${BASE}/health`);
}

export type ChatDone = {
  user_message: Msg;
  assistant_message: Msg;
  title: string;
};

export async function streamChat(
  chatId: number,
  message: string,
  onToken: (token: string) => void
): Promise<ChatDone> {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, message }),
  });
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let donePayload: ChatDone | null = null;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data:")) continue;
      const payload = JSON.parse(line.slice(5).trim());
      if (payload.token) onToken(payload.token);
      if (payload.done) donePayload = payload as ChatDone;
    }
  }
  if (!donePayload) throw new Error("Stream ended without done");
  return donePayload;
}

const MSK_FMT = new Intl.DateTimeFormat("ru-RU", {
  timeZone: "Europe/Moscow",
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export function fmtMoscow(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return MSK_FMT.format(d);
}
