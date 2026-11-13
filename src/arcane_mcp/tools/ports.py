"""Published port listing."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from ..client import api, env_path
from ..safety import ToolClass
from ._common import EnvId, Limit, Search, Start, make_tool, page


def register(mcp: FastMCP) -> None:
    tool = make_tool(mcp, "ports")

    @tool(ToolClass.READ)
    async def list_ports(env_id: EnvId = None, search: Search = None, start: Start = 0, limit: Limit = 100) -> Any:
        """Host ports published by containers (useful to find conflicts before deploying)."""
        return await api("GET", f"{env_path(env_id)}/ports", params=page(search, start, limit))
