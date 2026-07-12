import { useCallback, useEffect, useMemo, useState } from "react";
import {
  acknowledgeNotification,
  cancelReminder,
  cancelTask,
  completeTask,
  createReminder,
  createTask,
  fmtMoscow,
  listNotifications,
  listReminders,
  listTasks,
  updateTask,
  type Notification,
  type Reminder,
  type Task,
} from "../api";

type Filter = "open" | "done" | "cancelled" | "all";

function notificationTitle(notification: Notification): string {
  if (typeof notification.payload.title === "string") return notification.payload.title;
  if (typeof notification.payload.reminder?.title === "string") return notification.payload.reminder.title;
  return "Напоминание";
}

function formatDate(value: string): string {
  return fmtMoscow(value) || value.replace("T", " ");
}

export default function TasksView({ onClose }: { onClose: () => void }) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [filter, setFilter] = useState<Filter>("open");
  const [title, setTitle] = useState("");
  const [dueAt, setDueAt] = useState("");
  const [error, setError] = useState("");
  const [adding, setAdding] = useState(false);
  const [pendingReminderId, setPendingReminderId] = useState<number | null>(null);
  const [pendingNotificationId, setPendingNotificationId] = useState<number | null>(null);

  const refresh = useCallback(async () => {
    const [tasksResult, remindersResult, notificationsResult] = await Promise.allSettled([
      listTasks(filter === "all" ? undefined : filter),
      listReminders(),
      listNotifications(),
    ]);

    if (tasksResult.status === "fulfilled") setTasks(tasksResult.value);
    if (remindersResult.status === "fulfilled") setReminders(remindersResult.value);
    if (notificationsResult.status === "fulfilled") setNotifications(notificationsResult.value);

    if (
      tasksResult.status === "rejected" ||
      remindersResult.status === "rejected" ||
      notificationsResult.status === "rejected"
    ) {
      setError("Не удалось обновить часть данных. Попробуйте ещё раз.");
    }
  }, [filter]);

  useEffect(() => {
    void refresh();
    const interval = window.setInterval(() => { void refresh(); }, 30_000);
    return () => window.clearInterval(interval);
  }, [refresh]);

  const remindersByTask = useMemo(() => {
    const grouped = new Map<number, Reminder[]>();
    for (const reminder of reminders) {
      if (reminder.task_id == null) continue;
      const current = grouped.get(reminder.task_id) ?? [];
      current.push(reminder);
      grouped.set(reminder.task_id, current);
    }
    return grouped;
  }, [reminders]);

  const addTask = async () => {
    const taskTitle = title.trim();
    if (!taskTitle || adding) return;

    setAdding(true);
    setError("");
    let task: Task | null = null;
    try {
      task = await createTask({ title: taskTitle, due_at: dueAt || null });
      setTitle("");
      setDueAt("");
      if (dueAt) {
        await createReminder({ title: task.title, scheduled_at: dueAt, task_id: task.id });
      }
    } catch (err) {
      setError(task ? "Задача создана, но напоминание не удалось добавить." : (err as Error).message);
    } finally {
      setAdding(false);
      await refresh();
    }
  };

  const toggleDone = async (task: Task) => {
    setError("");
    try {
      await completeTask(task.id);
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const cancel = async (task: Task) => {
    setError("");
    try {
      await cancelTask(task.id);
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const cancelLinkedReminder = async (reminder: Reminder) => {
    setPendingReminderId(reminder.id);
    setError("");
    try {
      await cancelReminder(reminder.id);
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPendingReminderId(null);
    }
  };

  const acknowledge = async (notification: Notification) => {
    setPendingNotificationId(notification.id);
    setError("");
    try {
      await acknowledgeNotification(notification.id);
      setNotifications((current) => current.filter((item) => item.id !== notification.id));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPendingNotificationId(null);
    }
  };

  const changeTitle = async (task: Task) => {
    const next = prompt("Название задачи", task.title)?.trim();
    if (next && next !== task.title) {
      try {
        await updateTask(task.id, { title: next });
        await refresh();
      } catch (err) {
        setError((err as Error).message);
      }
    }
  };

  return (
    <div className="tasks-view">
      <header className="memory-topbar">
        <button className="back-btn" onClick={onClose}>← Чаты</button>
        <span className="memory-title">Задачи</span>
        <span className="memory-count">{tasks.length}</span>
      </header>

      {notifications.length > 0 && (
        <section className="task-notifications" aria-live="polite" aria-label="Новые напоминания">
          <div className="task-section-title">Напоминания сейчас</div>
          {notifications.map((notification) => (
            <div className="task-notification" key={notification.id}>
              <div>
                <strong>{notificationTitle(notification)}</strong>
                <span>{formatDate(notification.available_at)}</span>
              </div>
              <button
                onClick={() => acknowledge(notification)}
                disabled={pendingNotificationId === notification.id}
              >
                {pendingNotificationId === notification.id ? "Закрываем…" : "Закрыть"}
              </button>
            </div>
          ))}
        </section>
      )}

      <div className="memory-filters">
        {(["open", "done", "cancelled", "all"] as Filter[]).map((value) => (
          <button
            key={value}
            className={"mem-filter" + (filter === value ? " active" : "")}
            onClick={() => setFilter(value)}
          >
            {value === "open" ? "Открытые" : value === "done" ? "Готово" : value === "cancelled" ? "Отменённые" : "Все"}
          </button>
        ))}
      </div>

      <div className="task-create">
        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && void addTask()}
          placeholder="Новая задача"
          aria-label="Название новой задачи"
        />
        <input
          type="datetime-local"
          value={dueAt}
          onChange={(event) => setDueAt(event.target.value)}
          aria-label="Когда напомнить"
          title="При выборе времени будет создано напоминание"
        />
        <button onClick={() => void addTask()} disabled={!title.trim() || adding}>
          {adding ? "Добавляем…" : "Добавить"}
        </button>
      </div>

      {error && <div className="task-error" role="alert">{error}</div>}

      {reminders.length > 0 && (
        <section className="upcoming-reminders" aria-label="Ближайшие напоминания">
          <div className="task-section-title">Ближайшие напоминания</div>
          {reminders.map((reminder) => (
            <div className="upcoming-reminder" key={reminder.id}>
              <div>
                <strong>{reminder.title}</strong>
                <span>{formatDate(reminder.scheduled_at)}</span>
              </div>
              <button
                onClick={() => void cancelLinkedReminder(reminder)}
                disabled={pendingReminderId === reminder.id}
              >
                {pendingReminderId === reminder.id ? "Отмена…" : "Отменить"}
              </button>
            </div>
          ))}
        </section>
      )}

      <div className="memory-list">
        {tasks.length === 0 && <div className="memory-empty">Задач в этом списке пока нет.</div>}
        {tasks.map((task) => (
          <article key={task.id} className={"task-card status-" + task.status}>
            <button
              className="task-check"
              onClick={() => task.status === "open" && void toggleDone(task)}
              disabled={task.status !== "open"}
              title={task.status === "open" ? "Отметить выполненной" : task.status}
            >
              {task.status === "done" ? "✓" : task.status === "cancelled" ? "—" : "◯"}
            </button>
            <div className="task-main">
              <div className="task-title">{task.title}</div>
              {task.description && <div className="task-description">{task.description}</div>}
              {task.due_at && <div className="task-due">Срок: {formatDate(task.due_at)}</div>}
              {(remindersByTask.get(task.id) ?? []).map((reminder) => (
                <div className="task-reminder" key={reminder.id}>
                  <span>Напомнить: {formatDate(reminder.scheduled_at)}</span>
                  <button
                    onClick={() => void cancelLinkedReminder(reminder)}
                    disabled={pendingReminderId === reminder.id}
                  >
                    Отменить напоминание
                  </button>
                </div>
              ))}
            </div>
            {task.status === "open" && (
              <div className="task-actions">
                <button onClick={() => void changeTitle(task)}>Изменить</button>
                <button className="danger" onClick={() => void cancel(task)}>Отменить</button>
              </div>
            )}
          </article>
        ))}
      </div>
    </div>
  );
}
