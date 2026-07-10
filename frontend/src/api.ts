export type Chat = {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
  folder_id: number | null;
  pinned: boolean;
};

export type Folder = {
  id: number;
  name: string;
  description: string;
  created_at: string;
};

export type Msg = {
  id: number;
  chat_id: number;
  role: string;
  content: string;
  created_at: string;
  edited_at: string | null;
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

export function createChat(title?: string, folderId?: number): Promise<Chat> {
  return jpost<Chat>(`${BASE}/chats`, { title, folder_id: folderId ?? null });
}

export async function updateChat(
  id: number,
  patch: { title?: string; folder_id?: number | null; pinned?: boolean }
): Promise<Chat> {
  const r = await fetch(`${BASE}/chats/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function renameChat(id: number, title: string): Promise<Chat> {
  return updateChat(id, { title });
}

export async function setPinned(id: number, pinned: boolean): Promise<Chat> {
  return updateChat(id, { pinned });
}

export async function moveChat(id: number, folderId: number | null): Promise<Chat> {
  const r = await fetch(`${BASE}/chats/${id}/move`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder_id: folderId }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function deleteChat(id: number): Promise<void> {
  await fetch(`${BASE}/chats/${id}`, { method: "DELETE" });
}

// ---------------------------- folders ----------------------------

export async function listFolders(): Promise<Folder[]> {
  const data = await jget<{ folders: Folder[] }>(`${BASE}/folders`);
  return data.folders;
}

export function createFolder(name: string, description = ""): Promise<Folder> {
  return jpost<Folder>(`${BASE}/folders`, { name, description });
}

export async function updateFolder(
  id: number,
  patch: { name?: string; description?: string }
): Promise<Folder> {
  const r = await fetch(`${BASE}/folders/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function deleteFolder(id: number): Promise<void> {
  await fetch(`${BASE}/folders/${id}`, { method: "DELETE" });
}

// ---------------------------- messages ----------------------------

export async function editMessage(chatId: number, msgId: number, content: string): Promise<Msg> {
  const r = await fetch(`${BASE}/chats/${chatId}/messages/${msgId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function deleteMessage(chatId: number, msgId: number): Promise<void> {
  await fetch(`${BASE}/chats/${chatId}/messages/${msgId}`, { method: "DELETE" });
}

export type SearchHit = {
  messages: Msg[];
  count: number;
  query: string;
};

export async function searchMessages(q: string): Promise<SearchHit> {
  return jget<SearchHit>(`${BASE}/messages/search?q=${encodeURIComponent(q)}`);
}

// ---------------------------- export ----------------------------

export async function exportChatMarkdown(chatId: number): Promise<string> {
  const r = await fetch(`${BASE}/chats/${chatId}/export?format=md`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.text();
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
  onToken: (token: string) => void,
  signal?: AbortSignal
): Promise<ChatDone | null> {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, message }),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let donePayload: ChatDone | null = null;

  try {
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
  } catch (e) {
    // aborted by the user — partial text has already been streamed to onToken
    if (signal?.aborted) return null;
    throw e;
  }
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
