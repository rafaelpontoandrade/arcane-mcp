"""Updater tools: Arcane's image/container auto-update engine."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from ..client import api, env_path, seg
from ..safety import ToolClass, propose
from ._common import EnvId, make_tool, resolve_env


def register(mcp: FastMCP) -> None:
    tool = make_tool(mcp, "updater")

    @tool(ToolClass.READ)
    async def get_updater_status(env_id: EnvId = None) -> Any:
        """Whether the updater is running and when it last ran."""
        return await api("GET", f"{env_path(env_id)}/updater/status")

    @tool(ToolClass.READ)
    async def get_updater_history(limit: Annotated[int, Field(ge=1, le=500)] = 20, env_id: EnvId = None) -> Any:
        """Recent updater runs and what they changed."""
        return await api("GET", f"{env_path(env_id)}/updater/history", params={"limit": limit})

    @tool(ToolClass.WRITE, idempotent=True)
    async def check_project_updates(project_id: str, env_id: EnvId = None) -> Any:
        """Check a project's images for updates (no changes applied)."""
        return await api("POST", f"{env_path(env_id)}/updater/projects/{seg(project_id)}/check")

    @tool(ToolClass.READ)
    async def preview_updates(
        resource_type: Literal["image", "container", "project"] | None = None,
        resource_ids: list[str] | None = None,
        env_id: EnvId = None,
    ) -> Any:
        """Real dry run of the updater: lists what run_updater would change, without changing anything."""
        body: dict[str, Any] = {"dryRun": True}
        if resource_type:
            body["type"] = resource_type
        if resource_ids:
            body["resourceIds"] = resource_ids
        return await api("POST", f"{env_path(env_id)}/updater/run", json_body=body)

    @tool(ToolClass.DESTRUCTIVE)
    async def run_updater(
        resource_type: Literal["image", "container", "project"] | None = None,
        resource_ids: Annotated[list[str] | None, Field(description="Limit to these IDs (default: everything).")] = None,
        force: bool = False,
        respect_opt_out: Annotated[bool, Field(description="Honor opt-out labels like automatic runs do.")] = True,
        env_id: EnvId = None,
    ) -> Any:
        """Propose running the updater (pulls new images and recreates containers). Includes a dry-run preview."""
        env = resolve_env(env_id)
        body: dict[str, Any] = {"forceUpdate": force}
        if resource_type:
            body["type"] = resource_type
        if resource_ids:
            body["resourceIds"] = resource_ids
        params = {"automatic": respect_opt_out}
        preview = await api("POST", f"{env_path(env)}/updater/run", params=params, json_body={**body, "dryRun": True})

        async def run() -> Any:
            return await api("POST", f"{env_path(env)}/updater/run", params=params, json_body=body)

        return propose(
            action="run_updater",
            target=f"updater:{env}",
            env_id=env,
            args={**body, **params},
            executor=run,
            summary="Run the updater"
            + (f" on {resource_type}s" if resource_type else "")
            + (f" {resource_ids}" if resource_ids else "")
            + " (containers will be recreated)",
            details={"dry_run": preview},
        )
