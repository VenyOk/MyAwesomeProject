import { useCallback, useEffect, useState } from "react";
import {
  acknowledgeNotification,
  fmtMoscow,
  listNotifications,
  listReminders,
  listTasks,
  type Notification,
  type Reminder,
  type Task,
} from "../api";

function formatDate(value: string): string {
  return fmtMoscow(value) || value.replace("T", " ");
}

function notificationTitle(notification: Notification): string {
  if (typeof notification.payload.title === "string") return notification.payload.title;
  if (typeof notification.payload.reminder?.title === "string") return notification.payload.reminder.title;
  return "Напоминание";
}

export default function TodayView({ onClose }: { onClose: () => void }) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [error, setError] = useState("");
  const [pendingNotificationId, setPendingNotificationId] = useState<number | null>(null);

  const refresh = useCallback(async () => {
    const [tasksResult, remindersResult, notificationsResult] = await Promise.allSettled([
      listTasks("open"),
      listReminders(),
      listNotifications(),
    ]);
    if (tasksResult.status === "fulfilled") setTasks(tasksResult.value);
    if (remindersResult.status === "fulfilled") setReminders(remindersResult.value);
    if (notificationsResult.status === "fulfilled") setNotifications(notificationsResult.value);
    setError(
      tasksResult.status === "rejected" || remindersResult.status === "rejected" || notificationsResult.status === "rejected"
        ? "Не удалось обновить часть данных."
        : "",
    );
  }, []);

  useEffect(() => {
    void refresh();
    const interval = window.setInterval(() => { void refresh(); }, 30_000);
    return () => window.clearInterval(interval);
  }, [refresh]);

  const acknowledge = async (notification: Notification) => {
    setPendingNotificationId(notification.id);
    try {
      await acknowledgeNotification(notification.id);
      setNotifications((current) => current.filter((item) => item.id !== notification.id));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPendingNotificationId(null);
    }
  };

  return (
    <div className="today-view">
      <header className="memory-topbar">
        <button className="back-btn" onClick={onClose}>← Чаты</button>
        <span className="memory-title">Сегодня</span>
        <span className="memory-count">{tasks.length} задач</span>
      </header>

      <section className="today-hero">
        <div className="today-kicker">Локальный обзор</div>
        <h1>Что требует внимания</h1>
        <p>Уведомления, ближайшие напоминания и открытые обязательства в одном месте.</p>
      </section>

      {error && <div className="task-error" role="alert">{error}</div>}

      <section className="today-section" aria-label="Новые уведомления">
        <div className="task-section-title">Напоминания сейчас</div>
        {notifications.length === 0 ? (
          <div className="today-empty">Новых уведомлений нет.</div>
        ) : notifications.map((notification) => (
          <div className="today-row today-notification" key={notification.id}>
            <div>
              <strong>{notificationTitle(notification)}</strong>
              <span>{formatDate(notification.available_at)}</span>
            </div>
            <button
              onClick={() => void acknowledge(notification)}
              disabled={pendingNotificationId === notification.id}
            >
              {pendingNotificationId === notification.id ? "Закрываем…" : "Закрыть"}
            </button>
          </div>
        ))}
      </section>

      <section className="today-section" aria-label="Ближайшие напоминания">
        <div className="task-section-title">Ближайшие напоминания</div>
        {reminders.length === 0 ? (
          <div className="today-empty">Запланированных напоминаний нет.</div>
        ) : reminders.slice(0, 5).map((reminder) => (
          <div className="today-row" key={reminder.id}>
            <div>
              <strong>{reminder.title}</strong>
              <span>{formatDate(reminder.scheduled_at)} · {reminder.timezone}</span>
            </div>
          </div>
        ))}
      </section>

      <section className="today-section" aria-label="Открытые задачи">
        <div className="task-section-title">Открытые задачи</div>
        {tasks.length === 0 ? (
          <div className="today-empty">Открытых задач нет.</div>
        ) : tasks.slice(0, 8).map((task) => (
          <div className="today-row" key={task.id}>
            <div>
              <strong>{task.title}</strong>
              {task.due_at && <span>Срок: {formatDate(task.due_at)}</span>}
            </div>
          </div>
        ))}
      </section>
    </div>
  );
}
