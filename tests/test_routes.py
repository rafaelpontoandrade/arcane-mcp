"""Every HTTP call the tools make must exist in Arcane's API (v2.17.3 route table).

The route table was extracted from the Arcane v2.17.3 source and cross-checked against the
live /api/openapi.json of a v2.17.3 instance (paths, query params and body fields).
Regenerate it when targeting a new Arcane version.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from .conftest import FakeArcane, call

ROUTES_FILE = Path(__file__).parent / "data" / "arcane-v2.17.3-routes.txt"

DUMMY: dict[str, Any] = {
    "container_id": "c1",
    "image_id": "sha256:abc",
    "image": "nginx",
    "network_id": "n1",
    "name": "vol1",
    "project_id": "p1",
    "project_name": "demo",
    "compose_content": "services:\n  web:\n    image: nginx\n",
    "registry_id": "r1",
    "event_id": "e1",
    "ignore_id": "g1",
    "backup_id": "b1",
    "webhook_id": "w1",
    "activity_id": "a1",
    "environment_id": "0",
    "relative_path": "a.txt",
    "repository": "me/app",
    "term": "nginx",
    "image_ref": "nginx:latest",
    "enabled": True,
    "api_url": "http://a",
    "url": "ghcr.io",
    "token": "tok123456",
    "target_type": "container",
    "action_type": "restart",
    "vulnerability_id": "CVE-1",
    "pkg_name": "x",
    "docker_run_command": "docker run nginx",
    "env_id": "0",
}


def _route_patterns() -> list[tuple[str, re.Pattern[str]]]:
    out = []
    for line in ROUTES_FILE.read_text().splitlines():
        method, path = line.split(" ", 1)
        out.append((method, re.compile("^/api" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$")))
    return out


async def test_all_tool_requests_hit_known_routes(mcp: FastMCP, fake: FakeArcane) -> None:
    fake.on("POST", "/api/environments/0/projects", json_body={"success": True, "data": {"id": "p1"}})
    for suffix in ("pull", "up", "redeploy"):
        for path in (f"/api/environments/0/projects/p1/{suffix}", f"/api/environments/0/images/{suffix}"):
            fake.on("POST", path, text='{"done":true}\n')
    patterns = _route_patterns()
    tools = await mcp.list_tools()
    exercised = 0
    for tool in tools:
        if tool.name == "confirm_operation":
            continue
        props = tool.parameters.get("properties", {})
        kwargs = {k: DUMMY[k] for k in props if k in DUMMY}
        if tool.name == "remove_environment":
            kwargs["env_id"] = "e2"
        missing = [r for r in tool.parameters.get("required", []) if r not in kwargs]
        assert not missing, f"{tool.name}: add dummy values for {missing}"
        result = await call(mcp, tool.name, **kwargs)
        if isinstance(result, dict) and result.get("confirmation_token"):
            await call(mcp, "confirm_operation", token=result["confirmation_token"], operation_hash=result["operation_hash"])
        exercised += 1

    unknown = sorted(
        {f"{r.method} {r.url.path}" for r in fake.calls if not any(m == r.method and p.match(r.url.path) for m, p in patterns)}
    )
    assert unknown == [], f"Requests to routes that do not exist in Arcane v2.17.3: {unknown}"
    assert exercised > 100
