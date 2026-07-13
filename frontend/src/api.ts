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
  llm_provider?: string;
  model_loaded: boolean;
  memories: number;
  index_size: number;
  chats: number;
};

const BASE = "/api";

export class ApiError extends Error {
  constructor(public readonly status: number) {
    super(`HTTP ${status}`);
    this.name = "ApiError";
  }
}

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

export type EditMessageResult = {
  message: Msg;
  memory_recheck_count?: number;
};

export async function editMessage(
  chatId: number,
  msgId: number,
  content: string
): Promise<EditMessageResult> {
  const r = await fetch(`${BASE}/chats/${chatId}/messages/${msgId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!r.ok) throw new ApiError(r.status);
  const data = await r.json() as Msg & { message?: Msg; memory_recheck_count?: number };
  return {
    message: data.message ?? data,
    memory_recheck_count: data.memory_recheck_count,
  };
}

export async function deleteMessage(
  chatId: number,
  msgId: number,
  derivedMemories?: "keep" | "delete"
): Promise<void> {
  const suffix = derivedMemories ? `?derived_memories=${derivedMemories}` : "";
  const r = await fetch(`${BASE}/chats/${chatId}/messages/${msgId}${suffix}`, { method: "DELETE" });
  if (!r.ok) throw new ApiError(r.status);
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
  tool_run_id?: number | null;
  tool_events?: ChatEvent[];
  confirmation?: Confirmation | null;
};

export function runCommand(input: string, chatId: number | null): Promise<CommandResult> {
  return jpost<CommandResult>(`${BASE}/command`, { input, chat_id: chatId });
}

export function getHealth(): Promise<Health> {
  return jget<Health>(`${BASE}/health`);
}

export type AppSettings = {
  timezone: string;
  quiet_hours_start: string | null;
  quiet_hours_end: string | null;
  model: string;
  scheduler_interval_seconds: number;
};

export function getSettings(): Promise<AppSettings> {
  return jget<AppSettings>(`${BASE}/settings`);
}

export function updateSettings(patch: {
  timezone?: string;
  quiet_hours_start?: string | null;
  quiet_hours_end?: string | null;
}): Promise<AppSettings> {
  return fetch(`${BASE}/settings`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  }).then(async (response) => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json() as Promise<AppSettings>;
  });
}

// ---------------------------- memories ----------------------------

export type MemoryStatus = "candidate" | "active" | "superseded" | "deleted";
export type MemoryKind = "fact" | "preference" | "decision" | "idea" | "person" | "project";

export type Memory = {
  id: number;
  content: string;
  summary: string | null;
  tags: string[];
  source: string;
  created_at: string;
  updated_at: string;
  kind: string;
  importance: number;
  confidence: number;
  sensitivity: string;
  source_type: string;
  source_message_id: number | null;
  status: string;
  deleted_at: string | null;
};

export type Task = {
  id: number;
  title: string;
  description: string;
  status: "open" | "done" | "cancelled";
  priority: number;
  due_at: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

export async function listTasks(status?: string): Promise<Task[]> {
  const suffix = status ? `?status=${encodeURIComponent(status)}` : "?status=";
  const data = await jget<{ tasks: Task[] }>(`${BASE}/tasks${suffix}`);
  return data.tasks;
}

export function createTask(task: {
  title: string; description?: string; due_at?: string | null; priority?: number;
}): Promise<Task> {
  return jpost<Task>(`${BASE}/tasks`, task);
}

export function createTaskWithReminder(task: {
  title: string; description?: string; scheduled_at: string; timezone?: string; priority?: number;
}): Promise<{ task: Task; reminder: Reminder }> {
  return jpost<{ task: Task; reminder: Reminder }>(`${BASE}/tasks/with-reminder`, task);
}

export async function updateTask(id: number, patch: Partial<Pick<Task, "title" | "description" | "due_at" | "priority">>): Promise<Task> {
  const response = await fetch(`${BASE}/tasks/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

export function completeTask(id: number): Promise<Task> {
  return jpost<Task>(`${BASE}/tasks/${id}/complete`, {});
}

export function cancelTask(id: number): Promise<Task> {
  return jpost<Task>(`${BASE}/tasks/${id}/cancel`, {});
}

export type Reminder = {
  id: number;
  task_id: number | null;
  title: string;
  scheduled_at: string;
  timezone: string;
  recurrence_rule: string | null;
  status: "scheduled" | "fired" | "cancelled";
  channel: string;
  created_at: string;
  fired_at: string | null;
};

export type Notification = {
  id: number;
  channel: string;
  event_type: string;
  payload: {
    title?: string;
    reminder?: Partial<Reminder>;
    [key: string]: unknown;
  };
  available_at: string;
  status: "pending" | "sent";
};

export async function listReminders(status = "scheduled"): Promise<Reminder[]> {
  const data = await jget<{ reminders: Reminder[] }>(
    `${BASE}/reminders?status=${encodeURIComponent(status)}`
  );
  return data.reminders;
}

export function createReminder(reminder: {
  title: string;
  scheduled_at: string;
  task_id?: number;
}): Promise<Reminder> {
  return jpost<Reminder>(`${BASE}/reminders`, reminder);
}

export function cancelReminder(id: number): Promise<Reminder> {
  return jpost<Reminder>(`${BASE}/reminders/${id}/cancel`, {});
}

export async function listNotifications(status = "pending"): Promise<Notification[]> {
  const data = await jget<{ notifications: Notification[] }>(
    `${BASE}/notifications?status=${encodeURIComponent(status)}`
  );
  return data.notifications;
}

export function acknowledgeNotification(id: number): Promise<void> {
  return jpost<void>(`${BASE}/notifications/${id}/ack`, {});
}

export async function listMemories(params?: {
  q?: string; status?: string; kind?: string; limit?: number;
}): Promise<{ memories: Memory[]; count: number }> {
  const qs = new URLSearchParams();
  if (params?.q) qs.set("q", params.q);
  if (params?.status) qs.set("status", params.status);
  if (params?.kind) qs.set("kind", params.kind);
  if (params?.limit) qs.set("limit", String(params.limit));
  const tail = qs.toString();
  return jget<{ memories: Memory[]; count: number }>(`${BASE}/memories${tail ? "?" + tail : ""}`);
}

export async function updateMemory(id: number, patch: { content?: string; kind?: string; summary?: string }): Promise<Memory> {
  const r = await fetch(`${BASE}/memories/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function activateMemory(id: number): Promise<Memory> {
  const r = await fetch(`${BASE}/memories/${id}/activate`, { method: "POST" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function deleteMemory(id: number): Promise<void> {
  await fetch(`${BASE}/memories/${id}`, { method: "DELETE" });
}

export async function restoreMemory(id: number): Promise<Memory> {
  const r = await fetch(`${BASE}/memories/${id}/restore`, { method: "POST" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export type ChatDone = {
  user_message: Msg;
  assistant_message: Msg;
  title: string;
};

export type Confirmation = {
  id: number;
  chat_id: number | null;
  tool_name: string;
  arguments: Record<string, unknown>;
  risk: string;
  status: "pending" | "approved" | "rejected";
  created_at: string;
  resolved_at: string | null;
};

export type ToolRun = {
  id: number;
  chat_id: number | null;
  message_id: number | null;
  tool_name: string;
  arguments: Record<string, unknown>;
  result: unknown | null;
  policy_decision: string | null;
  risk: string | null;
  status: string;
  created_at: string;
  finished_at: string | null;
};

export type ToolRunEvent = {
  type: "tool_started" | "tool_finished" | "tool_error";
  tool_run_id?: number | null;
  name?: string;
  tool_name?: string;
  arguments?: Record<string, unknown>;
  result?: unknown;
  error?: unknown;
  risk?: string;
  policy_decision?: string;
  status?: string;
  needs_confirmation?: boolean;
};

export type ChatEvent =
  | { type: "confirmation_required"; confirmation: Confirmation; tool_run_id?: number | null }
  | ToolRunEvent;

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function asRecordOrJson(value: unknown): Record<string, unknown> {
  const record = asRecord(value);
  if (record) return record;
  if (typeof value !== "string") return {};
  try {
    return asRecord(JSON.parse(value)) ?? {};
  } catch {
    return {};
  }
}

function asNullableNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asNullableText(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function parseToolRun(value: unknown): ToolRun | null {
  const row = asRecord(value);
  const id = asNullableNumber(row?.id);
  const toolName = asNullableText(row?.tool_name ?? row?.name);
  if (id === null || !toolName) return null;
  return {
    id,
    chat_id: asNullableNumber(row?.chat_id),
    message_id: asNullableNumber(row?.message_id),
    tool_name: toolName,
    arguments: asRecordOrJson(row?.arguments ?? row?.arguments_json),
    result: row?.result ?? row?.result_json ?? null,
    policy_decision: asNullableText(row?.policy_decision),
    risk: asNullableText(row?.risk),
    status: asNullableText(row?.status) ?? "unknown",
    created_at: asNullableText(row?.created_at) ?? "",
    finished_at: asNullableText(row?.finished_at),
  };
}

export async function listToolRuns(chatId: number): Promise<ToolRun[]> {
  const payload = await jget<unknown>(`${BASE}/tool-runs?chat_id=${chatId}`);
  const envelope = asRecord(payload);
  const rows = Array.isArray(payload)
    ? payload
    : Array.isArray(envelope?.tool_runs)
      ? envelope.tool_runs
      : Array.isArray(envelope?.runs)
        ? envelope.runs
        : [];
  return rows.map(parseToolRun).filter((run): run is ToolRun => run !== null);
}

export async function listConfirmations(chatId: number): Promise<Confirmation[]> {
  const data = await jget<{ confirmations: Confirmation[] }>(
    `${BASE}/confirmations?chat_id=${chatId}`
  );
  return data.confirmations;
}

export async function resolveConfirmation(
  id: number,
  decision: "approve" | "reject"
): Promise<{ confirmation: Confirmation; result: Record<string, unknown> }> {
  return jpost(`${BASE}/confirmations/${id}/${decision}`, {});
}

export async function streamChat(
  chatId: number,
  message: string,
  onToken: (token: string) => void,
  signal?: AbortSignal,
  onEvent?: (event: ChatEvent) => void
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
        if (payload.type) onEvent?.(payload as ChatEvent);
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
