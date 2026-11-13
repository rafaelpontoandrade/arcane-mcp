"""Safety primitives: tool classes, confirmation tokens, audit log and compose risk analysis.

Destructive tools never call the API directly. They call :func:`propose`, which
stores the exact request (as a closure) behind a short-lived one-time token and
writes an ``operation_proposed`` audit event. ``confirm_operation`` (see
``tools/operations.py``) consumes the token, optionally asks the human through MCP
elicitation, executes the stored request and writes ``operation_approved`` and
``operation_result``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import yaml

from .config import get_settings

logger = logging.getLogger(__name__)

POLICY_VERSION = "2.0.0"
MAX_AUDIT_RESULT_CHARS = 2000


class ToolClass(StrEnum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


# ── Redaction ──

_SECRET_KEY = re.compile(r"(pass(word)?|secret|token|api[_-]?key|credential|auth)", re.I)


def redact(value: Any) -> Any:
    """Recursively replace values of secret-looking keys with '***'."""
    if isinstance(value, dict):
        return {k: ("***" if _SECRET_KEY.search(str(k)) and v not in (None, "") else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def _truncate(value: Any, limit: int = MAX_AUDIT_RESULT_CHARS) -> Any:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return value
    return {"truncated": True, "preview": text[:limit]}


# ── Operation hash ──


def compute_operation_hash(action: str, target: str, env_id: str, args: dict[str, Any] | None) -> str:
    """Deterministic fingerprint of an operation (action + target + env + arguments)."""
    raw = json.dumps(
        {"action": action, "target": target, "env_id": env_id, "args": args or {}},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── Token store ──

Executor = Callable[[], Awaitable[Any]]


@dataclass
class PendingOperation:
    token: str
    action: str
    target: str
    env_id: str
    args: dict[str, Any]
    operation_hash: str
    executor: Executor
    expires: float
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "action": self.action,
            "target": self.target,
            "env_id": self.env_id,
            "operation_hash": self.operation_hash,
            "summary": self.summary,
            "expires_in_seconds": max(0, int(self.expires - time.time())),
        }


class TokenStore:
    """One-time, expiring confirmation tokens bound to a stored request."""

    def __init__(self) -> None:
        self._ops: dict[str, PendingOperation] = {}

    def _purge(self) -> None:
        now = time.time()
        for t in [t for t, op in self._ops.items() if op.expires < now]:
            del self._ops[t]

    def create(
        self,
        *,
        action: str,
        target: str,
        env_id: str,
        args: dict[str, Any] | None,
        executor: Executor,
        summary: str,
        details: dict[str, Any] | None = None,
        ttl: int | None = None,
    ) -> PendingOperation:
        self._purge()
        token = secrets.token_urlsafe(12)
        op = PendingOperation(
            token=token,
            action=action,
            target=target,
            env_id=env_id,
            args=dict(args or {}),
            operation_hash=compute_operation_hash(action, target, env_id, args),
            executor=executor,
            expires=time.time() + (ttl if ttl is not None else get_settings().token_ttl),
            summary=summary,
            details=details or {},
        )
        self._ops[token] = op
        return op

    def consume(self, token: str, operation_hash: str | None = None) -> tuple[PendingOperation | None, str | None]:
        """Pop a token. Returns (operation, None) or (None, reason)."""
        self._purge()
        op = self._ops.get(token)
        if op is None:
            return None, "Token not found, already used, or expired."
        if operation_hash is not None and operation_hash != op.operation_hash:
            # Do not burn the token on a mismatch: the caller may simply have mixed up two tokens.
            return None, "operation_hash does not match the operation bound to this token."
        del self._ops[token]
        return op, None

    def peek(self, token: str) -> PendingOperation | None:
        self._purge()
        return self._ops.get(token)

    def pending(self) -> list[PendingOperation]:
        self._purge()
        return list(self._ops.values())

    def __len__(self) -> int:
        self._purge()
        return len(self._ops)


# ── Audit log ──


class AuditLog:
    """JSON-lines audit log (file mode 0600) with three events per destructive operation."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or get_settings().audit_log_path

    def _write(self, entry: dict[str, Any]) -> None:
        entry = {"timestamp": datetime.now(UTC).isoformat(), "policy_version": POLICY_VERSION, **entry}
        try:
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except OSError as e:
            logger.warning("Failed to write audit log to %s: %s", self.path, e)

    def proposed(self, op: PendingOperation) -> None:
        self._write(
            {
                "event": "operation_proposed",
                "token_id": op.token[:6],
                "action": op.action,
                "target": op.target,
                "env_id": op.env_id,
                "operation_hash": op.operation_hash,
                "args": redact(op.args),
            }
        )

    def approved(self, op: PendingOperation, approved_by: str) -> None:
        self._write(
            {
                "event": "operation_approved",
                "token_id": op.token[:6],
                "action": op.action,
                "target": op.target,
                "env_id": op.env_id,
                "operation_hash": op.operation_hash,
                "approved_by": approved_by,
            }
        )

    def result(self, op: PendingOperation, *, success: bool, result: Any = None, error: str | None = None) -> None:
        entry: dict[str, Any] = {
            "event": "operation_result",
            "token_id": op.token[:6],
            "action": op.action,
            "target": op.target,
            "env_id": op.env_id,
            "operation_hash": op.operation_hash,
            "success": success,
        }
        if result is not None:
            entry["result"] = _truncate(redact(result))
        if error:
            entry["error"] = error[:MAX_AUDIT_RESULT_CHARS]
        self._write(entry)

    def read_lines(self, n: int = 50) -> list[dict[str, Any]]:
        try:
            with open(self.path, encoding="utf-8") as f:
                tail = deque(f, maxlen=max(1, n))
        except OSError:
            return []
        entries: list[dict[str, Any]] = []
        for line in tail:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                entries.append({"raw": line})
        return entries


# ── Singletons ──

_token_store: TokenStore | None = None
_audit_log: AuditLog | None = None


def get_token_store() -> TokenStore:
    global _token_store
    if _token_store is None:
        _token_store = TokenStore()
    return _token_store


def get_audit_log() -> AuditLog:
    global _audit_log
    if _audit_log is None or _audit_log.path != get_settings().audit_log_path:
        _audit_log = AuditLog()
    return _audit_log


def reset_safety_state() -> None:
    global _token_store, _audit_log
    _token_store = None
    _audit_log = None


# ── Proposal helper used by every destructive tool ──


def propose(
    *,
    action: str,
    target: str,
    env_id: str,
    args: dict[str, Any] | None,
    executor: Executor,
    summary: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Register a destructive operation and return what the agent must show/confirm."""
    if get_settings().read_only:
        from fastmcp.exceptions import ToolError

        raise ToolError("Arcane MCP is running in read-only mode (ARCANE_MCP_READ_ONLY=true).")
    op = get_token_store().create(
        action=action, target=target, env_id=env_id, args=args, executor=executor, summary=summary, details=details
    )
    get_audit_log().proposed(op)
    response: dict[str, Any] = {
        "status": "confirmation_required",
        "summary": summary,
        "action": action,
        "target": target,
        "env_id": env_id,
        "confirmation_token": op.token,
        "operation_hash": op.operation_hash,
        "expires_in_seconds": get_settings().token_ttl,
        "next_step": "Show the summary to the user. Only if they agree, call confirm_operation(token, operation_hash).",
    }
    if details:
        response["details"] = details
    return response


# ── Compose risk analysis ──

_SENSITIVE_HOST_PATHS = (
    "/var/run/docker.sock",
    "/run/docker.sock",
    "/etc",
    "/root",
    "/home",
    "/proc",
    "/sys",
    "/dev",
    "/boot",
    "/var/lib/docker",
)


def _bind_source(volume: Any) -> str | None:
    if isinstance(volume, dict):
        if volume.get("type") == "bind":
            return str(volume.get("source", ""))
        return None
    if isinstance(volume, str):
        source = volume.split(":", 1)[0]
        if source.startswith(("/", "./", "../", "~")) or source in (".", ".."):
            return source
    return None


def analyze_compose(compose_content: str) -> dict[str, Any]:
    """Parse a compose file and list services, images, published ports and risky settings."""
    try:
        doc = yaml.safe_load(compose_content)
    except yaml.YAMLError as e:
        return {"valid": False, "error": f"Invalid YAML: {e}"}
    if not isinstance(doc, dict) or not isinstance(doc.get("services"), dict) or not doc["services"]:
        return {"valid": False, "error": "Compose file has no 'services' mapping."}

    services: list[dict[str, Any]] = []
    risks: list[str] = []
    for name, svc in doc["services"].items():
        svc = svc if isinstance(svc, dict) else {}
        info: dict[str, Any] = {"name": name}
        if svc.get("image"):
            info["image"] = svc["image"]
        if svc.get("build"):
            info["build"] = True
            risks.append(f"{name}: builds an image from local context")
        if svc.get("ports"):
            info["ports"] = [p if isinstance(p, str | int) else p.get("published", p) for p in svc["ports"]]
        binds = [b for b in (_bind_source(v) for v in svc.get("volumes") or []) if b]
        if binds:
            info["bind_mounts"] = binds
            for b in binds:
                norm = b.rstrip("/") or "/"
                if norm == "/" or any(norm == p or norm.startswith(p + "/") for p in _SENSITIVE_HOST_PATHS):
                    risks.append(f"{name}: mounts sensitive host path {b}")
        if svc.get("privileged"):
            risks.append(f"{name}: privileged: true (full host access)")
        for key in ("network_mode", "pid", "ipc", "userns_mode"):
            if str(svc.get(key, "")).lower() == "host":
                risks.append(f"{name}: {key}: host")
        if svc.get("cap_add"):
            risks.append(f"{name}: cap_add {svc['cap_add']}")
        if svc.get("devices"):
            risks.append(f"{name}: exposes host devices {svc['devices']}")
        for opt in svc.get("security_opt") or []:
            if "unconfined" in str(opt) or "disable" in str(opt):
                risks.append(f"{name}: security_opt {opt}")
        services.append(info)
    return {
        "valid": True,
        "services": services,
        "risk_flags": risks,
        "risk_level": "high" if risks else "normal",
    }
