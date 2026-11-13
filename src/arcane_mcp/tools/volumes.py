"""Volume tools (Arcane /environments/{id}/volumes), including backups and read-only file browsing."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from ..client import api, env_path, seg
from ..safety import ToolClass, propose
from ._common import EnvId, Limit, Search, Start, make_tool, page, resolve_env

MAX_TREE_ENTRIES = 500


def register(mcp: FastMCP) -> None:
    tool = make_tool(mcp, "volumes")

    @tool(ToolClass.READ)
    async def list_volumes(
        env_id: EnvId = None,
        search: Search = None,
        start: Start = 0,
        limit: Limit = 50,
        in_use: bool | None = None,
        include_internal: bool = False,
    ) -> Any:
        """List volumes with pagination."""
        return await api(
            "GET",
            f"{env_path(env_id)}/volumes",
            params=page(search, start, limit, inUse=in_use, includeInternal=include_internal),
        )

    @tool(ToolClass.READ)
    async def get_volume_counts(env_id: EnvId = None) -> Any:
        """Volume counts (total, in use, unused)."""
        return await api("GET", f"{env_path(env_id)}/volumes/counts")

    @tool(ToolClass.READ)
    async def get_volume_sizes(env_id: EnvId = None) -> Any:
        """Disk usage of every volume (can be slow on large hosts)."""
        return await api("GET", f"{env_path(env_id)}/volumes/sizes")

    @tool(ToolClass.READ)
    async def inspect_volume(name: str, env_id: EnvId = None) -> Any:
        """Details of one volume."""
        return await api("GET", f"{env_path(env_id)}/volumes/{seg(name)}")

    @tool(ToolClass.READ)
    async def get_volume_usage(name: str, env_id: EnvId = None) -> Any:
        """Which containers use a volume."""
        return await api("GET", f"{env_path(env_id)}/volumes/{seg(name)}/usage")

    @tool(ToolClass.WRITE)
    async def create_volume(
        name: str,
        driver: str = "local",
        labels: dict[str, str] | None = None,
        driver_opts: dict[str, str] | None = None,
        env_id: EnvId = None,
    ) -> Any:
        """Create a volume."""
        body: dict[str, Any] = {"name": name, "driver": driver}
        if labels:
            body["labels"] = labels
        if driver_opts:
            body["driverOpts"] = driver_opts
        return await api("POST", f"{env_path(env_id)}/volumes", json_body=body)

    # ── File browsing (read-only) ──

    @tool(ToolClass.READ)
    async def browse_volume(name: str, env_id: EnvId = None) -> Any:
        """File tree of a volume (first 500 entries)."""
        data = await api("GET", f"{env_path(env_id)}/volumes/{seg(name)}/workspace")
        if isinstance(data, dict) and isinstance(data.get("files"), list) and len(data["files"]) > MAX_TREE_ENTRIES:
            total = len(data["files"])
            data = {**data, "files": data["files"][:MAX_TREE_ENTRIES], "truncated_from": total}
        return data

    @tool(ToolClass.READ)
    async def read_volume_file(
        name: str,
        relative_path: Annotated[str, Field(description="Path relative to the volume root, e.g. 'config/app.yml'.")],
        env_id: EnvId = None,
    ) -> Any:
        """Read a text file inside a volume. May contain secrets: summarize rather than repeat them."""
        return await api("GET", f"{env_path(env_id)}/volumes/{seg(name)}/workspace/file", params={"relativePath": relative_path})

    # ── Backups ──

    @tool(ToolClass.READ)
    async def list_volume_backups(name: str, env_id: EnvId = None, search: Search = None, start: Start = 0, limit: Limit = 50) -> Any:
        """Backups of a volume."""
        return await api("GET", f"{env_path(env_id)}/volumes/{seg(name)}/backups", params=page(search, start, limit))

    @tool(ToolClass.READ)
    async def list_backup_files(backup_id: str, env_id: EnvId = None) -> Any:
        """Files contained in a volume backup."""
        return await api("GET", f"{env_path(env_id)}/volumes/backups/{seg(backup_id)}/files")

    @tool(ToolClass.WRITE)
    async def create_volume_backup(name: str, env_id: EnvId = None) -> Any:
        """Back up a volume to Arcane's default backup destination."""
        return await api("POST", f"{env_path(env_id)}/volumes/{seg(name)}/backups")

    @tool(ToolClass.DESTRUCTIVE)
    async def restore_volume_backup(name: str, backup_id: str, env_id: EnvId = None) -> Any:
        """Propose restoring a backup over a volume (current contents are replaced). Requires confirm_operation."""
        env = resolve_env(env_id)

        async def run() -> Any:
            return await api("POST", f"{env_path(env)}/volumes/{seg(name)}/backups/{seg(backup_id)}/restore")

        return propose(
            action="restore_volume_backup",
            target=f"volume:{name}",
            env_id=env,
            args={"backup_id": backup_id},
            executor=run,
            summary=f"Restore backup {backup_id} into volume {name}, overwriting its current contents",
        )

    @tool(ToolClass.DESTRUCTIVE)
    async def delete_volume_backup(backup_id: str, env_id: EnvId = None) -> Any:
        """Propose deleting a volume backup. Requires confirm_operation."""
        env = resolve_env(env_id)

        async def run() -> Any:
            return await api("DELETE", f"{env_path(env)}/volumes/backups/{seg(backup_id)}")

        return propose(
            action="delete_volume_backup",
            target=f"backup:{backup_id}",
            env_id=env,
            args={},
            executor=run,
            summary=f"Delete volume backup {backup_id}",
        )

    @tool(ToolClass.DESTRUCTIVE)
    async def remove_volume(name: str, force: bool = False, env_id: EnvId = None) -> Any:
        """Propose removing a volume and ALL its data. Requires confirm_operation."""
        env = resolve_env(env_id)
        usage = await api("GET", f"{env_path(env)}/volumes/{seg(name)}/usage")

        async def run() -> Any:
            return await api("DELETE", f"{env_path(env)}/volumes/{seg(name)}", params={"force": force})

        return propose(
            action="remove_volume",
            target=f"volume:{name}",
            env_id=env,
            args={"force": force},
            executor=run,
            summary=f"Permanently delete volume {name} and all of its data" + (" (force)" if force else ""),
            details={"usage": usage},
        )

    @tool(ToolClass.DESTRUCTIVE)
    async def prune_volumes(env_id: EnvId = None) -> Any:
        """Propose pruning unused anonymous volumes (named volumes are kept). Returns a preview and a token."""
        env = resolve_env(env_id)
        data = await api("GET", f"{env_path(env)}/volumes", params={"inUse": False, "start": 0, "limit": 500})
        items = data.get("items", []) if isinstance(data, dict) else data or []
        preview = [v.get("name") for v in items if isinstance(v, dict)]

        async def run() -> Any:
            return await api("POST", f"{env_path(env)}/volumes/prune")

        return propose(
            action="prune_volumes",
            target="volumes:unused",
            env_id=env,
            args={},
            executor=run,
            summary=f"Prune unused anonymous volumes ({len(preview)} unused volume(s) exist; Docker only removes the anonymous ones)",
            details={"unused_volumes": preview[:100], "unused_count": len(preview)},
        )
