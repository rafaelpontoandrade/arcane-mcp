"""Activity (background jobs) and event log tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from ..client import api, env_path, seg
from ..safety import ToolClass, propose
from ._common import EnvId, Limit, Search, Start, make_tool, page, resolve_env


def register(mcp: FastMCP) -> None:
    tool = make_tool(mcp, "activities")

    @tool(ToolClass.READ)
    async def list_activities(
        env_id: EnvId = None,
        status: str | None = None,
        search: Search = None,
        start: Start = 0,
        limit: Limit = 50,
    ) -> Any:
        """Recent activities (pulls, deploys, updates...), newest first."""
        return await api("GET", f"{env_path(env_id)}/activities", params=page(search, start, limit, status=status))

    @tool(ToolClass.READ)
    async def get_activity(activity_id: str, env_id: EnvId = None) -> Any:
        """One activity with its log lines (e.g. to follow a pull/deploy started earlier)."""
        return await api("GET", f"{env_path(env_id)}/activities/{seg(activity_id)}", params={"limit": 200})

    @tool(ToolClass.WRITE)
    async def cancel_activity(activity_id: str, env_id: EnvId = None) -> Any:
        """Cancel a running activity."""
        return await api("POST", f"{env_path(env_id)}/activities/{seg(activity_id)}/cancel")

    @tool(ToolClass.READ)
    async def list_events(severity: str | None = None, search: Search = None, start: Start = 0, limit: Limit = 50) -> Any:
        """Arcane event log (all environments)."""
        return await api("GET", "/api/events", params=page(search, start, limit, severity=severity, order="desc"))

    @tool(ToolClass.READ)
    async def get_environment_events(
        environment_id: str, severity: str | None = None, search: Search = None, start: Start = 0, limit: Limit = 50
    ) -> Any:
        """Event log of one environment."""
        return await api(
            "GET",
            f"/api/events/environment/{seg(environment_id)}",
            params=page(search, start, limit, severity=severity, order="desc"),
        )

    @tool(ToolClass.DESTRUCTIVE)
    async def clear_activity_history(env_id: EnvId = None) -> Any:
        """Propose deleting the finished-activity history of an environment."""
        env = resolve_env(env_id)

        async def run() -> Any:
            return await api("DELETE", f"{env_path(env)}/activities/history")

        return propose(
            action="clear_activity_history",
            target=f"activities:{env}",
            env_id=env,
            args={},
            executor=run,
            summary=f"Delete the activity history of environment {env}",
        )

    @tool(ToolClass.DESTRUCTIVE)
    async def delete_event(event_id: str) -> Any:
        """Propose deleting one event from the event log."""

        async def run() -> Any:
            return await api("DELETE", f"/api/events/{seg(event_id)}")

        return propose(
            action="delete_event",
            target=f"event:{event_id}",
            env_id="-",
            args={},
            executor=run,
            summary=f"Delete event {event_id}",
        )
