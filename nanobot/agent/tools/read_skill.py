"""Spawn tool for creating background subagents."""

from typing import TYPE_CHECKING, Any
from pathlib import Path

from nanobot.agent.tools.base import Tool
from nanobot.agent.skills import SkillsLoader


class ReadSkillTool(Tool):
    """Tool to spawn a subagent for background task execution."""

    def __init__(self, workspace: Path):
        self.skills = SkillsLoader(workspace)

    @property
    def name(self) -> str:
        return "read_skill"

    @property
    def description(self) -> str:
        return "Read the contents of a skill markdown file. Use this to access the instructions for a specific skill."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The name of the skill to read",
                },
            },
            "required": ["name"],
        }

    async def execute(self, name: str, **kwargs: Any) -> str:
        """Spawn a subagent to execute the given task."""
        return self.skills.load_skill(name) or f"Error: Skill not found: {name}"
