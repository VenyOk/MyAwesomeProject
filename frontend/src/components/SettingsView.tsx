import { useEffect, useState } from "react";
import { getSettings, updateSettings, type AppSettings } from "../api";

export default function SettingsView({ onClose }: { onClose: () => void }) {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [timezone, setTimezone] = useState("");
  const [quietStart, setQuietStart] = useState("");
  const [quietEnd, setQuietEnd] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    void getSettings().then((value) => {
      setSettings(value);
      setTimezone(value.timezone);
      setQuietStart(value.quiet_hours_start ?? "");
      setQuietEnd(value.quiet_hours_end ?? "");
    }).catch((err) => setError((err as Error).message));
  }, []);

  const save = async () => {
    setBusy(true);
    setMessage("");
    setError("");
    try {
      const value = await updateSettings({
        timezone: timezone.trim(),
        quiet_hours_start: quietStart || null,
        quiet_hours_end: quietEnd || null,
      });
      setSettings(value);
      setMessage("Настройки сохранены");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="settings-view">
      <header className="memory-topbar">
        <button className="back-btn" onClick={onClose}>← Чаты</button>
        <span className="memory-title">Настройки</span>
      </header>
      <section className="settings-card">
        <div className="settings-kicker">Рабочее пространство</div>
        <h1>Время и уведомления</h1>
        <p>Часовой пояс используется для новых напоминаний. Quiet hours откладывают доставку уведомлений, но не теряют их.</p>
        <label>
          Часовой пояс IANA
          <input value={timezone} onChange={(event) => setTimezone(event.target.value)} placeholder="Europe/Moscow" />
        </label>
        <div className="settings-time-row">
          <label>
            Quiet hours с
            <input type="time" value={quietStart} onChange={(event) => setQuietStart(event.target.value)} />
          </label>
          <label>
            Quiet hours до
            <input type="time" value={quietEnd} onChange={(event) => setQuietEnd(event.target.value)} />
          </label>
        </div>
        <button className="settings-save" onClick={() => void save()} disabled={busy || settings === null}>
          {busy ? "Сохраняем…" : "Сохранить"}
        </button>
        {message && <div className="settings-success" role="status">{message}</div>}
        {error && <div className="task-error" role="alert">{error}</div>}
      </section>
    </div>
  );
}
