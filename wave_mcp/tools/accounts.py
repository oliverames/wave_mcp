"""Chart-of-accounts tools: list, get, create, patch, archive."""

from __future__ import annotations

from typing import List, Optional

from .. import fragments as f
from ..formatting import kv_block, listing, render, success, yes_no
from ..runtime import business_id_or_default, get_client, mcp
from .common import compact

LIST_ACCOUNTS = f.build(
    """
    query ListAccounts(
      $businessId: ID!
      $page: Int!
      $pageSize: Int!
      $types: [AccountTypeValue!]
      $subtypes: [AccountSubtypeValue!]
      $excludedSubtypes: [AccountSubtypeValue!]
      $isArchived: Boolean
    ) {
      business(id: $businessId) {
        id
        accounts(
          page: $page
          pageSize: $pageSize
          types: $types
          subtypes: $subtypes
          excludedSubtypes: $excludedSubtypes
          isArchived: $isArchived
        ) {
          pageInfo { ...PageInfoFields }
          edges { node { ...AccountFields } }
        }
      }
    }
    """,
    f.PAGE_INFO,
    f.ACCOUNT,
)

GET_ACCOUNT = f.build(
    """
    query GetAccount($businessId: ID!, $id: ID!) {
      business(id: $businessId) { id account(id: $id) { ...AccountFields } }
    }
    """,
    f.ACCOUNT,
)

CREATE_ACCOUNT = f.build(
    f"""
    mutation CreateAccount($input: AccountCreateInput!) {{
      accountCreate(input: $input) {{
        didSucceed
        {f.INPUT_ERRORS}
        account {{ ...AccountFields }}
      }}
    }}
    """,
    f.ACCOUNT,
)

PATCH_ACCOUNT = f.build(
    f"""
    mutation PatchAccount($input: AccountPatchInput!) {{
      accountPatch(input: $input) {{
        didSucceed
        {f.INPUT_ERRORS}
        account {{ ...AccountFields }}
      }}
    }}
    """,
    f.ACCOUNT,
)

ARCHIVE_ACCOUNT = f"""
mutation ArchiveAccount($input: AccountArchiveInput!) {{
  accountArchive(input: $input) {{
    didSucceed
    {f.INPUT_ERRORS}
  }}
}}
"""

ACCOUNT_COLUMNS = [
    ("Name", "name"),
    ("ID", "id"),
    ("Type", lambda r: (r.get("type") or {}).get("name", "-")),
    ("Subtype", lambda r: (r.get("subtype") or {}).get("name", "-")),
    ("Balance", "balance"),
    ("Currency", lambda r: (r.get("currency") or {}).get("code", "-")),
    ("Archived", lambda r: yes_no(r.get("isArchived"))),
]


def _account_detail(account: dict) -> str:
    return f"**{account['name']}**\n\n" + kv_block(
        [
            ("ID", f"`{account['id']}`"),
            ("Display ID", account.get("displayId")),
            ("Description", account.get("description")),
            ("Type", (account.get("type") or {}).get("name")),
            ("Subtype", (account.get("subtype") or {}).get("name")),
            ("Normal balance", account.get("normalBalanceType")),
            ("Balance", account.get("balance")),
            ("Balance in business currency", account.get("balanceInBusinessCurrency")),
            ("Currency", (account.get("currency") or {}).get("code")),
            ("Sequence", account.get("sequence")),
            ("Archived", yes_no(account.get("isArchived"))),
        ]
    )


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def wave_list_accounts(
    business_id: Optional[str] = None,
    types: Optional[List[str]] = None,
    subtypes: Optional[List[str]] = None,
    excluded_subtypes: Optional[List[str]] = None,
    is_archived: Optional[bool] = None,
    page: int = 1,
    page_size: int = 50,
    fetch_all: bool = False,
    response_format: str = "markdown",
) -> str:
    """List the chart of accounts, with balances.

    Filter by type to find the account a transaction needs: EXPENSE for expense
    categories, INCOME for revenue, ASSET with subtype CASH_AND_BANK for bank
    accounts, LIABILITY with subtype CREDIT_CARD for cards.

    Args:
        business_id: Business to read. Defaults to the session business.
        types: Filter by type: ASSET, LIABILITY, EQUITY, INCOME, EXPENSE.
        subtypes: Filter by subtype, e.g. ["CASH_AND_BANK", "CREDIT_CARD"].
        excluded_subtypes: Subtypes to omit.
        is_archived: Filter to archived (true) or active (false) accounts.
        page: 1-based page number.
        page_size: Records per page (1-200).
        fetch_all: Walk every page. Recommended here, since a chart of accounts
            is usually small and partial results are misleading.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    result = await client.paginate(
        LIST_ACCOUNTS,
        compact(
            {
                "businessId": resolved,
                "types": [t.upper() for t in types] if types else None,
                "subtypes": [s.upper() for s in subtypes] if subtypes else None,
                "excludedSubtypes": (
                    [s.upper() for s in excluded_subtypes] if excluded_subtypes else None
                ),
                "isArchived": is_archived,
            }
        ),
        path=("business", "accounts"),
        page=page,
        page_size=page_size,
        fetch_all=fetch_all,
    )

    def as_markdown() -> str:
        return listing(result, "Chart of accounts", ACCOUNT_COLUMNS)

    return render(result, response_format, as_markdown)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def wave_get_account(
    account_id: str,
    business_id: Optional[str] = None,
    response_format: str = "markdown",
) -> str:
    """Get one account by ID, including its current balance.

    Args:
        account_id: The Wave account ID.
        business_id: Business the account belongs to. Defaults to the session business.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    data = await client.execute(GET_ACCOUNT, {"businessId": resolved, "id": account_id})
    account = (data.get("business") or {}).get("account")
    if not account:
        return f"No account found with ID `{account_id}` in this business."
    return render(account, response_format, lambda: _account_detail(account))


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def wave_create_account(
    name: str,
    subtype: str,
    business_id: Optional[str] = None,
    description: Optional[str] = None,
    display_id: Optional[str] = None,
    currency: Optional[str] = None,
    can_archive: Optional[bool] = None,
    response_format: str = "markdown",
) -> str:
    """Create an account in the chart of accounts.

    The subtype determines the account's type: pick it with
    `wave_list_account_subtypes`. Common choices are EXPENSE, INCOME,
    CASH_AND_BANK, CREDIT_CARD, and COST_OF_GOODS_SOLD.

    Args:
        name: Account name, e.g. "Software Subscriptions".
        subtype: Subtype value such as "EXPENSE" or "CASH_AND_BANK".
        business_id: Business to create in. Defaults to the session business.
        description: Optional longer description.
        display_id: Optional account number used for ordering and reports.
        currency: Currency code. Defaults to the business currency.
        can_archive: Whether the account may be archived later.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    payload = compact(
        {
            "businessId": resolved,
            "name": name,
            "subtype": subtype.upper(),
            "description": description,
            "displayId": display_id,
            "currency": currency.upper() if currency else None,
            "restrictions": None if can_archive is None else {"canArchive": can_archive},
        }
    )
    result = await client.mutate(
        CREATE_ACCOUNT, {"input": payload}, root_field="accountCreate"
    )
    account = result.get("account") or {}

    def as_markdown() -> str:
        return success(
            f"Created account **{account.get('name', name)}**.",
            [
                ("ID", f"`{account.get('id')}`"),
                ("Type", (account.get("type") or {}).get("name")),
                ("Subtype", (account.get("subtype") or {}).get("name")),
                ("Currency", (account.get("currency") or {}).get("code")),
            ],
        )

    return render(account, response_format, as_markdown)


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def wave_patch_account(
    account_id: str,
    sequence: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    display_id: Optional[str] = None,
    response_format: str = "markdown",
) -> str:
    """Update an account's name, description, or display ID.

    Wave requires the account's current `sequence` as an optimistic-concurrency
    check, so read the account first with `wave_get_account` and pass the value
    it returns. Only the fields you supply are changed.

    Args:
        account_id: The account to update.
        sequence: The account's current sequence, from `wave_get_account`.
        name: New name.
        description: New description.
        display_id: New display ID / account number.
        response_format: "markdown" or "json".
    """
    client = get_client()
    payload = compact(
        {
            "id": account_id,
            "sequence": sequence,
            "name": name,
            "description": description,
            "displayId": display_id,
        }
    )
    if len(payload) <= 2:
        return (
            "Nothing to update. Supply at least one of name, description, or "
            "display_id."
        )
    result = await client.mutate(
        PATCH_ACCOUNT, {"input": payload}, root_field="accountPatch"
    )
    account = result.get("account") or {}
    return render(
        account,
        response_format,
        lambda: success(f"Updated account **{account.get('name')}**.", [("ID", f"`{account.get('id')}`")]),
    )


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def wave_archive_account(account_id: str) -> str:
    """Archive an account, hiding it from pickers while keeping its history.

    Wave has no delete for accounts, and no un-archive through the API: to
    restore one, use the Wave web app. Accounts carrying a balance or created
    by Wave itself cannot be archived.

    Args:
        account_id: The account to archive.
    """
    client = get_client()
    await client.mutate(
        ARCHIVE_ACCOUNT, {"input": {"id": account_id}}, root_field="accountArchive"
    )
    return (
        f"Archived account `{account_id}`. Past transactions keep it; it no "
        "longer appears when categorizing. Restore it from the Wave web app."
    )
