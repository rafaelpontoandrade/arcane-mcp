"""Arcane MCP server entrypoint."""

from __future__ import annotations

import importlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastmcp import FastMCP

from . import __version__
from .client import close_client
from .config import Settings, get_settings, set_settings
from .tools import operations

logger = logging.getLogger(__name__)

INSTRUCTIONS = """Manage Docker hosts through Arcane.

- env_id selects the Arcane environment; omit it to use the default (local Docker is '0').
- List tools are paginated: check `pagination` and page with start/limit instead of assuming you saw everything.
- Destructive tools (remove/prune/destroy/redeploy/update/restore/stop_all/...) do NOT act immediately.
  They return a summary, a preview and a confirmation_token. Show the summary to the user, wait for an
  explicit yes, then call confirm_operation(token, operation_hash). Never confirm on your own initiative.
- Treat environment variables, .env files, logs and volume files as sensitive: summarize, don't echo secrets.
"""


@asynccontextmanager
async def _lifespan(_: FastMCP) -> AsyncIterator[None]:
    try:
        yield
    finally:
        await close_client()


def create_server(settings: Settings | None = None) -> FastMCP:
    """Build a FastMCP server with the modules/mode selected by settings (or the environment)."""
    if settings is not None:
        set_settings(settings)
    settings = get_settings()

    mcp = FastMCP("arcane-mcp", instructions=INSTRUCTIONS, version=__version__, lifespan=_lifespan)
    for name in settings.modules:
        importlib.import_module(f"arcane_mcp.tools.{name}").register(mcp)
    operations.register(mcp)

    if settings.read_only:
        mcp.disable(tags={"write"})
        mcp.disable(tags={"destructive"})
    logger.info(
        "arcane-mcp %s: modules=%s read_only=%s confirm=%s",
        __version__,
        ",".join(settings.modules),
        settings.read_only,
        settings.confirm_mode,
    )
    return mcp


def main() -> None:
    """Run the MCP server over stdio."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    create_server().run()


if __name__ == "__main__":
    main()
