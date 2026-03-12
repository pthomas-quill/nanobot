"""Configuration loading utilities."""

import yaml
from pathlib import Path

from nanobot.config.schema import Config


# Global variable to store current config path (for multi-instance support)
_current_config_path: Path | None = None


def set_config_path(path: Path) -> None:
    """Set the current config path (used to derive data directory)."""
    global _current_config_path
    _current_config_path = path


def get_config_path() -> Path:
    """Get the configuration file path."""
    if _current_config_path:
        return _current_config_path
    return Path.home() / ".nanobot" / "config.yaml"


def load_config(config_path: Path | None = None, interpolate_env_vars: bool = True) -> Config:
    """
    Load configuration from file or create default.

    Args:
        config_path: Optional path to config file. Uses default if not provided.

    Returns:
        Loaded configuration object.
    """
    path = config_path or get_config_path()

    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                config_str = f.read()
            if interpolate_env_vars:
                config_str =  _interpolate_env_vars(config_str)
            data = _migrate_config(yaml.safe_load(config_str))
            return Config.model_validate(data)
        except (yaml.YAMLError, ValueError) as e:
            print(f"Warning: Failed to load config from {path}: {e}")
            print("Using default configuration.")

    return Config()


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to file.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump(by_alias=True)

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, indent=2, allow_unicode=False, sort_keys=False)


def _migrate_config(data: dict) -> dict:
    """Migrate old config formats to current."""
    # Move tools.exec.restrictToWorkspace → tools.restrictToWorkspace
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")
    return data

def _interpolate_env_vars(value: str) -> str:
    """Interpolate environment variables in a string."""
    import re
    import os
    pattern = re.compile(r"\$\{([^}]+)\}")
    
    def replacer(match):
        var_name = match.group(1)
        return os.environ.get(var_name, "")
    
    return pattern.sub(replacer, value)    