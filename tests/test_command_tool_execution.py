from __future__ import annotations


def _chat_id(client) -> int:
    return client.post("/api/chats", json={"title": "Commands"}).json()["id"]


def test_slash_save_uses_shared_tool_run_and_keeps_manual_provenance(client):
    chat_id = _chat_id(client)

    response = client.post(
        "/api/command",
        json={"input": "/save manual project note", "chat_id": chat_id},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["text"] == "Saved as memory #1."
    assert data["confirmation"] is None
    assert data["tool_run_id"] is not None
    assert [event["type"] for event in data["tool_events"]] == [
        "tool_started",
        "tool_finished",
    ]

    memory = client.get("/api/memories/1").json()
    assert memory["source"] == "manual"
    assert memory["source_type"] == "manual"

    tool_run = client.get(f"/api/tool-runs?chat_id={chat_id}").json()["tool_runs"][0]
    assert tool_run["tool_name"] == "memory.create"
    assert tool_run["arguments"]["source"] == "manual"
    assert tool_run["policy_decision"] == "low_write"
    assert tool_run["status"] == "succeeded"


def test_slash_search_uses_shared_tool_run_and_keeps_its_legacy_output(client):
    chat_id = _chat_id(client)
    client.post(
        "/api/command",
        json={"input": "/save semantic search phrase", "chat_id": chat_id},
    )

    response = client.post(
        "/api/command",
        json={"input": "/search semantic search", "chat_id": chat_id},
    )

    data = response.json()
    assert data["error"] is False
    assert "Search results:\n#1" in data["text"]
    assert "semantic search phrase" in data["text"]
    assert [event["type"] for event in data["tool_events"]] == [
        "tool_started",
        "tool_finished",
    ]
    tool_run = client.get(f"/api/tool-runs?chat_id={chat_id}").json()["tool_runs"][0]
    assert tool_run["tool_name"] == "memory.search"
    assert tool_run["status"] == "succeeded"


def test_slash_forget_requires_the_same_durable_confirmation_then_deletes(client):
    chat_id = _chat_id(client)
    client.post(
        "/api/command",
        json={"input": "/save remove after approval", "chat_id": chat_id},
    )

    response = client.post(
        "/api/command",
        json={"input": "/forget 1", "chat_id": chat_id},
    )

    data = response.json()
    assert data["error"] is False
    assert data["confirmation"]["tool_name"] == "memory.delete"
    assert data["confirmation"]["tool_run_id"] == data["tool_run_id"]
    assert [event["type"] for event in data["tool_events"]] == [
        "tool_started",
        "confirmation_required",
    ]
    assert client.get("/api/memories/1").status_code == 200

    pending_run = client.get(f"/api/tool-runs?chat_id={chat_id}").json()["tool_runs"][0]
    assert pending_run["tool_name"] == "memory.delete"
    assert pending_run["status"] == "pending_confirmation"

    approved = client.post(f"/api/confirmations/{data['confirmation']['id']}/approve")
    assert approved.status_code == 200
    assert approved.json()["result"] == {"id": 1, "status": "deleted"}
    assert client.get("/api/memories/1").status_code == 404


def test_invalid_slash_forget_keeps_legacy_usage_error_without_a_tool_run(client):
    chat_id = _chat_id(client)
    before = client.get(f"/api/tool-runs?chat_id={chat_id}").json()["tool_runs"]

    response = client.post(
        "/api/command",
        json={"input": "/forget nope", "chat_id": chat_id},
    )

    assert response.json() == {
        "is_command": True,
        "text": "Usage: /forget <id>",
        "error": True,
        "tool_run_id": None,
        "tool_events": [],
        "confirmation": None,
    }
    assert client.get(f"/api/tool-runs?chat_id={chat_id}").json()["tool_runs"] == before
