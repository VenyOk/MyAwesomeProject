from __future__ import annotations

from app.chat.commands import REGISTRY, CommandContext


def _run(line: str, ctx: CommandContext):
    return REGISTRY.dispatch(line, ctx)


def test_parse_non_command():
    assert REGISTRY.parse("hello") is None
    assert REGISTRY.parse("") is None


def test_parse_command_and_args():
    assert REGISTRY.parse("/save some text") == ("save", "some text")
    assert REGISTRY.parse("/help") == ("help", "")


def test_help(ctx: CommandContext):
    res = _run("/help", ctx)
    assert res is not None and not res.error
    assert "/save" in res.text and "/search" in res.text


def test_save_and_search(ctx: CommandContext):
    res = _run("/save I learned that water boils at 100C", ctx)
    assert not res.error
    assert "#1" in res.text
    res = _run("/search water boils", ctx)
    assert not res.error
    assert "water boils at 100C" in res.text


def test_recent_and_count(ctx: CommandContext):
    _run("/save first note", ctx)
    _run("/save second note", ctx)
    res = _run("/recent", ctx)
    assert "second note" in res.text and "first note" in res.text
    assert ctx.store.count() == 2


def test_tag_and_tags(ctx: CommandContext):
    _run("/save important thing", ctx)
    res = _run("/tag 1 urgent work", ctx)
    assert not res.error
    res = _run("/tags", ctx)
    assert "urgent" in res.text and "work" in res.text


def test_forget(ctx: CommandContext):
    _run("/save delete me", ctx)
    assert ctx.store.count() == 1
    res = _run("/forget 1", ctx)
    assert not res.error
    assert ctx.store.count() == 0
    assert ctx.recall.index.size == 0


def test_think_toggle(ctx: CommandContext):
    assert ctx.llm.thinking is False
    _run("/think on", ctx)
    assert ctx.llm.thinking is True
    res = _run("/think off", ctx)
    assert "OFF" in res.text
    assert ctx.llm.thinking is False


def test_think_invalid(ctx: CommandContext):
    res = _run("/think maybe", ctx)
    assert res.error


def test_wipe_requires_confirmation(ctx: CommandContext):
    _run("/save keep me", ctx)
    res = _run("/wipe", ctx)
    assert res.error
    assert ctx.store.count() == 1  # not wiped


def test_wipe_everything(ctx: CommandContext):
    _run("/save one", ctx)
    _run("/save two", ctx)
    res = _run("/wipe everything", ctx)
    assert not res.error
    assert ctx.store.count() == 0


def test_clear(ctx: CommandContext):
    ctx.session.add("user", "hi")
    assert len(ctx.session.history()) == 1
    _run("/clear", ctx)
    assert len(ctx.session.history()) == 0


def test_export(ctx: CommandContext):
    _run("/save export me", ctx)
    res = _run("/export", ctx)
    assert "export me" in res.text


def test_summary(ctx: CommandContext):
    _run("/save a note about cats", ctx)
    res = _run("/summary", ctx)
    assert not res.error
    assert "FakeLLM" in res.text


def test_unknown_command(ctx: CommandContext):
    res = _run("/nope", ctx)
    assert res.error
    assert "Unknown command" in res.text


def test_status(ctx: CommandContext):
    res = _run("/status", ctx)
    assert not res.error
    assert "Memories:" in res.text
