"""Server assembly and entry point.

Importing ``wave_mcp.tools`` registers every tool on the shared FastMCP
instance, so this module only has to wire configuration and start a transport.
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

from .runtime import configure_logging, mcp

logger = logging.getLogger("wave-mcp-server")


def build_server():
    """Load configuration, register every tool, and return the FastMCP server."""
    load_dotenv()
    configure_logging()

    # Both register on the shared FastMCP instance as a side effect of import.
    from . import resources, tools  # noqa: F401

    if not os.getenv("WAVE_ACCESS_TOKEN"):
        logger.warning(
            "WAVE_ACCESS_TOKEN is not set. The server will start and list its "
            "tools, but every call will fail until a token is configured."
        )
    if business_id := os.getenv("WAVE_BUSINESS_ID"):
        logger.info("Default business ID: %s", business_id)

    return mcp


def main() -> None:
    """Run the server over stdio, the transport MCP clients launch locally."""
    server = build_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
