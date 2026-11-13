"""Shared fixtures: isolated settings, a recording mock Arcane API and a server instance."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastmcp import FastMCP

from arcane_mcp import client as client_mod
from arcane_mcp.config import ALL_MODULES, Settings, set_settings
from arcane_mcp.safety import reset_safety_state
from arcane_mcp.server import create_server

BASE = "http://arcane.test"


@dataclass
class FakeArcane:
    """Minimal programmable Arcane API backed by httpx.MockTransport."""

    routes: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]] = field(default_factory=dict)
    calls: list[httpx.Request] = field(default_factory=list)

    def on(self, method: str, path: str, *, json_body: Any = None, status: int = 200, text: str | None = None) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            if text is not None:
                return httpx.Response(status, text=text)
            return httpx.Response(status, json=json_body if json_body is not None else {"success": True, "data": {}})

        self.routes[(method.upper(), path)] = handler

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        handler = self.routes.get((request.method, request.url.path))
        if handler is None:
            return httpx.Response(200, json={"success": True, "data": {}})
        return handler(request)

    def find(self, method: str, path: str) -> list[httpx.Request]:
        return [c for c in self.calls if c.method == method.upper() and c.url.path == path]


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "base_url": BASE,
        "api_key": "test-key",
        "audit_log_path": str(tmp_path / "audit.log"),
        "confirm_mode": "token",
        "modules": ALL_MODULES,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def settings(tmp_path: Path) -> Iterator[Settings]:
    s = make_settings(tmp_path)
    set_settings(s)
    reset_safety_state()
    yield s
    set_settings(None)
    reset_safety_state()


@pytest.fixture
def fake(settings: Settings) -> Iterator[FakeArcane]:
    fake = FakeArcane()
    client_mod._client = httpx.AsyncClient(base_url=BASE, transport=httpx.MockTransport(fake.handle), headers={"X-API-Key": "test-key"})
    client_mod._client_settings = settings
    yield fake
    client_mod._client = None
    client_mod._client_settings = None


@pytest.fixture
def mcp(settings: Settings, fake: FakeArcane) -> FastMCP:
    return create_server(settings)


async def call(mcp: FastMCP, name: str, /, **kwargs: Any) -> Any:
    """Call a tool and return its decoded result (raises ToolError on failure)."""
    result = await mcp.call_tool(name, kwargs)
    if result.structured_content is not None:
        data = result.structured_content
        return data.get("result", data) if set(data) == {"result"} else data
    if not result.content:
        return None
    return json.loads(result.content[0].text)  # type: ignore[union-attr]
