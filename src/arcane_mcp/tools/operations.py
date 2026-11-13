"""Safety tools: confirm a proposed destructive operation, inspect pending ones, read the audit log."""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ClientCapabilities, ElicitationCapability, ToolAnnotations
from pydantic import Field

from ..config import get_settings
from ..safety import get_audit_log, get_token_store

logger = logging.getLogger(__name__)

CONFIRM = "Confirm"
CANCEL = "Cancel"


def _client_can_elicit(ctx: Context | None) -> bool:
    if ctx is None:
        return False
    try:
        return bool(ctx.session.check_client_capability(ClientCapabilities(elicitation=ElicitationCapability())))
    except Exception:  # no active session (e.g. direct call in tests)
        return False


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        tags={"safety", "destructive"},
        annotations=ToolAnnotations(
            title="Confirm destructive operation",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    async def confirm_operation(
        token: Annotated[str, Field(description="confirmation_token returned by the proposing tool.")],
        operation_hash: Annotated[str | None, Field(description="operation_hash returned with the token (recommended).")] = None,
        ctx: Context | None = None,
    ) -> Any:
        """Execute a destructive operation previously proposed by another tool.

        Only call this after the user has seen the proposal summary and agreed. Depending on
        ARCANE_MCP_CONFIRM, the server may also ask the user directly (MCP elicitation).
        Tokens are single-use and expire (default 120 s).
        """
        settings = get_settings()
        if settings.read_only:
            raise ToolError("Arcane MCP is running in read-only mode (ARCANE_MCP_READ_ONLY=true).")
        store = get_token_store()
        audit = get_audit_log()

        op, reason = store.consume(token, operation_hash)
        if op is None:
            raise ToolError(f"Cannot confirm: {reason} Call the proposing tool again to get a fresh token.")

        approved_by = "agent"
        mode = settings.confirm_mode
        can_elicit = _client_can_elicit(ctx)
        if mode == "elicit" and not can_elicit:
            audit.result(op, success=False, error="client does not support elicitation (ARCANE_MCP_CONFIRM=elicit)")
            raise ToolError(
                "This server requires the user to confirm destructive operations (ARCANE_MCP_CONFIRM=elicit), "
                "but the MCP client does not support elicitation."
            )
        if mode in ("auto", "elicit") and can_elicit and ctx is not None:
            answer = await ctx.elicit(
                f"Arcane: {op.summary}\n\nEnvironment: {op.env_id}. Proceed?",
                [CONFIRM, CANCEL],  # type: ignore[arg-type]  # list[str] overload not resolved by mypy
            )
            if answer.action != "accept" or getattr(answer, "data", None) != CONFIRM:
                audit.result(op, success=False, error=f"rejected by user ({answer.action})")
                return {"status": "cancelled", "action": op.action, "target": op.target, "reason": "The user declined."}
            approved_by = "user"

        audit.approved(op, approved_by)
        try:
            result = await op.executor()
        except ToolError as e:
            audit.result(op, success=False, error=str(e))
            raise
        except Exception as e:
            logger.exception("Unexpected error executing %s on %s", op.action, op.target)
            audit.result(op, success=False, error=f"{type(e).__name__}: {e}")
            raise ToolError(f"{op.action} failed: {type(e).__name__}: {e}") from e
        audit.result(op, success=True, result=result)
        return {"status": "done", "action": op.action, "target": op.target, "approved_by": approved_by, "result": result}

    @mcp.tool(
        tags={"safety", "read"},
        annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
    )
    async def list_pending_operations() -> Any:
        """Destructive operations proposed but not yet confirmed (and not expired)."""
        return [op.public() for op in get_token_store().pending()]

    @mcp.tool(
        tags={"safety", "read"},
        annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
    )
    async def read_audit_log(
        lines: Annotated[int, Field(ge=1, le=1000)] = 50,
        event_type: Literal["operation_proposed", "operation_approved", "operation_result"] | None = None,
    ) -> Any:
        """Last N audit log events of destructive operations (secrets redacted)."""
        entries = get_audit_log().read_lines(lines)
        if event_type:
            entries = [e for e in entries if e.get("event") == event_type]
        return entries
