from __future__ import annotations


def test_settings_get_and_patch_updates_live_scheduler(client, services):
    initial = client.get("/api/settings")
    assert initial.status_code == 200
    assert initial.json()["timezone"] == services.settings.timezone

    updated = client.patch(
        "/api/settings",
        json={
            "timezone": "UTC",
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:30",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["timezone"] == "UTC"
    assert updated.json()["quiet_hours_start"] == "22:00"
    assert updated.json()["quiet_hours_end"] == "07:30"
    assert services.settings.timezone == "UTC"
    assert services.reminder_scheduler is None or services.reminder_scheduler.quiet_hours_start is not None


def test_settings_reject_invalid_timezone_and_incomplete_quiet_hours(client):
    invalid_timezone = client.patch("/api/settings", json={"timezone": "Mars/Phobos"})
    assert invalid_timezone.status_code == 422

    incomplete = client.patch("/api/settings", json={"quiet_hours_start": "22:00", "quiet_hours_end": None})
    assert incomplete.status_code == 422
