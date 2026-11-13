"""Image tools (Arcane /environments/{id}/images and /image-updates)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from ..client import api, api_stream, env_path, seg
from ..safety import ToolClass, propose
from ._common import EnvId, Limit, Search, Start, make_tool, page, resolve_env


async def _unused_images(env: str) -> list[dict[str, Any]]:
    data = await api("GET", f"{env_path(env)}/images", params={"inUse": False, "start": 0, "limit": 500})
    items = data.get("items", []) if isinstance(data, dict) else data or []
    return [
        {"id": str(i.get("id", ""))[:19], "tags": i.get("repoTags") or ["<none>"], "size": i.get("size")}
        for i in items
        if isinstance(i, dict)
    ]


def register(mcp: FastMCP) -> None:
    tool = make_tool(mcp, "images")

    @tool(ToolClass.READ)
    async def list_images(
        env_id: EnvId = None,
        search: Search = None,
        start: Start = 0,
        limit: Limit = 50,
        in_use: Annotated[bool | None, Field(description="Filter by whether a container uses the image.")] = None,
        has_updates: Annotated[bool | None, Field(description="Only images with an available update.")] = None,
    ) -> Any:
        """List images with pagination."""
        return await api("GET", f"{env_path(env_id)}/images", params=page(search, start, limit, inUse=in_use, updates=has_updates))

    @tool(ToolClass.READ)
    async def get_image_counts(env_id: EnvId = None) -> Any:
        """Image counts (total, in use, unused, total size)."""
        return await api("GET", f"{env_path(env_id)}/images/counts")

    @tool(ToolClass.READ)
    async def inspect_image(image_id: str, env_id: EnvId = None) -> Any:
        """Details of one image."""
        return await api("GET", f"{env_path(env_id)}/images/{seg(image_id)}")

    @tool(ToolClass.READ)
    async def get_image_history(image: str, env_id: EnvId = None) -> Any:
        """Layer history of an image (ID or name:tag)."""
        return await api("GET", f"{env_path(env_id)}/images/{seg(image)}/history")

    @tool(ToolClass.READ)
    async def get_image_attestations(image: str, env_id: EnvId = None) -> Any:
        """Supply-chain attestations (SBOM/provenance) of an image."""
        return await api("GET", f"{env_path(env_id)}/images/{seg(image)}/attestations")

    @tool(ToolClass.READ)
    async def search_images(term: str, env_id: EnvId = None) -> Any:
        """Search Docker Hub for images."""
        return await api("GET", f"{env_path(env_id)}/images/search", params={"term": term})

    @tool(ToolClass.WRITE, idempotent=True)
    async def pull_image(
        image: Annotated[str, Field(description="Image name, e.g. 'nginx' or 'ghcr.io/org/app'.")],
        tag: str = "latest",
        env_id: EnvId = None,
    ) -> Any:
        """Pull an image. Waits for the pull to finish and returns a short summary of the output."""
        return await api_stream("POST", f"{env_path(env_id)}/images/pull", json_body={"imageName": image, "tag": tag})

    @tool(ToolClass.WRITE)
    async def tag_image(image: str, repository: str, tag: str = "latest", env_id: EnvId = None) -> Any:
        """Add a tag (repository:tag) to an existing image."""
        return await api("POST", f"{env_path(env_id)}/images/{seg(image)}/tag", json_body={"repository": repository, "tag": tag})

    # ── Vulnerabilities per image ──

    @tool(ToolClass.WRITE, idempotent=True)
    async def scan_image_vulnerabilities(image_id: str, env_id: EnvId = None) -> Any:
        """Start a vulnerability scan (Trivy) of an image."""
        return await api("POST", f"{env_path(env_id)}/images/{seg(image_id)}/vulnerabilities/scan")

    @tool(ToolClass.READ)
    async def get_image_vulnerability_summary(image_id: str, env_id: EnvId = None) -> Any:
        """Vulnerability counts by severity for one image."""
        return await api("GET", f"{env_path(env_id)}/images/{seg(image_id)}/vulnerabilities/summary")

    @tool(ToolClass.READ)
    async def list_image_vulnerabilities(
        image_id: str,
        env_id: EnvId = None,
        severity: Annotated[str | None, Field(description="Comma-separated: CRITICAL,HIGH,MEDIUM,LOW,UNKNOWN")] = None,
        search: Search = None,
        start: Start = 0,
        limit: Limit = 50,
    ) -> Any:
        """Paginated vulnerabilities of one image."""
        return await api(
            "GET",
            f"{env_path(env_id)}/images/{seg(image_id)}/vulnerabilities/list",
            params=page(search, start, limit, severity=severity),
        )

    # ── Updates ──

    @tool(ToolClass.READ)
    async def check_image_update(image_ref: str, env_id: EnvId = None) -> Any:
        """Check whether a newer version exists for an image reference (e.g. 'nginx:latest')."""
        return await api("GET", f"{env_path(env_id)}/image-updates/check", params={"imageRef": image_ref})

    @tool(ToolClass.WRITE, idempotent=True)
    async def check_all_image_updates(env_id: EnvId = None) -> Any:
        """Check every image in the environment for updates (refreshes Arcane's update cache)."""
        return await api("POST", f"{env_path(env_id)}/image-updates/check-all", json_body={})

    @tool(ToolClass.READ)
    async def get_image_update_summary(env_id: EnvId = None) -> Any:
        """Summary of images with pending updates."""
        return await api("GET", f"{env_path(env_id)}/image-updates/summary")

    # ── Destructive ──

    @tool(ToolClass.DESTRUCTIVE)
    async def remove_image(image_id: str, force: bool = False, env_id: EnvId = None) -> Any:
        """Propose removing an image. Requires confirm_operation."""
        env = resolve_env(env_id)

        async def run() -> Any:
            return await api("DELETE", f"{env_path(env)}/images/{seg(image_id)}", params={"force": force})

        return propose(
            action="remove_image",
            target=image_id,
            env_id=env,
            args={"force": force},
            executor=run,
            summary=f"Remove image {image_id}" + (" (force)" if force else ""),
        )

    @tool(ToolClass.DESTRUCTIVE)
    async def prune_images(
        mode: Annotated[
            Literal["dangling", "all"],
            Field(description="'dangling' removes untagged images only; 'all' removes every image not used by a container."),
        ] = "dangling",
        env_id: EnvId = None,
    ) -> Any:
        """Propose pruning images. Returns a preview of unused images plus a confirmation token."""
        env = resolve_env(env_id)
        unused = await _unused_images(env)
        candidates = [i for i in unused if mode == "all" or i["tags"] in (["<none>"], ["<none>:<none>"])]

        async def run() -> Any:
            return await api("POST", f"{env_path(env)}/images/prune", json_body={"mode": mode})

        return propose(
            action="prune_images",
            target=f"images:{mode}",
            env_id=env,
            args={"mode": mode},
            executor=run,
            summary=f"Prune {mode} images: about {len(candidates)} image(s) would be removed",
            details={"preview": candidates[:100], "preview_count": len(candidates)},
        )
