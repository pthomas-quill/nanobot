import asyncio
import os
from typing import Any

from nanobot.sandbox.sandbox import Sandbox, ShellResult


class HostBox(Sandbox):
    """Executes code directly on the host system."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
    
    async def execute(self, command: str, working_dir: str | None = None, **kwargs: Any) -> str:
        cwd = working_dir or self.working_dir or os.getcwd()
        guard_error = self._guard_command(command, cwd)
        if guard_error:
            return ShellResult(stdout="", stderr=guard_error, returncode=-1)
        
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
                process.communicate(),
                timeout=self.timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            # Wait for the process to fully terminate so pipes are
            # drained and file descriptors are released.
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
            return ShellResult(stdout="", stderr=f"Error: Command timed out after {self.timeout} seconds", returncode=-1)
        
        return ShellResult(stdout=stdout.decode("utf-8", errors="replace"), stderr=stderr.decode("utf-8", errors="replace"), returncode=process.returncode)

    def setup(self) -> None:
        pass

    def teardown(self) -> None:
        pass

    def is_running(self) -> bool:
        return True

