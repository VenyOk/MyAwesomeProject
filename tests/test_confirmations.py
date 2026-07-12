from __future__ import annotations

from app.agent.policies import decide


def test_confirm_and_destructive_tools_do_not_auto_execute():
    assert decide("task.complete").auto_execute is False
    assert decide("memory.delete").auto_execute is False
    assert decide("task.create").auto_execute is True


def test_approval_executes_only_the_stored_action(client):
    services = client.app.state.services
    run_id = services.agent_store.start_tool_run(
        "task.create",
        {"title": "Подготовить демо", "description": "", "due_at": None},
        chat_id=None,
        policy_decision="confirm",
    )
    confirmation = services.agent_store.create_confirmation(
        tool_run_id=run_id,
        tool_name="task.create",
        arguments={"title": "Подготовить демо", "description": "", "due_at": None},
        risk="confirm",
        chat_id=None,
    )

    assert services.task_store.count() == 0
    response = client.post(f"/api/confirmations/{confirmation.id}/approve")
    assert response.status_code == 200
    assert response.json()["confirmation"]["status"] == "approved"
    assert response.json()["result"]["title"] == "Подготовить демо"
    assert services.task_store.count() == 1

    # A second click must not execute the stored action again.
    assert client.post(f"/api/confirmations/{confirmation.id}/approve").status_code == 409
    assert services.task_store.count() == 1


def test_rejection_keeps_action_unexecuted(client):
    services = client.app.state.services
    run_id = services.agent_store.start_tool_run(
        "task.create",
        {"title": "Не создавать", "description": "", "due_at": None},
        chat_id=None,
        policy_decision="confirm",
    )
    confirmation = services.agent_store.create_confirmation(
        tool_run_id=run_id,
        tool_name="task.create",
        arguments={"title": "Не создавать", "description": "", "due_at": None},
        risk="confirm",
        chat_id=None,
    )

    response = client.post(f"/api/confirmations/{confirmation.id}/reject")
    assert response.status_code == 200
    assert response.json()["confirmation"]["status"] == "rejected"
    assert services.task_store.count() == 0


def test_approval_completes_task_only_after_confirmation(client):
    services = client.app.state.services
    task = services.task_store.create("Закрыть задачу")
    run_id = services.agent_store.start_tool_run(
        "task.complete", {"id": task.id}, chat_id=None, policy_decision="confirm"
    )
    confirmation = services.agent_store.create_confirmation(
        tool_run_id=run_id,
        tool_name="task.complete",
        arguments={"id": task.id},
        risk="confirm",
        chat_id=None,
    )

    assert services.task_store.get(task.id).status == "open"
    assert client.post(f"/api/confirmations/{confirmation.id}/approve").status_code == 200
    assert services.task_store.get(task.id).status == "done"
