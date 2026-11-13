"""Shared helpers for tool modules: registration with MCP annotations/tags and list params."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, TypeVar

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from ..config import get_settings
from ..safety import ToolClass

F = TypeVar("F", bound=Callable[..., Any])

EnvId = Annotated[
    str | None,
    Field(description="Arcane environment ID. Omit to use ARCANE_DEFAULT_ENV (local Docker is '0')."),
]
Limit = Annotated[int, Field(ge=1, le=500, description="Page size (max 500).")]
Start = Annotated[int, Field(ge=0, description="Offset of the first item.")]
Search = Annotated[str | None, Field(description="Free-text filter.")]


def make_tool(mcp: FastMCP, module: str) -> Callable[..., Callable[[F], F]]:
    """Return a decorator factory: ``tool(ToolClass.READ)`` registers with matching hints and tags.

    Tags: the module name plus the class ('read' / 'write' / 'destructive'), which
    server.py uses for ARCANE_MCP_MODULES and ARCANE_MCP_READ_ONLY.
    """

    def tool(kind: ToolClass, *, idempotent: bool = False, title: str | None = None) -> Callable[[F], F]:
        annotations = ToolAnnotations(
            title=title,
            readOnlyHint=kind is ToolClass.READ,
            destructiveHint=kind is ToolClass.DESTRUCTIVE if kind is not ToolClass.READ else None,
            idempotentHint=True if kind is ToolClass.READ else idempotent,
            openWorldHint=False,
        )

        def decorator(fn: F) -> F:
            mcp.tool(tags={module, kind.value}, annotations=annotations)(fn)
            return fn

        return decorator

    return tool


def page(search: str | None, start: int, limit: int, **extra: Any) -> dict[str, Any]:
    """Standard Arcane pagination/search query params."""
    return {"search": search, "start": start, "limit": limit, **extra}


def resolve_env(env_id: str | None) -> str:
    """Concrete environment ID (falls back to ARCANE_DEFAULT_ENV)."""
    return env_id or get_settings().default_env


def secret_value(value: str | None, env_var: str | None, label: str) -> str | None:
    """Resolve a secret either from a direct value or (preferred) from an env var on the MCP host.

    Reading from an env var keeps the secret out of the model's context and transcript.
    """
    import os

    from fastmcp.exceptions import ToolError

    if env_var:
        resolved = os.environ.get(env_var)
        if not resolved:
            raise ToolError(f"Environment variable {env_var} (for {label}) is not set on the MCP server.")
        return resolved
    return value
