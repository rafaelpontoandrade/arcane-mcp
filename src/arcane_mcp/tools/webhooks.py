"""Webhook tools (Arcane /environments/{id}/webhooks and the public trigger endpoint)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from ..client import api, env_path, seg
from ..safety import ToolClass, propose
from ._common import EnvId, make_tool, resolve_env, secret_value


def register(mcp: FastMCP) -> None:
    tool = make_tool(mcp, "webhooks")

    @tool(ToolClass.READ)
    async def list_webhooks(env_id: EnvId = None) -> Any:
        """List webhooks (tokens are masked)."""
        return await api("GET", f"{env_path(env_id)}/webhooks")

    @tool(ToolClass.WRITE)
    async def create_webhook(
        name: str,
        target_type: Literal["container", "project", "updater", "gitops"],
        action_type: Literal["update", "start", "stop", "restart", "redeploy", "up", "down", "run", "sync"],
        target_id: Annotated[str, Field(description="Container/project/GitOps sync ID; empty for 'updater'.")] = "",
        env_id: EnvId = None,
    ) -> Any:
        """Create a webhook. The full token is returned ONCE: tell the user to store it, don't repeat it later."""
        body = {"name": name, "targetType": target_type, "actionType": action_type, "targetId": target_id}
        return await api("POST", f"{env_path(env_id)}/webhooks", json_body=body)

    @tool(ToolClass.WRITE, idempotent=True)
    async def set_webhook_enabled(webhook_id: str, enabled: bool, env_id: EnvId = None) -> Any:
        """Enable or disable a webhook."""
        return await api("PATCH", f"{env_path(env_id)}/webhooks/{seg(webhook_id)}", json_body={"enabled": enabled})

    @tool(ToolClass.DESTRUCTIVE)
    async def delete_webhook(webhook_id: str, env_id: EnvId = None) -> Any:
        """Propose deleting a webhook. Requires confirm_operation."""
        env = resolve_env(env_id)

        async def run() -> Any:
            return await api("DELETE", f"{env_path(env)}/webhooks/{seg(webhook_id)}")

        return propose(
            action="delete_webhook",
            target=f"webhook:{webhook_id}",
            env_id=env,
            args={},
            executor=run,
            summary=f"Delete webhook {webhook_id} (anything calling it will start failing)",
        )

    @tool(ToolClass.DESTRUCTIVE)
    async def trigger_webhook(
        token: Annotated[str | None, Field(description="Webhook token. Prefer token_env.")] = None,
        token_env: Annotated[str | None, Field(description="Env var on the MCP server holding the token.")] = None,
    ) -> Any:
        """Propose firing a webhook (runs its configured action in the background)."""
        secret = secret_value(token, token_env, "webhook token")
        if not secret:
            from fastmcp.exceptions import ToolError

            raise ToolError("Pass token or token_env.")

        async def run() -> Any:
            return await api("POST", f"/api/webhooks/trigger/{seg(secret)}")

        return propose(
            action="trigger_webhook",
            target=f"webhook:{secret[:6]}…",
            env_id="-",
            args={"token_prefix": secret[:6]},
            executor=run,
            summary=f"Fire webhook {secret[:6]}… (its action runs in the background)",
        )
