import asyncio
from typing import Any
from urllib.parse import urlparse
import shlex
import subprocess

from loguru import logger

from nanobot.agent.tools.base import Tool
from nanobot.sandbox.base import ShellResult
from nanobot.sandbox.hostbox import HostBox

class GOGTool(Tool):
    """Use the gog cli to interact with google workspace services (gmail, calendar, drive, etc...)."""

    name = "gog"
    description = "Interact with Google workspace services (gmail, calendar, drive, tasks, etc...) via the gog cli tool. Use the '--help' flag to see available commands and options."
    parameters = {
        "type": "object",
        "properties": {
            "arguments": {"type": "string", "description": "arguments to the gog command (e.g. '--help', 'gmail list in:inbox --max 10')"},
        },
    }

    def __init__(self, gog_command: str = "gog", authorize_send: bool = False, timeout: int = 60):
        self.gog_command = gog_command
        result = subprocess.run([self.gog_command, "--version"], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to run '{self.gog_command} --version': {result.stderr.strip()}")
        logger.info(f"Initialized GOGTool: {result.stdout.strip()}")

        self.authorize_send = authorize_send
        self.sandbox = HostBox(workspace=".", restrict_to_workspace=False, timeout=timeout)
    
    def _validate_arguments(self, arguments: str) -> tuple[bool, str]:
        """Validate the arguments for the gog command."""
        args = shlex.split(arguments)
        args_lower = [a.lower() for a in args]
        if "auth" in args_lower:
            return None, "Authentication commands are not allowed."
        if "admin" in args_lower:
            return None, "Admin commands are not allowed."
        if "logout" in args_lower or "login" in args_lower:
            return None, "Login/logout commands are not allowed."
        if not self.authorize_send and "send" in args_lower:
            return None, "Sending emails is not authorized."
        return args, ""
        
    async def execute(self, arguments:str, **kwargs: Any) -> str:
        try:
            args, error = self._validate_arguments(arguments)
            if error:
                return error
            
            cmd = shlex.join([self.gog_command] + args)
            logger.info(f"Executing gog command: {cmd}")

            result: ShellResult = await self.sandbox.execute(cmd)

            return str(result)
            
        except Exception as e:
            logger.error("gog cli error: {}", e)
            return f"Error: {e}"