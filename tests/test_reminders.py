"""Stage 5 checks for reliable web reminders and the delivery outbox."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.jobs.outbox import OutboxStore
from app.jobs.scheduler import ReminderScheduler, ReminderSchedulerLoop
from app.tasks.reminders import ReminderStore


def test_scheduler_claims_due_reminder_once_and_creates_one_notification(tmp_path):
    """A repeated scheduler tick must not duplicate a due notification."""
    db_path = tmp_path / "brain.db"
    reminders = ReminderStore(db_path)
    outbox = OutboxStore(db_path)
    scheduler = ReminderScheduler(reminders, outbox)
    try:
        reminder = reminders.create(
            "Позвонить врачу",
            "2026-07-12T09:00:00+03:00",
        )

        assert scheduler.tick(now="2026-07-12T06:00:00+00:00") == 1
        pending = outbox.list_pending()
        assert len(pending) == 1
        assert pending[0].event_type == "reminder.due"
        assert pending[0].payload["reminder"]["id"] == reminder.id
        assert pending[0].payload["reminder"]["title"] == "Позвонить врачу"

        # ``claim_due`` transitions the row before delivery, so an immediate
        # second tick cannot queue the same event again.
        assert scheduler.tick(now="2026-07-12T06:01:00+00:00") == 0
        assert len(outbox.list_pending()) == 1
        assert reminders.get(reminder.id).status == "fired"
    finally:
        outbox.close()
        reminders.close()


def test_scheduler_delivers_a_missed_reminder_after_restart(tmp_path):
    """A reminder that became due while the app was off is still surfaced."""
    db_path = tmp_path / "brain.db"
    reminders = ReminderStore(db_path)
    outbox = OutboxStore(db_path)
    scheduler = ReminderScheduler(reminders, outbox)
    try:
        reminder = reminders.create(
            "Подать документы",
            "2026-07-11T15:00:00+00:00",
        )

        delivered = scheduler.tick(now="2026-07-12T09:00:00+00:00")

        assert delivered == 1
        assert reminders.get(reminder.id).status == "fired"
        pending = outbox.list_pending()
        assert len(pending) == 1
        assert pending[0].payload["reminder"]["id"] == reminder.id
    finally:
        outbox.close()
        reminders.close()


def test_scheduler_recovers_a_fired_reminder_missing_its_outbox_event(tmp_path):
    """A crash after claim and before outbox commit is recovered without a duplicate."""
    db_path = tmp_path / "brain.db"
    reminders = ReminderStore(db_path)
    outbox = OutboxStore(db_path)
    scheduler = ReminderScheduler(reminders, outbox)
    try:
        reminder = reminders.create("Проверить резервное восстановление", "2026-07-12T09:00:00+00:00")
        # Simulate an interrupted previous tick: status changed, no outbox row.
        claimed = reminders.claim_due("2026-07-12T10:00:00+00:00")
        assert [item.id for item in claimed] == [reminder.id]
        assert outbox.list_pending() == []

        assert scheduler.tick(now="2026-07-12T10:01:00+00:00") == 0
        assert len(outbox.list_pending()) == 1
        assert scheduler.tick(now="2026-07-12T10:02:00+00:00") == 0
        assert len(outbox.list_pending()) == 1
    finally:
        outbox.close()
        reminders.close()


def test_scheduler_defers_web_delivery_during_quiet_hours(tmp_path):
    db_path = tmp_path / "brain.db"
    reminders = ReminderStore(db_path)
    outbox = OutboxStore(db_path)
    scheduler = ReminderScheduler(
        reminders,
        outbox,
        quiet_hours_start="22:00",
        quiet_hours_end="08:00",
    )
    try:
        reminders.create("Выключить свет", "2026-07-12T22:30:00+03:00")
        scheduler.tick(now="2026-07-12T20:00:00+00:00")  # 23:00 in Moscow

        assert len(outbox.list_pending()) == 1
        assert outbox.list_available(now_utc="2026-07-12T20:00:00+00:00") == []
        available = outbox.list_available(now_utc="2026-07-13T05:00:00+00:00")
        assert len(available) == 1  # 08:00 in Moscow
    finally:
        outbox.close()
        reminders.close()


def test_scheduler_loop_retries_after_a_tick_error():
    class FlakyScheduler:
        def __init__(self):
            self.calls = 0

        def tick(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary sqlite error")

    async def run_loop() -> int:
        scheduler = FlakyScheduler()
        loop = ReminderSchedulerLoop(scheduler, interval_seconds=0.01)
        await loop.start()
        await asyncio.sleep(0.03)
        await loop.stop()
        return scheduler.calls

    assert asyncio.run(run_loop()) >= 2


@pytest.fixture
def reminder_client(services, tmp_path):
    """Attach the Stage 5 stores to the regular lightweight API fixture."""
    from fastapi.testclient import TestClient

    db_path = tmp_path / "reminders.db"
    reminders = ReminderStore(db_path)
    outbox = OutboxStore(db_path)
    services.reminder_store = reminders
    services.outbox_store = outbox
    services.reminder_scheduler = ReminderScheduler(reminders, outbox)

    from app.main import create_app

    try:
        with TestClient(create_app(services)) as client:
            yield client
    finally:
        outbox.close()
        reminders.close()


def test_reminder_crud_and_notification_acknowledgement(reminder_client):
    scheduled_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    created = reminder_client.post(
        "/api/reminders",
        json={
            "title": "Проверить черновик",
            "scheduled_at": scheduled_at,
            "timezone": "Europe/Moscow",
        },
    )
    assert created.status_code == 200
    reminder = created.json()
    assert reminder["title"] == "Проверить черновик"
    assert reminder["status"] == "scheduled"

    updated = reminder_client.patch(
        f"/api/reminders/{reminder['id']}",
        json={"title": "Проверить финальный черновик"},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Проверить финальный черновик"
    reminder = updated.json()

    listed = reminder_client.get("/api/reminders")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["reminders"]] == [reminder["id"]]

    cancelled = reminder_client.post(f"/api/reminders/{reminder['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    services = reminder_client.app.state.services
    queued = services.outbox_store.create(
        "reminder.due",
        {"reminder": {"id": reminder["id"], "title": reminder["title"]}},
    )
    notifications = reminder_client.get("/api/notifications")
    assert notifications.status_code == 200
    assert notifications.json()["notifications"][0]["id"] == queued.id

    acknowledged = reminder_client.post(f"/api/notifications/{queued.id}/ack")
    assert acknowledged.status_code == 200
    assert acknowledged.json()["status"] == "sent"
    assert reminder_client.get("/api/notifications").json()["notifications"] == []


def test_confirmed_agent_tool_creates_linked_task_and_reminder(reminder_client):
    from app.agent.policies import decide

    services = reminder_client.app.state.services
    scheduled_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    arguments = {
        "title": "Отправить отчёт",
        "description": "Финальная проверка",
        "scheduled_at": scheduled_at,
        "timezone": "Europe/Moscow",
    }
    assert decide("task.create_with_reminder").needs_confirmation is True

    tool_run_id = services.agent_store.start_tool_run(
        "task.create_with_reminder",
        arguments,
        chat_id=None,
        policy_decision="confirm",
    )
    confirmation = services.agent_store.create_confirmation(
        tool_run_id=tool_run_id,
        tool_name="task.create_with_reminder",
        arguments=arguments,
        risk="confirm",
        chat_id=None,
    )
    approved = reminder_client.post(f"/api/confirmations/{confirmation.id}/approve")

    assert approved.status_code == 200
    result = approved.json()["result"]
    assert result["task"]["title"] == "Отправить отчёт"
    assert result["reminder"]["task_id"] == result["task"]["id"]
