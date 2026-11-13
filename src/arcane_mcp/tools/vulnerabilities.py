"""Environment-wide vulnerability tools (Arcane /environments/{id}/vulnerabilities)."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from ..client import api, env_path, seg
from ..safety import ToolClass
from ._common import EnvId, Limit, Search, Start, make_tool, page

Severity = Annotated[str | None, Field(description="Comma-separated: CRITICAL,HIGH,MEDIUM,LOW,UNKNOWN")]


def register(mcp: FastMCP) -> None:
    tool = make_tool(mcp, "vulnerabilities")

    @tool(ToolClass.READ)
    async def get_vulnerability_overview(env_id: EnvId = None) -> Any:
        """Environment-wide vulnerability overview (counts by severity, most affected images)."""
        return await api("GET", f"{env_path(env_id)}/vulnerabilities/overview")

    @tool(ToolClass.READ)
    async def get_vulnerability_summary(env_id: EnvId = None) -> Any:
        """Vulnerability counts by severity across all images of the environment."""
        return await api("GET", f"{env_path(env_id)}/vulnerabilities/summary")

    @tool(ToolClass.READ)
    async def list_vulnerabilities(
        env_id: EnvId = None,
        severity: Severity = "CRITICAL,HIGH",
        image_name: str | None = None,
        fix_available: bool | None = None,
        include_ignored: bool = False,
        search: Search = None,
        start: Start = 0,
        limit: Limit = 50,
    ) -> Any:
        """Paginated vulnerabilities across all images (default: CRITICAL and HIGH, not ignored)."""
        return await api(
            "GET",
            f"{env_path(env_id)}/vulnerabilities/all",
            params=page(
                search,
                start,
                limit,
                severity=severity,
                imageName=image_name,
                fixAvailable=fix_available,
                ignored=include_ignored if include_ignored else None,
            ),
        )

    @tool(ToolClass.READ)
    async def get_scanner_status(env_id: EnvId = None) -> Any:
        """Status of the vulnerability scanner (Trivy)."""
        return await api("GET", f"{env_path(env_id)}/vulnerabilities/scanner-status")

    @tool(ToolClass.READ)
    async def list_ignored_vulnerabilities(env_id: EnvId = None, search: Search = None, start: Start = 0, limit: Limit = 50) -> Any:
        """Vulnerabilities marked as ignored."""
        return await api("GET", f"{env_path(env_id)}/vulnerabilities/ignored", params=page(search, start, limit))

    @tool(ToolClass.WRITE)
    async def ignore_vulnerability(
        image_id: str,
        vulnerability_id: Annotated[str, Field(description="e.g. CVE-2023-1234")],
        pkg_name: str,
        installed_version: str | None = None,
        reason: str | None = None,
        env_id: EnvId = None,
    ) -> Any:
        """Mark a vulnerability as ignored (false positive / accepted risk) for one image."""
        body: dict[str, Any] = {"imageId": image_id, "vulnerabilityId": vulnerability_id, "pkgName": pkg_name}
        if installed_version:
            body["installedVersion"] = installed_version
        if reason:
            body["reason"] = reason
        return await api("POST", f"{env_path(env_id)}/vulnerabilities/ignore", json_body=body)

    @tool(ToolClass.WRITE)
    async def unignore_vulnerability(ignore_id: str, env_id: EnvId = None) -> Any:
        """Remove an ignore rule (the vulnerability is reported again)."""
        return await api("DELETE", f"{env_path(env_id)}/vulnerabilities/ignore/{seg(ignore_id)}")
