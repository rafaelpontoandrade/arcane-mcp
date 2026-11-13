"""Network tools (Arcane /environments/{id}/networks)."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from ..client import api, env_path, seg
from ..safety import ToolClass, propose
from ._common import EnvId, Limit, Search, Start, make_tool, page, resolve_env

_BUILTIN = {"bridge", "host", "none", "ingress", "docker_gwbridge"}


def register(mcp: FastMCP) -> None:
    tool = make_tool(mcp, "networks")

    @tool(ToolClass.READ)
    async def list_networks(
        env_id: EnvId = None, search: Search = None, start: Start = 0, limit: Limit = 50, in_use: bool | None = None
    ) -> Any:
        """List networks with pagination."""
        return await api("GET", f"{env_path(env_id)}/networks", params=page(search, start, limit, inUse=in_use))

    @tool(ToolClass.READ)
    async def get_network_counts(env_id: EnvId = None) -> Any:
        """Network counts (total, in use, unused)."""
        return await api("GET", f"{env_path(env_id)}/networks/counts")

    @tool(ToolClass.READ)
    async def get_network_topology(env_id: EnvId = None) -> Any:
        """Graph of networks and the containers attached to them."""
        return await api("GET", f"{env_path(env_id)}/networks/topology")

    @tool(ToolClass.READ)
    async def inspect_network(network_id: str, env_id: EnvId = None) -> Any:
        """Details of one network, including connected containers."""
        return await api("GET", f"{env_path(env_id)}/networks/{seg(network_id)}")

    @tool(ToolClass.WRITE)
    async def create_network(
        name: str,
        driver: str = "bridge",
        internal: bool = False,
        attachable: bool = False,
        labels: dict[str, str] | None = None,
        env_id: EnvId = None,
    ) -> Any:
        """Create a network."""
        options: dict[str, Any] = {"driver": driver, "internal": internal, "attachable": attachable}
        if labels:
            options["labels"] = labels
        return await api("POST", f"{env_path(env_id)}/networks", json_body={"name": name, "options": options})

    @tool(ToolClass.WRITE, idempotent=True)
    async def connect_container_to_network(
        network_id: str, container_id: str, aliases: list[str] | None = None, env_id: EnvId = None
    ) -> Any:
        """Attach a container to a network."""
        body: dict[str, Any] = {"containerId": container_id}
        if aliases:
            body["aliases"] = aliases
        return await api("POST", f"{env_path(env_id)}/networks/{seg(network_id)}/connect", json_body=body)

    @tool(ToolClass.WRITE, idempotent=True)
    async def disconnect_container_from_network(network_id: str, container_id: str, force: bool = False, env_id: EnvId = None) -> Any:
        """Detach a container from a network."""
        return await api(
            "POST",
            f"{env_path(env_id)}/networks/{seg(network_id)}/disconnect",
            json_body={"containerId": container_id, "force": force},
        )

    @tool(ToolClass.DESTRUCTIVE)
    async def remove_network(network_id: str, env_id: EnvId = None) -> Any:
        """Propose removing a network. Requires confirm_operation."""
        env = resolve_env(env_id)

        async def run() -> Any:
            return await api("DELETE", f"{env_path(env)}/networks/{seg(network_id)}")

        return propose(
            action="remove_network",
            target=network_id,
            env_id=env,
            args={},
            executor=run,
            summary=f"Remove network {network_id}",
        )

    @tool(ToolClass.DESTRUCTIVE)
    async def prune_networks(env_id: EnvId = None) -> Any:
        """Propose removing all unused custom networks. Returns a preview and a confirmation token."""
        env = resolve_env(env_id)
        data = await api("GET", f"{env_path(env)}/networks", params={"inUse": False, "start": 0, "limit": 500})
        items = data.get("items", []) if isinstance(data, dict) else data or []
        preview = [n.get("name") for n in items if isinstance(n, dict) and n.get("name") not in _BUILTIN]

        async def run() -> Any:
            return await api("POST", f"{env_path(env)}/networks/prune")

        return propose(
            action="prune_networks",
            target="networks:unused",
            env_id=env,
            args={},
            executor=run,
            summary=f"Prune unused networks: about {len(preview)} network(s) would be removed",
            details={"preview": preview[:100], "preview_count": len(preview)},
        )
