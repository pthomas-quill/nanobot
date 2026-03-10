"""Shell execution tool."""

import asyncio
import os
import re
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.sandbox.base import Sandbox, ShellResult
from nanobot.sandbox.container import ContainerBox


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


class PackageTool(Tool):
    """Tool to install packages in the container sandbox."""

    def __init__(self, sandbox: ContainerBox):
        self.sandbox = sandbox

    @property
    def name(self) -> str:
        return "manage_packages"

    @property
    def description(self) -> str:
        return "Manage packages in the sandbox environment."

    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "list", "remove"],
                    "description": "Action to perform",
                },
                "manager": {
                    "type": "string",
                    "enum": ["apt", "brew", "npm", "pip"],
                    "description": "Package manager to use."
                },
                "package": {
                    "type": "string",
                    "description": "Package name (or space-separated list of packages) to add or remove."
                },
            },
            "required": ["action", "manager"]
        }

    async def execute(self, action: str, manager: str, package: str | None = None, **kwargs: Any) -> str:
        action = action.lower()
        manager = manager.lower()
        if action == "list":
            try:
                data = self.sandbox.list_packages(manager)
            except Exception as e:
                return f"Error listing packages: {e}"
            return str(data) if data else "No packages installed."
        

        if action not in ("add", "remove"):
            return f"Unknown action: {action}"
        
        if not package:
            return "Error: 'package' parameter is required for add/remove actions."
        packages = package.split()

        if action == "add":
            try:
                self.sandbox.add_packages(manager, packages)
            except Exception as e:
                return f"Error adding packages: {e}"
            return f"Package(s) '{package}' added successfully."
        
        if action == "remove":
            try:
                self.sandbox.remove_packages(manager, packages)
            except Exception as e:
                return f"Error removing packages: {e}"
            return f"Package(s) '{package}' removed successfully."
            