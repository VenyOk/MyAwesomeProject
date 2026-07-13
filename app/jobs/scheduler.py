"""Reliable local scheduling for one-off web reminders."""
from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.jobs.outbox import OutboxStore
from app.tasks.reminders import Reminder, ReminderStore, normalize_scheduled_at


class ReminderScheduler:
    """Claims due reminders and puts one notification per reminder in outbox."""

    def __init__(
        self,
        reminders: ReminderStore,
        outbox: OutboxStore,
        *,
        quiet_hours_start: str | None = None,
        quiet_hours_end: str | None = None,
        workspace_id: int = 1,
    ) -> None:
        self.reminders = reminders
        self.outbox = outbox
        self.quiet_hours_start = _parse_clock(quiet_hours_start)
        self.quiet_hours_end = _parse_clock(quiet_hours_end)
        self.workspace_id = workspace_id

    def set_quiet_hours(self, start: str | None, end: str | None) -> None:
        """Apply validated quiet-hour values to the live scheduler."""
        self.quiet_hours_start = _parse_clock(start)
        self.quiet_hours_end = _parse_clock(end)

    def tick(self, now: str | None = None) -> int:
        """Process all currently due reminders exactly once.

        ``claim_due`` commits the state transition before an outbox record is
        created, so a repeated tick cannot queue the same reminder twice.
        """
        now_utc = normalize_scheduled_at(now, "UTC") if now else _now_utc()
        due = self.reminders.claim_due(now_utc, workspace_id=self.workspace_id)
        # Recover only fired reminders that lack an outbox row. This handles a
        # crash between the claim and outbox commit without rescanning every
        # already delivered reminder on each minute-long tick.
        pending_recovery = self.reminders.list_fired_without_outbox(
            workspace_id=self.workspace_id
        )
        for reminder in pending_recovery:
            delivery_time = reminder.fired_at or now_utc
            self.outbox.create(
                "reminder.due",
                {"reminder": reminder.to_dict()},
                available_at=self._available_at(reminder, delivery_time),
                channel=reminder.channel,
                workspace_id=reminder.workspace_id,
                dedupe_key=f"reminder:{reminder.id}",
            )
        return len(due)

    def _available_at(self, reminder: Reminder, now_utc: str) -> str:
        """Delay UI delivery until quiet hours end, without losing the event."""
        if self.quiet_hours_start is None or self.quiet_hours_end is None:
            return now_utc
        if self.quiet_hours_start == self.quiet_hours_end:
            return now_utc
        try:
            zone = ZoneInfo(reminder.timezone)
        except ZoneInfoNotFoundError:
            return now_utc

        now = datetime.fromisoformat(now_utc.replace("Z", "+00:00")).astimezone(zone)
        current = now.timetz().replace(tzinfo=None)
        start, end = self.quiet_hours_start, self.quiet_hours_end
        crosses_midnight = start > end
        in_quiet = (
            start <= current < end
            if not crosses_midnight
            else current >= start or current < end
        )
        if not in_quiet:
            return now_utc

        end_date = now.date()
        if crosses_midnight and current >= start:
            end_date += timedelta(days=1)
        deliver_at = datetime.combine(end_date, end, tzinfo=zone)
        return deliver_at.astimezone(timezone.utc).isoformat(timespec="seconds")


class ReminderSchedulerLoop:
    """Small asyncio loop started with FastAPI; no separate worker is needed."""

    def __init__(self, scheduler: ReminderScheduler, interval_seconds: float = 60) -> None:
        self.scheduler = scheduler
        self.interval_seconds = interval_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        try:
            self.scheduler.tick()  # also recovers reminders missed while offline
        except Exception as exc:  # noqa: BLE001 - retry on the next interval
            print(f"[second-brain] initial reminder tick failed: {exc}")
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
                return
            except TimeoutError:
                try:
                    self.scheduler.tick()
                except Exception as exc:  # noqa: BLE001 - scheduler must keep retrying
                    print(f"[second-brain] reminder tick failed: {exc}")


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_clock(value: str | None) -> time | None:
    if not value:
        return None
    try:
        return time.fromisoformat(value)
    except ValueError:
        return None
