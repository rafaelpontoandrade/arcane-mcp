from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

from arcane_mcp.client import api, api_stream, api_text, seg, unwrap

from .conftest import FakeArcane


def test_unwrap_plain_data() -> None:
    assert unwrap({"$schema": "x", "success": True, "data": {"a": 1}}) == {"a": 1}


def test_unwrap_keeps_pagination() -> None:
    body = {"$schema": "x", "success": True, "data": [1], "pagination": {"totalItems": 7}, "resourceSortSupported": True}
    assert unwrap(body) == {"items": [1], "pagination": {"totalItems": 7}}


def test_unwrap_strips_schema_from_raw_objects() -> None:
    assert unwrap({"$schema": "x", "ID": "abc"}) == {"ID": "abc"}


def test_seg_encodes_traversal_and_slashes() -> None:
    assert seg("../../etc") == "..%2F..%2Fetc"
    assert seg("ghcr.io/org/app:1") == "ghcr.io%2Forg%2Fapp%3A1"
    with pytest.raises(ToolError):
        seg("")


async def test_http_error_becomes_tool_error_with_detail(fake: FakeArcane) -> None:
    fake.on(
        "GET",
        "/api/x",
        status=422,
        json_body={"title": "Unprocessable Entity", "detail": "validation failed", "errors": [{"message": "bad", "location": "body.name"}]},
    )
    with pytest.raises(ToolError) as exc:
        await api("GET", "/api/x")
    msg = str(exc.value)
    assert "422" in msg and "validation failed" in msg and "body.name" in msg


async def test_401_hint(fake: FakeArcane) -> None:
    fake.on("GET", "/api/x", status=401, json_body={"title": "Unauthorized"})
    with pytest.raises(ToolError, match="ARCANE_API_KEY"):
        await api("GET", "/api/x")


async def test_bool_params_are_lowercase_and_none_dropped(fake: FakeArcane) -> None:
    await api("GET", "/api/x", params={"a": True, "b": None, "c": 3})
    assert str(fake.calls[-1].url.query, "ascii") == "a=true&c=3"


async def test_stream_summary(fake: FakeArcane) -> None:
    lines = [
        '{"type":"activity","activityId":"act-1"}',
        '{"status":"Pulling from library/nginx","id":"latest"}',
        '{"status":"Downloading","progressDetail":{"current":1,"total":9},"id":"abc"}',
        '{"status":"Download complete","id":"abc"}',
        '{"done":true}',
    ]
    fake.on("POST", "/api/s", text="\n".join(lines) + "\n")
    out = await api_stream("POST", "/api/s")
    assert out["done"] is True
    assert out["activity_id"] == "act-1"
    assert out["output_tail"] == ["latest: Pulling from library/nginx", "abc: Download complete"]


async def test_stream_error_raises(fake: FakeArcane) -> None:
    fake.on("POST", "/api/s", text='{"status":"Pulling"}\n{"error":"manifest unknown"}\n')
    with pytest.raises(ToolError, match="manifest unknown"):
        await api_stream("POST", "/api/s")


async def test_api_text_tail(fake: FakeArcane) -> None:
    fake.on("GET", "/api/logs", text="\n".join(f"line {i}" for i in range(1000)))
    out = await api_text("/api/logs", tail=3)
    assert out["total_lines"] == 1000
    assert out["lines"] == ["line 997", "line 998", "line 999"]
