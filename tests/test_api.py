from __future__ import annotations


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["memories"] == 0


def test_command_help(client):
    res = client.post("/api/command", json={"input": "/help"})
    assert res.status_code == 200
    data = res.json()
    assert data["is_command"] is True
    assert "/save" in data["text"]


def test_command_not_a_command(client):
    res = client.post("/api/command", json={"input": "just text"})
    assert res.json()["is_command"] is False


def test_save_then_list_memories(client):
    client.post("/api/command", json={"input": "/save my first memory"})
    res = client.get("/api/memories")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 1
    assert "first memory" in data["memories"][0]["content"]


def test_get_and_delete_memory(client):
    client.post("/api/command", json={"input": "/save to be deleted"})
    mem_id = client.get("/api/memories").json()["memories"][0]["id"]
    got = client.get(f"/api/memories/{mem_id}")
    assert got.status_code == 200
    deleted = client.delete(f"/api/memories/{mem_id}")
    assert deleted.status_code == 200
    assert client.get(f"/api/memories/{mem_id}").status_code == 404


def test_tags_endpoint(client):
    client.post("/api/command", json={"input": "/save tagged note"})
    client.post("/api/command", json={"input": "/tag 1 alpha"})
    res = client.get("/api/tags")
    assert res.status_code == 200
    assert res.json()["tags"]["alpha"] == 1


def test_chat_streams_tokens(client):
    res = client.post("/api/chat", json={"message": "hello brain"})
    assert res.status_code == 200
    body = res.text
    assert "data:" in body
    assert "Ответ" in body
    assert '"done": true' in body


def test_chat_autosaves_memory(client):
    client.post("/api/chat", json={"message": "remember the number 42"})
    data = client.get("/api/memories").json()
    assert data["count"] == 1
    assert "42" in data["memories"][0]["content"]


def test_chat_appends_session(client):
    client.post("/api/chat", json={"message": "first"})
    assert len(client.app.state.services.session.history()) == 2  # user + assistant


def test_chat_empty_rejected(client):
    res = client.post("/api/chat", json={"message": "   "})
    assert res.status_code == 400
