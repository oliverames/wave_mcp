"""Shared runtime state: the FastMCP instance, the Wave client, shared types.

Tool modules import from here rather than from ``server``, which keeps the
import graph acyclic: ``server`` imports the tool modules, and the tool modules
import only this.
"""

from __future__ import annotations

import logging
import os
from typing import Annotated, Any, Callable, Dict, Literal, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from . import __version__
from .client import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, WaveClient
from .errors import WaveConfigError

logger = logging.getLogger("wave_mcp")

INSTRUCTIONS = """\
Tools for Wave Accounting (waveapps.com) covering the full public GraphQL API:
businesses, chart of accounts, customers, vendors, products, sales taxes,
invoices and invoice payments, estimates and deposit payments, and money
(bookkeeping) transactions.

Most tools operate on one business. Call `wave_list_businesses` first, then
`wave_set_default_business` so later calls can omit `business_id`. Any tool
still accepts an explicit `business_id` to override the default.

Every read tool accepts `response_format` ("markdown" for a compact summary,
"json" for the complete record) and paginates with `page`/`page_size`, or
`fetch_all=true` to walk every page.

Wave has no query for money transactions: they can be created but not read
back. Vendors are read-only. Invoice and estimate line items must each
reference a product.
"""

# The mcp-builder convention for Python servers is {service}_mcp.
mcp = FastMCP("wave_mcp", instructions=INSTRUCTIONS)

# FastMCP takes no version argument, so it reports the MCP SDK's version in the
# initialize handshake. The underlying low-level server does carry the field,
# so set it there to advertise this server's own version instead.
mcp._mcp_server.version = __version__


# --------------------------------------------------------------- shared types

ResponseFormat = Annotated[
    Literal["markdown", "json"],
    Field(
        description=(
            'Output format: "markdown" for a compact human-readable summary, '
            '"json" for the complete record with every field.'
        )
    ),
]

PageNumber = Annotated[
    int, Field(ge=1, description="1-based page number for offset pagination.")
]

PageSize = Annotated[
    int,
    Field(
        ge=1,
        le=MAX_PAGE_SIZE,
        description=f"Records per page (1-{MAX_PAGE_SIZE}).",
    ),
]

DEFAULT_PAGE = 1
PAGE_SIZE_DEFAULT = DEFAULT_PAGE_SIZE


# ---------------------------------------------------------------- tool helper


def _title_from(name: str) -> str:
    """Turn a tool function name into a human-readable title.

    ``wave_list_invoices`` becomes ``Wave: List Invoices``, which is what an MCP
    client shows in a tool picker.
    """
    words = name.removeprefix("wave_").split("_")
    # Keep short domain words capitalized normally; PDF is the one acronym here.
    pretty = " ".join(w.upper() if w == "pdf" else w.capitalize() for w in words)
    return f"Wave: {pretty}"


def tool(
    *,
    read_only: bool = False,
    destructive: bool = False,
    idempotent: bool = False,
    title: Optional[str] = None,
) -> Callable:
    """Register a tool with a consistent name, title, and annotation set.

    Wraps ``mcp.tool`` so every tool declares all four MCP annotation hints and
    a title, rather than each module repeating the same dictionary. The tool
    name is the function name, which already carries the ``wave_`` prefix that
    keeps it from colliding with other MCP servers.

    ``openWorldHint`` is always true: every tool reaches Wave's API.
    """

    def decorator(fn: Callable) -> Callable:
        annotations: Dict[str, Any] = {
            "title": title or _title_from(fn.__name__),
            "readOnlyHint": read_only,
            "destructiveHint": destructive,
            # A read-only tool is idempotent by definition: calling it again
            # changes nothing either way.
            "idempotentHint": idempotent or read_only,
            "openWorldHint": True,
        }
        return mcp.tool(name=fn.__name__, annotations=annotations)(fn)

    return decorator


# --------------------------------------------------------------------- client

_client: Optional[WaveClient] = None


def get_client() -> WaveClient:
    """Return the process-wide Wave client, creating it on first use.

    Construction is lazy so the server can start and list its tools even when
    no token is configured; the error then surfaces on the first real call,
    where it is actionable, instead of at import time. It also keeps startup
    well inside Codex's 10-second default ``startup_timeout_sec``.
    """
    global _client
    if _client is None:
        _client = WaveClient.from_env()
    return _client


def set_client(client: WaveClient) -> None:
    """Install a client explicitly. Used by tests and by the entry point."""
    global _client
    _client = client


def reset_client() -> None:
    global _client
    _client = None


def business_id_or_default(business_id: Optional[str] = None) -> str:
    return get_client().require_business_id(business_id)


def configure_logging() -> None:
    """Log to stderr only.

    A stdio MCP server speaks JSON-RPC on stdout, so anything written there
    corrupts the protocol stream.
    """
    import sys

    level = os.getenv("WAVE_MCP_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


__all__ = [
    "mcp",
    "tool",
    "ResponseFormat",
    "PageNumber",
    "PageSize",
    "PAGE_SIZE_DEFAULT",
    "get_client",
    "set_client",
    "reset_client",
    "business_id_or_default",
    "configure_logging",
    "WaveConfigError",
]
