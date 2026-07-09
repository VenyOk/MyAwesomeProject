from __future__ import annotations


def _make_chat(client, title: str | None = None) -> int:
    body = {"title": title} if title else {}
    return client.post("/api/chats", json=body).json()["id"]


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["memories"] == 0
    assert data["chats"] == 0


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


# ---------------------------- chats ----------------------------


def test_create_list_delete_chat(client):
    cid = _make_chat(client, "Work")
    assert cid >= 1
    chats = client.get("/api/chats").json()["chats"]
    assert any(c["id"] == cid and c["title"] == "Work" for c in chats)
    assert client.delete(f"/api/chats/{cid}").status_code == 200
    assert client.get("/api/chats").json()["chats"] == []


def test_rename_chat(client):
    cid = _make_chat(client)
    res = client.patch(f"/api/chats/{cid}", json={"title": "Renamed"})
    assert res.status_code == 200
    assert res.json()["title"] == "Renamed"


def test_chat_404(client):
    assert client.get("/api/chats/9999").status_code == 404


def test_chat_messages_endpoint(client):
    cid = _make_chat(client)
    client.post("/api/chat", json={"chat_id": cid, "message": "hello"})
    msgs = client.get(f"/api/chats/{cid}/messages").json()["messages"]
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"


# ---------------------------- chat (LLM) ----------------------------


def test_chat_streams_tokens(client):
    cid = _make_chat(client)
    res = client.post("/api/chat", json={"chat_id": cid, "message": "hello brain"})
    assert res.status_code == 200
    body = res.text
    assert "data:" in body
    assert "Ответ" in body
    assert '"done": true' in body


def test_chat_autosaves_memory(client):
    cid = _make_chat(client)
    client.post("/api/chat", json={"chat_id": cid, "message": "remember the number 42"})
    data = client.get("/api/memories").json()
    assert data["count"] == 1
    assert "42" in data["memories"][0]["content"]


def test_chat_persists_messages(client):
    cid = _make_chat(client)
    client.post("/api/chat", json={"chat_id": cid, "message": "first"})
    msgs = client.app.state.services.chat_store.list_messages(cid)
    assert len(msgs) == 2  # user + assistant
    assert msgs[0].role == "user" and msgs[0].content == "first"


def test_chat_auto_title(client):
    cid = _make_chat(client)
    client.post("/api/chat", json={"chat_id": cid, "message": "Как настроить VPN на роутере?"})
    title = client.app.state.services.chat_store.get(cid).title
    assert title.startswith("Как настроить VPN")


def test_chats_independent(client):
    a = _make_chat(client)
    b = _make_chat(client)
    client.post("/api/chat", json={"chat_id": a, "message": "in A"})
    client.post("/api/chat", json={"chat_id": b, "message": "in B"})
    na = len(client.app.state.services.chat_store.list_messages(a))
    nb = len(client.app.state.services.chat_store.list_messages(b))
    assert na == 2 and nb == 2  # each chat keeps its own turns


def test_chat_empty_rejected(client):
    cid = _make_chat(client)
    res = client.post("/api/chat", json={"chat_id": cid, "message": "   "})
    assert res.status_code == 400


def test_clear_command_clears_chat(client):
    cid = _make_chat(client)
    client.post("/api/chat", json={"chat_id": cid, "message": "hello"})
    assert len(client.app.state.services.chat_store.list_messages(cid)) == 2
    res = client.post("/api/command", json={"input": "/clear", "chat_id": cid})
    assert res.status_code == 200
    assert client.app.state.services.chat_store.list_messages(cid) == []
