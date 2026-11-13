"""Environment tools (Arcane /environments): local Docker and remote agents."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from ..client import api, seg
from ..safety import ToolClass, propose
from ._common import Limit, Search, Start, make_tool, page, secret_value

TokenEnv = Annotated[
    str | None,
    Field(description="Name of an env var on the MCP server holding the access token (keeps it out of the chat)."),
]


def register(mcp: FastMCP) -> None:
    tool = make_tool(mcp, "environments")

    @tool(ToolClass.READ)
    async def list_environments(search: Search = None, start: Start = 0, limit: Limit = 50) -> Any:
        """List Arcane environments (ID '0' is the local Docker host)."""
        return await api("GET", "/api/environments", params=page(search, start, limit))

    @tool(ToolClass.READ)
    async def get_environment(env_id: str) -> Any:
        """Details of one environment."""
        return await api("GET", f"/api/environments/{seg(env_id)}")

    @tool(ToolClass.WRITE, idempotent=True)
    async def test_environment(env_id: str) -> Any:
        """Test connectivity to an environment's Docker/agent."""
        return await api("POST", f"/api/environments/{seg(env_id)}/test")

    @tool(ToolClass.WRITE)
    async def create_environment(
        api_url: str,
        name: str | None = None,
        access_token: Annotated[str | None, Field(description="Prefer access_token_env.")] = None,
        access_token_env: TokenEnv = None,
        enabled: bool = True,
    ) -> Any:
        """Register a remote environment (Arcane agent)."""
        body: dict[str, Any] = {"apiUrl": api_url, "enabled": enabled}
        if name:
            body["name"] = name
        token = secret_value(access_token, access_token_env, "access token")
        if token:
            body["accessToken"] = token
        return await api("POST", "/api/environments", json_body=body)

    @tool(ToolClass.WRITE)
    async def update_environment(
        env_id: str,
        name: str | None = None,
        api_url: str | None = None,
        enabled: bool | None = None,
        access_token: Annotated[str | None, Field(description="Prefer access_token_env.")] = None,
        access_token_env: TokenEnv = None,
    ) -> Any:
        """Update an environment's name, URL, enabled flag or access token."""
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if api_url is not None:
            body["apiUrl"] = api_url
        if enabled is not None:
            body["enabled"] = enabled
        token = secret_value(access_token, access_token_env, "access token")
        if token:
            body["accessToken"] = token
        return await api("PUT", f"/api/environments/{seg(env_id)}", json_body=body)

    @tool(ToolClass.DESTRUCTIVE)
    async def remove_environment(env_id: str) -> Any:
        """Propose removing an environment from Arcane (containers on it are not touched)."""
        if env_id == "0":
            from fastmcp.exceptions import ToolError

            raise ToolError("Refusing to remove the local environment '0'.")
        info = await api("GET", f"/api/environments/{seg(env_id)}")

        async def run() -> Any:
            return await api("DELETE", f"/api/environments/{seg(env_id)}")

        name = info.get("name") if isinstance(info, dict) else None
        return propose(
            action="remove_environment",
            target=f"environment:{env_id}",
            env_id=env_id,
            args={},
            executor=run,
            summary=f"Remove environment {name or env_id} from Arcane",
        )
