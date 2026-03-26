"""File system tools: read, write, edit, list."""

import difflib
import mimetypes
from pathlib import Path
from typing import Any
import hashlib

from nanobot.agent.tools.base import Tool
from nanobot.utils.helpers import build_image_content_blocks, detect_image_mime

def _hash_line(line: str) -> str:
    """Generate a short hash for a line of text."""
    return hashlib.sha1(line.encode("utf-8")).hexdigest()[:3]

def _resolve_path(
    path: str,
    workspace: Path | None = None,
    allowed_dir: Path | None = None,
    extra_allowed_dirs: list[Path] | None = None,
) -> Path:
    """Resolve path against workspace (if relative) and enforce directory restriction."""
    p = Path(path).expanduser()
    if not p.is_absolute() and workspace:
        p = workspace / p
    resolved = p.resolve()
    if allowed_dir:
        all_dirs = [allowed_dir] + (extra_allowed_dirs or [])
        if not any(_is_under(resolved, d) for d in all_dirs):
            raise PermissionError(f"Path {path} is outside allowed directory {allowed_dir}")
    return resolved


def _is_under(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory.resolve())
        return True
    except ValueError:
        return False


class _FsTool(Tool):
    """Shared base for filesystem tools — common init and path resolution."""

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
        extra_allowed_dirs: list[Path] | None = None,
    ):
        self._workspace = workspace
        self._allowed_dir = allowed_dir
        self._extra_allowed_dirs = extra_allowed_dirs

    def _resolve(self, path: str) -> Path:
        return _resolve_path(path, self._workspace, self._allowed_dir, self._extra_allowed_dirs)


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------

class ReadFileTool(_FsTool):
    """Read file contents with optional line-based pagination."""

    _MAX_CHARS = 128_000
    _DEFAULT_LIMIT = 2000

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read the contents of a file. Returns numbered:hashed lines. "
            "Use offset and limit to paginate through large files."
            "Each line is prefixed with its line number and a hash of the content for reference in edits."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file path to read"},
                "offset": {
                    "type": "integer",
                    "description": "Line number to start reading from (1-indexed, default 1)",
                    "minimum": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read (default 2000)",
                    "minimum": 1,
                },
            },
            "required": ["path"],
        }

    async def execute(self, path: str | None = None, offset: int = 1, limit: int | None = None, **kwargs: Any) -> Any:
        try:
            if not path:
                return "Error reading file: Unknown path"
            fp = self._resolve(path)
            if not fp.exists():
                return f"Error: File not found: {path}"
            if not fp.is_file():
                return f"Error: Not a file: {path}"

            raw = fp.read_bytes()
            if not raw:
                return f"(Empty file: {path})"

            mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
            if mime and mime.startswith("image/"):
                return build_image_content_blocks(raw, mime, str(fp), f"(Image file: {path})")

            try:
                text_content = raw.decode("utf-8")
            except UnicodeDecodeError:
                return f"Error: Cannot read binary file {path} (MIME: {mime or 'unknown'}). Only UTF-8 text and images are supported."

            all_lines = text_content.splitlines()
            total = len(all_lines)

            if offset < 1:
                offset = 1
            if offset > total:
                return f"Error: offset {offset} is beyond end of file ({total} lines)"

            start = offset - 1
            end = min(start + (limit or self._DEFAULT_LIMIT), total)
            numbered = [f"{start + i + 1}#{_hash_line(line)}| {line}" for i, line in enumerate(all_lines[start:end])]
            result = "\n".join(numbered)

            if len(result) > self._MAX_CHARS:
                trimmed, chars = [], 0
                for line in numbered:
                    chars += len(line) + 1
                    if chars > self._MAX_CHARS:
                        break
                    trimmed.append(line)
                end = start + len(trimmed)
                result = "\n".join(trimmed)

            if end < total:
                result += f"\n\n(Showing lines {offset}-{end} of {total}. Use offset={end + 1} to continue.)"
            else:
                result += f"\n\n(End of file — {total} lines total)"
            return result
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error reading file: {e}"


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------

class WriteFileTool(_FsTool):
    """Write content to a file."""

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write content to a file at the given path. Creates parent directories if needed."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file path to write to"},
                "content": {"type": "string", "description": "The content to write"},
            },
            "required": ["path", "content"],
        }

    async def execute(self, path: str | None = None, content: str | None = None, **kwargs: Any) -> str:
        try:
            if not path:
                raise ValueError("Unknown path")
            if content is None:
                raise ValueError("Unknown content")
            fp = self._resolve(path)
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8")
            return f"Successfully wrote {len(content)} bytes to {fp}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error writing file: {e}"


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------

def _find_match(content: str, old_text: str) -> tuple[str | None, int]:
    """Locate old_text in content: exact first, then line-trimmed sliding window.

    Both inputs should use LF line endings (caller normalises CRLF).
    Returns (matched_fragment, count) or (None, 0).
    """
    if old_text in content:
        return old_text, content.count(old_text)

    old_lines = old_text.splitlines()
    if not old_lines:
        return None, 0
    stripped_old = [l.strip() for l in old_lines]
    content_lines = content.splitlines()

    candidates = []
    for i in range(len(content_lines) - len(stripped_old) + 1):
        window = content_lines[i : i + len(stripped_old)]
        if [l.strip() for l in window] == stripped_old:
            candidates.append("\n".join(window))

    if candidates:
        return candidates[0], len(candidates)
    return None, 0


class EditFileTool(_FsTool):
    """Edit a file by replacing text with fallback matching."""

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Edit a file by replacing old_text with new_text. "
            "Supports minor whitespace/line-ending differences. "
            "Set replace_all=true to replace every occurrence."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file path to edit"},
                "old_text": {"type": "string", "description": "The text to find and replace"},
                "new_text": {"type": "string", "description": "The text to replace with"},
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace all occurrences (default false)",
                },
            },
            "required": ["path", "old_text", "new_text"],
        }

    async def execute(
        self, path: str | None = None, old_text: str | None = None,
        new_text: str | None = None,
        replace_all: bool = False, **kwargs: Any,
    ) -> str:
        try:
            if not path:
                raise ValueError("Unknown path")
            if old_text is None:
                raise ValueError("Unknown old_text")
            if new_text is None:
                raise ValueError("Unknown new_text")

            fp = self._resolve(path)
            if not fp.exists():
                return f"Error: File not found: {path}"

            raw = fp.read_bytes()
            uses_crlf = b"\r\n" in raw
            content = raw.decode("utf-8").replace("\r\n", "\n")
            match, count = _find_match(content, old_text.replace("\r\n", "\n"))

            if match is None:
                return self._not_found_msg(old_text, content, path)
            if count > 1 and not replace_all:
                return (
                    f"Warning: old_text appears {count} times. "
                    "Provide more context to make it unique, or set replace_all=true."
                )

            norm_new = new_text.replace("\r\n", "\n")
            new_content = content.replace(match, norm_new) if replace_all else content.replace(match, norm_new, 1)
            if uses_crlf:
                new_content = new_content.replace("\n", "\r\n")

            fp.write_bytes(new_content.encode("utf-8"))
            return f"Successfully edited {fp}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error editing file: {e}"

    @staticmethod
    def _not_found_msg(old_text: str, content: str, path: str) -> str:
        lines = content.splitlines(keepends=True)
        old_lines = old_text.splitlines(keepends=True)
        window = len(old_lines)

        best_ratio, best_start = 0.0, 0
        for i in range(max(1, len(lines) - window + 1)):
            ratio = difflib.SequenceMatcher(None, old_lines, lines[i : i + window]).ratio()
            if ratio > best_ratio:
                best_ratio, best_start = ratio, i

        if best_ratio > 0.5:
            diff = "\n".join(difflib.unified_diff(
                old_lines, lines[best_start : best_start + window],
                fromfile="old_text (provided)",
                tofile=f"{path} (actual, line {best_start + 1})",
                lineterm="",
            ))
            return f"Error: old_text not found in {path}.\nBest match ({best_ratio:.0%} similar) at line {best_start + 1}:\n{diff}"
        return f"Error: old_text not found in {path}. No similar text found. Verify the file content."

# ---------------------------------------------------------------------------
# line_edit
# ---------------------------------------------------------------------------

class LineEditTool(_FsTool):
    """Edit a file by replacing a line or inserting after of before a line number."""

    @property
    def name(self) -> str:
        return "line_edit"

    @property
    def description(self) -> str:
        return (
            "Edit a file by replacing a line or inserting after of before a specific line."
            "Lines are identified by their line number and a hash of their content to ensure correct matching (e.g. 42#1a2)."
            "If line_id_end is provided, the edit will replace all lines from line_id to line_id_end (inclusive)."
            "This tool should be prefered over edit_file when possible."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file path to edit"},
                "line_id": {"type": "string", "description": "The line id (number#hash) to edit."},
                "content": {
                    "type": "string",
                    "description": "The text to write in the line edit.",
                },
                "action": {
                    "type": "string", 
                    "enum": ["replace","delete", "insert_after", "insert_before"],
                    "description": "The edit action to perform: replace the line, delete the line, insert a new line after or before the specified line. (default: replace)"
                },
                "line_id_end": {
                    "type": "string",
                    "description": "Optional end line id (number#hash) for range edits. If provided, the edit will replace or delete all lines from line_id to line_id_end (inclusive).",
                },
                
            },
            "required": ["path", "line_id","content"],
        }

    async def execute(
        self, path: str, line_id: str, content: str, action: str | None = None, line_id_end: str | None = None,
         **kwargs: Any,
    ) -> str:
        try:
            fp = self._resolve(path)
            if not fp.exists():
                return f"Error: File not found: {path}"

            lines = fp.read_text(encoding="utf-8").splitlines()
            total = len(lines)

            line_number, line_hash = line_id.split(":", 1)
            line_number = int(line_number)
            idx = line_number - 1 if line_number > 0 else total + line_number

            if idx < 0 or idx >= total:
                return f"Error: line number {line_number} is out of range for file with {total} lines"
            
            true_hash = _hash_line(lines[idx])
            if true_hash != line_hash:
                return f"Error: line hash mismatch at line {line_number}. Expected {line_hash}, but actual hash is {true_hash}. Verify the file content and line_id."
            
            if action is None:
                action = "replace"

            if line_id_end is not None:
                line_number_end, line_hash_end = line_id_end.split(":", 1)
                line_number_end = int(line_number_end)
                if action not in  ("replace", "delete"):
                    return f"Error: line_id_end can only be used with 'replace' or 'delete' actions"
                end_idx = line_number_end - 1 if line_number_end > 0 else total + line_number_end
                if end_idx < idx or end_idx >= total:
                    return f"Error: line number {line_number_end} is out of range or before line_number for file with {total} lines"
                true_hash_end = _hash_line(lines[end_idx])
                if true_hash_end != line_hash_end:
                    return f"Error: line hash mismatch at line {line_number_end}. Expected {line_hash_end}, but actual hash is {true_hash_end}. Verify the file content and line_id_end."
                idx, end_idx = min(idx, end_idx), max(idx, end_idx)
                if action == "delete":
                    del lines[idx : end_idx + 1]
                elif action == "replace":
                    lines[idx] = content
                    del lines[idx + 1 : end_idx + 1]
            
            elif action == "replace":
                lines[idx] = content
            elif action == "delete":
                del lines[idx]
            elif action == "insert_after":
                lines.insert(idx + 1, content)
            elif action == "insert_before":
                lines.insert(idx, content)
            else:
                return f"Error: Invalid action {action}. Must be one of replace, delete, insert_after, insert_before."

            fp.write_text("\n".join(lines), encoding="utf-8")
            return f"Successfully edited {fp}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error editing file: {e}"
        

# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------

class ListDirTool(_FsTool):
    """List directory contents with optional recursion."""

    _DEFAULT_MAX = 200
    _IGNORE_DIRS = {
        ".git", "node_modules", "__pycache__", ".venv", "venv",
        "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
        ".ruff_cache", ".coverage", "htmlcov",
    }

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return (
            "List the contents of a directory. "
            "Set recursive=true to explore nested structure. "
            "Common noise directories (.git, node_modules, __pycache__, etc.) are auto-ignored."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The directory path to list"},
                "recursive": {
                    "type": "boolean",
                    "description": "Recursively list all files (default false)",
                },
                "max_entries": {
                    "type": "integer",
                    "description": "Maximum entries to return (default 200)",
                    "minimum": 1,
                },
            },
            "required": ["path"],
        }

    async def execute(
        self, path: str | None = None, recursive: bool = False,
        max_entries: int | None = None, **kwargs: Any,
    ) -> str:
        try:
            if path is None:
                raise ValueError("Unknown path")
            dp = self._resolve(path)
            if not dp.exists():
                return f"Error: Directory not found: {path}"
            if not dp.is_dir():
                return f"Error: Not a directory: {path}"

            cap = max_entries or self._DEFAULT_MAX
            items: list[str] = []
            total = 0

            if recursive:
                for item in sorted(dp.rglob("*")):
                    if any(p in self._IGNORE_DIRS for p in item.parts):
                        continue
                    total += 1
                    if len(items) < cap:
                        rel = item.relative_to(dp)
                        items.append(f"{rel}/" if item.is_dir() else str(rel))
            else:
                for item in sorted(dp.iterdir()):
                    if item.name in self._IGNORE_DIRS:
                        continue
                    total += 1
                    if len(items) < cap:
                        pfx = "📁 " if item.is_dir() else "📄 "
                        items.append(f"{pfx}{item.name}")

            if not items and total == 0:
                return f"Directory {path} is empty"

            result = "\n".join(items)
            if total > cap:
                result += f"\n\n(truncated, showing first {cap} of {total} entries)"
            return result
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error listing directory: {e}"
