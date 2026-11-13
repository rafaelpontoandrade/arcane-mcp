"""Compose project tools (Arcane /environments/{id}/projects)."""

from __future__ import annotations

import difflib
import json
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from ..client import api, api_stream, env_path, seg
from ..safety import ToolClass, analyze_compose, propose
from ._common import EnvId, Limit, Search, Start, make_tool, page, resolve_env

PullPolicy = Literal["missing", "always", "never"]


def _diff(old: str, new: str, name: str, max_lines: int = 200) -> list[str]:
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), f"{name} (current)", f"{name} (proposed)", lineterm=""))
    return lines[:max_lines] + ([f"... {len(lines) - max_lines} more lines"] if len(lines) > max_lines else [])


def register(mcp: FastMCP) -> None:
    tool = make_tool(mcp, "projects")

    @tool(ToolClass.READ)
    async def list_projects(
        env_id: EnvId = None,
        search: Search = None,
        start: Start = 0,
        limit: Limit = 50,
        status: Annotated[str | None, Field(description="Filter by status, e.g. running, stopped, partially running.")] = None,
        archived: bool | None = None,
    ) -> Any:
        """List Compose projects with pagination."""
        return await api("GET", f"{env_path(env_id)}/projects", params=page(search, start, limit, status=status, archived=archived))

    @tool(ToolClass.READ)
    async def get_project_counts(env_id: EnvId = None) -> Any:
        """Project counts by status."""
        return await api("GET", f"{env_path(env_id)}/projects/counts")

    @tool(ToolClass.READ)
    async def get_project(project_id: str, env_id: EnvId = None) -> Any:
        """Details of one project (services, status)."""
        return await api("GET", f"{env_path(env_id)}/projects/{seg(project_id)}")

    @tool(ToolClass.READ)
    async def get_project_compose(project_id: str, env_id: EnvId = None) -> Any:
        """Compose file and .env of a project. The .env may hold secrets: don't repeat them."""
        return await api("GET", f"{env_path(env_id)}/projects/{seg(project_id)}/compose")

    @tool(ToolClass.READ)
    async def get_project_runtime(project_id: str, env_id: EnvId = None) -> Any:
        """Runtime state of a project's services (containers, health, ports)."""
        return await api("GET", f"{env_path(env_id)}/projects/{seg(project_id)}/runtime")

    @tool(ToolClass.READ)
    async def get_project_updates(project_id: str, env_id: EnvId = None) -> Any:
        """Image updates available for a project's services."""
        return await api("GET", f"{env_path(env_id)}/projects/{seg(project_id)}/updates")

    @tool(ToolClass.WRITE)
    async def start_project(project_id: str, pull_policy: PullPolicy = "missing", env_id: EnvId = None) -> Any:
        """`docker compose up -d` for an existing project. Waits and returns a short output summary."""
        return await api_stream("POST", f"{env_path(env_id)}/projects/{seg(project_id)}/up", json_body={"pullPolicy": pull_policy})

    @tool(ToolClass.WRITE)
    async def restart_project(
        project_id: str,
        services: Annotated[list[str] | None, Field(description="Only these services (default: all).")] = None,
        env_id: EnvId = None,
    ) -> Any:
        """Restart a project's containers."""
        params = {"services": ",".join(services)} if services else None
        return await api("POST", f"{env_path(env_id)}/projects/{seg(project_id)}/restart", params=params)

    @tool(ToolClass.WRITE, idempotent=True)
    async def pull_project_images(project_id: str, env_id: EnvId = None) -> Any:
        """Pull the images of a project without recreating containers."""
        return await api_stream("POST", f"{env_path(env_id)}/projects/{seg(project_id)}/pull")

    @tool(ToolClass.WRITE)
    async def build_project(project_id: str, services: list[str] | None = None, env_id: EnvId = None) -> Any:
        """Build the images of a project's services that have a `build:` section."""
        body: dict[str, Any] = {"services": services} if services else {}
        return await api("POST", f"{env_path(env_id)}/projects/{seg(project_id)}/build", json_body=body)

    @tool(ToolClass.WRITE, idempotent=True)
    async def archive_project(project_id: str, env_id: EnvId = None) -> Any:
        """Archive a project (hidden from the default list)."""
        return await api("POST", f"{env_path(env_id)}/projects/{seg(project_id)}/archive")

    @tool(ToolClass.WRITE, idempotent=True)
    async def unarchive_project(project_id: str, env_id: EnvId = None) -> Any:
        """Unarchive a project."""
        return await api("POST", f"{env_path(env_id)}/projects/{seg(project_id)}/unarchive")

    # ── Destructive / high impact ──

    @tool(ToolClass.DESTRUCTIVE)
    async def deploy_project(
        project_name: str,
        compose_content: Annotated[str, Field(description="Full docker-compose.yml content.")],
        env_content: Annotated[str | None, Field(description="Optional .env file content.")] = None,
        start: Annotated[bool, Field(description="Run `compose up` right after creating.")] = True,
        env_id: EnvId = None,
    ) -> Any:
        """Propose creating (and starting) a new Compose project. Returns a risk analysis and a token."""
        env = resolve_env(env_id)
        analysis = analyze_compose(compose_content)
        if not analysis.get("valid"):
            from fastmcp.exceptions import ToolError

            raise ToolError(f"Compose file rejected before sending: {analysis.get('error')}")

        async def run() -> Any:
            project: dict[str, Any] = {"name": project_name, "composeContent": compose_content}
            if env_content:
                project["envContent"] = env_content
            files = {
                "project": (None, json.dumps(project), "application/json"),
                "manifest": (None, json.dumps({"fileChanges": []}), "application/json"),
            }
            created = await api("POST", f"{env_path(env)}/projects", files=files)
            result: dict[str, Any] = {"created": created}
            project_id = created.get("id") if isinstance(created, dict) else None
            if start and project_id:
                result["up"] = await api_stream(
                    "POST", f"{env_path(env)}/projects/{seg(project_id)}/up", json_body={"pullPolicy": "missing"}
                )
            return result

        return propose(
            action="deploy_project",
            target=project_name,
            env_id=env,
            args={"compose": compose_content, "env": bool(env_content), "start": start},
            executor=run,
            summary=f"Create project '{project_name}' with {len(analysis['services'])} service(s)"
            + (" and start it" if start else "")
            + (f". RISK FLAGS: {len(analysis['risk_flags'])}" if analysis["risk_flags"] else ""),
            details={"plan": analysis},
        )

    @tool(ToolClass.DESTRUCTIVE)
    async def update_project(
        project_id: str,
        compose_content: str | None = None,
        env_content: str | None = None,
        new_name: str | None = None,
        env_id: EnvId = None,
    ) -> Any:
        """Propose changing a project's compose/.env. Shows a diff and risk analysis. Does not redeploy."""
        env = resolve_env(env_id)
        current = await api("GET", f"{env_path(env)}/projects/{seg(project_id)}/compose")
        current = current if isinstance(current, dict) else {}
        details: dict[str, Any] = {}
        body: dict[str, Any] = {}
        if compose_content is not None:
            analysis = analyze_compose(compose_content)
            if not analysis.get("valid"):
                from fastmcp.exceptions import ToolError

                raise ToolError(f"Compose file rejected before sending: {analysis.get('error')}")
            body["composeContent"] = compose_content
            details["compose_diff"] = _diff(str(current.get("composeContent") or ""), compose_content, "compose")
            details["plan"] = analysis
        if env_content is not None:
            body["envContent"] = env_content
            details["env_changed"] = True  # values not shown: may contain secrets
        if new_name:
            body["name"] = new_name
        if not body:
            from fastmcp.exceptions import ToolError

            raise ToolError("Nothing to update: pass compose_content, env_content or new_name.")

        async def run() -> Any:
            return await api("PUT", f"{env_path(env)}/projects/{seg(project_id)}", json_body=body)

        return propose(
            action="update_project",
            target=project_id,
            env_id=env,
            args=body,
            executor=run,
            summary=f"Update project {project_id} ({', '.join(body)}). Run redeploy_project afterwards to apply.",
            details=details,
        )

    @tool(ToolClass.DESTRUCTIVE)
    async def redeploy_project(project_id: str, pull_policy: PullPolicy = "missing", env_id: EnvId = None) -> Any:
        """Propose `compose down` + `up` (containers are recreated; brief downtime)."""
        env = resolve_env(env_id)

        async def run() -> Any:
            return await api_stream("POST", f"{env_path(env)}/projects/{seg(project_id)}/redeploy", json_body={"pullPolicy": pull_policy})

        return propose(
            action="redeploy_project",
            target=project_id,
            env_id=env,
            args={"pullPolicy": pull_policy},
            executor=run,
            summary=f"Redeploy project {project_id} (down + up, pull policy '{pull_policy}')",
        )

    @tool(ToolClass.DESTRUCTIVE)
    async def update_project_services(
        project_id: str,
        services: Annotated[list[str] | None, Field(description="Services to update (default: all).")] = None,
        env_id: EnvId = None,
    ) -> Any:
        """Propose pulling newer images and recreating the project's services."""
        env = resolve_env(env_id)
        body = {"services": services} if services else {}

        async def run() -> Any:
            return await api("POST", f"{env_path(env)}/projects/{seg(project_id)}/update-services", json_body=body)

        return propose(
            action="update_project_services",
            target=project_id,
            env_id=env,
            args=body,
            executor=run,
            summary=f"Update services of project {project_id}: {', '.join(services) if services else 'all'}",
        )

    @tool(ToolClass.DESTRUCTIVE)
    async def stop_project(project_id: str, env_id: EnvId = None) -> Any:
        """Propose `docker compose down` (containers removed; named volumes kept)."""
        env = resolve_env(env_id)

        async def run() -> Any:
            return await api("POST", f"{env_path(env)}/projects/{seg(project_id)}/down")

        return propose(
            action="stop_project",
            target=project_id,
            env_id=env,
            args={},
            executor=run,
            summary=f"Bring project {project_id} down (containers stopped and removed, volumes kept)",
        )

    @tool(ToolClass.DESTRUCTIVE)
    async def destroy_project(
        project_id: str,
        remove_files: Annotated[bool, Field(description="Also delete the project directory on disk.")] = False,
        remove_volumes: Annotated[bool, Field(description="Also delete the project's volumes (DATA LOSS).")] = False,
        env_id: EnvId = None,
    ) -> Any:
        """Propose destroying a project (down + delete from Arcane). Requires confirm_operation."""
        env = resolve_env(env_id)
        body = {"removeFiles": remove_files, "removeVolumes": remove_volumes}

        async def run() -> Any:
            return await api("DELETE", f"{env_path(env)}/projects/{seg(project_id)}/destroy", json_body=body)

        return propose(
            action="destroy_project",
            target=project_id,
            env_id=env,
            args=body,
            executor=run,
            summary=f"Destroy project {project_id}"
            + (", delete its files" if remove_files else "")
            + (", DELETE ITS VOLUMES" if remove_volumes else ""),
        )
