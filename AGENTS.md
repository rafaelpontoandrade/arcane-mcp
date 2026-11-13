# AGENTS.md — arcane-mcp

MCP server exposing the Arcane (v2.17.x) REST API. FastMCP 3, httpx, Python ≥ 3.11, managed with uv.

## Commands

```bash
uv sync --all-extras
uv run pytest                                   # includes the route-table check
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy src
uv run fastmcp dev src/arcane_mcp/server.py     # inspector
```

## Layout

- `config.py`: `Settings.from_env()`; everything configurable lives here.
- `client.py`: `api()`, `api_stream()` (JSON-lines progress), `api_text()` (log tail), `seg()` (URL-encode one path segment), `env_path()`. All errors become `ToolError`.
- `safety.py`: `ToolClass`, `TokenStore`, `AuditLog`, `propose()`, `analyze_compose()`, `redact()`.
- `tools/_common.py`: `make_tool(mcp, module)` registers a tool with tags `{module, class}` and MCP annotations; shared param types (`EnvId`, `Limit`, ...), `resolve_env()`, `secret_value()`.
- `tools/<module>.py`: one `register(mcp)` per module. `tools/operations.py`: `confirm_operation`, `list_pending_operations`, `read_audit_log`.
- `server.py`: `create_server(settings)` registers the selected modules and applies read-only filtering.

## Writing a tool

```python
tool = make_tool(mcp, "containers")

@tool(ToolClass.READ)
async def inspect_container(container_id: str, env_id: EnvId = None) -> Any:
    """Docstring = tool description the model sees."""
    return await api("GET", f"{env_path(env_id)}/containers/{seg(container_id)}")
```

Destructive tools must not call the API; they build an executor and return `propose(...)`:

```python
@tool(ToolClass.DESTRUCTIVE)
async def remove_network(network_id: str, env_id: EnvId = None) -> Any:
    env = resolve_env(env_id)
    async def run() -> Any:
        return await api("DELETE", f"{env_path(env)}/networks/{seg(network_id)}")
    return propose(action="remove_network", target=network_id, env_id=env, args={}, executor=run,
                   summary=f"Remove network {network_id}")
```

Rules:
- Always wrap identifiers in `seg()`; never interpolate raw user input into paths.
- Use Arcane's field names (check the OpenAPI spec at `<arcane>/api/openapi.json`).
- Prefer previews in `details=` (what will be deleted, diffs) so the user can decide.
- Add every new route to `tests/data/arcane-<version>-routes.txt` only if it really exists; `tests/test_routes.py` calls every tool and fails on unknown routes.
