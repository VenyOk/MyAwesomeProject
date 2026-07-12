import { useCallback, useEffect, useState } from "react";
import {
  cancelTask,
  completeTask,
  createTask,
  listTasks,
  updateTask,
  type Task,
} from "../api";

type Filter = "open" | "done" | "cancelled" | "all";

export default function TasksView({ onClose }: { onClose: () => void }) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [filter, setFilter] = useState<Filter>("open");
  const [title, setTitle] = useState("");
  const [dueAt, setDueAt] = useState("");

  const refresh = useCallback(async () => {
    setTasks(await listTasks(filter === "all" ? undefined : filter));
  }, [filter]);

  useEffect(() => { refresh(); }, [refresh]);

  const addTask = async () => {
    if (!title.trim()) return;
    await createTask({ title: title.trim(), due_at: dueAt || null });
    setTitle("");
    setDueAt("");
    refresh();
  };

  const toggleDone = async (task: Task) => {
    await completeTask(task.id);
    refresh();
  };

  const cancel = async (task: Task) => {
    await cancelTask(task.id);
    refresh();
  };

  const changeTitle = async (task: Task) => {
    const next = prompt("Название задачи", task.title)?.trim();
    if (next && next !== task.title) {
      await updateTask(task.id, { title: next });
      refresh();
    }
  };

  return (
    <div className="tasks-view">
      <header className="memory-topbar">
        <button className="back-btn" onClick={onClose}>← Чаты</button>
        <span className="memory-title">Задачи</span>
        <span className="memory-count">{tasks.length}</span>
      </header>

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
          onKeyDown={(event) => event.key === "Enter" && addTask()}
          placeholder="Новая задача"
        />
        <input type="datetime-local" value={dueAt} onChange={(event) => setDueAt(event.target.value)} />
        <button onClick={addTask} disabled={!title.trim()}>Добавить</button>
      </div>

      <div className="memory-list">
        {tasks.length === 0 && <div className="memory-empty">Задач в этом списке пока нет.</div>}
        {tasks.map((task) => (
          <article key={task.id} className={"task-card status-" + task.status}>
            <button
              className="task-check"
              onClick={() => task.status === "open" && toggleDone(task)}
              disabled={task.status !== "open"}
              title={task.status === "open" ? "Отметить выполненной" : task.status}
            >
              {task.status === "done" ? "✓" : task.status === "cancelled" ? "—" : "○"}
            </button>
            <div className="task-main">
              <div className="task-title">{task.title}</div>
              {task.description && <div className="task-description">{task.description}</div>}
              {task.due_at && <div className="task-due">Срок: {task.due_at.replace("T", " ")}</div>}
            </div>
            {task.status === "open" && (
              <div className="task-actions">
                <button onClick={() => changeTitle(task)}>Изменить</button>
                <button className="danger" onClick={() => cancel(task)}>Отменить</button>
              </div>
            )}
          </article>
        ))}
      </div>
    </div>
  );
}
