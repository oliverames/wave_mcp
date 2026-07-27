#!/usr/bin/env python3
"""Wave Accounting MCP server -- entry point.

Kept at the repository root so existing MCP client configurations that point at
`mcp_server.py` keep working. The implementation lives in the `wave_mcp`
package.

Usage:
    python mcp_server.py

Environment:
    WAVE_ACCESS_TOKEN  Wave OAuth2 access token (required)
    WAVE_BUSINESS_ID   Default business ID (optional)
    WAVE_MCP_LOG_LEVEL Log level, default INFO (optional)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wave_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main()
