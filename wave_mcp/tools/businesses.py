"""Business tools: discovery, detail, session default, and branding settings."""

from __future__ import annotations

from typing import Optional

from .. import fragments as f
from ..formatting import address as fmt_address
from ..formatting import kv_block, listing, render, yes_no
from ..runtime import business_id_or_default, get_client, mcp

LIST_BUSINESSES = f.build(
    """
    query ListBusinesses($page: Int!, $pageSize: Int!, $isArchived: Boolean) {
      businesses(page: $page, pageSize: $pageSize, isArchived: $isArchived) {
        pageInfo { ...PageInfoFields }
        edges {
          node {
            id
            name
            isPersonal
            isClassicAccounting
            isClassicInvoicing
            isArchived
            currency { code symbol }
            type { name value }
            subtype { name value }
          }
        }
      }
    }
    """,
    f.PAGE_INFO,
)

GET_BUSINESS = f.build(
    """
    query GetBusiness($id: ID!) {
      business(id: $id) { ...BusinessFields }
    }
    """,
    *f.BUSINESS_SET,
)

GET_SETTINGS = """
query GetInvoiceEstimateSettings($id: ID!) {
  business(id: $id) {
    id
    name
    emailSendEnabled
    invoiceEstimateSettings {
      generalSettings { accentColor logoUrl }
    }
  }
}
"""


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
async def wave_list_businesses(
    page: int = 1,
    page_size: int = 50,
    is_archived: Optional[bool] = None,
    fetch_all: bool = False,
    response_format: str = "markdown",
) -> str:
    """List the Wave businesses this access token can reach.

    Start here: every other tool needs a business ID. Pass one to
    `wave_set_default_business` so later calls can omit it.

    Args:
        page: 1-based page number.
        page_size: Records per page (1-200).
        is_archived: Filter to archived (true) or active (false) businesses.
        fetch_all: Walk every page instead of returning just one.
        response_format: "markdown" or "json".
    """
    client = get_client()
    result = await client.paginate(
        LIST_BUSINESSES,
        {"isArchived": is_archived},
        path=("businesses",),
        page=page,
        page_size=page_size,
        fetch_all=fetch_all,
    )

    def as_markdown() -> str:
        return listing(
            result,
            "Wave businesses",
            [
                ("Name", "name"),
                ("ID", "id"),
                ("Currency", lambda r: (r.get("currency") or {}).get("code", "-")),
                ("Type", lambda r: (r.get("type") or {}).get("name", "-")),
                ("Personal", lambda r: yes_no(r.get("isPersonal"))),
                ("Archived", lambda r: yes_no(r.get("isArchived"))),
            ],
        )

    return render(result, response_format, as_markdown)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def wave_get_business(
    business_id: Optional[str] = None,
    response_format: str = "markdown",
) -> str:
    """Get full detail for one business: currency, address, type, and settings.

    Args:
        business_id: Business to read. Defaults to the session business.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    data = await client.execute(GET_BUSINESS, {"id": resolved})
    business = data.get("business")
    if not business:
        return f"No business found with ID `{resolved}`. Call wave_list_businesses to see valid IDs."

    def as_markdown() -> str:
        currency = business.get("currency") or {}
        return f"**{business['name']}**\n\n" + kv_block(
            [
                ("ID", f"`{business['id']}`"),
                ("Currency", f"{currency.get('code')} ({currency.get('name')})"),
                ("Type", (business.get("type") or {}).get("name")),
                ("Subtype", (business.get("subtype") or {}).get("name")),
                ("Organizational type", business.get("organizationalType")),
                ("Address", fmt_address(business.get("address"))),
                ("Phone", business.get("phone")),
                ("Mobile", business.get("mobile")),
                ("Fax", business.get("fax")),
                ("Toll free", business.get("tollFree")),
                ("Website", business.get("website")),
                ("Timezone", business.get("timezone")),
                ("Email sending enabled", yes_no(business.get("emailSendEnabled"))),
                ("Classic accounting", yes_no(business.get("isClassicAccounting"))),
                ("Classic invoicing", yes_no(business.get("isClassicInvoicing"))),
                ("Personal", yes_no(business.get("isPersonal"))),
                ("Archived", yes_no(business.get("isArchived"))),
                ("Created", business.get("createdAt")),
                ("Modified", business.get("modifiedAt")),
            ]
        )

    return render(business, response_format, as_markdown)


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def wave_set_default_business(business_id: str) -> str:
    """Set the business that later tool calls use when none is given.

    This is session state on the running server, not a change in Wave. Set
    WAVE_BUSINESS_ID in the environment to make it persist across restarts.

    Args:
        business_id: The Wave business ID to make the default.
    """
    client = get_client()
    data = await client.execute(GET_BUSINESS, {"id": business_id})
    business = data.get("business")
    if not business:
        return (
            f"No business found with ID `{business_id}`, so the default is unchanged. "
            "Call wave_list_businesses to see valid IDs."
        )
    client.business_id = business_id
    return (
        f"Default business set to **{business['name']}** (`{business_id}`). "
        "Later calls can omit business_id."
    )


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def wave_get_invoice_estimate_settings(
    business_id: Optional[str] = None,
    response_format: str = "markdown",
) -> str:
    """Get the branding applied to invoices and estimates: accent color and logo.

    Args:
        business_id: Business to read. Defaults to the session business.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    data = await client.execute(GET_SETTINGS, {"id": resolved})
    business = data.get("business") or {}
    settings = (business.get("invoiceEstimateSettings") or {}).get("generalSettings") or {}

    def as_markdown() -> str:
        return "**Invoice and estimate settings**\n\n" + kv_block(
            [
                ("Business", business.get("name")),
                ("Accent color", settings.get("accentColor")),
                ("Logo URL", settings.get("logoUrl")),
                ("Email sending enabled", yes_no(business.get("emailSendEnabled"))),
            ]
        )

    return render(business, response_format, as_markdown)
