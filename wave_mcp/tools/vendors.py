"""Vendor tools.

Wave's public API exposes vendors as read-only: there is no vendorCreate,
vendorPatch, or vendorDelete mutation in the schema. New vendors must be added
in the Wave web app, under Purchases > Vendors.
"""

from __future__ import annotations

from typing import Optional

from .. import fragments as f
from ..formatting import address as fmt_address
from ..formatting import kv_block, listing, render, yes_no
from ..runtime import business_id_or_default, get_client, mcp
from .common import compact

LIST_VENDORS = f.build(
    """
    query ListVendors(
      $businessId: ID!
      $page: Int!
      $pageSize: Int!
      $email: String
      $modifiedAtAfter: DateTime
      $modifiedAtBefore: DateTime
    ) {
      business(id: $businessId) {
        id
        vendors(
          page: $page
          pageSize: $pageSize
          email: $email
          modifiedAtAfter: $modifiedAtAfter
          modifiedAtBefore: $modifiedAtBefore
        ) {
          pageInfo { ...PageInfoFields }
          edges { node { ...VendorFields } }
        }
      }
    }
    """,
    f.PAGE_INFO,
    *f.VENDOR_SET,
)

GET_VENDOR = f.build(
    """
    query GetVendor($businessId: ID!, $id: ID!) {
      business(id: $businessId) { id vendor(id: $id) { ...VendorFields } }
    }
    """,
    *f.VENDOR_SET,
)

VENDOR_COLUMNS = [
    ("Name", "name"),
    ("ID", "id"),
    ("Email", "email"),
    ("Phone", "phone"),
    ("Archived", lambda r: yes_no(r.get("isArchived"))),
]

CREATE_HINT = (
    "Wave's API has no vendor mutations, so vendors can only be added in the "
    "web app under Purchases > Vendors."
)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def wave_list_vendors(
    business_id: Optional[str] = None,
    email: Optional[str] = None,
    name_contains: Optional[str] = None,
    modified_after: Optional[str] = None,
    modified_before: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    fetch_all: bool = False,
    response_format: str = "markdown",
) -> str:
    """List vendors -- the suppliers a business buys from.

    Wave filters by exact email only; `name_contains` is applied by this server
    after fetching, so pair it with `fetch_all=true` on a long vendor list.

    Vendors are read-only in Wave's API: they can be listed and read but not
    created, changed, or deleted.

    Args:
        business_id: Business to read. Defaults to the session business.
        email: Exact email match, applied by Wave.
        name_contains: Case-insensitive substring match on name, applied locally.
        modified_after: ISO 8601 timestamp; only vendors changed after it.
        modified_before: ISO 8601 timestamp; only vendors changed before it.
        page: 1-based page number.
        page_size: Records per page (1-200).
        fetch_all: Walk every page.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    result = await client.paginate(
        LIST_VENDORS,
        compact(
            {
                "businessId": resolved,
                "email": email,
                "modifiedAtAfter": modified_after,
                "modifiedAtBefore": modified_before,
            }
        ),
        path=("business", "vendors"),
        page=page,
        page_size=page_size,
        fetch_all=fetch_all,
    )

    if name_contains:
        needle = name_contains.lower()
        matched = [v for v in result["items"] if needle in (v.get("name") or "").lower()]
        result = {**result, "items": matched, "count": len(matched)}

    def as_markdown() -> str:
        return listing(result, "Vendors", VENDOR_COLUMNS, empty_hint=CREATE_HINT)

    return render(result, response_format, as_markdown)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def wave_get_vendor(
    vendor_id: str,
    business_id: Optional[str] = None,
    response_format: str = "markdown",
) -> str:
    """Get one vendor by ID, including address and shipping details.

    Args:
        vendor_id: The Wave vendor ID.
        business_id: Business the vendor belongs to. Defaults to the session business.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    data = await client.execute(GET_VENDOR, {"businessId": resolved, "id": vendor_id})
    vendor = (data.get("business") or {}).get("vendor")
    if not vendor:
        return f"No vendor found with ID `{vendor_id}` in this business."

    def as_markdown() -> str:
        shipping = vendor.get("shippingDetails") or {}
        return f"**{vendor['name']}**\n\n" + kv_block(
            [
                ("ID", f"`{vendor['id']}`"),
                ("Display ID", vendor.get("displayId")),
                ("First name", vendor.get("firstName")),
                ("Last name", vendor.get("lastName")),
                ("Email", vendor.get("email")),
                ("Phone", vendor.get("phone")),
                ("Mobile", vendor.get("mobile")),
                ("Fax", vendor.get("fax")),
                ("Toll free", vendor.get("tollFree")),
                ("Website", vendor.get("website")),
                ("Currency", (vendor.get("currency") or {}).get("code")),
                ("Address", fmt_address(vendor.get("address"))),
                ("Shipping name", shipping.get("name")),
                ("Shipping address", fmt_address(shipping.get("address"))),
                ("Internal notes", vendor.get("internalNotes")),
                ("Archived", yes_no(vendor.get("isArchived"))),
                ("Created", vendor.get("createdAt")),
                ("Modified", vendor.get("modifiedAt")),
            ]
        )

    return render(vendor, response_format, as_markdown)
