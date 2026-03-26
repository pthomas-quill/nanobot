import asyncio
import os
from typing import Any
from pathlib import Path
import re
import sys

from loguru import logger


from nanobot.sandbox.base import Sandbox, ShellResult


class HostBox(Sandbox):
    """Executes code directly on the host system."""

    def __init__(
        self,
        *args,
        restrict_to_workspace: bool = True,
        path_append: str = "",
        strip_env_vars: list[str] | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.restrict_to_workspace = restrict_to_workspace
        self.allowed_dir = self.workspace if restrict_to_workspace else None
        self.path_append = path_append
        self.strip_env_vars = strip_env_vars or []

    @staticmethod
    def _extract_absolute_paths(command: str) -> list[str]:
        win_paths = re.findall(r"[A-Za-z]:\\[^\s\"'|><;]+", command)   # Windows: C:\...
        posix_paths = re.findall(r"(?:^|[\s|>'\"])(/[^\s\"'>;|<]+)", command) # POSIX: /absolute only
        home_paths = re.findall(r"(?:^|[\s|>'\"])(~[^\s\"'>;|<]*)", command) # POSIX/Windows home shortcut: ~
        return win_paths + posix_paths + home_paths

    def _guard_workspace(self, cmd: str) -> bool:
        if "..\\" in cmd or "../" in cmd:
            return "Error: Command blocked by safety guard (path traversal detected)"

        for raw in self._extract_absolute_paths(cmd):
            try:
                expanded = os.path.expandvars(raw.strip())
                p = Path(expanded).expanduser().resolve()
            except Exception:
                continue
            if p.is_absolute() and self.workspace not in p.parents and p != self.workspace:
                return "Error: Command blocked by safety guard (path outside workspace)"

    async def execute(
        self, command: str, timeout: int, working_dir: str | None = None, **kwargs: Any
    ) -> str:
        if working_dir is None:
            cwd = self.workspace
        else:
            try:
                cwd = self._resolve_path(working_dir, self.workspace, self.allowed_dir)
            except PermissionError:
                return ShellResult(
                    stdout="",
                    stderr="Error: Command blocked by safety guard (working_dir outside workspace)",
                    returncode=-1,
                )
            except Exception as e:
                return ShellResult(
                    stdout="",
                    stderr=f"Error: Invalid working directory: {str(e)}",
                    returncode=-1,
                )

        guard_error = self._guard_command(command)
        if guard_error:
            return ShellResult(stdout="", stderr=guard_error, returncode=-1)
        if self.restrict_to_workspace:
            workspace_guard = self._guard_workspace(command)
            if workspace_guard:
                return ShellResult(stdout="", stderr=workspace_guard, returncode=-1)
        
        env = os.environ.copy()
        if self.path_append:
            env["PATH"] = env.get("PATH", "") + os.pathsep + self.path_append
        for var in self.strip_env_vars:
            env.pop(var, None)

        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            # Wait for the process to fully terminate so pipes are
            # drained and file descriptors are released.
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
            finally:
                if sys.platform != "win32":
                    try:
                        os.waitpid(process.pid, os.WNOHANG)
                    except (ProcessLookupError, ChildProcessError) as e:
                        logger.debug("Process already reaped or not found: {}", e)
            return ShellResult(
                stdout="",
                stderr=f"Error: Command timed out after {timeout} seconds",
                returncode=-1,
            )

        return ShellResult(
            stdout=stdout,
            stderr=stderr,
            returncode=process.returncode,
        )

    def is_isolated(self) -> bool:
        return False
