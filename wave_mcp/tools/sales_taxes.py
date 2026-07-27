"""Sales tax tools: list, get, create, patch, archive."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import fragments as f
from ..errors import WaveError
from ..formatting import kv_block, listing, render, success, yes_no
from ..runtime import business_id_or_default, get_client, mcp
from .common import compact, decimal_str

LIST_SALES_TAXES = f.build(
    """
    query ListSalesTaxes(
      $businessId: ID!
      $page: Int!
      $pageSize: Int!
      $isArchived: Boolean
      $modifiedAtAfter: DateTime
      $modifiedAtBefore: DateTime
    ) {
      business(id: $businessId) {
        id
        salesTaxes(
          page: $page
          pageSize: $pageSize
          isArchived: $isArchived
          modifiedAtAfter: $modifiedAtAfter
          modifiedAtBefore: $modifiedAtBefore
        ) {
          pageInfo { ...PageInfoFields }
          edges { node { ...SalesTaxFields } }
        }
      }
    }
    """,
    f.PAGE_INFO,
    f.SALES_TAX,
)

GET_SALES_TAX = f.build(
    """
    query GetSalesTax($businessId: ID!, $id: ID!) {
      business(id: $businessId) { id salesTax(id: $id) { ...SalesTaxFields } }
    }
    """,
    f.SALES_TAX,
)

CREATE_SALES_TAX = f.build(
    f"""
    mutation CreateSalesTax($input: SalesTaxCreateInput!) {{
      salesTaxCreate(input: $input) {{
        didSucceed
        {f.INPUT_ERRORS}
        salesTax {{ ...SalesTaxFields }}
      }}
    }}
    """,
    f.SALES_TAX,
)

PATCH_SALES_TAX = f.build(
    f"""
    mutation PatchSalesTax($input: SalesTaxPatchInput!) {{
      salesTaxPatch(input: $input) {{
        didSucceed
        {f.INPUT_ERRORS}
        salesTax {{ ...SalesTaxFields }}
      }}
    }}
    """,
    f.SALES_TAX,
)

ARCHIVE_SALES_TAX = f.build(
    f"""
    mutation ArchiveSalesTax($input: SalesTaxArchiveInput!) {{
      salesTaxArchive(input: $input) {{
        didSucceed
        {f.INPUT_ERRORS}
        salesTax {{ id name isArchived }}
      }}
    }}
    """,
)

SALES_TAX_COLUMNS = [
    ("Name", "name"),
    ("Abbr.", "abbreviation"),
    ("ID", "id"),
    ("Rate", "rate"),
    ("Compound", lambda r: yes_no(r.get("isCompound"))),
    ("Recoverable", lambda r: yes_no(r.get("isRecoverable"))),
    ("Archived", lambda r: yes_no(r.get("isArchived"))),
]


def _normalize_rates(rates: Optional[List[Any]]) -> Optional[List[Dict[str, str]]]:
    """Validate a rate schedule for `wave_patch_sales_tax`.

    Wave models a rate change as a dated entry rather than an edit, so history
    stays intact and past invoices keep the rate they were issued under.
    """
    if not rates:
        return None
    normalized = []
    for index, entry in enumerate(rates):
        if not isinstance(entry, dict):
            raise WaveError(
                f"Rate {index + 1} must be an object like "
                '{"effective": "2026-01-01", "rate": "0.07"}.'
            )
        effective = entry.get("effective") or entry.get("effectiveDate")
        rate = entry.get("rate")
        if not effective or rate is None:
            raise WaveError(
                f"Rate {index + 1} needs both 'effective' (YYYY-MM-DD) and 'rate'."
            )
        normalized.append({"effective": str(effective), "rate": decimal_str(rate)})
    return normalized


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def wave_list_sales_taxes(
    business_id: Optional[str] = None,
    is_archived: Optional[bool] = None,
    modified_after: Optional[str] = None,
    modified_before: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    fetch_all: bool = False,
    response_format: str = "markdown",
) -> str:
    """List sales taxes, with their current rate and rate history.

    Args:
        business_id: Business to read. Defaults to the session business.
        is_archived: Filter to archived (true) or active (false) taxes.
        modified_after: ISO 8601 timestamp; only taxes changed after it.
        modified_before: ISO 8601 timestamp; only taxes changed before it.
        page: 1-based page number.
        page_size: Records per page (1-200).
        fetch_all: Walk every page.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    result = await client.paginate(
        LIST_SALES_TAXES,
        compact(
            {
                "businessId": resolved,
                "isArchived": is_archived,
                "modifiedAtAfter": modified_after,
                "modifiedAtBefore": modified_before,
            }
        ),
        path=("business", "salesTaxes"),
        page=page,
        page_size=page_size,
        fetch_all=fetch_all,
    )

    def as_markdown() -> str:
        return listing(result, "Sales taxes", SALES_TAX_COLUMNS)

    return render(result, response_format, as_markdown)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def wave_get_sales_tax(
    sales_tax_id: str,
    business_id: Optional[str] = None,
    response_format: str = "markdown",
) -> str:
    """Get one sales tax by ID, including its full rate history.

    Args:
        sales_tax_id: The Wave sales tax ID.
        business_id: Business the tax belongs to. Defaults to the session business.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    data = await client.execute(
        GET_SALES_TAX, {"businessId": resolved, "id": sales_tax_id}
    )
    tax = (data.get("business") or {}).get("salesTax")
    if not tax:
        return f"No sales tax found with ID `{sales_tax_id}` in this business."

    def as_markdown() -> str:
        rates = tax.get("rates") or []
        history = "; ".join(f"{r['effective']}: {r['rate']}" for r in rates)
        return f"**{tax['name']} ({tax['abbreviation']})**\n\n" + kv_block(
            [
                ("ID", f"`{tax['id']}`"),
                ("Current rate", tax.get("rate")),
                ("Rate history", history),
                ("Description", tax.get("description")),
                ("Tax number", tax.get("taxNumber")),
                ("Show number on invoices", yes_no(tax.get("showTaxNumberOnInvoices"))),
                ("Compound", yes_no(tax.get("isCompound"))),
                ("Recoverable", yes_no(tax.get("isRecoverable"))),
                ("Archived", yes_no(tax.get("isArchived"))),
                ("Created", tax.get("createdAt")),
                ("Modified", tax.get("modifiedAt")),
            ]
        )

    return render(tax, response_format, as_markdown)


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def wave_create_sales_tax(
    name: str,
    abbreviation: str,
    rate: str,
    business_id: Optional[str] = None,
    description: Optional[str] = None,
    tax_number: Optional[str] = None,
    show_tax_number_on_invoices: Optional[bool] = None,
    is_compound: Optional[bool] = None,
    is_recoverable: Optional[bool] = None,
    response_format: str = "markdown",
) -> str:
    """Create a sales tax.

    The rate is a decimal fraction, not a percentage: 7% is "0.07".

    Args:
        name: Full name, e.g. "Harmonized Sales Tax". Required.
        abbreviation: Short code shown on invoices, e.g. "HST". Required.
        rate: Decimal fraction, e.g. "0.13" for 13%. Required.
        business_id: Business to create in. Defaults to the session business.
        description: Optional description.
        tax_number: Your registration number for this tax.
        show_tax_number_on_invoices: Print the registration number on invoices.
        is_compound: Calculate this tax on top of other taxes rather than on the
            subtotal.
        is_recoverable: Tax you can reclaim, such as input tax credits.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    payload = compact(
        {
            "businessId": resolved,
            "name": name,
            "abbreviation": abbreviation,
            "rate": decimal_str(rate),
            "description": description,
            "taxNumber": tax_number,
            "showTaxNumberOnInvoices": show_tax_number_on_invoices,
            "isCompound": is_compound,
            "isRecoverable": is_recoverable,
        }
    )
    result = await client.mutate(
        CREATE_SALES_TAX, {"input": payload}, root_field="salesTaxCreate"
    )
    tax = result.get("salesTax") or {}
    return render(
        tax,
        response_format,
        lambda: success(
            f"Created sales tax **{tax.get('name', name)}** ({tax.get('abbreviation', abbreviation)}).",
            [("ID", f"`{tax.get('id')}`"), ("Rate", tax.get("rate"))],
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
async def wave_patch_sales_tax(
    sales_tax_id: str,
    name: Optional[str] = None,
    abbreviation: Optional[str] = None,
    description: Optional[str] = None,
    tax_number: Optional[str] = None,
    show_tax_number_on_invoices: Optional[bool] = None,
    rates: Optional[List[Dict[str, Any]]] = None,
    response_format: str = "markdown",
) -> str:
    """Update a sales tax, including scheduling a new rate.

    A rate is never edited in place: add a dated entry to `rates` and Wave
    applies it from that date on, so invoices issued earlier keep the old rate.
    Whether a tax is compound or recoverable is fixed at creation.

    Args:
        sales_tax_id: The sales tax to update. Required.
        name: New full name.
        abbreviation: New short code.
        description: New description.
        tax_number: New registration number.
        show_tax_number_on_invoices: Print the registration number on invoices.
        rates: Rate schedule entries, each
            `{"effective": "YYYY-MM-DD", "rate": "0.07"}`.
        response_format: "markdown" or "json".
    """
    client = get_client()
    payload = compact(
        {
            "id": sales_tax_id,
            "name": name,
            "abbreviation": abbreviation,
            "description": description,
            "taxNumber": tax_number,
            "showTaxNumberOnInvoices": show_tax_number_on_invoices,
            "rates": _normalize_rates(rates),
        }
    )
    if len(payload) == 1:
        return "Nothing to update. Supply at least one field to change."

    result = await client.mutate(
        PATCH_SALES_TAX, {"input": payload}, root_field="salesTaxPatch"
    )
    tax = result.get("salesTax") or {}
    return render(
        tax,
        response_format,
        lambda: success(
            f"Updated sales tax **{tax.get('name')}**.",
            [("ID", f"`{tax.get('id')}`"), ("Current rate", tax.get("rate"))],
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
async def wave_archive_sales_tax(sales_tax_id: str) -> str:
    """Archive a sales tax so it stops appearing on new invoices.

    Existing invoices keep the tax they were issued with.

    Args:
        sales_tax_id: The sales tax to archive.
    """
    client = get_client()
    result = await client.mutate(
        ARCHIVE_SALES_TAX, {"input": {"id": sales_tax_id}}, root_field="salesTaxArchive"
    )
    tax = result.get("salesTax") or {}
    name = tax.get("name") or sales_tax_id
    return (
        f"Archived sales tax **{name}**. Existing invoices keep it; it no "
        "longer appears on new ones."
    )
