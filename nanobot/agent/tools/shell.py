"""Shell execution tool."""

import asyncio
import os
import re
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.sandbox.base import Sandbox, ShellResult


class ExecTool(Tool):
    """Tool to execute shell commands."""

    def __init__(self, sandbox: Sandbox):
        self.sandbox = sandbox

    @property
    def name(self) -> str:
        return "exec"

    @property
    def description(self) -> str:
        return "Execute a shell command and return its output. Use with caution."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute"
                },
                "working_dir": {
                    "type": "string",
                    "description": "Optional working directory for the command"
                }
            },
            "required": ["command"]
        }
    
    async def execute(self, command: str, working_dir: str | None = None, **kwargs: Any) -> str:
        try:
            result: ShellResult = await self.sandbox.execute(command, working_dir)

            stdout = result.stdout.strip()
            stderr = result.stderr.strip()
            returncode = result.returncode

            output_parts = []
            
            max_len = 10000
            if stdout:
                if len(stdout) > max_len:
                    stdout = stdout[:max_len] + f"\n... (truncated, {len(stdout) - max_len} more chars)"
                output_parts.append(stdout)
            
            max_len = 5000
            if stderr:
                if len(stdout) > max_len:
                    stdout = stdout[:max_len] + f"\n... (truncated, {len(stdout) - max_len} more chars)"
                output_parts.append(f"STDERR:\n{stderr}")
            
            if returncode != 0:
                output_parts.append(f"\nExit code: {returncode}")
            
            result = "\n".join(output_parts) if output_parts else "(no output)"
            
            return result
            
        except Exception as e:
            return f"Error executing command: {str(e)}"
