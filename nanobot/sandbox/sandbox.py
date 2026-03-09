from abc import ABC, abstractmethod
import re
from pathlib import Path
from typing import Any

class ShellResult:
    """Represents the result of executing a shell command in the sandbox."""
    
    def __init__(self, stdout: str, stderr: str, returncode: int=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode

class Sandbox(ABC):
    """Abstract base class for shell sandboxes."""

    def __init__(self, 
        timeout: int = 60,
        working_dir: str | None = None,
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = False,
        path_append: str = "",
        strip_env_vars: list[str] | None = None,             
    ):
        self.timeout = timeout
        self.working_dir = working_dir
        self.deny_patterns = deny_patterns or [
            r"\brm\s+-[rf]{1,2}\b",          # rm -r, rm -rf, rm -fr
            r"\bdel\s+/[fq]\b",              # del /f, del /q
            r"\brmdir\s+/s\b",               # rmdir /s
            r"(?:^|[;&|]\s*)format\b",       # format (as standalone command only)
            r"\b(mkfs|diskpart)\b",          # disk operations
            r"\bdd\s+if=",                   # dd
            r">\s*/dev/sd",                  # write to disk
            r"\b(shutdown|reboot|poweroff)\b",  # system power
            r":\(\)\s*\{.*\};\s*:",          # fork bomb
        ]
        self.allow_patterns = allow_patterns or []
        self.restrict_to_workspace = restrict_to_workspace
        self.path_append = path_append
        self.strip_env_vars = strip_env_vars or []
    
    def _guard_command(self, command: str, cwd: str) -> str | None:
        """Best-effort safety guard for potentially destructive commands."""
        cmd = command.strip()
        lower = cmd.lower()

        for pattern in self.deny_patterns:
            if re.search(pattern, lower):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"

        if self.allow_patterns:
            if not any(re.search(p, lower) for p in self.allow_patterns):
                return "Error: Command blocked by safety guard (not in allowlist)"

        if self.restrict_to_workspace:
            if "..\\" in cmd or "../" in cmd:
                return "Error: Command blocked by safety guard (path traversal detected)"

            cwd_path = Path(cwd).resolve()

            for raw in self._extract_absolute_paths(cmd):
                try:
                    p = Path(raw.strip()).resolve()
                except Exception:
                    continue
                if p.is_absolute() and cwd_path not in p.parents and p != cwd_path:
                    return "Error: Command blocked by safety guard (path outside working dir)"

        return None

    @staticmethod
    def _extract_absolute_paths(command: str) -> list[str]:
        win_paths = re.findall(r"[A-Za-z]:\\[^\s\"'|><;]+", command)   # Windows: C:\...
        posix_paths = re.findall(r"(?:^|[\s|>])(/[^\s\"'>]+)", command) # POSIX: /absolute only
        return win_paths + posix_paths


    @abstractmethod
    def execute(self, command: str, working_dir: str | None = None, **kwargs: Any) -> ShellResult:
        """Execute a shell command in the sandbox.
        
        Args:
            command: The shell command to execute
            
        Returns:
            The command output as a string
        """
        pass

    @abstractmethod
    def setup(self) -> None:
        """Initialize and setup the sandbox environment."""
        pass

    @abstractmethod
    def teardown(self) -> None:
        """Clean up and destroy the sandbox environment."""
        pass

    @abstractmethod
    def is_running(self) -> bool:
        """Check if the sandbox is currently running.
        
        Returns:
            True if sandbox is running, False otherwise
        """
        pass