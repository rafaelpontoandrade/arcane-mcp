"""Container tools (Arcane /environments/{id}/containers)."""

from __future__ import annotations

import shlex
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from ..client import api, api_text, env_path, seg
from ..safety import ToolClass, propose
from ._common import EnvId, Limit, Search, Start, make_tool, page, resolve_env


def _compact(c: Any) -> Any:
    if not isinstance(c, dict):
        return c
    ports = [
        f"{p.get('ip', '')}:{p['publicPort']}->{p.get('privatePort')}/{p.get('type', 'tcp')}".lstrip(":")
        for p in c.get("ports") or []
        if isinstance(p, dict) and p.get("publicPort")
    ]
    labels = c.get("labels") or {}
    return {
        "id": str(c.get("id", ""))[:12],
        "name": (c.get("names") or [""])[0].lstrip("/"),
        "image": c.get("image"),
        "state": c.get("state"),
        "status": c.get("status"),
        "ports": ports,
        "project": labels.get("com.docker.compose.project"),
    }


def register(mcp: FastMCP) -> None:
    tool = make_tool(mcp, "containers")

    @tool(ToolClass.READ)
    async def list_containers(
        env_id: EnvId = None,
        search: Search = None,
        start: Start = 0,
        limit: Limit = 50,
        include_internal: bool = False,
        verbose: Annotated[bool, Field(description="Return full objects (hostConfig, networkSettings, labels).")] = False,
    ) -> Any:
        """List containers (running and stopped) with pagination. Returns {items, pagination, counts}."""
        data = await api(
            "GET",
            f"{env_path(env_id)}/containers",
            params=page(search, start, limit, includeInternal=include_internal),
        )
        if verbose or not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return data
        return {**data, "items": [_compact(c) for c in data["items"]]}

    @tool(ToolClass.READ)
    async def get_container_counts(env_id: EnvId = None, include_internal: bool = False) -> Any:
        """Container counts by state (running, stopped, total)."""
        return await api("GET", f"{env_path(env_id)}/containers/counts", params={"includeInternal": include_internal})

    @tool(ToolClass.READ)
    async def inspect_container(container_id: str, env_id: EnvId = None) -> Any:
        """Full details of one container. May include environment variables and secrets: do not echo them back."""
        return await api("GET", f"{env_path(env_id)}/containers/{seg(container_id)}")

    @tool(ToolClass.READ)
    async def get_container_processes(container_id: str, env_id: EnvId = None) -> Any:
        """Processes running inside a container (like `docker top`)."""
        return await api("GET", f"{env_path(env_id)}/containers/{seg(container_id)}/processes")

    @tool(ToolClass.READ)
    async def get_container_logs(
        container_id: str,
        tail: Annotated[int, Field(ge=1, le=5000, description="Number of most recent lines to return.")] = 200,
        env_id: EnvId = None,
    ) -> Any:
        """Last N log lines of a container. Logs may contain secrets: summarize, don't repeat them verbatim."""
        return await api_text(f"{env_path(env_id)}/containers/{seg(container_id)}/logs/download", tail=tail)

    @tool(ToolClass.WRITE, idempotent=True)
    async def start_container(container_id: str, env_id: EnvId = None) -> Any:
        """Start a stopped container."""
        return await api("POST", f"{env_path(env_id)}/containers/{seg(container_id)}/start")

    @tool(ToolClass.WRITE, idempotent=True)
    async def stop_container(container_id: str, env_id: EnvId = None) -> Any:
        """Stop a running container (graceful SIGTERM, then SIGKILL after Docker's timeout)."""
        return await api("POST", f"{env_path(env_id)}/containers/{seg(container_id)}/stop")

    @tool(ToolClass.WRITE)
    async def restart_container(container_id: str, env_id: EnvId = None) -> Any:
        """Restart a container."""
        return await api("POST", f"{env_path(env_id)}/containers/{seg(container_id)}/restart")

    @tool(ToolClass.WRITE, idempotent=True)
    async def pause_container(container_id: str, env_id: EnvId = None) -> Any:
        """Pause (freeze) all processes of a container."""
        return await api("POST", f"{env_path(env_id)}/containers/{seg(container_id)}/pause")

    @tool(ToolClass.WRITE, idempotent=True)
    async def unpause_container(container_id: str, env_id: EnvId = None) -> Any:
        """Unpause a paused container."""
        return await api("POST", f"{env_path(env_id)}/containers/{seg(container_id)}/unpause")

    @tool(ToolClass.WRITE, idempotent=True)
    async def set_container_auto_update(container_id: str, enabled: bool, env_id: EnvId = None) -> Any:
        """Enable or disable Arcane auto-update for a container."""
        return await api("PUT", f"{env_path(env_id)}/containers/{seg(container_id)}/auto-update", json_body={"enabled": enabled})

    @tool(ToolClass.WRITE)
    async def commit_container(
        container_id: str,
        repository: str,
        tag: str = "latest",
        comment: str = "",
        env_id: EnvId = None,
    ) -> Any:
        """Commit a container's current filesystem to a new image (repository:tag)."""
        body = {"repository": repository, "tag": tag}
        if comment:
            body["comment"] = comment
        return await api("POST", f"{env_path(env_id)}/containers/{seg(container_id)}/commit", json_body=body)

    @tool(ToolClass.WRITE)
    async def create_container(
        image: str,
        name: str,
        command: Annotated[str | None, Field(description="Command line, split shell-style (quotes respected).")] = None,
        env: Annotated[list[str] | None, Field(description="Environment variables as KEY=VALUE strings.")] = None,
        labels: dict[str, str] | None = None,
        ports: Annotated[dict[str, str] | None, Field(description="Port map container->host, e.g. {'80/tcp': '8080'}.")] = None,
        volumes: Annotated[list[str] | None, Field(description="Volume specs like 'data:/data' or '/host:/ctr:ro'.")] = None,
        networks: list[str] | None = None,
        restart_policy: Annotated[str | None, Field(description="no | always | unless-stopped | on-failure")] = None,
        env_id: EnvId = None,
    ) -> Any:
        """Create a container (not privileged; use a Compose project for advanced settings)."""
        body: dict[str, Any] = {"image": image, "name": name}
        if command:
            body["cmd"] = shlex.split(command)
        if env:
            body["env"] = env
        if labels:
            body["labels"] = labels
        if ports:
            body["ports"] = ports
        if volumes:
            body["volumes"] = volumes
        if networks:
            body["networks"] = networks
        if restart_policy:
            body["restartPolicy"] = restart_policy
        return await api("POST", f"{env_path(env_id)}/containers", json_body=body)

    # ── Destructive: return a confirmation token ──

    @tool(ToolClass.DESTRUCTIVE)
    async def remove_container(container_id: str, force: bool = False, remove_volumes: bool = False, env_id: EnvId = None) -> Any:
        """Propose removing a container. Returns a confirmation token; nothing is deleted until confirm_operation."""
        env = resolve_env(env_id)
        params = {"force": force, "volumes": remove_volumes}

        async def run() -> Any:
            return await api("DELETE", f"{env_path(env)}/containers/{seg(container_id)}", params=params)

        return propose(
            action="remove_container",
            target=container_id,
            env_id=env,
            args=params,
            executor=run,
            summary=f"Remove container {container_id}"
            + (" (force)" if force else "")
            + (" and its anonymous volumes" if remove_volumes else ""),
        )

    @tool(ToolClass.DESTRUCTIVE)
    async def kill_container(container_id: str, signal: str = "SIGKILL", env_id: EnvId = None) -> Any:
        """Propose sending a signal (default SIGKILL) to a container. Requires confirm_operation."""
        env = resolve_env(env_id)

        async def run() -> Any:
            return await api("POST", f"{env_path(env)}/containers/{seg(container_id)}/kill", params={"signal": signal})

        return propose(
            action="kill_container",
            target=container_id,
            env_id=env,
            args={"signal": signal},
            executor=run,
            summary=f"Send {signal} to container {container_id}",
        )

    @tool(ToolClass.DESTRUCTIVE)
    async def redeploy_container(container_id: str, env_id: EnvId = None) -> Any:
        """Propose recreating a container from its image and config (data not in volumes is lost)."""
        env = resolve_env(env_id)

        async def run() -> Any:
            return await api("POST", f"{env_path(env)}/containers/{seg(container_id)}/redeploy")

        return propose(
            action="redeploy_container",
            target=container_id,
            env_id=env,
            args={},
            executor=run,
            summary=f"Recreate container {container_id} (filesystem changes outside volumes are lost)",
        )

    @tool(ToolClass.DESTRUCTIVE)
    async def update_container(container_id: str, env_id: EnvId = None) -> Any:
        """Propose pulling the latest image and recreating the container."""
        env = resolve_env(env_id)

        async def run() -> Any:
            return await api("POST", f"{env_path(env)}/containers/{seg(container_id)}/update")

        return propose(
            action="update_container",
            target=container_id,
            env_id=env,
            args={},
            executor=run,
            summary=f"Update container {container_id} to its latest image (pull + recreate)",
        )
