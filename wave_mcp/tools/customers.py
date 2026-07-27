"""Customer tools: list, get, create, patch, delete."""

from __future__ import annotations

from typing import List, Optional

from .. import fragments as f
from ..formatting import address as fmt_address
from ..formatting import kv_block, listing, money, render, success, yes_no
from ..runtime import business_id_or_default, get_client, mcp
from .common import DEFAULT_CUSTOMER_SORT, compact, optional_address, optional_shipping

LIST_CUSTOMERS = f.build(
    """
    query ListCustomers(
      $businessId: ID!
      $page: Int!
      $pageSize: Int!
      $sort: [CustomerSort!]!
      $email: String
      $modifiedAtAfter: DateTime
      $modifiedAtBefore: DateTime
    ) {
      business(id: $businessId) {
        id
        customers(
          page: $page
          pageSize: $pageSize
          sort: $sort
          email: $email
          modifiedAtAfter: $modifiedAtAfter
          modifiedAtBefore: $modifiedAtBefore
        ) {
          pageInfo { ...PageInfoFields }
          edges { node { ...CustomerFields } }
        }
      }
    }
    """,
    f.PAGE_INFO,
    *f.CUSTOMER_SET,
)

GET_CUSTOMER = f.build(
    """
    query GetCustomer($businessId: ID!, $id: ID!) {
      business(id: $businessId) { id customer(id: $id) { ...CustomerFields } }
    }
    """,
    *f.CUSTOMER_SET,
)

CREATE_CUSTOMER = f.build(
    f"""
    mutation CreateCustomer($input: CustomerCreateInput!) {{
      customerCreate(input: $input) {{
        didSucceed
        {f.INPUT_ERRORS}
        customer {{ ...CustomerFields }}
      }}
    }}
    """,
    *f.CUSTOMER_SET,
)

PATCH_CUSTOMER = f.build(
    f"""
    mutation PatchCustomer($input: CustomerPatchInput!) {{
      customerPatch(input: $input) {{
        didSucceed
        {f.INPUT_ERRORS}
        customer {{ ...CustomerFields }}
      }}
    }}
    """,
    *f.CUSTOMER_SET,
)

DELETE_CUSTOMER = f"""
mutation DeleteCustomer($input: CustomerDeleteInput!) {{
  customerDelete(input: $input) {{
    didSucceed
    {f.INPUT_ERRORS}
  }}
}}
"""

CUSTOMER_COLUMNS = [
    ("Name", "name"),
    ("ID", "id"),
    ("Email", "email"),
    ("Phone", "phone"),
    ("Outstanding", lambda r: money(r.get("outstandingAmount"))),
    ("Overdue", lambda r: money(r.get("overdueAmount"))),
]


def _customer_detail(customer: dict) -> str:
    shipping = customer.get("shippingDetails") or {}
    return f"**{customer['name']}**\n\n" + kv_block(
        [
            ("ID", f"`{customer['id']}`"),
            ("Display ID", customer.get("displayId")),
            ("First name", customer.get("firstName")),
            ("Last name", customer.get("lastName")),
            ("Email", customer.get("email")),
            ("Phone", customer.get("phone")),
            ("Mobile", customer.get("mobile")),
            ("Fax", customer.get("fax")),
            ("Toll free", customer.get("tollFree")),
            ("Website", customer.get("website")),
            ("Currency", (customer.get("currency") or {}).get("code")),
            ("Address", fmt_address(customer.get("address"))),
            ("Shipping name", shipping.get("name")),
            ("Shipping address", fmt_address(shipping.get("address"))),
            ("Shipping phone", shipping.get("phone")),
            ("Shipping instructions", shipping.get("instructions")),
            ("Internal notes", customer.get("internalNotes")),
            ("Outstanding", money(customer.get("outstandingAmount"))),
            ("Overdue", money(customer.get("overdueAmount"))),
            ("Archived", yes_no(customer.get("isArchived"))),
            ("Created", customer.get("createdAt")),
            ("Modified", customer.get("modifiedAt")),
        ]
    )


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def wave_list_customers(
    business_id: Optional[str] = None,
    email: Optional[str] = None,
    name_contains: Optional[str] = None,
    sort: Optional[List[str]] = None,
    modified_after: Optional[str] = None,
    modified_before: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    fetch_all: bool = False,
    response_format: str = "markdown",
) -> str:
    """List customers, with each one's outstanding and overdue balance.

    Wave can filter by exact email only. `name_contains` is applied by this
    server after fetching, so combine it with `fetch_all=true` when searching a
    large customer list.

    Args:
        business_id: Business to read. Defaults to the session business.
        email: Exact email match, applied by Wave.
        name_contains: Case-insensitive substring match on name, applied locally.
        sort: Any of NAME_ASC, NAME_DESC, CREATED_AT_ASC, CREATED_AT_DESC,
            MODIFIED_AT_ASC, MODIFIED_AT_DESC. Defaults to NAME_ASC.
        modified_after: ISO 8601 timestamp; only customers changed after it.
        modified_before: ISO 8601 timestamp; only customers changed before it.
        page: 1-based page number.
        page_size: Records per page (1-200).
        fetch_all: Walk every page.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    result = await client.paginate(
        LIST_CUSTOMERS,
        compact(
            {
                "businessId": resolved,
                "sort": [s.upper() for s in sort] if sort else DEFAULT_CUSTOMER_SORT,
                "email": email,
                "modifiedAtAfter": modified_after,
                "modifiedAtBefore": modified_before,
            }
        ),
        path=("business", "customers"),
        page=page,
        page_size=page_size,
        fetch_all=fetch_all,
    )

    if name_contains:
        needle = name_contains.lower()
        matched = [c for c in result["items"] if needle in (c.get("name") or "").lower()]
        result = {**result, "items": matched, "count": len(matched)}

    def as_markdown() -> str:
        hint = (
            "Wave filters customers by exact email only; pass fetch_all=true "
            "when searching by name."
            if name_contains
            else ""
        )
        return listing(result, "Customers", CUSTOMER_COLUMNS, empty_hint=hint)

    return render(result, response_format, as_markdown)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def wave_get_customer(
    customer_id: str,
    business_id: Optional[str] = None,
    response_format: str = "markdown",
) -> str:
    """Get one customer by ID, including address and shipping details.

    Args:
        customer_id: The Wave customer ID.
        business_id: Business the customer belongs to. Defaults to the session business.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    data = await client.execute(GET_CUSTOMER, {"businessId": resolved, "id": customer_id})
    customer = (data.get("business") or {}).get("customer")
    if not customer:
        return f"No customer found with ID `{customer_id}` in this business."
    return render(customer, response_format, lambda: _customer_detail(customer))


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def wave_create_customer(
    name: str,
    business_id: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    mobile: Optional[str] = None,
    fax: Optional[str] = None,
    toll_free: Optional[str] = None,
    website: Optional[str] = None,
    display_id: Optional[str] = None,
    internal_notes: Optional[str] = None,
    currency: Optional[str] = None,
    address_line1: Optional[str] = None,
    address_line2: Optional[str] = None,
    city: Optional[str] = None,
    province_code: Optional[str] = None,
    country_code: Optional[str] = None,
    postal_code: Optional[str] = None,
    shipping_name: Optional[str] = None,
    shipping_phone: Optional[str] = None,
    shipping_instructions: Optional[str] = None,
    shipping_address_line1: Optional[str] = None,
    shipping_address_line2: Optional[str] = None,
    shipping_city: Optional[str] = None,
    shipping_province_code: Optional[str] = None,
    shipping_country_code: Optional[str] = None,
    shipping_postal_code: Optional[str] = None,
    response_format: str = "markdown",
) -> str:
    """Create a customer. Only the name is required.

    Customers are what invoices and estimates are billed to.

    Args:
        name: Customer or company name. Required.
        business_id: Business to create in. Defaults to the session business.
        first_name: Contact first name.
        last_name: Contact last name.
        email: Email address, used when sending invoices.
        phone: Phone number.
        mobile: Mobile number.
        fax: Fax number.
        toll_free: Toll-free number.
        website: Website URL.
        display_id: Your own customer number.
        internal_notes: Notes visible only to you.
        currency: Currency code. Defaults to the business currency.
        address_line1: Billing street address.
        address_line2: Billing address line 2.
        city: Billing city.
        province_code: Billing province or state code, e.g. "CA-ON", "US-NY".
        country_code: Billing ISO country code, e.g. "US", "CA".
        postal_code: Billing postal or ZIP code.
        shipping_name: Shipping recipient name.
        shipping_phone: Shipping contact phone.
        shipping_instructions: Delivery instructions.
        shipping_address_line1: Shipping street address.
        shipping_address_line2: Shipping address line 2.
        shipping_city: Shipping city.
        shipping_province_code: Shipping province or state code.
        shipping_country_code: Shipping ISO country code.
        shipping_postal_code: Shipping postal or ZIP code.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    payload = compact(
        {
            "businessId": resolved,
            "name": name,
            "firstName": first_name,
            "lastName": last_name,
            "email": email,
            "phone": phone,
            "mobile": mobile,
            "fax": fax,
            "tollFree": toll_free,
            "website": website,
            "displayId": display_id,
            "internalNotes": internal_notes,
            "currency": currency.upper() if currency else None,
            "address": optional_address(
                address_line1, address_line2, city, province_code, country_code, postal_code
            ),
            "shippingDetails": optional_shipping(
                shipping_name,
                shipping_phone,
                shipping_instructions,
                optional_address(
                    shipping_address_line1,
                    shipping_address_line2,
                    shipping_city,
                    shipping_province_code,
                    shipping_country_code,
                    shipping_postal_code,
                ),
            ),
        }
    )
    result = await client.mutate(
        CREATE_CUSTOMER, {"input": payload}, root_field="customerCreate"
    )
    customer = result.get("customer") or {}
    return render(
        customer,
        response_format,
        lambda: success(
            f"Created customer **{customer.get('name', name)}**.",
            [("ID", f"`{customer.get('id')}`"), ("Email", customer.get("email"))],
        ),
    )


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def wave_patch_customer(
    customer_id: str,
    name: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    mobile: Optional[str] = None,
    fax: Optional[str] = None,
    toll_free: Optional[str] = None,
    website: Optional[str] = None,
    display_id: Optional[str] = None,
    internal_notes: Optional[str] = None,
    currency: Optional[str] = None,
    address_line1: Optional[str] = None,
    address_line2: Optional[str] = None,
    city: Optional[str] = None,
    province_code: Optional[str] = None,
    country_code: Optional[str] = None,
    postal_code: Optional[str] = None,
    shipping_name: Optional[str] = None,
    shipping_phone: Optional[str] = None,
    shipping_instructions: Optional[str] = None,
    shipping_address_line1: Optional[str] = None,
    shipping_address_line2: Optional[str] = None,
    shipping_city: Optional[str] = None,
    shipping_province_code: Optional[str] = None,
    shipping_country_code: Optional[str] = None,
    shipping_postal_code: Optional[str] = None,
    response_format: str = "markdown",
) -> str:
    """Update a customer. Only the fields you supply change.

    Address and shipping are replaced wholesale when any part of them is
    supplied, so include every line you want to keep.

    Args:
        customer_id: The customer to update. Required.
        name: New name.
        first_name: New contact first name.
        last_name: New contact last name.
        email: New email address.
        phone: New phone number.
        mobile: New mobile number.
        fax: New fax number.
        toll_free: New toll-free number.
        website: New website URL.
        display_id: New customer number.
        internal_notes: New internal notes.
        currency: New currency code.
        address_line1: Billing street address.
        address_line2: Billing address line 2.
        city: Billing city.
        province_code: Billing province or state code.
        country_code: Billing ISO country code.
        postal_code: Billing postal or ZIP code.
        shipping_name: Shipping recipient name.
        shipping_phone: Shipping contact phone.
        shipping_instructions: Delivery instructions.
        shipping_address_line1: Shipping street address.
        shipping_address_line2: Shipping address line 2.
        shipping_city: Shipping city.
        shipping_province_code: Shipping province or state code.
        shipping_country_code: Shipping ISO country code.
        shipping_postal_code: Shipping postal or ZIP code.
        response_format: "markdown" or "json".
    """
    client = get_client()
    payload = compact(
        {
            "id": customer_id,
            "name": name,
            "firstName": first_name,
            "lastName": last_name,
            "email": email,
            "phone": phone,
            "mobile": mobile,
            "fax": fax,
            "tollFree": toll_free,
            "website": website,
            "displayId": display_id,
            "internalNotes": internal_notes,
            "currency": currency.upper() if currency else None,
            "address": optional_address(
                address_line1, address_line2, city, province_code, country_code, postal_code
            ),
            "shippingDetails": optional_shipping(
                shipping_name,
                shipping_phone,
                shipping_instructions,
                optional_address(
                    shipping_address_line1,
                    shipping_address_line2,
                    shipping_city,
                    shipping_province_code,
                    shipping_country_code,
                    shipping_postal_code,
                ),
            ),
        }
    )
    if len(payload) == 1:
        return "Nothing to update. Supply at least one field to change."

    result = await client.mutate(
        PATCH_CUSTOMER, {"input": payload}, root_field="customerPatch"
    )
    customer = result.get("customer") or {}
    return render(
        customer,
        response_format,
        lambda: success(
            f"Updated customer **{customer.get('name')}**.",
            [("ID", f"`{customer.get('id')}`")],
        ),
    )


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def wave_delete_customer(customer_id: str) -> str:
    """Delete a customer.

    Wave archives rather than hard-deletes a customer that has invoices or
    transactions, so history is preserved either way. This cannot be undone
    through the API.

    Args:
        customer_id: The customer to delete.
    """
    client = get_client()
    await client.mutate(
        DELETE_CUSTOMER, {"input": {"id": customer_id}}, root_field="customerDelete"
    )
    return (
        f"Deleted customer `{customer_id}`. Wave archives instead of deleting "
        "when the customer has invoices or transactions, so any history remains."
    )
