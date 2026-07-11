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


def test_chat_does_not_autosave_casual_message(client):
    # Plan §10 / acceptance #3: a casual message must NOT become a memory.
    # With the FakeLLM (returns plain text, not extractor JSON), extraction
    # yields nothing, so the store stays empty.
    cid = _make_chat(client)
    before = client.get("/api/memories").json()["count"]
    client.post("/api/chat", json={"chat_id": cid, "message": "привет"})
    after = client.get("/api/memories").json()["count"]
    assert after == before


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


# ---------------------------- folders ----------------------------


def test_folder_crud(client):
    res = client.post("/api/folders", json={"name": "Проект", "description": "контекст"})
    assert res.status_code == 200
    fid = res.json()["id"]
    assert res.json()["description"] == "контекст"
    assert any(f["id"] == fid for f in client.get("/api/folders").json()["folders"])
    # rename folder
    res = client.patch(f"/api/folders/{fid}", json={"name": "Проект2", "description": "d2"})
    assert res.json()["name"] == "Проект2" and res.json()["description"] == "d2"
    # delete folder
    assert client.delete(f"/api/folders/{fid}").status_code == 200
    assert client.get("/api/folders").json()["folders"] == []


def test_move_chat_to_folder(client):
    fid = client.post("/api/folders", json={"name": "F"}).json()["id"]
    cid = _make_chat(client)
    res = client.patch(f"/api/chats/{cid}/move", json={"folder_id": fid})
    assert res.json()["folder_id"] == fid
    # deleting the folder unsets folder_id on its chats (SET NULL)
    client.delete(f"/api/folders/{fid}")
    chat = client.get(f"/api/chats/{cid}").json()
    assert chat["folder_id"] is None


def test_pin_chat(client):
    cid = _make_chat(client)
    res = client.patch(f"/api/chats/{cid}", json={"pinned": True})
    assert res.json()["pinned"] is True
    # pinned chat sorts first
    other = _make_chat(client)
    client.patch(f"/api/chats/{other}", json={"pinned": False})
    chats = client.get("/api/chats").json()["chats"]
    assert chats[0]["id"] == cid and chats[0]["pinned"] is True


def test_folder_description_in_context(client):
    """Folder description is surfaced in the system prompt on the next chat turn."""
    fid = client.post(
        "/api/folders", json={"name": "Код", "description": "Отвечай только на Python"}
    ).json()["id"]
    cid = _make_chat(client)
    client.patch(f"/api/chats/{cid}/move", json={"folder_id": fid})
    captured = {}

    original = client.app.state.services.llm.generate

    def spy(messages, max_new_tokens=None):
        # Capture only the chat system prompt; ignore the extractor call which
        # follows in the same request and has a different system message.
        captured.setdefault(
            "system",
            next(m["content"] for m in messages if m["role"] == "system"),
        )
        yield from original(messages, max_new_tokens)

    client.app.state.services.llm.generate = spy
    try:
        client.post("/api/chat", json={"chat_id": cid, "message": "напиши пример"})
    finally:
        client.app.state.services.llm.generate = original
    assert "Отвечай только на Python" in captured["system"]
    assert "Контекст папки" in captured["system"]


# ---------------------------- messages: edit/delete/search ----------------------------


def _send(client, cid, message):
    client.post("/api/chat", json={"chat_id": cid, "message": message})


def test_delete_message(client):
    cid = _make_chat(client)
    _send(client, cid, "hello there")
    msgs = client.get(f"/api/chats/{cid}/messages").json()["messages"]
    user_id = msgs[0]["id"]
    res = client.delete(f"/api/chats/{cid}/messages/{user_id}")
    assert res.status_code == 200
    remaining = client.get(f"/api/chats/{cid}/messages").json()["messages"]
    assert all(m["id"] != user_id for m in remaining)


def test_edit_message(client):
    cid = _make_chat(client)
    _send(client, cid, "original text")
    msgs = client.get(f"/api/chats/{cid}/messages").json()["messages"]
    user_id = msgs[0]["id"]
    res = client.patch(f"/api/chats/{cid}/messages/{user_id}", json={"content": "edited text"})
    assert res.status_code == 200
    assert res.json()["content"] == "edited text"
    assert res.json()["edited_at"]  # timestamp set


def test_search_messages(client):
    cid = _make_chat(client)
    _send(client, cid, "uniquephrase banana")
    res = client.get("/api/messages/search", params={"q": "uniquephrase"})
    assert res.status_code == 200
    data = res.json()
    assert data["count"] >= 1
    assert any("uniquephrase" in m["content"] for m in data["messages"])


def test_export_chat_markdown(client):
    cid = _make_chat(client, "Мой диалог")
    _send(client, cid, "привет")
    res = client.get(f"/api/chats/{cid}/export")
    assert res.status_code == 200
    text = res.text
    assert "# Мой диалог" in text
    assert "привет" in text
    assert "Пользователь" in text

