"""Pydantic argument schemas for the MVP tool set (plan §12.1).

Each schema doubles as validation for the model's tool-call arguments and as
the source of the JSON schema advertised to the model.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class MemorySearchArgs(BaseModel):
    query: str = Field(..., description="Текст поиска: слово, фраза или тема.")


class MemoryCreateArgs(BaseModel):
    content: str = Field(..., description="Что запомнить. Короткое утверждение о пользователе.")
    kind: str = Field(default="fact", description="Тип: fact | preference | decision | idea | person | project")
    source: Literal["agent", "manual"] = Field(
        default="agent",
        description="Источник сохранения: agent для вызова модели, manual для явной команды пользователя.",
    )


class MemoryUpdateArgs(BaseModel):
    id: int = Field(..., description="ID воспоминания для изменения.")
    content: str | None = Field(default=None, description="Новый текст воспоминания.")
    kind: str | None = Field(default=None, description="Новый тип воспоминания.")


class MemoryDeleteArgs(BaseModel):
    id: int = Field(..., description="ID воспоминания для удаления.")


class TaskCreateArgs(BaseModel):
    title: str = Field(..., description="Короткое название задачи.")
    description: str = Field(default="", description="Детали задачи (необязательно).")
    due_at: str | None = Field(default=None, description="Срок в ISO-формате, например 2026-07-15T18:00. Не угадывай дату.")


class TaskListArgs(BaseModel):
    status: str = Field(default="open", description="Фильтр статуса: open | done | cancelled. Пустая строка = все.")


class TaskIdArgs(BaseModel):
    id: int = Field(..., description="ID задачи.")


class ReminderCreateArgs(BaseModel):
    title: str = Field(..., description="Текст напоминания.")
    scheduled_at: str = Field(
        ...,
        description="Точное время в ISO-формате, например 2026-07-15T18:00. Не угадывай дату.",
    )
    task_id: int | None = Field(default=None, description="Связанная задача, если она уже создана.")
    timezone: str | None = Field(default=None, description="Часовой пояс IANA, например Europe/Moscow.")


class ReminderListArgs(BaseModel):
    status: str = Field(default="scheduled", description="Фильтр: scheduled | fired | cancelled. Пустая строка = все.")


class ReminderIdArgs(BaseModel):
    id: int = Field(..., description="ID напоминания.")


class TaskWithReminderArgs(BaseModel):
    title: str = Field(..., description="Короткое название задачи и напоминания.")
    scheduled_at: str = Field(
        ...,
        description="Точное время напоминания в ISO-формате, например 2026-07-15T18:00. Не угадывай дату.",
    )
    description: str = Field(default="", description="Детали задачи (необязательно).")
    timezone: str | None = Field(default=None, description="Часовой пояс IANA, например Europe/Moscow.")
