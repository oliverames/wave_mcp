"""Product and service tools: list, get, create, patch, archive.

Products matter beyond a catalogue: every invoice and estimate line item must
reference one, so a product usually has to exist before an invoice can be
created.
"""

from __future__ import annotations

from typing import List, Optional

from .. import fragments as f
from ..formatting import kv_block, listing, render, success, yes_no
from ..runtime import PAGE_SIZE_DEFAULT, PageNumber, PageSize, ResponseFormat, business_id_or_default, get_client, tool
from .common import DEFAULT_PRODUCT_SORT, compact, decimal_str

LIST_PRODUCTS = f.build(
    """
    query ListProducts(
      $businessId: ID!
      $page: Int!
      $pageSize: Int!
      $sort: [ProductSort!]!
      $isSold: Boolean
      $isBought: Boolean
      $isArchived: Boolean
      $modifiedAtAfter: DateTime
      $modifiedAtBefore: DateTime
    ) {
      business(id: $businessId) {
        id
        products(
          page: $page
          pageSize: $pageSize
          sort: $sort
          isSold: $isSold
          isBought: $isBought
          isArchived: $isArchived
          modifiedAtAfter: $modifiedAtAfter
          modifiedAtBefore: $modifiedAtBefore
        ) {
          pageInfo { ...PageInfoFields }
          edges { node { ...ProductFields } }
        }
      }
    }
    """,
    f.PAGE_INFO,
    f.PRODUCT,
)

GET_PRODUCT = f.build(
    """
    query GetProduct($businessId: ID!, $id: ID!) {
      business(id: $businessId) { id product(id: $id) { ...ProductFields } }
    }
    """,
    f.PRODUCT,
)

CREATE_PRODUCT = f.build(
    f"""
    mutation CreateProduct($input: ProductCreateInput!) {{
      productCreate(input: $input) {{
        didSucceed
        {f.INPUT_ERRORS}
        product {{ ...ProductFields }}
      }}
    }}
    """,
    f.PRODUCT,
)

PATCH_PRODUCT = f.build(
    f"""
    mutation PatchProduct($input: ProductPatchInput!) {{
      productPatch(input: $input) {{
        didSucceed
        {f.INPUT_ERRORS}
        product {{ ...ProductFields }}
      }}
    }}
    """,
    f.PRODUCT,
)

ARCHIVE_PRODUCT = f.build(
    f"""
    mutation ArchiveProduct($input: ProductArchiveInput!) {{
      productArchive(input: $input) {{
        didSucceed
        {f.INPUT_ERRORS}
        product {{ id name isArchived }}
      }}
    }}
    """,
)

PRODUCT_COLUMNS = [
    ("Name", "name"),
    ("ID", "id"),
    ("Unit price", "unitPrice"),
    ("Sold", lambda r: yes_no(r.get("isSold"))),
    ("Bought", lambda r: yes_no(r.get("isBought"))),
    ("Income account", lambda r: (r.get("incomeAccount") or {}).get("name", "-")),
    ("Archived", lambda r: yes_no(r.get("isArchived"))),
]


def _product_detail(product: dict) -> str:
    taxes = product.get("defaultSalesTaxes") or []
    tax_summary = ", ".join(
        f"{t['name']} ({t['abbreviation']} {t['rate']})" for t in taxes
    )
    return f"**{product['name']}**\n\n" + kv_block(
        [
            ("ID", f"`{product['id']}`"),
            ("Description", product.get("description")),
            ("Unit price", product.get("unitPrice")),
            ("Sold to customers", yes_no(product.get("isSold"))),
            ("Bought from vendors", yes_no(product.get("isBought"))),
            ("Income account", (product.get("incomeAccount") or {}).get("name")),
            ("Expense account", (product.get("expenseAccount") or {}).get("name")),
            ("Default sales taxes", tax_summary),
            ("Archived", yes_no(product.get("isArchived"))),
            ("Created", product.get("createdAt")),
            ("Modified", product.get("modifiedAt")),
        ]
    )


@tool(read_only=True)
async def wave_list_products(
    business_id: Optional[str] = None,
    is_sold: Optional[bool] = None,
    is_bought: Optional[bool] = None,
    is_archived: Optional[bool] = None,
    name_contains: Optional[str] = None,
    sort: Optional[List[str]] = None,
    modified_after: Optional[str] = None,
    modified_before: Optional[str] = None,
    page: PageNumber = 1,
    page_size: PageSize = PAGE_SIZE_DEFAULT,
    fetch_all: bool = False,
    response_format: ResponseFormat = "markdown",
) -> str:
    """List products and services.

    Invoice and estimate line items must reference a product, so this is the
    usual first step when building either one.

    Args:
        business_id: Business to read. Defaults to the session business.
        is_sold: Only products sold to customers.
        is_bought: Only products bought from vendors.
        is_archived: Filter to archived (true) or active (false) products.
        name_contains: Case-insensitive substring match on name, applied locally.
        sort: Any of NAME_ASC, NAME_DESC, CREATED_AT_ASC, CREATED_AT_DESC,
            MODIFIED_AT_ASC, MODIFIED_AT_DESC. Defaults to NAME_ASC.
        modified_after: ISO 8601 timestamp; only products changed after it.
        modified_before: ISO 8601 timestamp; only products changed before it.
        page: 1-based page number.
        page_size: Records per page (1-200).
        fetch_all: Walk every page.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    result = await client.paginate(
        LIST_PRODUCTS,
        compact(
            {
                "businessId": resolved,
                "sort": [s.upper() for s in sort] if sort else DEFAULT_PRODUCT_SORT,
                "isSold": is_sold,
                "isBought": is_bought,
                "isArchived": is_archived,
                "modifiedAtAfter": modified_after,
                "modifiedAtBefore": modified_before,
            }
        ),
        path=("business", "products"),
        page=page,
        page_size=page_size,
        fetch_all=fetch_all,
    )

    if name_contains:
        needle = name_contains.lower()
        matched = [p for p in result["items"] if needle in (p.get("name") or "").lower()]
        result = {**result, "items": matched, "count": len(matched)}

    def as_markdown() -> str:
        return listing(result, "Products and services", PRODUCT_COLUMNS)

    return render(result, response_format, as_markdown)


@tool(read_only=True)
async def wave_get_product(
    product_id: str,
    business_id: Optional[str] = None,
    response_format: ResponseFormat = "markdown",
) -> str:
    """Get one product by ID, including its accounts and default sales taxes.

    Args:
        product_id: The Wave product ID.
        business_id: Business the product belongs to. Defaults to the session business.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    data = await client.execute(GET_PRODUCT, {"businessId": resolved, "id": product_id})
    product = (data.get("business") or {}).get("product")
    if not product:
        return f"No product found with ID `{product_id}` in this business."
    return render(product, response_format, lambda: _product_detail(product))


@tool()
async def wave_create_product(
    name: str,
    unit_price: str,
    business_id: Optional[str] = None,
    description: Optional[str] = None,
    income_account_id: Optional[str] = None,
    expense_account_id: Optional[str] = None,
    default_sales_tax_ids: Optional[List[str]] = None,
    response_format: ResponseFormat = "markdown",
) -> str:
    """Create a product or service.

    Set `income_account_id` to make it sellable on invoices and estimates, and
    `expense_account_id` to make it purchasable. Find IDs with
    `wave_list_accounts`.

    Args:
        name: Product or service name. Required.
        unit_price: Default price per unit, e.g. "150.00". Required.
        business_id: Business to create in. Defaults to the session business.
        description: Default line-item description on invoices.
        income_account_id: Income account credited when sold.
        expense_account_id: Expense account debited when bought.
        default_sales_tax_ids: Sales taxes applied by default. See
            `wave_list_sales_taxes`.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    payload = compact(
        {
            "businessId": resolved,
            "name": name,
            "unitPrice": decimal_str(unit_price),
            "description": description,
            "incomeAccountId": income_account_id,
            "expenseAccountId": expense_account_id,
            "defaultSalesTaxIds": default_sales_tax_ids,
        }
    )
    result = await client.mutate(
        CREATE_PRODUCT, {"input": payload}, root_field="productCreate"
    )
    product = result.get("product") or {}
    return render(
        product,
        response_format,
        lambda: success(
            f"Created product **{product.get('name', name)}**.",
            [
                ("ID", f"`{product.get('id')}`"),
                ("Unit price", product.get("unitPrice")),
                ("Sold", yes_no(product.get("isSold"))),
                ("Bought", yes_no(product.get("isBought"))),
            ],
        ),
    )


@tool(idempotent=True)
async def wave_patch_product(
    product_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    unit_price: Optional[str] = None,
    income_account_id: Optional[str] = None,
    expense_account_id: Optional[str] = None,
    default_sales_tax_ids: Optional[List[str]] = None,
    response_format: ResponseFormat = "markdown",
) -> str:
    """Update a product. Only the fields you supply change.

    Supplying `default_sales_tax_ids` replaces the whole list, so include every
    tax you want to keep. Pass an empty list to clear them.

    Args:
        product_id: The product to update. Required.
        name: New name.
        description: New default description.
        unit_price: New unit price, e.g. "175.00".
        income_account_id: New income account.
        expense_account_id: New expense account.
        default_sales_tax_ids: Replacement list of default sales taxes.
        response_format: "markdown" or "json".
    """
    client = get_client()
    payload = compact(
        {
            "id": product_id,
            "name": name,
            "description": description,
            "unitPrice": decimal_str(unit_price) if unit_price is not None else None,
            "incomeAccountId": income_account_id,
            "expenseAccountId": expense_account_id,
            "defaultSalesTaxIds": default_sales_tax_ids,
        }
    )
    if len(payload) == 1:
        return "Nothing to update. Supply at least one field to change."

    result = await client.mutate(
        PATCH_PRODUCT, {"input": payload}, root_field="productPatch"
    )
    product = result.get("product") or {}
    return render(
        product,
        response_format,
        lambda: success(
            f"Updated product **{product.get('name')}**.",
            [("ID", f"`{product.get('id')}`"), ("Unit price", product.get("unitPrice"))],
        ),
    )


@tool(destructive=True, idempotent=True)
async def wave_archive_product(product_id: str) -> str:
    """Archive a product, removing it from pickers on new invoices.

    Existing invoices that use it are untouched. Wave has no product delete,
    and no un-archive through the API: restore one from the web app.

    Args:
        product_id: The product to archive.
    """
    client = get_client()
    result = await client.mutate(
        ARCHIVE_PRODUCT, {"input": {"id": product_id}}, root_field="productArchive"
    )
    product = result.get("product") or {}
    name = product.get("name") or product_id
    return (
        f"Archived product **{name}**. Existing invoices are unaffected; it no "
        "longer appears when building new ones."
    )
