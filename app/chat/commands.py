from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from app.chat.session import ChatSession, DEFAULT_PERSONA
from app.config import Settings
from app.llm.gemma import clean_response
from app.memory.recall import RecallService
from app.memory.store import Memory, MemoryStore


@dataclass
class CommandContext:
    store: MemoryStore
    recall: RecallService
    session: ChatSession
    llm: "LLMLike"
    settings: Settings
    persona: str = DEFAULT_PERSONA


@dataclass
class CommandResult:
    text: str | None = None
    error: bool = False
    is_command: bool = True


CommandHandler = Callable[[str, CommandContext], CommandResult]


def _format_memories(memories: list[Memory], scores: list[float] | None = None) -> str:
    if not memories:
        return "(nothing found)"
    lines = []
    for i, mem in enumerate(memories, start=1):
        score = f" ({scores[i - 1]:.2f})" if scores else ""
        tags = f" [{', '.join(mem.tags)}]" if mem.tags else ""
        preview = mem.content.strip().replace("\n", " ")
        if len(preview) > 200:
            preview = preview[:197] + "..."
        lines.append(f"#{mem.id}{tags}{score} {preview}")
    return "\n".join(lines)


# --- handlers ---


def cmd_help(args: str, ctx: CommandContext) -> CommandResult:
    return CommandResult(text=REGISTRY.help_text())


def cmd_save(args: str, ctx: CommandContext) -> CommandResult:
    text = args.strip()
    if not text:
        return CommandResult(text="Usage: /save <text to remember>", error=True)
    mem = ctx.store.add(content=text, source="manual")
    ctx.recall.add_memory(mem)
    return CommandResult(text=f"Saved as memory #{mem.id}.")


def cmd_search(args: str, ctx: CommandContext) -> CommandResult:
    query = args.strip()
    if not query:
        return CommandResult(text="Usage: /search <query>", error=True)
    hits = ctx.recall.recall(query, k=ctx.settings.recall_top_k)
    memories = [m for m, _ in hits]
    scores = [s for _, s in hits]
    return CommandResult(text="Search results:\n" + _format_memories(memories, scores))


def cmd_recent(args: str, ctx: CommandContext) -> CommandResult:
    try:
        n = int(args.strip()) if args.strip() else ctx.settings.recent_default
    except ValueError:
        n = ctx.settings.recent_default
    memories = ctx.store.list_recent(n)
    return CommandResult(text=f"Last {len(memories)} memories:\n" + _format_memories(memories))


def cmd_tags(args: str, ctx: CommandContext) -> CommandResult:
    counts = ctx.store.tag_counts()
    if not counts:
        return CommandResult(text="No tags yet.")
    lines = [f"{tag} ({count})" for tag, count in sorted(counts.items())]
    return CommandResult(text="Tags:\n" + "\n".join(lines))


def cmd_tag(args: str, ctx: CommandContext) -> CommandResult:
    parts = args.split(maxsplit=1)
    if len(parts) < 2:
        return CommandResult(text="Usage: /tag <id> <tag> [more tags...]", error=True)
    try:
        memory_id = int(parts[0])
    except ValueError:
        return CommandResult(text="First argument must be a memory id.", error=True)
    tags = [t for t in parts[1].replace(",", " ").split() if t]
    memory = ctx.store.get(memory_id)
    if memory is None:
        return CommandResult(text=f"Memory #{memory_id} not found.", error=True)
    for tag in tags:
        ctx.store.add_tag(memory_id, tag)
    return CommandResult(text=f"Tagged #{memory_id} with: {', '.join(tags)}")


def cmd_forget(args: str, ctx: CommandContext) -> CommandResult:
    try:
        memory_id = int(args.strip())
    except ValueError:
        return CommandResult(text="Usage: /forget <id>", error=True)
    if ctx.store.delete(memory_id):
        ctx.recall.rebuild_from_store()
        return CommandResult(text=f"Forgot memory #{memory_id}.")
    return CommandResult(text=f"Memory #{memory_id} not found.", error=True)


def cmd_summary(args: str, ctx: CommandContext) -> CommandResult:
    try:
        n = int(args.strip()) if args.strip() else 20
    except ValueError:
        n = 20
    memories = ctx.store.list_recent(n)
    if not memories:
        return CommandResult(text="No memories to summarize yet.")
    notes = "\n".join(f"- {m.content}" for m in reversed(memories))
    messages = [
        {
            "role": "system",
            "content": "Summarize the user's notes into a concise digest: key themes, "
            "facts to remember, and action items. Use the user's language.",
        },
        {"role": "user", "content": notes},
    ]
    pieces = []
    for chunk in ctx.llm.generate(messages, max_new_tokens=512):
        pieces.append(chunk)
    digest = clean_response("".join(pieces))
    return CommandResult(text="Summary:\n" + digest)


def cmd_context(args: str, ctx: CommandContext) -> CommandResult:
    query = args.strip() or ctx.session.last_user_message() or ""
    if not query:
        return CommandResult(text="Nothing to build context from yet.")
    context = ctx.recall.build_context(query, k=ctx.settings.recall_top_k)
    return CommandResult(text=context or "(no relevant memories found)")


def cmd_export(args: str, ctx: CommandContext) -> CommandResult:
    data = [m.to_dict() for m in ctx.store.all()]
    return CommandResult(text="```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```")


def cmd_clear(args: str, ctx: CommandContext) -> CommandResult:
    ctx.session.clear()
    return CommandResult(text="Conversation cleared (memories kept).")


def cmd_think(args: str, ctx: CommandContext) -> CommandResult:
    value = args.strip().lower()
    if value in {"on", "true", "1", "yes"}:
        ctx.llm.thinking = True
        return CommandResult(text="Thinking mode: ON")
    if value in {"off", "false", "0", "no"}:
        ctx.llm.thinking = False
        return CommandResult(text="Thinking mode: OFF")
    return CommandResult(text="Usage: /think on|off  (currently " +
                                 ("ON" if ctx.llm.thinking else "OFF") + ")", error=True)


def cmd_wipe(args: str, ctx: CommandContext) -> CommandResult:
    if args.strip().lower() != "everything":
        return CommandResult(
            text="This is destructive. To delete ALL memories, type: /wipe everything",
            error=True,
        )
    for mem in ctx.store.all():
        ctx.store.delete(mem.id)
    ctx.recall.rebuild_from_store()
    ctx.session.clear()
    return CommandResult(text="All memories deleted and conversation cleared.")


def cmd_status(args: str, ctx: CommandContext) -> CommandResult:
    lines = [
        f"Model: {ctx.settings.model_id} (loaded: {ctx.llm.is_loaded()})",
        f"Quantization: {'4-bit' if ctx.settings.load_in_4bit else 'bf16'}",
        f"Thinking mode: {'ON' if ctx.llm.thinking else 'OFF'}",
        f"Memories: {ctx.store.count()}",
        f"Index size: {ctx.recall.index.size}",
    ]
    return CommandResult(text="Status:\n" + "\n".join(lines))


@dataclass
class _Entry:
    handler: CommandHandler
    description: str


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, _Entry] = {}

    def register(self, name: str, handler: CommandHandler, description: str) -> None:
        self._commands[name] = _Entry(handler, description)

    @property
    def names(self) -> list[str]:
        return sorted(self._commands.keys())

    def help_text(self) -> str:
        lines = ["Available commands:"]
        width = max(len(n) for n in self._commands)
        for name in sorted(self._commands):
            lines.append(f"  /{name.ljust(width)}  {self._commands[name].description}")
        return "\n".join(lines)

    def parse(self, line: str) -> tuple[str, str] | None:
        line = line.strip()
        if not line.startswith("/"):
            return None
        body = line[1:]
        if not body:
            return None
        name, _, args = body.partition(" ")
        return name.lower(), args

    def dispatch(self, line: str, ctx: CommandContext) -> CommandResult | None:
        parsed = self.parse(line)
        if parsed is None:
            return None
        name, args = parsed
        entry = self._commands.get(name)
        if entry is None:
            return CommandResult(
                text=f"Unknown command: /{name}. Type /help for the list.", error=True
            )
        try:
            return entry.handler(args, ctx)
        except Exception as exc:  # noqa: BLE001 - surface any error to the chat
            return CommandResult(text=f"Command failed: {exc}", error=True)


REGISTRY = CommandRegistry()
REGISTRY.register("help", cmd_help, "Show available commands")
REGISTRY.register("save", cmd_save, "Save a memory: /save <text>")
REGISTRY.register("search", cmd_search, "Semantic search memories: /search <query>")
REGISTRY.register("recent", cmd_recent, "Show recent memories: /recent [n]")
REGISTRY.register("tags", cmd_tags, "List all tags")
REGISTRY.register("tag", cmd_tag, "Tag a memory: /tag <id> <tag>")
REGISTRY.register("forget", cmd_forget, "Delete a memory: /forget <id>")
REGISTRY.register("summary", cmd_summary, "Summarize recent memories (uses LLM)")
REGISTRY.register("context", cmd_context, "Show recalled context for a query")
REGISTRY.register("export", cmd_export, "Export all memories as JSON")
REGISTRY.register("clear", cmd_clear, "Clear the conversation (keeps memories)")
REGISTRY.register("think", cmd_think, "Toggle thinking mode: /think on|off")
REGISTRY.register("wipe", cmd_wipe, "Delete ALL memories: /wipe everything")
REGISTRY.register("status", cmd_status, "Show app status")
