# Changelog

## 0.4.0

Rebuilt against Arcane v2.17.3 (the previous code targeted v2.2 and many calls no longer matched the API).

### Fixed
- 11 tools called routes that no longer exist (container logs/stats/exec, volume file browser, project file, `DELETE /projects/{id}`); request bodies were wrong for pull, tag, commit, network connect/create, project create/update, registries, prune and webhooks.
- `operation_hash` returned by `remove_network`, `remove_volume`, `delete_volume_backup` and `delete_volume_file` never matched the stored one, so confirming with the hash always failed.
- The compose "plan" shown before `deploy_project` was always empty (services are indented under `services:`); it now parses YAML and flags risky settings.
- `operation_proposed` was never written to the audit log; the resource-change check never ran (removed); "dry run" returned no preview.
- Errors are raised as `ToolError` (MCP `isError=true`) instead of being returned as successful results.
- List tools silently returned only Arcane's default 20 items; pagination is now explicit.
- Streaming endpoints (pull, up, redeploy) were parsed as a single JSON document.

### Changed
- One HTTP helper (`client.api`, `api_stream`, `api_text`) instead of ~120 copies of try/except; IDs are URL-encoded.
- Tools carry MCP annotations and tags; new `ARCANE_MCP_READ_ONLY`, `ARCANE_MCP_MODULES`, `ARCANE_MCP_CONFIRM` (MCP elicitation), `ARCANE_TIMEOUT`, `ARCANE_STREAM_TIMEOUT`, `ARCANE_VERIFY_SSL`, `ARCANE_DEFAULT_ENV`.
- Removed the per-tool `agent_token` parameter (an agent-to-manager credential, not something an API-key client sends). Secrets can be passed via `*_env` parameters.
- Audit log is created with mode 0600, redacts secret-looking fields and truncates results.
- Real previews: prune tools list what would be removed, `run_updater` includes an updater dry run, `update_project` shows a compose diff, `remove_volume` shows which containers use the volume.

### Renamed / removed tools
- `project_down` → `stop_project`; `remove_project` → `destroy_project` (options `remove_files`, `remove_volumes`); new `start_project`.
- `get_docker_version` → `get_arcane_version`; `update_webhook` → `set_webhook_enabled`; `get_vulnerability_summary_all` → `get_vulnerability_summary`; `list_all_vulnerabilities` → `list_vulnerabilities`; per-image `get_vulnerability_summary` → `get_image_vulnerability_summary`.
- Removed (no REST endpoint in v2.17): `exec_in_container`, `get_container_stats` (both WebSocket-only), `upload_image`, `build_image`, `get_image_export` (binary), `download_volume_backup`, `create_volume_directory`, `upload_to_volume`, `delete_volume_file` (volume writes now use a multipart workspace manifest), `get_project_file`, `classify_tool`, `get_operation_status` (see `list_pending_operations`).
- Added: `get_container_processes`, `get_project_updates`, `get_dashboard`, `convert_docker_run`, `preview_updates`, `check_project_updates`, `get_vulnerability_overview`, `list_backup_files`, `test_environment`, `list_pending_operations`.
