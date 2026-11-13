"""Runtime configuration, read from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

from dotenv import load_dotenv

ConfirmMode = Literal["auto", "elicit", "token"]

ALL_MODULES = (
    "activities",
    "containers",
    "environments",
    "images",
    "networks",
    "ports",
    "projects",
    "registries",
    "system",
    "updater",
    "volumes",
    "vulnerabilities",
    "webhooks",
)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"{name} must be a boolean (true/false), got {raw!r}")


def _verify() -> bool | str:
    """ARCANE_VERIFY_SSL accepts true/false or a path to a CA bundle."""
    raw = os.environ.get("ARCANE_VERIFY_SSL", "").strip()
    if not raw:
        return True
    if raw.lower() in _TRUE:
        return True
    if raw.lower() in _FALSE:
        return False
    return os.path.expanduser(raw)


@dataclass(frozen=True)
class Settings:
    base_url: str | None
    api_key: str | None
    timeout: float = 30.0
    stream_timeout: float = 600.0
    verify: bool | str = True
    default_env: str = "0"
    read_only: bool = False
    modules: tuple[str, ...] = ALL_MODULES
    confirm_mode: ConfirmMode = "auto"
    token_ttl: int = 120
    audit_log_path: str = field(default_factory=lambda: os.path.expanduser("~/.arcane-mcp-audit.log"))

    @classmethod
    def from_env(cls, *, load_dotenv_file: bool = True) -> Settings:
        if load_dotenv_file:
            load_dotenv()
        modules_raw = os.environ.get("ARCANE_MCP_MODULES", "").strip()
        if modules_raw and modules_raw.lower() != "all":
            modules = tuple(m.strip().lower() for m in modules_raw.split(",") if m.strip())
            unknown = sorted(set(modules) - set(ALL_MODULES))
            if unknown:
                raise ValueError(f"ARCANE_MCP_MODULES has unknown modules {unknown}; valid: {', '.join(ALL_MODULES)}")
        else:
            modules = ALL_MODULES
        confirm = os.environ.get("ARCANE_MCP_CONFIRM", "auto").strip().lower() or "auto"
        if confirm not in ("auto", "elicit", "token"):
            raise ValueError("ARCANE_MCP_CONFIRM must be one of: auto, elicit, token")
        return cls(
            base_url=(os.environ.get("ARCANE_BASE_URL") or "").rstrip("/") or None,
            api_key=os.environ.get("ARCANE_API_KEY") or None,
            timeout=float(os.environ.get("ARCANE_TIMEOUT", "30")),
            stream_timeout=float(os.environ.get("ARCANE_STREAM_TIMEOUT", "600")),
            verify=_verify(),
            default_env=os.environ.get("ARCANE_DEFAULT_ENV", "0").strip() or "0",
            read_only=_bool("ARCANE_MCP_READ_ONLY", False),
            modules=modules,
            confirm_mode=confirm,  # type: ignore[arg-type]
            token_ttl=int(os.environ.get("ARCANE_MCP_TOKEN_TTL", "120")),
            audit_log_path=os.path.expanduser(os.environ.get("ARCANE_MCP_AUDIT_LOG", "~/.arcane-mcp-audit.log")),
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def set_settings(settings: Settings | None) -> None:
    """Override the active settings (used by tests and by create_server)."""
    global _settings
    _settings = settings
