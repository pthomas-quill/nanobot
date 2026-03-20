"""Memory agent for persistent memory management."""

from __future__ import annotations

import asyncio
import json
import weakref
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger

from nanobot.utils.helpers import ensure_dir, estimate_message_tokens, estimate_prompt_tokens_chain
from nanobot.agent.tools import ToolRegistry, Tool
from nanobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool, LineEditTool

if TYPE_CHECKING:
    from nanobot.providers.base import LLMProvider
    from nanobot.session.manager import Session, SessionManager


class DailyAppend(Tool):
    """append new content to the daily log file."""

    def __init__(self, daily_dir: Path):
        self.daily_dir = daily_dir

    @property
    def name(self) -> str:
        return "daily_append"

    @property
    def description(self) -> str:
        return (
            "Append text to today's daily log file."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The text to append to today's daily log file. Format as a list of markdown bullet points, e.g. '- [HH:mm] Learned about X\n- [HH:mm] Made a decision about Y'. Include timestamps for each entry."},
            },
            "required": ["content", ],
        }

    async def execute(
        self, content:str, **kwargs: Any,
    ) -> str:
        try:
            fp = (self.daily_dir / f"{datetime.now().strftime('%Y-%m-%d')}.md").resolve()
            fp.touch()

            with open(fp, "a", encoding="utf-8") as f:
                f.write(content.rstrip() + "\n")

            return f"Successfully appended to {fp}"
        except Exception as e:
            return f"Error appending to daily log file: {e}"

class MemoryAgent:
    """Owns consolidation policy, locking, and session offset updates."""

    _MAX_CONSOLIDATION_ROUNDS = 5
    _MAX_FAILURES_BEFORE_RAW_ARCHIVE = 3

    def __init__(
        self,
        workspace: Path,
        provider: LLMProvider,
        model: str,
        sessions: SessionManager,
        context_window_tokens: int,
        build_messages: Callable[..., list[dict[str, Any]]],
        get_tool_definitions: Callable[[], list[dict[str, Any]]],
    ):
        self.provider = provider
        self.model = model or provider.get_default_model()
        self.sessions = sessions
        self.context_window_tokens = context_window_tokens
        self._build_messages = build_messages
        self._get_tool_definitions = get_tool_definitions
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

        self.workspace = workspace
        self.memory_dir = ensure_dir(workspace / "memory")
        self.daily_dir = ensure_dir(self.memory_dir / "daily")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self._consecutive_failures = 0
        self.max_iterations = 40

        self.tools = ToolRegistry()
        self.tools.register(ReadFileTool(workspace=workspace, allowed_dir=workspace))
        for cls in (WriteFileTool,LineEditTool, EditFileTool, ListDirTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=self.memory_dir))
        self.tools.register(DailyAppend(daily_dir=self.daily_dir))

    def get_lock(self, session_key: str) -> asyncio.Lock:
        """Return the shared consolidation lock for one session."""
        return self._locks.setdefault(session_key, asyncio.Lock())
    
    @staticmethod
    def _format_messages(messages: list[dict]) -> str:
        lines = []
        for message in messages:
            if not message.get("content"):
                continue
            tools = f" [tools: {', '.join(message['tools_used'])}]" if message.get("tools_used") else ""
            lines.append(
                f"[{message.get('timestamp', '?')[:16]}] {message['role'].upper()}{tools}: {message['content']}"
            )
        return "\n".join(lines)
    
    def get_system_prompt(self) -> str:
        return f"""# Memory Consolidation Agent

You are a memory consolidation agent. You are in charge of managing the main agent's memory (long-term facts, daily logs, knowledge base, etc..).

## Memory Workspace
Memory is materialized as markdown files in the "memory" subdirectory of the workspace (full path: {self.memory_dir}). You can read all the files in the workspace and can write in the memory directory.

## Daily Logs (mandatory)
You should maintain daily log files in the "memory/daily/YYYY-MM-DD.md" format. These should contain a chronological record of the day's significant events, thoughts, and learnings. They serve as a diary of the agent's journey. Use the daily_append tool to add entries to the daily log. Each entry should ideally have a timestamp and be formatted as a markdown bullet point. If an entry refers to a previous day, write down the full date in the timestamp.

## MEMORY.md
The main long-term memory file is MEMORY.md. This should contain only the most important facts, lessons, opinions, and events that the agent needs to remember over time. It should be concise and focused on things that will stay relevant over several months, not a dump of everything.
Examples of what to include in MEMORY.md:
- User preferences and important facts about the user
- Important information about the agent's identity, environment, overarching tasks and goals, and how to use tools
What NOT to include in MEMORY.md:
- Daily logs of events (these belong in the daily log files)
- Temporary information that is only relevant for a short time
- Unimportant details that are unlikely to be relevant in the future

## Knowledge Base
You are also STRONGLY ENCOURAGED to create other markdown files and subdirectories in the memory directory to serve as a knowledge base on specific topics. For example, if the agent learns about a new concept or tool, you can create a dedicated markdown file summarizing that knowledge.

## Memory Consolidation
When consolidating, review the recent conversation and the current MEMORY.md. Extract any new significant facts, insights, decisions, or lessons from the conversation and update your memory file, daily log (mandatory, use the daily_append tool) and knowledge base as needed.

"""

    async def consolidate_messages(self, messages: list[dict[str, object]]) -> bool:
        """Consolidate the provided message chunk into MEMORY.md + HISTORY.md."""
        if not messages:
            return True
        
        from nanobot.agent.context import build_assistant_message

        # print("### MEM: Consolidating messages:",len(messages))

        today_log = self.daily_dir / f"{datetime.now().strftime('%Y-%m-%d')}.md"
        # current_memory = self.read_long_term()
        prompt = f"""Today's daily log file is at {today_log}
        
Process this conversation and consolidate it into your memory system.

## Conversation to Process
{self._format_messages(messages)}"""
        
        messages = [
            {"role": "system", "content": self.get_system_prompt()},
            {"role": "user", "content": prompt},
        ]
        iteration = 0
        edited_daily = False
        tools_used: list[str] = []
        steering_count = 0

        try:
            while iteration < self.max_iterations:
                # # print("### MEM: Consolidation iteration", iteration)
                iteration += 1

                tool_defs = self.tools.get_definitions()

                response = await self.provider.chat_with_retry(
                    messages=messages,
                    tools=tool_defs,
                    model=self.model,
                )

                if response.has_tool_calls:
                    tool_call_dicts = [
                        tc.to_openai_tool_call()
                        for tc in response.tool_calls
                    ]
                    messages.append(build_assistant_message(
                        response.content, tool_call_dicts,
                        reasoning_content=response.reasoning_content,
                        thinking_blocks=response.thinking_blocks,
                    ))

                    for tool_call in response.tool_calls:
                        tools_used.append(tool_call.name)
                        args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                        logger.info("Tool call: {}({})", tool_call.name, args_str[:200])
                        # # print(f"### MEM: Tool call: {tool_call.name}({args_str})")
                        result = None
                        if tool_call.name in ("write_file", "edit_file", "line_edit"):
                            filename = tool_call.arguments.get("path", "")
                            if "memory/daily/" in filename:
                                # print(f"### MEM: Detected daily log edit attempt: {filename}")
                                result = "Error: Direct edits to daily log files are not allowed. Please use the daily_append tool to add entries to today's daily log."
                        if result is None:
                            result = await self.tools.execute(tool_call.name, tool_call.arguments)
                        # print(f"### MEM: Tool call result: {result}")
                        # check if this is a daily log edit to set the flag for steering later iterations
                        if tool_call.name == "daily_append" :
                            # print(f"### MEM: Detected daily log edit: {result}")
                            edited_daily = True
                        messages.append({"role": "tool", "tool_call_id": tool_call.id, "name": tool_call.name, "content": result})
                else:
                    if edited_daily:
                        break
                    
                    steering_count += 1
                    if steering_count > self._MAX_FAILURES_BEFORE_RAW_ARCHIVE:
                        logger.warning("Memory consolidation: max steering attempts reached without editing daily log")
                        # print("### MEM: Max steering attempts reached without editing daily log")
                        return self._fail_or_raw_archive(messages)
                    
                    logger.warning("Daily log was not edited during consolidation, adding reminder message for next iteration")
                    messages.append({"role": "user", "content": f"You should at least create or update today's daily log in the {today_log} file to record the day's events, even if there are no significant facts worth adding to MEMORY.md. Please do that now."})

            if len(tools_used) == 0:
                logger.warning("Memory consolidation: no tool calls detected ")
                # print("### MEM: No tool calls detected during consolidation")
                return self._fail_or_raw_archive(messages)
            
            if not edited_daily:
                logger.warning("Memory consolidation: daily log file was not edited during consolidation")
                # print("### MEM: Daily log file was not edited during consolidation")
                return self._fail_or_raw_archive(messages)
        
            logger.info("Memory consolidation done for {} messages with {} tool calls", len(messages), len(tools_used))
            # print(f"### MEM: Consolidation successful for {len(messages)} messages with {len(tools_used)} tool calls")
            self._consecutive_failures = 0
            return True
        except Exception as e:
            logger.warning("Memory consolidation failed: {}", e)
            # print(f"### MEM: Consolidation failed with exception: {e}")
            return self._fail_or_raw_archive(messages)

    
    def _fail_or_raw_archive(self, messages: list[dict]) -> bool:
        """Increment failure count; after threshold, raw-archive messages and return True."""
        self._consecutive_failures += 1
        if self._consecutive_failures < self._MAX_FAILURES_BEFORE_RAW_ARCHIVE:
            return False
        self._raw_archive(messages)
        self._consecutive_failures = 0
        return True

    def _raw_archive(self, messages: list[dict]) -> None:
        """Fallback: dump raw messages to HISTORY.md without LLM summarization."""
        ts = datetime.now().strftime("%H:%M")
        self.append_history(
            f"[{ts}] [RAW] {len(messages)} messages\n"
            f"{self._format_messages(messages)}"
        )
        logger.warning(
            "Memory consolidation degraded: raw-archived {} messages", len(messages)
        )
    
    def append_history(self, entry: str) -> None:
        today_log = self.daily_dir / f"{datetime.now().strftime('%Y-%m-%d')}.md"
        with open(today_log, "a", encoding="utf-8") as f:
            f.write(entry.rstrip() + "\n\n")

    def pick_consolidation_boundary(
        self,
        session: Session,
        tokens_to_remove: int,
    ) -> tuple[int, int] | None:
        """Pick a user-turn boundary that removes enough old prompt tokens."""
        start = session.last_consolidated
        if start >= len(session.messages) or tokens_to_remove <= 0:
            return None

        removed_tokens = 0
        last_boundary: tuple[int, int] | None = None
        for idx in range(start, len(session.messages)):
            message = session.messages[idx]
            if idx > start and message.get("role") == "user":
                last_boundary = (idx, removed_tokens)
                if removed_tokens >= tokens_to_remove:
                    return last_boundary
            removed_tokens += estimate_message_tokens(message)

        return last_boundary

    def estimate_session_prompt_tokens(self, session: Session) -> tuple[int, str]:
        """Estimate current prompt size for the normal session history view."""
        history = session.get_history(max_messages=0)
        channel, chat_id = (session.key.split(":", 1) if ":" in session.key else (None, None))
        probe_messages = self._build_messages(
            history=history,
            current_message="[token-probe]",
            channel=channel,
            chat_id=chat_id,
        )
        return estimate_prompt_tokens_chain(
            self.provider,
            self.model,
            probe_messages,
            self._get_tool_definitions(),
        )

    async def archive_messages(self, messages: list[dict[str, object]]) -> bool:
        """Archive messages with guaranteed persistence (retries until raw-dump fallback)."""
        if not messages:
            return True
        for _ in range(self._MAX_FAILURES_BEFORE_RAW_ARCHIVE):
            if await self.consolidate_messages(messages):
                return True
        return True

    async def maybe_consolidate_by_tokens(self, session: Session) -> None:
        """Loop: archive old messages until prompt fits within half the context window."""
        if not session.messages or self.context_window_tokens <= 0:
            return

        lock = self.get_lock(session.key)
        async with lock:
            target = self.context_window_tokens // 2
            estimated, source = self.estimate_session_prompt_tokens(session)
            if estimated <= 0:
                return
            if estimated < self.context_window_tokens:
                logger.debug(
                    "Token consolidation idle {}: {}/{} via {}",
                    session.key,
                    estimated,
                    self.context_window_tokens,
                    source,
                )
                return

            for round_num in range(self._MAX_CONSOLIDATION_ROUNDS):
                # print(f"### MEM: Consolidation check round {round_num} for {session.key}: {estimated}/{self.context_window_tokens} tokens (source: {source})")
                if estimated <= target:
                    return

                boundary = self.pick_consolidation_boundary(session, max(1, estimated - target))
                if boundary is None:
                    logger.debug(
                        "Token consolidation: no safe boundary for {} (round {})",
                        session.key,
                        round_num,
                    )
                    return

                end_idx = boundary[0]
                chunk = session.messages[session.last_consolidated:end_idx]
                if not chunk:
                    return

                logger.info(
                    "Token consolidation round {} for {}: {}/{} via {}, chunk={} msgs",
                    round_num,
                    session.key,
                    estimated,
                    self.context_window_tokens,
                    source,
                    len(chunk),
                )
                if not await self.consolidate_messages(chunk):
                    return
                session.last_consolidated = end_idx
                self.sessions.save(session)

                estimated, source = self.estimate_session_prompt_tokens(session)
                if estimated <= 0:
                    return