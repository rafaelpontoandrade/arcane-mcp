"""End-to-end safety flow through the MCP server."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from arcane_mcp.server import create_server

from .conftest import FakeArcane, call, make_settings


async def test_destructive_tool_does_nothing_until_confirmed(mcp: FastMCP, fake: FakeArcane) -> None:
    proposal = await call(mcp, "remove_container", container_id="web-1", force=True)
    assert proposal["status"] == "confirmation_required"
    assert fake.find("DELETE", "/api/environments/0/containers/web-1") == []

    done = await call(mcp, "confirm_operation", token=proposal["confirmation_token"], operation_hash=proposal["operation_hash"])
    assert done["status"] == "done" and done["approved_by"] == "agent"
    [req] = fake.find("DELETE", "/api/environments/0/containers/web-1")
    assert dict(req.url.params) == {"force": "true", "volumes": "false"}


@pytest.mark.parametrize(
    ("tool", "kwargs"),
    [
        ("remove_container", {"container_id": "c1"}),
        ("remove_image", {"image_id": "sha256:abc"}),
        ("remove_network", {"network_id": "n1"}),
        ("remove_volume", {"name": "v1"}),
        ("delete_volume_backup", {"backup_id": "b1"}),
        ("restore_volume_backup", {"name": "v1", "backup_id": "b1"}),
        ("destroy_project", {"project_id": "p1"}),
        ("prune_system", {}),
        ("delete_webhook", {"webhook_id": "w1"}),
    ],
)
async def test_returned_hash_always_matches(mcp: FastMCP, fake: FakeArcane, tool: str, kwargs: dict[str, str]) -> None:
    """Regression: the old code computed the returned hash differently from the stored one for 4 tools."""
    proposal = await call(mcp, tool, **kwargs)
    done = await call(mcp, "confirm_operation", token=proposal["confirmation_token"], operation_hash=proposal["operation_hash"])
    assert done["status"] == "done"


async def test_wrong_hash_is_rejected_and_nothing_runs(mcp: FastMCP, fake: FakeArcane) -> None:
    proposal = await call(mcp, "remove_volume", name="data")
    with pytest.raises(ToolError, match="operation_hash"):
        await call(mcp, "confirm_operation", token=proposal["confirmation_token"], operation_hash="deadbeefdeadbeef")
    assert fake.find("DELETE", "/api/environments/0/volumes/data") == []


async def test_token_cannot_be_reused(mcp: FastMCP, fake: FakeArcane) -> None:
    proposal = await call(mcp, "kill_container", container_id="c1")
    await call(mcp, "confirm_operation", token=proposal["confirmation_token"])
    with pytest.raises(ToolError, match="already used"):
        await call(mcp, "confirm_operation", token=proposal["confirmation_token"])
    assert len(fake.find("POST", "/api/environments/0/containers/c1/kill")) == 1


async def test_failed_execution_is_audited(mcp: FastMCP, fake: FakeArcane, settings: object) -> None:
    fake.on("DELETE", "/api/environments/0/networks/n1", status=409, json_body={"title": "Conflict", "detail": "in use"})
    proposal = await call(mcp, "remove_network", network_id="n1")
    with pytest.raises(ToolError, match="in use"):
        await call(mcp, "confirm_operation", token=proposal["confirmation_token"])
    log = await call(mcp, "read_audit_log", lines=10)
    assert [e["event"] for e in log] == ["operation_proposed", "operation_approved", "operation_result"]
    assert log[-1]["success"] is False


async def test_pending_operations_listed(mcp: FastMCP, fake: FakeArcane) -> None:
    await call(mcp, "stop_all_containers")
    pending = await call(mcp, "list_pending_operations")
    assert pending[0]["action"] == "stop_all_containers"
    assert "executor" not in pending[0]


async def test_read_only_mode_hides_write_tools(tmp_path: Path, fake: FakeArcane) -> None:
    server = create_server(make_settings(tmp_path, read_only=True))
    names = {t.name for t in await server.list_tools()}
    assert "list_containers" in names and "read_audit_log" in names
    for hidden in ("remove_container", "start_container", "confirm_operation", "deploy_project", "create_volume"):
        assert hidden not in names


async def test_module_selection(tmp_path: Path, fake: FakeArcane) -> None:
    server = create_server(make_settings(tmp_path, modules=("containers",)))
    names = {t.name for t in await server.list_tools()}
    assert "list_containers" in names and "confirm_operation" in names
    assert "list_images" not in names


async def test_elicit_mode_requires_capable_client(tmp_path: Path, fake: FakeArcane) -> None:
    server = create_server(make_settings(tmp_path, confirm_mode="elicit"))
    proposal = await call(server, "remove_container", container_id="c1")
    with pytest.raises(ToolError, match="elicitation"):
        await call(server, "confirm_operation", token=proposal["confirmation_token"])
    assert fake.find("DELETE", "/api/environments/0/containers/c1") == []


async def test_elicitation_flow_with_real_client(tmp_path: Path, fake: FakeArcane) -> None:
    """With a client that supports elicitation, the user decides (here: declines, then accepts)."""
    from fastmcp import Client
    from fastmcp.client.elicitation import ElicitResult

    server = create_server(make_settings(tmp_path, confirm_mode="auto"))
    answers = iter(["Cancel", "Confirm"])

    async def handler(message: str, response_type: type, params: object, context: object) -> ElicitResult:
        assert "Remove container c9" in message
        return ElicitResult(action="accept", content={"value": next(answers)})

    async with Client(server, elicitation_handler=handler) as client:
        p1 = json.loads((await client.call_tool("remove_container", {"container_id": "c9"})).content[0].text)
        r1 = json.loads((await client.call_tool("confirm_operation", {"token": p1["confirmation_token"]})).content[0].text)
        assert r1["status"] == "cancelled"
        assert fake.find("DELETE", "/api/environments/0/containers/c9") == []

        p2 = json.loads((await client.call_tool("remove_container", {"container_id": "c9"})).content[0].text)
        r2 = json.loads((await client.call_tool("confirm_operation", {"token": p2["confirmation_token"]})).content[0].text)
        assert r2["status"] == "done" and r2["approved_by"] == "user"
        assert len(fake.find("DELETE", "/api/environments/0/containers/c9")) == 1


async def test_errors_are_flagged_as_mcp_errors(tmp_path: Path, fake: FakeArcane) -> None:
    from fastmcp import Client

    fake.on("GET", "/api/environments/0/containers/nope", status=404, json_body={"title": "Not Found"})
    server = create_server(make_settings(tmp_path))
    async with Client(server) as client:
        result = await client.call_tool("inspect_container", {"container_id": "nope"}, raise_on_error=False)
    assert result.is_error is True
