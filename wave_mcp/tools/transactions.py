"""Money (bookkeeping) transaction tools.

These write directly to the general ledger: expenses, income, transfers, and
merchant deposits. Wave's model is double-entry:

* The *anchor* is the account the money physically moved through -- a bank
  account or credit card -- with a direction of DEPOSIT or WITHDRAWAL.
* The *line items* are the categories that money is attributed to, and their
  amounts must total the anchor amount.

One important asymmetry: Wave's public API can create money transactions but
offers no query to read them back. There is no `transactions` connection on
`Business` in the schema. To review recorded transactions, use the Wave web app
or an accounting report.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .. import fragments as f
from ..errors import WaveError
from ..formatting import render, success, table
from ..runtime import business_id_or_default, get_client, mcp
from .common import compact, decimal_str

CREATE_TRANSACTION = f"""
mutation CreateMoneyTransaction($input: MoneyTransactionCreateInput!) {{
  moneyTransactionCreate(input: $input) {{
    didSucceed
    {f.INPUT_ERRORS}
    transaction {{ id }}
  }}
}}
"""

CREATE_TRANSACTIONS = f"""
mutation CreateMoneyTransactions($input: MoneyTransactionsCreateInput!) {{
  moneyTransactionsCreate(input: $input) {{
    didSucceed
    {f.INPUT_ERRORS}
    transactions {{ id }}
  }}
}}
"""

CREATE_DEPOSIT_TRANSACTION = f"""
mutation CreateMoneyDepositTransaction(
  $input: MoneyDepositTransactionCreateInput!
) {{
  moneyDepositTransactionCreate(input: $input) {{
    didSucceed
    {f.INPUT_ERRORS}
  }}
}}
"""


def _external_id(prefix: str, supplied: Optional[str]) -> str:
    """Return the caller's external ID, or mint one.

    Wave requires ``externalId`` and uses it to deduplicate, so passing a
    stable value of your own makes retries safe. When none is given, a
    timestamped value keeps each call distinct.
    """
    if supplied:
        return supplied
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}"


def _normalize_transaction_line_items(
    line_items: List[Dict[str, Any]],
    *,
    context: str,
) -> List[Dict[str, Any]]:
    normalized = []
    for index, item in enumerate(line_items):
        if not isinstance(item, dict):
            raise WaveError(f"{context} line item {index + 1} must be an object.")
        account_id = item.get("accountId") or item.get("account_id")
        amount = item.get("amount")
        if not account_id:
            raise WaveError(
                f"{context} line item {index + 1} is missing accountId. Call "
                "wave_list_accounts to find the category account."
            )
        if amount is None:
            raise WaveError(f"{context} line item {index + 1} is missing amount.")

        entry: Dict[str, Any] = {
            "accountId": account_id,
            "amount": decimal_str(amount),
            "balance": str(item.get("balance", "INCREASE")).upper(),
        }
        if item.get("customerId") or item.get("customer_id"):
            entry["customerId"] = item.get("customerId") or item.get("customer_id")
        if item.get("description") is not None:
            entry["description"] = item["description"]
        if item.get("taxes"):
            entry["taxes"] = [
                _normalize_transaction_tax(t, context, index) for t in item["taxes"]
            ]
        normalized.append(entry)
    return normalized


def _normalize_transaction_tax(tax: Any, context: str, index: int) -> Dict[str, str]:
    if not isinstance(tax, dict):
        raise WaveError(
            f"{context} line item {index + 1} has an invalid tax entry; expected "
            '{"salesTaxId": "...", "amount": "1.30"}.'
        )
    sales_tax_id = tax.get("salesTaxId") or tax.get("sales_tax_id")
    amount = tax.get("amount")
    if not sales_tax_id or amount is None:
        raise WaveError(
            f"{context} line item {index + 1} tax entries need both salesTaxId "
            "and amount."
        )
    return {"salesTaxId": sales_tax_id, "amount": decimal_str(amount)}


def _check_balance(anchor_amount: Any, line_items: List[Dict[str, Any]], *, context: str) -> None:
    """Warn early when line items do not sum to the anchor amount.

    Wave rejects an unbalanced transaction, but its error names neither figure.
    Comparing here produces a message that says exactly what is off, and by how
    much.
    """
    from decimal import Decimal, InvalidOperation

    try:
        anchor = Decimal(str(anchor_amount))
        total = sum(Decimal(str(item["amount"])) for item in line_items)
    except (InvalidOperation, ArithmeticError):
        return  # Let Wave adjudicate anything unparseable.

    if anchor != total:
        raise WaveError(
            f"{context} does not balance: the anchor amount is {anchor} but the "
            f"line items total {total} (a difference of {anchor - total}). Every "
            "line item amount must add up to the anchor amount."
        )


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def wave_create_money_transaction(
    anchor_account_id: str,
    direction: str,
    amount: str,
    date: str,
    description: str,
    line_items: List[Dict[str, Any]],
    business_id: Optional[str] = None,
    external_id: Optional[str] = None,
    notes: Optional[str] = None,
    response_format: str = "markdown",
) -> str:
    """Record a bookkeeping transaction: an expense, income, or transfer.

    Wave is double-entry, so a transaction has two sides:

    - The **anchor** is the bank account or credit card the money moved
      through. `direction` is WITHDRAWAL for money out, DEPOSIT for money in.
    - The **line items** are the categories it is attributed to. Their amounts
      must total the anchor amount.

    A $50 office-supplies expense paid from checking: anchor is the checking
    account with direction WITHDRAWAL and amount "50.00"; one line item on the
    Office Supplies expense account for "50.00".

    Split transactions just add line items. A $100 withdrawal covering $60 of
    fuel and $40 of meals is one anchor and two line items.

    Args:
        anchor_account_id: Bank or credit card account the money moved through.
            Required.
        direction: WITHDRAWAL for money out, DEPOSIT for money in. Required.
        amount: Total transaction amount, e.g. "50.00". Required.
        date: Transaction date, YYYY-MM-DD. Required.
        description: What the transaction was for. Required.
        line_items: Category allocations, each
            `{"accountId": "...", "amount": "50.00", "balance": "INCREASE",
            "description": "...", "customerId": "...",
            "taxes": [{"salesTaxId": "...", "amount": "6.50"}]}`.
            Amounts must total `amount`. Required.
        business_id: Business to record in. Defaults to the session business.
        external_id: Your own idempotency key. Wave dedupes on it, so reusing a
            value makes a retry safe. Generated if omitted.
        notes: Internal note on the transaction.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    if not line_items:
        raise WaveError(
            "At least one line item is required. Each needs an accountId and an "
            "amount, and together they must total the anchor amount."
        )
    normalized = _normalize_transaction_line_items(
        line_items, context="wave_create_money_transaction"
    )
    _check_balance(amount, normalized, context="wave_create_money_transaction")

    payload = compact(
        {
            "businessId": resolved,
            "externalId": _external_id("wave-mcp", external_id),
            "date": date,
            "description": description,
            "notes": notes,
            "anchor": {
                "accountId": anchor_account_id,
                "amount": decimal_str(amount),
                "direction": direction.upper(),
            },
            "lineItems": normalized,
        }
    )
    result = await client.mutate(
        CREATE_TRANSACTION, {"input": payload}, root_field="moneyTransactionCreate"
    )
    transaction = result.get("transaction") or {}
    return render(
        transaction,
        response_format,
        lambda: success(
            f"Recorded a {direction.upper()} of {amount} on {date}.",
            [
                ("Transaction ID", f"`{transaction.get('id')}`"),
                ("Description", description),
                ("Line items", len(normalized)),
                ("External ID", payload["externalId"]),
            ],
        ),
    )


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def wave_create_money_transactions(
    transactions: List[Dict[str, Any]],
    business_id: Optional[str] = None,
    response_format: str = "markdown",
) -> str:
    """Record several bookkeeping transactions in one call.

    Wave applies the batch atomically: if one transaction is rejected, none are
    recorded. Use this for bulk import rather than looping over
    `wave_create_money_transaction`.

    Each entry takes the same shape as a single transaction:

        {
          "date": "2026-07-01",
          "description": "Office supplies",
          "externalId": "import-001",
          "notes": "optional",
          "anchor": {"accountId": "...", "amount": "50.00",
                     "direction": "WITHDRAWAL"},
          "lineItems": [{"accountId": "...", "amount": "50.00",
                         "balance": "INCREASE"}]
        }

    Args:
        transactions: The transactions to record. Required.
        business_id: Business to record in. Defaults to the session business.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    if not transactions:
        raise WaveError("Supply at least one transaction.")

    prepared = []
    for index, entry in enumerate(transactions):
        if not isinstance(entry, dict):
            raise WaveError(f"Transaction {index + 1} must be an object.")
        anchor = entry.get("anchor")
        if not isinstance(anchor, dict):
            raise WaveError(
                f"Transaction {index + 1} is missing its anchor. Supply "
                '{"accountId": "...", "amount": "...", "direction": "WITHDRAWAL"}.'
            )
        for field in ("date", "description"):
            if not entry.get(field):
                raise WaveError(f"Transaction {index + 1} is missing '{field}'.")

        normalized = _normalize_transaction_line_items(
            entry.get("lineItems") or entry.get("line_items") or [],
            context=f"Transaction {index + 1}",
        )
        if not normalized:
            raise WaveError(f"Transaction {index + 1} has no line items.")
        _check_balance(
            anchor.get("amount"), normalized, context=f"Transaction {index + 1}"
        )

        prepared.append(
            compact(
                {
                    "externalId": _external_id(f"wave-mcp-batch-{index + 1}", entry.get("externalId")),
                    "date": entry["date"],
                    "description": entry["description"],
                    "notes": entry.get("notes"),
                    "anchor": {
                        "accountId": anchor["accountId"],
                        "amount": decimal_str(anchor["amount"]),
                        "direction": str(anchor["direction"]).upper(),
                    },
                    "lineItems": normalized,
                }
            )
        )

    result = await client.mutate(
        CREATE_TRANSACTIONS,
        {"input": {"businessId": resolved, "transactions": prepared}},
        root_field="moneyTransactionsCreate",
    )
    created = [t for t in (result.get("transactions") or []) if t]

    def as_markdown() -> str:
        body = success(f"Recorded {len(created)} transaction(s).")
        if created:
            body += "\n\n" + table(
                [
                    {"n": i + 1, "id": t.get("id"), "description": prepared[i]["description"]}
                    for i, t in enumerate(created)
                ],
                [("#", "n"), ("Transaction ID", "id"), ("Description", "description")],
            )
        return body

    return render(created, response_format, as_markdown)


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def wave_create_deposit_transaction(
    deposit_account_id: str,
    deposit_amount: str,
    date: str,
    description: str,
    line_items: List[Dict[str, Any]],
    business_id: Optional[str] = None,
    fees: Optional[List[Dict[str, Any]]] = None,
    origin: str = "MANUAL",
    external_id: Optional[str] = None,
    notes: Optional[str] = None,
    response_format: str = "markdown",
) -> str:
    """Record a deposit whose net differs from its gross because of fees.

    This is the shape of a payment-processor payout: the customer paid $100,
    the processor kept $3, and $97 reached the bank. `deposit_amount` is the
    $97 that landed, line items carry the $100 of income, and fees carry the
    $3, so gross and net both stay correct.

    Line items must total the deposit amount plus the fees.

    Args:
        deposit_account_id: Bank account the net amount landed in. Required.
        deposit_amount: Net amount deposited, e.g. "97.00". Required.
        date: Deposit date, YYYY-MM-DD. Required.
        description: What the deposit was for. Required.
        line_items: Gross allocations, each
            `{"accountId": "...", "amount": "100.00", "customerId": "...",
            "taxes": [{"abbreviation": "HST", "amount": "13.00"}]}`. Required.
        business_id: Business to record in. Defaults to the session business.
        fees: Amounts withheld, each `{"accountId": "...", "amount": "3.00"}`.
            The account is usually a payment-processing-fees expense account.
        origin: MANUAL or ZAPIER. Defaults to MANUAL.
        external_id: Your own idempotency key. Generated if omitted.
        notes: Internal note on the transaction.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    if not line_items:
        raise WaveError("At least one line item is required.")

    normalized_items = []
    for index, item in enumerate(line_items):
        if not isinstance(item, dict):
            raise WaveError(f"Line item {index + 1} must be an object.")
        account_id = item.get("accountId") or item.get("account_id")
        amount = item.get("amount")
        if not account_id or amount is None:
            raise WaveError(
                f"Line item {index + 1} needs both accountId and amount."
            )
        entry: Dict[str, Any] = {
            "accountId": account_id,
            "amount": float(amount),
            # Wave requires the taxes list to be present even when empty.
            "taxes": [
                {
                    "abbreviation": t["abbreviation"],
                    "amount": float(t["amount"]),
                }
                for t in (item.get("taxes") or [])
            ],
        }
        if item.get("customerId") or item.get("customer_id"):
            entry["customerId"] = item.get("customerId") or item.get("customer_id")
        normalized_items.append(entry)

    normalized_fees = []
    for index, fee in enumerate(fees or []):
        if not isinstance(fee, dict):
            raise WaveError(f"Fee {index + 1} must be an object.")
        account_id = fee.get("accountId") or fee.get("account_id")
        amount = fee.get("amount")
        if not account_id or amount is None:
            raise WaveError(f"Fee {index + 1} needs both accountId and amount.")
        normalized_fees.append({"accountId": account_id, "amount": float(amount)})

    payload = compact(
        {
            "businessId": resolved,
            "externalId": _external_id("wave-mcp-deposit", external_id),
            "date": date,
            "description": description,
            "notes": notes,
            "origin": origin.upper(),
            "deposit": {
                "accountId": deposit_account_id,
                "amount": float(deposit_amount),
            },
            "lineItems": normalized_items,
            "fees": normalized_fees or None,
        }
    )
    await client.mutate(
        CREATE_DEPOSIT_TRANSACTION,
        {"input": payload},
        root_field="moneyDepositTransactionCreate",
    )

    fee_total = sum(fee["amount"] for fee in normalized_fees)
    gross = sum(item["amount"] for item in normalized_items)
    return render(
        {"deposited": deposit_amount, "gross": gross, "fees": fee_total},
        response_format,
        lambda: success(
            f"Recorded a deposit of {deposit_amount} on {date}.",
            [
                ("Description", description),
                ("Gross line items", f"{gross:.2f}"),
                ("Fees withheld", f"{fee_total:.2f}"),
                ("External ID", payload["externalId"]),
            ],
        )
        + (
            "\n\nWave's API returns no ID for deposit transactions, so there is "
            "nothing to reference later. Find it in the Wave web app under "
            "Accounting > Transactions."
        ),
    )
