"""MCP resources for Wave reference data.

Resources suit data an agent may want to pull in as context without deciding
on arguments first. Everything here is also reachable through a tool; these
are the read-only, no-argument views, kept because the 1.x server exposed
them and some clients prefer resources for grounding context.

Each returns JSON, since a resource is context for a model rather than a
rendered answer for a person.

Note that MCP clients differ in resource support -- Codex CLI, for one,
consumes tools and the server ``instructions`` field but not resources. No
capability is available only as a resource; tools remain the complete surface.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from .runtime import get_client, mcp
from .tools.accounts import LIST_ACCOUNTS
from .tools.businesses import LIST_BUSINESSES
from .tools.customers import LIST_CUSTOMERS
from .tools.products import LIST_PRODUCTS
from .tools.reference import LIST_ACCOUNT_SUBTYPES, LIST_ACCOUNT_TYPES
from .tools.sales_taxes import LIST_SALES_TAXES
from .tools.vendors import LIST_VENDORS

# Resources take no arguments, so they are capped rather than paginated: they
# exist to ground a model, not to page through a ledger.
RESOURCE_PAGE_SIZE = 200


def _json(payload: Any) -> str:
    return json.dumps(payload, indent=2, default=str)


async def _collect(query: str, variables: Dict[str, Any], path: tuple) -> List[Dict[str, Any]]:
    result = await get_client().paginate(
        query, variables, path=path, page_size=RESOURCE_PAGE_SIZE, fetch_all=True
    )
    return result["items"]


@mcp.resource(
    "wave://businesses",
    name="Wave businesses",
    description="Every Wave business this access token can reach, with IDs and currencies.",
    mime_type="application/json",
)
async def businesses_resource() -> str:
    return _json(await _collect(LIST_BUSINESSES, {}, ("businesses",)))


@mcp.resource(
    "wave://accounts",
    name="Chart of accounts",
    description="The default business's chart of accounts, with types, subtypes, and balances.",
    mime_type="application/json",
)
async def accounts_resource() -> str:
    business_id = get_client().require_business_id()
    return _json(
        await _collect(
            LIST_ACCOUNTS, {"businessId": business_id}, ("business", "accounts")
        )
    )


@mcp.resource(
    "wave://customers",
    name="Customers",
    description="The default business's customers, with outstanding and overdue balances.",
    mime_type="application/json",
)
async def customers_resource() -> str:
    business_id = get_client().require_business_id()
    return _json(
        await _collect(
            LIST_CUSTOMERS,
            {"businessId": business_id, "sort": ["NAME_ASC"]},
            ("business", "customers"),
        )
    )


@mcp.resource(
    "wave://vendors",
    name="Vendors",
    description="The default business's vendors. Read-only: Wave's API has no vendor mutations.",
    mime_type="application/json",
)
async def vendors_resource() -> str:
    business_id = get_client().require_business_id()
    return _json(
        await _collect(
            LIST_VENDORS, {"businessId": business_id}, ("business", "vendors")
        )
    )


@mcp.resource(
    "wave://products",
    name="Products and services",
    description="The default business's products. Invoice and estimate line items must reference one.",
    mime_type="application/json",
)
async def products_resource() -> str:
    business_id = get_client().require_business_id()
    return _json(
        await _collect(
            LIST_PRODUCTS,
            {"businessId": business_id, "sort": ["NAME_ASC"]},
            ("business", "products"),
        )
    )


@mcp.resource(
    "wave://sales-taxes",
    name="Sales taxes",
    description="The default business's sales taxes, with current rates and rate history.",
    mime_type="application/json",
)
async def sales_taxes_resource() -> str:
    business_id = get_client().require_business_id()
    return _json(
        await _collect(
            LIST_SALES_TAXES, {"businessId": business_id}, ("business", "salesTaxes")
        )
    )


@mcp.resource(
    "wave://account-taxonomy",
    name="Account type taxonomy",
    description="Wave's account types and subtypes -- the vocabulary wave_create_account expects.",
    mime_type="application/json",
)
async def account_taxonomy_resource() -> str:
    client = get_client()
    types = await client.execute(LIST_ACCOUNT_TYPES)
    subtypes = await client.execute(LIST_ACCOUNT_SUBTYPES)
    return _json(
        {
            "accountTypes": types.get("accountTypes") or [],
            "accountSubtypes": subtypes.get("accountSubtypes") or [],
        }
    )
