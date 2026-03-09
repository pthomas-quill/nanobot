import asyncio
import os
from typing import Any
from pathlib import Path
import re

from nanobot.sandbox.base import Sandbox, ShellResult

class ContainerBox(Sandbox):
    """Executes code directly on the host system."""

    def __init__(
        self,
        workspace: Path,
        *args,
        image: str = "python:3.13-slim",
        container_name: str | None = None,
        backend: str = "podman",
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.workspace = Path(workspace).expanduser().resolve().absolute()
        self.image = image
        self.container_name = container_name or f"nanobot_sandbox_{os.getpid()}"
        self.backend = backend.lower()
        assert self.backend in ("docker", "podman"), f"Unsupported container backend {self.backend}"

    def _container_cmd(self, working_dir, command):
        return [
            self.backend,
            "run",
            "--rm",

            # workspace mount
            "-v", f"{str(self.workspace)}:{str(self.workspace)}",
            "-w", f"{str(working_dir)}",

            self.image,
            "bash",
            "-lc",
            command,
        ]

    async def execute(
        self, command: str, working_dir: str | None = None, **kwargs: Any
    ) -> str:
        if working_dir is None:
            cwd = self.workspace
        else:
            cwd = self._resolve_path(working_dir, self.workspace, None)
        
        guard_error = self._guard_command(command)
        if guard_error:
            return ShellResult(stdout="", stderr=guard_error, returncode=-1)

        process = await asyncio.create_subprocess_exec(
            *self._container_cmd(cwd, command),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            # Wait for the process to fully terminate so pipes are
            # drained and file descriptors are released.
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
            return ShellResult(
                stdout="",
                stderr=f"Error: Command timed out after {self.timeout} seconds",
                returncode=-1,
            )

        return ShellResult(
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            returncode=process.returncode,
        )

    def is_isolated(self) -> bool:
        return True
