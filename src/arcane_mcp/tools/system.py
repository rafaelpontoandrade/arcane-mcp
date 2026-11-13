"""System tools: Docker info, health, Arcane version/upgrade, bulk container actions and prune."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from ..client import api, env_path
from ..safety import ToolClass, propose
from ._common import EnvId, make_tool, resolve_env


def register(mcp: FastMCP) -> None:
    tool = make_tool(mcp, "system")

    @tool(ToolClass.READ)
    async def get_docker_info(env_id: EnvId = None) -> Any:
        """Docker engine info (version, OS, CPUs, memory, container/image counts)."""
        return await api("GET", f"{env_path(env_id)}/system/docker/info")

    @tool(ToolClass.READ)
    async def get_arcane_version(env_id: EnvId = None) -> Any:
        """Arcane version of the server (and of the given environment's agent, if any)."""
        app = await api("GET", "/api/version")
        env = await api("GET", f"{env_path(env_id)}/version")
        return {"server": app, "environment": env}

    @tool(ToolClass.READ)
    async def get_system_health(env_id: EnvId = None) -> Any:
        """Health of the Arcane server and of the environment's Docker connection."""
        server = await api("GET", "/api/health")
        try:
            await api("HEAD", f"{env_path(env_id)}/system/health")
            env_ok: Any = True
        except ToolError as e:
            env_ok = {"healthy": False, "error": str(e)}
        return {"server": server, "environment_healthy": env_ok}

    @tool(ToolClass.READ)
    async def get_dashboard(env_id: EnvId = None) -> Any:
        """Arcane dashboard summary for an environment (counts, resource usage, alerts)."""
        return await api("GET", f"{env_path(env_id)}/dashboard")

    @tool(ToolClass.READ)
    async def check_system_upgrade(env_id: EnvId = None) -> Any:
        """Check whether a newer Arcane version is available."""
        return await api("GET", f"{env_path(env_id)}/system/upgrade/check")

    @tool(ToolClass.READ)
    async def convert_docker_run(docker_run_command: str, env_id: EnvId = None) -> Any:
        """Convert a `docker run ...` command into an equivalent docker-compose service (no side effects)."""
        return await api("POST", f"{env_path(env_id)}/system/convert", json_body={"dockerRunCommand": docker_run_command})

    @tool(ToolClass.WRITE, idempotent=True)
    async def start_all_containers(env_id: EnvId = None) -> Any:
        """Start every container in the environment."""
        return await api("POST", f"{env_path(env_id)}/system/containers/start-all")

    @tool(ToolClass.WRITE, idempotent=True)
    async def start_stopped_containers(env_id: EnvId = None) -> Any:
        """Start only the containers that are currently stopped."""
        return await api("POST", f"{env_path(env_id)}/system/containers/start-stopped")

    @tool(ToolClass.DESTRUCTIVE)
    async def stop_all_containers(env_id: EnvId = None) -> Any:
        """Propose stopping EVERY container in the environment. Requires confirm_operation."""
        env = resolve_env(env_id)

        async def run() -> Any:
            return await api("POST", f"{env_path(env)}/system/containers/stop-all")

        return propose(
            action="stop_all_containers",
            target=f"environment:{env}",
            env_id=env,
            args={},
            executor=run,
            summary=f"Stop ALL containers in environment {env} (every service goes down)",
        )

    @tool(ToolClass.DESTRUCTIVE)
    async def trigger_upgrade(
        target_version: Annotated[str | None, Field(description="Version to install (default: latest).")] = None,
        env_id: EnvId = None,
    ) -> Any:
        """Propose upgrading Arcane itself (the server restarts). Requires confirm_operation."""
        env = resolve_env(env_id)
        check = await api("GET", f"{env_path(env)}/system/upgrade/check")
        body = {"targetVersion": target_version} if target_version else None

        async def run() -> Any:
            return await api("POST", f"{env_path(env)}/system/upgrade", json_body=body)

        return propose(
            action="trigger_upgrade",
            target=f"arcane:{env}",
            env_id=env,
            args=body or {},
            executor=run,
            summary=f"Upgrade Arcane to {target_version or 'the latest version'} (Arcane will restart)",
            details={"upgrade_check": check},
        )

    @tool(ToolClass.DESTRUCTIVE)
    async def prune_system(
        containers: Literal["none", "stopped"] = "stopped",
        images: Literal["none", "dangling", "all"] = "dangling",
        volumes: Literal["none", "anonymous", "all"] = "none",
        networks: Literal["none", "unused"] = "unused",
        build_cache: Literal["none", "unused", "all"] = "none",
        env_id: EnvId = None,
    ) -> Any:
        """Propose a system prune. Defaults are conservative: stopped containers, dangling images, unused networks.

        volumes='all' deletes every unused named volume (DATA LOSS); images='all' deletes every unused image.
        """
        env = resolve_env(env_id)
        body: dict[str, dict[str, str]] = {
            "containers": {"mode": containers},
            "images": {"mode": images},
            "volumes": {"mode": volumes},
            "networks": {"mode": networks},
            "buildCache": {"mode": build_cache},
        }
        preview: dict[str, Any] = {}
        if containers == "stopped":
            data = await api("GET", f"{env_path(env)}/containers", params={"start": 0, "limit": 500})
            items = data.get("items", []) if isinstance(data, dict) else data or []
            preview["stopped_containers"] = [
                (c.get("names") or [c.get("name") or c.get("id")])[0]
                for c in items
                if isinstance(c, dict) and str(c.get("state", "")).lower() in ("exited", "created", "dead")
            ][:100]
        if volumes != "none":
            data = await api("GET", f"{env_path(env)}/volumes", params={"inUse": False, "start": 0, "limit": 500})
            items = data.get("items", []) if isinstance(data, dict) else data or []
            preview["unused_volumes"] = [v.get("name") for v in items if isinstance(v, dict)][:100]
        if images != "none":
            data = await api("GET", f"{env_path(env)}/images", params={"inUse": False, "start": 0, "limit": 500})
            items = data.get("items", []) if isinstance(data, dict) else data or []
            preview["unused_images"] = [(i.get("repoTags") or ["<none>"])[0] for i in items if isinstance(i, dict)][:100]

        async def run() -> Any:
            return await api("POST", f"{env_path(env)}/system/prune", json_body=body)

        danger = []
        if volumes == "all":
            danger.append("ALL unused named volumes will be deleted")
        if images == "all":
            danger.append("all unused images will be deleted")
        return propose(
            action="prune_system",
            target=f"environment:{env}",
            env_id=env,
            args=body,
            executor=run,
            summary="System prune: "
            + ", ".join(f"{k}={v['mode']}" for k, v in body.items())
            + (f". WARNING: {'; '.join(danger)}" if danger else ""),
            details={"preview": preview},
        )
