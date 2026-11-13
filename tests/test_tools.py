"""Behaviour of individual tools against the fake API."""

from __future__ import annotations

import json

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from .conftest import FakeArcane, call

COMPOSE = "services:\n  web:\n    image: nginx\n    privileged: true\n"


async def test_list_containers_is_compact_and_paginated(mcp: FastMCP, fake: FakeArcane) -> None:
    fake.on(
        "GET",
        "/api/environments/0/containers",
        json_body={
            "$schema": "x",
            "success": True,
            "data": [
                {
                    "id": "0123456789abcdef",
                    "names": ["/web"],
                    "image": "nginx",
                    "state": "running",
                    "status": "Up 2 hours",
                    "ports": [{"ip": "0.0.0.0", "privatePort": 80, "publicPort": 8080, "type": "tcp"}],
                    "labels": {"com.docker.compose.project": "site"},
                    "hostConfig": {"big": True},
                }
            ],
            "pagination": {"totalItems": 41},
            "counts": {"runningContainers": 1},
        },
    )
    out = await call(mcp, "list_containers", limit=1)
    assert out["pagination"]["totalItems"] == 41
    assert out["items"] == [
        {
            "id": "0123456789ab",
            "name": "web",
            "image": "nginx",
            "state": "running",
            "status": "Up 2 hours",
            "ports": ["0.0.0.0:8080->80/tcp"],
            "project": "site",
        }
    ]
    assert dict(fake.calls[-1].url.params) == {"start": "0", "limit": "1", "includeInternal": "false"}


async def test_pull_image_sends_arcane_field_names(mcp: FastMCP, fake: FakeArcane) -> None:
    fake.on("POST", "/api/environments/0/images/pull", text='{"status":"Pulled"}\n{"done":true}\n')
    out = await call(mcp, "pull_image", image="nginx", tag="1.27")
    assert out["done"] is True
    assert json.loads(fake.calls[-1].content) == {"imageName": "nginx", "tag": "1.27"}


async def test_prune_images_previews_and_defaults_to_dangling(mcp: FastMCP, fake: FakeArcane) -> None:
    fake.on(
        "GET",
        "/api/environments/0/images",
        json_body={
            "success": True,
            "data": [
                {"id": "sha256:aaa", "repoTags": ["old:1"], "size": 10},
                {"id": "sha256:bbb", "repoTags": None, "size": 20},
            ],
            "pagination": {},
        },
    )
    proposal = await call(mcp, "prune_images")
    assert proposal["details"]["preview_count"] == 1
    await call(mcp, "confirm_operation", token=proposal["confirmation_token"])
    [req] = fake.find("POST", "/api/environments/0/images/prune")
    assert json.loads(req.content) == {"mode": "dangling"}


async def test_deploy_project_creates_multipart_then_up(mcp: FastMCP, fake: FakeArcane) -> None:
    fake.on("POST", "/api/environments/0/projects", json_body={"success": True, "data": {"id": "proj-1", "name": "site"}})
    fake.on("POST", "/api/environments/0/projects/proj-1/up", text='{"done":true}\n')
    proposal = await call(mcp, "deploy_project", project_name="site", compose_content=COMPOSE)
    assert proposal["details"]["plan"]["risk_level"] == "high"
    assert "RISK FLAGS" in proposal["summary"]
    assert fake.find("POST", "/api/environments/0/projects") == []

    done = await call(mcp, "confirm_operation", token=proposal["confirmation_token"])
    assert done["result"]["created"]["id"] == "proj-1"
    [create] = fake.find("POST", "/api/environments/0/projects")
    body = create.content.decode()
    assert 'name="project"' in body and '"composeContent"' in body and 'name="manifest"' in body
    assert len(fake.find("POST", "/api/environments/0/projects/proj-1/up")) == 1


async def test_deploy_project_rejects_invalid_compose(mcp: FastMCP, fake: FakeArcane) -> None:
    with pytest.raises(ToolError, match="services"):
        await call(mcp, "deploy_project", project_name="x", compose_content="image: nginx")


async def test_update_project_shows_diff(mcp: FastMCP, fake: FakeArcane) -> None:
    fake.on(
        "GET",
        "/api/environments/0/projects/p1/compose",
        json_body={"success": True, "data": {"composeContent": "services:\n  web:\n    image: nginx:1.25\n"}},
    )
    proposal = await call(mcp, "update_project", project_id="p1", compose_content="services:\n  web:\n    image: nginx:1.27\n")
    diff = "\n".join(proposal["details"]["compose_diff"])
    assert "-    image: nginx:1.25" in diff and "+    image: nginx:1.27" in diff


async def test_container_logs_tail(mcp: FastMCP, fake: FakeArcane) -> None:
    fake.on("GET", "/api/environments/0/containers/c1/logs/download", text="a\nb\nc\n")
    out = await call(mcp, "get_container_logs", container_id="c1", tail=2)
    assert out["lines"] == ["b", "c"]


async def test_ids_are_url_encoded(mcp: FastMCP, fake: FakeArcane) -> None:
    await call(mcp, "get_image_history", image="ghcr.io/org/app:1")
    assert fake.calls[-1].url.raw_path == b"/api/environments/0/images/ghcr.io%2Forg%2Fapp%3A1/history"


async def test_default_env_from_settings(tmp_path: object, fake: FakeArcane) -> None:
    from pathlib import Path

    from arcane_mcp.server import create_server

    from .conftest import make_settings

    server = create_server(make_settings(Path(str(tmp_path)), default_env="remote-7"))
    await call(server, "get_docker_info")
    assert fake.calls[-1].url.path == "/api/environments/remote-7/system/docker/info"


async def test_secret_from_env_var(mcp: FastMCP, fake: FakeArcane, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GHCR_TOKEN", "s3cr3t")
    await call(mcp, "create_registry", url="ghcr.io", username="me", token_env="GHCR_TOKEN")
    body = json.loads(fake.calls[-1].content)
    assert body["token"] == "s3cr3t" and body["registryType"] == "generic"


async def test_update_registry_sends_nulls_for_unchanged(mcp: FastMCP, fake: FakeArcane) -> None:
    await call(mcp, "update_registry", registry_id="r1", enabled=False)
    body = json.loads(fake.calls[-1].content)
    assert body["enabled"] is False and body["url"] is None and body["token"] is None


async def test_remove_local_environment_refused(mcp: FastMCP, fake: FakeArcane) -> None:
    with pytest.raises(ToolError, match="local environment"):
        await call(mcp, "remove_environment", env_id="0")


async def test_run_updater_includes_real_dry_run(mcp: FastMCP, fake: FakeArcane) -> None:
    fake.on("POST", "/api/environments/0/updater/run", json_body={"success": True, "data": {"checked": 3, "updated": 0}})
    proposal = await call(mcp, "run_updater")
    [dry] = fake.find("POST", "/api/environments/0/updater/run")
    assert json.loads(dry.content)["dryRun"] is True
    assert proposal["details"]["dry_run"]["checked"] == 3
