"""Convenience wrappers that resolve account names for you.

These carry forward the workflow the original server was built around: hand it
a receipt and a category word like "meals" or "fuel", and it finds the matching
expense account itself instead of making you look up an ID first.

They are shortcuts over `wave_create_money_transaction`, which remains the
tool to reach for when you already know the account IDs or need a split
transaction.

One deliberate change from the original implementation: its account matcher
carried a hardcoded rule for apartment numbers 142-146, specific to that
author's rental properties, which would silently mis-categorize anyone else's
income. The generic scoring -- exact match, prefix, per-word, then a synonym
table -- is kept; the hardcoded rule is not.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from ..errors import WaveError
from ..formatting import render, success
from ..runtime import ResponseFormat, business_id_or_default, get_client, tool
from .accounts import LIST_ACCOUNTS
from .transactions import CREATE_TRANSACTION, _external_id

# Everyday words mapped onto the vocabulary a chart of accounts actually uses.
EXPENSE_SYNONYMS: Dict[str, List[str]] = {
    "food": ["meals", "restaurant", "dining", "entertainment"],
    "gas": ["fuel", "gasoline", "petrol", "diesel", "motor"],
    "travel": ["transportation", "transport", "trip", "mileage", "airfare"],
    "office": ["supplies", "equipment", "materials", "stationery"],
    "car": ["vehicle", "auto", "automobile", "automotive", "motor"],
    "phone": ["mobile", "cellular", "telecommunications", "telecom"],
    "internet": ["web", "online", "broadband", "wifi", "telecommunications"],
    "insurance": ["coverage", "policy", "premium"],
    "rent": ["rental", "lease", "leasing", "occupancy"],
    "utilities": ["electric", "electricity", "water", "power", "heat"],
    "marketing": ["advertising", "promotion", "ads", "publicity"],
    "software": ["subscription", "saas", "dues", "computer"],
    "training": ["education", "learning", "course", "workshop", "development"],
    "legal": ["attorney", "lawyer", "law", "professional"],
    "accounting": ["bookkeeping", "tax", "professional", "financial"],
    "maintenance": ["repair", "service", "upkeep", "cleaning"],
    "bank": ["fees", "charges", "service charge", "interest"],
    "shipping": ["postage", "freight", "delivery", "courier"],
}

INCOME_SYNONYMS: Dict[str, List[str]] = {
    "sales": ["revenue", "income", "receipts", "earnings", "product"],
    "consulting": ["services", "professional", "advisory", "fees"],
    "freelance": ["contract", "project", "services"],
    "commission": ["referral", "bonus", "incentive"],
    "interest": ["dividend", "investment", "return"],
    "rental": ["rent", "lease", "property", "tenant", "leasing"],
    "royalty": ["licensing", "intellectual property", "patent"],
    "other": ["miscellaneous", "misc", "various", "general"],
}

# Below this, a match is too weak to act on silently.
MIN_CONFIDENCE = 0.55


def _score_account(
    category: str,
    account: Dict[str, Any],
    synonyms: Dict[str, List[str]],
) -> Tuple[float, str]:
    """Score how well one account matches a category word.

    Returns ``(score, explanation)``. The explanation travels back to the
    caller so a low-confidence pick is visible rather than silent.
    """
    needle = category.lower().strip()
    name = (account.get("name") or "").lower()

    if needle == name:
        return 1.0, f"exact name match on '{account['name']}'"
    if needle in name:
        return 0.95, f"'{category}' appears in '{account['name']}'"
    if name.startswith(needle):
        return 0.9, f"'{account['name']}' starts with '{category}'"

    best = 0.0
    why = ""

    ratio = SequenceMatcher(None, needle, name).ratio()
    if ratio > best:
        best, why = ratio, f"'{account['name']}' is similar to '{category}'"

    for word in name.split():
        word_ratio = SequenceMatcher(None, needle, word).ratio()
        if word_ratio > best:
            best, why = word_ratio, f"'{word}' in '{account['name']}' matches '{category}'"

    # Synonyms: map the caller's word onto a family of related terms, then look
    # for any of them in the account name.
    for key, related in synonyms.items():
        family = [key] + related
        if needle not in family:
            continue
        for term in family:
            if term in name:
                score = 0.85 if term == key else 0.8
                if score > best:
                    best = score
                    why = (
                        f"'{category}' relates to '{term}', found in "
                        f"'{account['name']}'"
                    )

    return best, why


def _match_account(
    category: str,
    accounts: List[Dict[str, Any]],
    synonyms: Dict[str, List[str]],
    *,
    kind: str,
) -> Tuple[Dict[str, Any], float, str]:
    if not accounts:
        raise WaveError(
            f"This business has no active {kind} accounts, so there is nothing "
            f"to categorize against. Create one with wave_create_account."
        )

    scored = [(*_score_account(category, acc, synonyms), acc) for acc in accounts]
    scored.sort(key=lambda entry: entry[0], reverse=True)
    best_score, best_why, best_account = scored[0]

    if best_score < MIN_CONFIDENCE:
        options = ", ".join(f"'{a['name']}'" for a in accounts[:10])
        raise WaveError(
            f"No {kind} account confidently matches '{category}' (best was "
            f"'{best_account['name']}' at {best_score:.0%}). Rather than guess, "
            f"pick one explicitly and call wave_create_money_transaction. "
            f"Available {kind} accounts: {options}"
        )

    return best_account, best_score, best_why


async def _active_accounts(business_id: str, types: List[str]) -> List[Dict[str, Any]]:
    client = get_client()
    result = await client.paginate(
        LIST_ACCOUNTS,
        {"businessId": business_id, "types": types, "isArchived": False},
        path=("business", "accounts"),
        page_size=200,
        fetch_all=True,
    )
    return result["items"]


async def _payment_account(
    business_id: str,
    requested_name: Optional[str],
    *,
    purpose: str,
) -> Dict[str, Any]:
    """Resolve the bank or card account money moves through."""
    accounts = await _active_accounts(business_id, ["ASSET", "LIABILITY"])
    usable = [
        a
        for a in accounts
        if (a.get("subtype") or {}).get("value")
        in ("CASH_AND_BANK", "CREDIT_CARD", "LOANS", "MONEY_IN_TRANSIT")
    ]
    if not usable:
        raise WaveError(
            "No bank, cash, or credit card account exists in this business, so "
            f"there is nothing to {purpose}. Create one with wave_create_account "
            'using subtype "CASH_AND_BANK".'
        )

    if not requested_name:
        return usable[0]

    needle = requested_name.lower().strip()
    for account in usable:
        if (account.get("name") or "").lower() == needle:
            return account
    for account in usable:
        if needle in (account.get("name") or "").lower():
            return account

    options = ", ".join(f"'{a['name']}'" for a in usable)
    raise WaveError(
        f"No account named '{requested_name}' was found. Available accounts: {options}"
    )


@tool()
async def wave_create_expense_from_receipt(
    amount: str,
    date: str,
    category: str = "General Expenses",
    business_id: Optional[str] = None,
    vendor_name: Optional[str] = None,
    description: Optional[str] = None,
    payment_account: Optional[str] = None,
    receipt_text: Optional[str] = None,
    notes: Optional[str] = None,
    response_format: ResponseFormat = "markdown",
) -> str:
    """Record an expense, matching a category word to an expense account.

    A shortcut over `wave_create_money_transaction` for the common case of
    entering a receipt: give it "fuel" or "office supplies" and it finds the
    account. When nothing matches confidently it says so and lists the options
    rather than guessing.

    Wave's API cannot attach a vendor to a money transaction, so `vendor_name`
    is recorded in the description. Use an invoice or bill in the Wave web app
    if you need a real vendor link.

    Args:
        amount: Total on the receipt, e.g. "42.50". Required.
        date: Date on the receipt, YYYY-MM-DD. Required.
        category: Category word to match, e.g. "meals", "fuel", "software".
        business_id: Business to record in. Defaults to the session business.
        vendor_name: Merchant name, folded into the description.
        description: Overrides the generated description.
        payment_account: Name of the account paid from, e.g. "Business Checking".
            Defaults to the first bank or card account.
        receipt_text: Raw receipt text, stored in the transaction notes.
        notes: Internal note. Takes precedence over receipt_text.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)

    expense_accounts = await _active_accounts(resolved, ["EXPENSE"])
    account, score, why = _match_account(
        category, expense_accounts, EXPENSE_SYNONYMS, kind="expense"
    )
    anchor = await _payment_account(resolved, payment_account, purpose="pay from")

    final_description = description or (
        f"Expense - {vendor_name}" if vendor_name else f"Expense - {category}"
    )
    payload = {
        "businessId": resolved,
        "externalId": _external_id("wave-mcp-receipt", None),
        "date": date,
        "description": final_description,
        "notes": notes or receipt_text,
        "anchor": {
            "accountId": anchor["id"],
            "amount": str(amount),
            "direction": "WITHDRAWAL",
        },
        "lineItems": [
            {"accountId": account["id"], "amount": str(amount), "balance": "INCREASE"}
        ],
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    result = await client.mutate(
        CREATE_TRANSACTION, {"input": payload}, root_field="moneyTransactionCreate"
    )
    transaction = result.get("transaction") or {}

    def as_markdown() -> str:
        body = success(
            f"Recorded an expense of {amount} on {date}.",
            [
                ("Transaction ID", f"`{transaction.get('id')}`"),
                ("Category", f"{account['name']} (matched {score:.0%}: {why})"),
                ("Paid from", anchor["name"]),
                ("Description", final_description),
            ],
        )
        if vendor_name:
            body += (
                f"\n\nThe vendor '{vendor_name}' is recorded in the description; "
                "Wave's API cannot link a vendor to a money transaction."
            )
        return body

    return render(transaction, response_format, as_markdown)


@tool()
async def wave_create_income_from_payment(
    amount: str,
    date: str,
    income_category: str = "Sales",
    business_id: Optional[str] = None,
    customer_name: Optional[str] = None,
    payment_description: Optional[str] = None,
    description: Optional[str] = None,
    deposit_to_account: Optional[str] = None,
    notes: Optional[str] = None,
    response_format: ResponseFormat = "markdown",
) -> str:
    """Record income, matching a category word to an income account.

    A shortcut over `wave_create_money_transaction` for money received outside
    the invoicing flow. When no income account matches confidently it says so
    and lists the options rather than guessing.

    Naming a customer links the income to them on the line item, so it shows up
    in that customer's income reports.

    To record payment of an existing invoice, use
    `wave_create_invoice_payment` instead: this tool creates standalone income
    and would double-count.

    Args:
        amount: Amount received, e.g. "500.00". Required.
        date: Date received, YYYY-MM-DD. Required.
        income_category: Category word to match, e.g. "consulting", "rental".
        business_id: Business to record in. Defaults to the session business.
        customer_name: Customer who paid. Matched by name and linked if found.
        payment_description: What the payment was for.
        description: Overrides the generated description.
        deposit_to_account: Name of the account deposited to. Defaults to the
            first bank account.
        notes: Internal note on the transaction.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)

    income_accounts = await _active_accounts(resolved, ["INCOME"])
    account, score, why = _match_account(
        income_category, income_accounts, INCOME_SYNONYMS, kind="income"
    )
    anchor = await _payment_account(resolved, deposit_to_account, purpose="deposit into")

    customer_id = None
    customer_note = ""
    if customer_name:
        from .customers import LIST_CUSTOMERS

        found = await client.paginate(
            LIST_CUSTOMERS,
            {"businessId": resolved, "sort": ["NAME_ASC"]},
            path=("business", "customers"),
            page_size=200,
            fetch_all=True,
        )
        needle = customer_name.lower().strip()
        match = next(
            (c for c in found["items"] if (c.get("name") or "").lower() == needle),
            None,
        ) or next(
            (c for c in found["items"] if needle in (c.get("name") or "").lower()),
            None,
        )
        if match:
            customer_id = match["id"]
            customer_note = match["name"]
        else:
            customer_note = (
                f"'{customer_name}' not found, so the income is not linked to a "
                "customer. Create them with wave_create_customer."
            )

    line_item: Dict[str, Any] = {
        "accountId": account["id"],
        "amount": str(amount),
        "balance": "INCREASE",
    }
    if customer_id:
        line_item["customerId"] = customer_id

    final_description = (
        description or payment_description or f"Income - {income_category}"
    )
    payload = {
        "businessId": resolved,
        "externalId": _external_id("wave-mcp-income", None),
        "date": date,
        "description": final_description,
        "notes": notes,
        "anchor": {
            "accountId": anchor["id"],
            "amount": str(amount),
            "direction": "DEPOSIT",
        },
        "lineItems": [line_item],
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    result = await client.mutate(
        CREATE_TRANSACTION, {"input": payload}, root_field="moneyTransactionCreate"
    )
    transaction = result.get("transaction") or {}

    def as_markdown() -> str:
        return success(
            f"Recorded income of {amount} on {date}.",
            [
                ("Transaction ID", f"`{transaction.get('id')}`"),
                ("Category", f"{account['name']} (matched {score:.0%}: {why})"),
                ("Deposited to", anchor["name"]),
                ("Customer", customer_note or None),
                ("Description", final_description),
            ],
        )

    return render(transaction, response_format, as_markdown)
