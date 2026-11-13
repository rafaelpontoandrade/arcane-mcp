"""Container registry credential tools (Arcane /container-registries)."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from ..client import api, seg
from ..safety import ToolClass, propose
from ._common import Limit, Search, Start, make_tool, page, secret_value

TokenEnv = Annotated[
    str | None,
    Field(description="Name of an env var on the MCP server holding the password/token (keeps it out of the chat)."),
]


def register(mcp: FastMCP) -> None:
    tool = make_tool(mcp, "registries")

    @tool(ToolClass.READ)
    async def list_registries(search: Search = None, start: Start = 0, limit: Limit = 50) -> Any:
        """List configured container registries (credentials are not returned)."""
        return await api("GET", "/api/container-registries", params=page(search, start, limit))

    @tool(ToolClass.READ)
    async def get_registry(registry_id: str) -> Any:
        """Details of one registry."""
        return await api("GET", f"/api/container-registries/{seg(registry_id)}")

    @tool(ToolClass.WRITE, idempotent=True)
    async def test_registry(registry_id: str) -> Any:
        """Test the stored credentials against the registry."""
        return await api("POST", f"/api/container-registries/{seg(registry_id)}/test")

    @tool(ToolClass.WRITE)
    async def create_registry(
        url: str,
        username: str = "",
        token: Annotated[str | None, Field(description="Password or access token. Prefer token_env.")] = None,
        token_env: TokenEnv = None,
        description: str | None = None,
        insecure: bool = False,
    ) -> Any:
        """Add registry credentials (e.g. ghcr.io, Docker Hub)."""
        body: dict[str, Any] = {
            "url": url,
            "username": username,
            "token": secret_value(token, token_env, "registry token") or "",
            "description": description,
            "insecure": insecure,
            "enabled": True,
            "registryType": "generic",
            "repositoryNames": None,
            "awsAccessKeyId": "",
            "awsSecretAccessKey": "",
            "awsRegion": "",
        }
        return await api("POST", "/api/container-registries", json_body=body)

    @tool(ToolClass.WRITE)
    async def update_registry(
        registry_id: str,
        url: str | None = None,
        username: str | None = None,
        token: Annotated[str | None, Field(description="Prefer token_env.")] = None,
        token_env: TokenEnv = None,
        description: str | None = None,
        insecure: bool | None = None,
        enabled: bool | None = None,
    ) -> Any:
        """Update registry settings or credentials (only the fields given; the others are kept)."""
        # Arcane's schema lists every field as required but nullable: null means "keep current value".
        body: dict[str, Any] = {
            "url": url,
            "username": username,
            "token": secret_value(token, token_env, "registry token"),
            "description": description,
            "insecure": insecure,
            "enabled": enabled,
            "registryType": None,
            "repositoryNames": None,
            "awsAccessKeyId": None,
            "awsSecretAccessKey": None,
            "awsRegion": None,
        }
        return await api("PUT", f"/api/container-registries/{seg(registry_id)}", json_body=body)

    @tool(ToolClass.DESTRUCTIVE)
    async def delete_registry(registry_id: str) -> Any:
        """Propose deleting registry credentials. Requires confirm_operation."""

        async def run() -> Any:
            return await api("DELETE", f"/api/container-registries/{seg(registry_id)}")

        return propose(
            action="delete_registry",
            target=f"registry:{registry_id}",
            env_id="-",
            args={},
            executor=run,
            summary=f"Delete registry credentials {registry_id}",
        )
