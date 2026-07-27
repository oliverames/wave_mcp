"""Tool modules.

Importing this package registers every tool on the shared FastMCP instance;
each module calls ``@mcp.tool()`` at import time.
"""

from . import (  # noqa: F401
    accounts,
    businesses,
    customers,
    estimates,
    invoices,
    legacy,
    payments,
    products,
    reference,
    sales_taxes,
    transactions,
    vendors,
)

__all__ = [
    "accounts",
    "businesses",
    "customers",
    "estimates",
    "invoices",
    "legacy",
    "payments",
    "products",
    "reference",
    "sales_taxes",
    "transactions",
    "vendors",
]
