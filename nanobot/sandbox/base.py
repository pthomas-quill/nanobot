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
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        **kwargs,
    ):
        self.timeout = timeout
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
    
    def _guard_command(self, command: str) -> str | None:
        """Best-effort safety guard for potentially destructive commands."""
        cmd = command.strip()
        lower = cmd.lower()

        for pattern in self.deny_patterns:
            if re.search(pattern, lower):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"

        if self.allow_patterns:
            if not any(re.search(p, lower) for p in self.allow_patterns):
                return "Error: Command blocked by safety guard (not in allowlist)"

        return None
    
    @staticmethod
    def _resolve_path(
        path: str, workspace: Path | None = None, allowed_dir: Path | None = None
    ) -> Path:
        """Resolve path against workspace (if relative) and enforce directory restriction."""
        p = Path(path).expanduser()
        if not p.is_absolute() and workspace:
            p = workspace / p
        resolved = p.resolve()
        if allowed_dir:
            try:
                resolved.relative_to(allowed_dir.resolve())
            except ValueError:
                raise PermissionError(f"Path {path} is outside allowed directory {allowed_dir}")
        return resolved


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
    def is_isolated(self) -> bool:
        """Check if the sandbox provides isolation from the host system.
        
        Returns:
            True if sandbox is isolated, False otherwise
        """
        pass