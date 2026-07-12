from __future__ import annotations


def _linked_message_and_memory(client, *, content: str = "source fact"):
    chat = client.post("/api/chats", json={"title": "Source"}).json()
    services = client.app.state.services
    message = services.chat_store.add_message(chat["id"], "user", "original source")
    memory = services.store.add(
        content=content,
        source="chat",
        source_type="chat",
        source_message_id=message.id,
        status="active",
    )
    services.recall.add_memory(memory)
    return chat["id"], message.id, memory


def test_editing_source_message_marks_linked_memory_for_recheck_and_removes_recall(client):
    chat_id, message_id, memory = _linked_message_and_memory(client)
    services = client.app.state.services
    assert services.recall.build_context("source fact")

    response = client.patch(
        f"/api/chats/{chat_id}/messages/{message_id}",
        json={"content": "corrected source"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "corrected source"
    assert body["edited_at"]
    assert body["memory_recheck_count"] == 1
    assert body["memory_recheck_memory_ids"] == [memory.id]
    updated_memory = services.store.get(memory.id)
    assert updated_memory is not None
    assert updated_memory.status == "candidate"
    assert updated_memory.embedding_status == "pending"
    assert services.recall.build_context("source fact") == ""
    assert services.recall.index.size == 0


def test_list_linked_memories_for_message(client):
    chat_id, message_id, memory = _linked_message_and_memory(client)

    response = client.get(f"/api/chats/{chat_id}/messages/{message_id}/memories")

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["memories"][0]["id"] == memory.id


def test_delete_message_requires_choice_for_linked_memories(client):
    chat_id, message_id, memory = _linked_message_and_memory(client)

    response = client.delete(f"/api/chats/{chat_id}/messages/{message_id}")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "derived_memories_confirmation_required"
    assert detail["allowed_actions"] == ["keep", "delete"]
    assert detail["derived_memories"]["count"] == 1
    assert detail["derived_memories"]["memories"][0]["id"] == memory.id
    assert client.app.state.services.chat_store.get_message(message_id) is not None


def test_delete_message_can_keep_linked_memories(client):
    chat_id, message_id, memory = _linked_message_and_memory(client)

    response = client.delete(
        f"/api/chats/{chat_id}/messages/{message_id}",
        params={"derived_memories": "keep"},
    )

    assert response.status_code == 200
    assert response.json()["derived_memories"]["action"] == "keep"
    kept_memory = client.app.state.services.store.get(memory.id)
    assert kept_memory is not None
    assert kept_memory.status == "active"
    assert kept_memory.deleted_at is None


def test_delete_message_can_soft_delete_linked_memories_and_rebuild_recall(client):
    chat_id, message_id, memory = _linked_message_and_memory(client)
    services = client.app.state.services
    assert services.recall.index.size == 1

    response = client.delete(
        f"/api/chats/{chat_id}/messages/{message_id}",
        params={"derived_memories": "delete"},
    )

    assert response.status_code == 200
    assert response.json()["derived_memories"]["action"] == "delete"
    assert response.json()["derived_memories"]["count"] == 1
    deleted_memory = services.store.get(memory.id)
    assert deleted_memory is not None
    assert deleted_memory.status == "deleted"
    assert deleted_memory.deleted_at is not None
    assert services.recall.index.size == 0
    assert services.recall.build_context("source fact") == ""


def test_restore_resets_a_pending_memory_to_ready_for_recall(client):
    chat_id, message_id, memory = _linked_message_and_memory(client)
    services = client.app.state.services
    client.patch(
        f"/api/chats/{chat_id}/messages/{message_id}",
        json={"content": "corrected source"},
    )
    assert services.store.delete(memory.id)

    restored = services.store.restore(memory.id)

    assert restored is not None
    assert restored.status == "active"
    assert restored.embedding_status == "ready"
    services.recall.rebuild_from_store()
    assert services.recall.build_context("source fact")
