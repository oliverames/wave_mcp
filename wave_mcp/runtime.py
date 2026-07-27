"""Shared runtime state: the FastMCP instance and the Wave client singleton.

Tool modules import from here rather than from ``server``, which keeps the
import graph acyclic: ``server`` imports the tool modules, and the tool modules
import only this.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from mcp.server.fastmcp import FastMCP

from . import __version__
from .client import WaveClient
from .errors import WaveConfigError

logger = logging.getLogger("wave-mcp-server")

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
"""

mcp = FastMCP("wave-accounting", instructions=INSTRUCTIONS)

# FastMCP takes no version argument, so it reports the MCP SDK's version in the
# initialize handshake. The underlying low-level server does carry the field,
# so set it there to advertise this server's own version instead.
mcp._mcp_server.version = __version__

_client: Optional[WaveClient] = None


def get_client() -> WaveClient:
    """Return the process-wide Wave client, creating it on first use.

    Construction is lazy so the server can start and list its tools even when
    no token is configured; the error then surfaces on the first real call,
    where it is actionable, instead of at import time.
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
    "get_client",
    "set_client",
    "reset_client",
    "business_id_or_default",
    "configure_logging",
    "WaveConfigError",
]
