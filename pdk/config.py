"""Per-machine settings: an optional TOML file whose values fill in the PDK environment variables that are unset.

Everything in PDK reads environment variables; this file only spares setting them for every shell, the MCP server
and the hook. Default location ~/.pdk/config.toml, or PDK_CONFIG. Example:

    gateway_url = "http://127.0.0.1:8088"
    ignition_home = "C:/Program Files/Inductive Automation/Ignition"
    api_key = "..."            # or keep it in ~/.pdk/ignition-api-key
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

CONFIG_ENV = "PDK_CONFIG"
DEFAULT_PATH = Path.home() / ".pdk" / "config.toml"
KEYS: dict[str, str] = {
    "gateway_url": "PDK_GATEWAY_URL",
    "ignition_home": "IGNITION_HOME",
    "api_key": "PDK_IGNITION_API_KEY",
    "work_dir": "PDK_WORK_DIR",
    "schemas_dir": "PDK_SCHEMAS_DIR",
    "perspective_version": "PDK_PERSPECTIVE_VERSION",
}


def config_path() -> Path:
    return Path(os.environ[CONFIG_ENV]) if os.environ.get(CONFIG_ENV) else DEFAULT_PATH


def load() -> tuple[Path, dict[str, str], str | None]:
    """The config file, the variables it set (unset ones only), and a problem reading it, if any."""
    path = config_path()
    if not path.is_file():
        return path, {}, None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return path, {}, f"{path} is not valid TOML: {exc}"
    applied: dict[str, str] = {}
    unknown = sorted(set(data) - set(KEYS))
    for key, variable in KEYS.items():
        value = data.get(key)
        if value is None or os.environ.get(variable):
            continue
        os.environ[variable] = str(value)
        applied[variable] = key
    problem = f"{path}: unknown keys {', '.join(unknown)} (known: {', '.join(KEYS)})" if unknown else None
    return path, applied, problem


LOADED = load()
