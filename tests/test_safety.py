from __future__ import annotations

import json
import os
import stat
import time
from typing import Any

from arcane_mcp.config import Settings
from arcane_mcp.safety import AuditLog, TokenStore, analyze_compose, redact


async def _noop() -> Any:
    return {"ok": True}


def _store_op(store: TokenStore, **kw: Any) -> Any:
    args = {"action": "remove_container", "target": "c1", "env_id": "0", "args": {"force": True}, "executor": _noop, "summary": "s"}
    args.update(kw)
    return store.create(**args)


def test_token_is_single_use(settings: Settings) -> None:
    store = TokenStore()
    op = _store_op(store)
    got, _ = store.consume(op.token, op.operation_hash)
    assert got is op
    again, reason = store.consume(op.token, op.operation_hash)
    assert again is None and reason


def test_hash_mismatch_does_not_burn_token(settings: Settings) -> None:
    store = TokenStore()
    op = _store_op(store)
    bad, reason = store.consume(op.token, "0" * 16)
    assert bad is None and "operation_hash" in str(reason)
    good, _ = store.consume(op.token, op.operation_hash)
    assert good is op


def test_token_expires(settings: Settings) -> None:
    store = TokenStore()
    op = _store_op(store, ttl=0)
    time.sleep(0.01)
    got, reason = store.consume(op.token)
    assert got is None and "expired" in str(reason)


def test_hash_depends_on_arguments(settings: Settings) -> None:
    store = TokenStore()
    a = _store_op(store, args={"force": True})
    b = _store_op(store, args={"force": False})
    c = _store_op(store, target="c2")
    assert len({a.operation_hash, b.operation_hash, c.operation_hash}) == 3


def test_audit_log_permissions_and_redaction(settings: Settings, tmp_path: Any) -> None:
    path = str(tmp_path / "sub" / "audit.log")
    log = AuditLog(path)
    store = TokenStore()
    op = _store_op(store, args={"token": "supersecret", "name": "x"})
    log.proposed(op)
    log.approved(op, "user")
    log.result(op, success=True, result={"password": "p", "big": "x" * 5000})
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600
    raw = open(path).read()
    assert "supersecret" not in raw and '"p"' not in raw
    events = [json.loads(line)["event"] for line in raw.splitlines()]
    assert events == ["operation_proposed", "operation_approved", "operation_result"]
    assert log.read_lines(1)[0]["result"]["truncated"] is True
    assert "token" not in json.loads(raw.splitlines()[0])  # only a short token_id is logged


def test_redact_nested() -> None:
    assert redact({"a": {"apiKey": "k", "x": [{"secret": "s"}]}}) == {"a": {"apiKey": "***", "x": [{"secret": "***"}]}}


def test_analyze_compose_reads_indented_services() -> None:
    plan = analyze_compose(
        """
services:
  web:
    image: nginx:1.27
    ports: ["8080:80"]
  db:
    image: postgres:16
    volumes:
      - dbdata:/var/lib/postgresql/data
volumes:
  dbdata:
"""
    )
    assert plan["valid"] is True
    assert [s["name"] for s in plan["services"]] == ["web", "db"]
    assert plan["services"][0]["image"] == "nginx:1.27"
    assert plan["risk_flags"] == []


def test_analyze_compose_flags_risky_settings() -> None:
    plan = analyze_compose(
        """
services:
  agent:
    image: x
    privileged: true
    network_mode: host
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /:/host:ro
      - ./data:/data
    cap_add: [SYS_ADMIN]
"""
    )
    flags = " ".join(plan["risk_flags"])
    assert plan["risk_level"] == "high"
    for needle in ("privileged", "network_mode: host", "docker.sock", "sensitive host path /", "cap_add"):
        assert needle in flags
    assert "./data" not in flags


def test_analyze_compose_invalid() -> None:
    assert analyze_compose("services: [")["valid"] is False
    assert analyze_compose("version: '3'")["valid"] is False
