# arcane-mcp

An MCP server that lets AI agents manage Docker through [Arcane](https://getarcane.app). Built with FastMCP.

Targets **Arcane v2.17.x**. Every request the tools make is checked in CI against the Arcane v2.17.3 route
table (`tests/data/arcane-v2.17.3-routes.txt`), which was cross-checked against the live
`/api/openapi.json` of a v2.17.3 instance (paths, query parameters and request body fields).

**126 tools: 123 in 13 modules plus 3 safety tools.** 60 are read-only (what remains with `ARCANE_MCP_READ_ONLY=true`).

## Install

```bash
# uv
uv tool install git+https://github.com/rafaelpontoandrade/arcane-mcp.git

# pip
pip install git+https://github.com/rafaelpontoandrade/arcane-mcp.git
```

## Configuration

Environment variables (a `.env` in the working directory is also read). See `.env.example`.

| Variable | Default | Purpose |
|---|---|---|
| `ARCANE_BASE_URL` | required | e.g. `http://10.0.0.10:3552` |
| `ARCANE_API_KEY` | required | Arcane API key (sent as `X-API-Key`) |
| `ARCANE_DEFAULT_ENV` | `0` | Environment used when a tool call omits `env_id` |
| `ARCANE_TIMEOUT` | `30` | Seconds for normal requests |
| `ARCANE_STREAM_TIMEOUT` | `600` | Seconds for pulls, deploys and redeploys |
| `ARCANE_VERIFY_SSL` | `true` | `true`, `false`, or a path to a CA bundle (self-signed certs) |
| `ARCANE_MCP_READ_ONLY` | `false` | Hide every tool that changes something |
| `ARCANE_MCP_MODULES` | all | Comma-separated subset, e.g. `containers,projects,images` (fewer tools = less context used) |
| `ARCANE_MCP_CONFIRM` | `auto` | How destructive operations are approved, see below |
| `ARCANE_MCP_TOKEN_TTL` | `120` | Seconds a confirmation token stays valid |
| `ARCANE_MCP_AUDIT_LOG` | `~/.arcane-mcp-audit.log` | JSON-lines audit log (created with mode 0600) |

### Claude Code / Claude Desktop

```json
{
  "mcpServers": {
    "arcane": {
      "command": "arcane-mcp",
      "env": {
        "ARCANE_BASE_URL": "http://10.0.0.10:3552",
        "ARCANE_API_KEY": "arc_xxx",
        "ARCANE_MCP_READ_ONLY": "true"
      }
    }
  }
}
```

### OpenCode

```json
{
  "mcp": {
    "arcane": {
      "type": "local",
      "command": ["arcane-mcp"],
      "environment": { "ARCANE_BASE_URL": "http://10.0.0.10:3552", "ARCANE_API_KEY": "arc_xxx" }
    }
  }
}
```

## Safety model

Tools are classified as **read**, **write** or **destructive**, and the class is published as MCP tool
annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`) so clients can ask for approval per tool.

**Destructive tools never act directly.** They return a summary, a preview where possible and a
single-use `confirmation_token` with its `operation_hash`. Only `confirm_operation` executes the stored
request. Examples of what the proposal contains:

- `prune_images`, `prune_networks`, `prune_volumes`, `prune_system`: the actual list of unused resources
  that would be removed (Arcane has no prune dry-run, so the server lists them first).
- `deploy_project`, `update_project`: a parsed compose plan with risk flags (`privileged`,
  `network_mode: host`, Docker socket or `/etc`-style bind mounts, `cap_add`, devices...) and, for updates,
  a unified diff against the current compose file.
- `run_updater`: the result of a real updater dry run.
- `remove_volume`: which containers still use the volume.

**Who approves** depends on `ARCANE_MCP_CONFIRM`:

- `auto` (default): if the MCP client supports elicitation, `confirm_operation` asks the user directly
  (Confirm / Cancel) and the agent cannot approve on its own. Otherwise the token flow alone applies.
- `elicit`: always require the user's answer; refuse if the client cannot ask.
- `token`: never ask; rely on the agent and on the client's own tool approval.

**Audit log.** Each destructive operation writes `operation_proposed`, `operation_approved` (with
`approved_by: user|agent`) and `operation_result`. Secret-looking fields are redacted and results are
truncated. Read it with `read_audit_log`; see open proposals with `list_pending_operations`.

**Secrets stay out of the chat when possible.** `create_registry`, `update_registry`,
`create_environment`, `update_environment` and `trigger_webhook` accept `*_env` parameters naming an
environment variable on the MCP host instead of the secret itself. Identifiers are URL-encoded, so a
crafted ID cannot reach another endpoint.

## Tools

Destructive tools are marked with ⚠ (they need `confirm_operation`).

- **Containers (17)**: list_containers (compact by default), get_container_counts, inspect_container, get_container_processes, get_container_logs, start/stop/restart/pause/unpause_container, set_container_auto_update, commit_container, create_container, ⚠ remove_container, ⚠ kill_container, ⚠ redeploy_container, ⚠ update_container
- **Images (16)**: list_images, get_image_counts, inspect_image, get_image_history, get_image_attestations, search_images, pull_image, tag_image, scan_image_vulnerabilities, get_image_vulnerability_summary, list_image_vulnerabilities, check_image_update, check_all_image_updates, get_image_update_summary, ⚠ remove_image, ⚠ prune_images
- **Projects / Compose (18)**: list_projects, get_project_counts, get_project, get_project_compose, get_project_runtime, get_project_updates, start_project, restart_project, pull_project_images, build_project, archive_project, unarchive_project, ⚠ deploy_project, ⚠ update_project, ⚠ redeploy_project, ⚠ update_project_services, ⚠ stop_project, ⚠ destroy_project
- **Volumes (15)**: list_volumes, get_volume_counts, get_volume_sizes, inspect_volume, get_volume_usage, create_volume, browse_volume, read_volume_file, list_volume_backups, list_backup_files, create_volume_backup, ⚠ restore_volume_backup, ⚠ delete_volume_backup, ⚠ remove_volume, ⚠ prune_volumes
- **Networks (9)**: list_networks, get_network_counts, get_network_topology, inspect_network, create_network, connect_container_to_network, disconnect_container_from_network, ⚠ remove_network, ⚠ prune_networks
- **System (11)**: get_docker_info, get_arcane_version, get_system_health, get_dashboard, check_system_upgrade, convert_docker_run, start_all_containers, start_stopped_containers, ⚠ stop_all_containers, ⚠ trigger_upgrade, ⚠ prune_system
- **Vulnerabilities (7)**: get_vulnerability_overview, get_vulnerability_summary, list_vulnerabilities, get_scanner_status, list_ignored_vulnerabilities, ignore_vulnerability, unignore_vulnerability
- **Activities & events (7)**: list_activities, get_activity, cancel_activity, list_events, get_environment_events, ⚠ clear_activity_history, ⚠ delete_event
- **Environments (6)**: list_environments, get_environment, test_environment, create_environment, update_environment, ⚠ remove_environment
- **Registries (6)**: list_registries, get_registry, test_registry, create_registry, update_registry, ⚠ delete_registry
- **Updater (5)**: get_updater_status, get_updater_history, check_project_updates, preview_updates, ⚠ run_updater
- **Webhooks (5)**: list_webhooks, create_webhook, set_webhook_enabled, ⚠ delete_webhook, ⚠ trigger_webhook
- **Ports (1)**: list_ports
- **Safety (3)**: confirm_operation, list_pending_operations, read_audit_log

List tools are paginated (`start`, `limit`, `search`) and return `{items, pagination}`; Arcane's own default
page size is 20, so agents should look at `pagination.totalItems` before concluding anything.
Long-running calls (`pull_image`, `start_project`, `redeploy_project`, `deploy_project`) wait for the
progress stream to finish and return a short summary instead of thousands of progress lines.

## Development

```bash
uv sync --all-extras
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy src
uv run fastmcp dev src/arcane_mcp/server.py   # inspector
```

When targeting a new Arcane version, regenerate `tests/data/arcane-<version>-routes.txt` from the
instance's `/api/openapi.json` (or from the backend source) and fix whatever `tests/test_routes.py` flags.

## License

MIT
