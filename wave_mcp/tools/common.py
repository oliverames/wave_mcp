"""Helpers shared across tool modules."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from ..errors import WaveError

# Wave requires a non-null sort argument on several connections. These are the
# defaults the tools apply when the caller does not choose one.
DEFAULT_CUSTOMER_SORT = ["NAME_ASC"]
DEFAULT_INVOICE_SORT = ["INVOICE_DATE_DESC"]
DEFAULT_PRODUCT_SORT = ["NAME_ASC"]
DEFAULT_ESTIMATE_SORT = "ESTIMATE_DATE_DESC"


def optional_address(
    address_line1: Optional[str] = None,
    address_line2: Optional[str] = None,
    city: Optional[str] = None,
    province_code: Optional[str] = None,
    country_code: Optional[str] = None,
    postal_code: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Build an ``AddressInput``, or ``None`` when no part was supplied.

    Wave rejects an address object whose fields are all null, so an empty
    address has to be omitted entirely rather than sent as ``{}``.
    """
    address = {
        "addressLine1": address_line1,
        "addressLine2": address_line2,
        "city": city,
        "provinceCode": province_code,
        "countryCode": country_code,
        "postalCode": postal_code,
    }
    populated = {k: v for k, v in address.items() if v is not None}
    return populated or None


def optional_shipping(
    name: Optional[str] = None,
    phone: Optional[str] = None,
    instructions: Optional[str] = None,
    address: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    shipping = {
        "name": name,
        "phone": phone,
        "instructions": instructions,
        "address": address,
    }
    populated = {k: v for k, v in shipping.items() if v is not None}
    return populated or None


def compact(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Drop keys whose value is ``None``.

    Patch mutations treat an explicit null as "clear this field", so unset
    arguments must not reach Wave at all.
    """
    return {k: v for k, v in payload.items() if v is not None}


def require_items(items: Optional[Sequence[Any]], what: str) -> List[Any]:
    if not items:
        raise WaveError(
            f"At least one {what} is required. Supply a list such as "
            f'[{{"productId": "...", "quantity": 1, "unitPrice": "10.00"}}].'
        )
    return list(items)


def normalize_line_items(
    items: Sequence[Dict[str, Any]],
    *,
    context: str,
    allow_name: bool = False,
) -> List[Dict[str, Any]]:
    """Validate and normalize invoice/estimate line items.

    Wave requires ``productId`` on every line: a line item is always tied to a
    product, and description/price merely override that product's defaults.
    Validating here produces a far clearer message than Wave's own.
    """
    normalized: List[Dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise WaveError(
                f"{context} item {index + 1} must be an object, got {type(item).__name__}."
            )
        product_id = item.get("productId") or item.get("product_id")
        if not product_id:
            raise WaveError(
                f"{context} item {index + 1} is missing productId. Every Wave "
                "line item must reference a product -- call wave_list_products "
                "to find one, or wave_create_product to add it."
            )
        entry: Dict[str, Any] = {"productId": product_id}

        description = item.get("description")
        if description is not None:
            entry["description"] = description
        if allow_name and item.get("name") is not None:
            entry["name"] = item["name"]

        quantity = item.get("quantity")
        if quantity is not None:
            entry["quantity"] = str(quantity)

        unit_price = item.get("unitPrice", item.get("unit_price"))
        if unit_price is not None:
            entry["unitPrice"] = str(unit_price)

        taxes = item.get("taxes")
        if taxes:
            entry["taxes"] = [_normalize_tax(t, context, index) for t in taxes]

        normalized.append(entry)
    return normalized


def _normalize_tax(tax: Any, context: str, index: int) -> Dict[str, Any]:
    if isinstance(tax, str):
        return {"salesTaxId": tax}
    if not isinstance(tax, dict):
        raise WaveError(
            f"{context} item {index + 1} has an invalid tax entry: expected a "
            'sales tax ID or {"salesTaxId": "..."}.'
        )
    sales_tax_id = tax.get("salesTaxId") or tax.get("sales_tax_id") or tax.get("id")
    if not sales_tax_id:
        raise WaveError(
            f"{context} item {index + 1} has a tax entry without salesTaxId. "
            "Call wave_list_sales_taxes to find the ID."
        )
    entry: Dict[str, Any] = {"salesTaxId": sales_tax_id}
    if tax.get("amount") is not None:
        entry["amount"] = str(tax["amount"])
    return entry


def strip_estimate_item_taxes(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop tax ``amount`` fields for estimates.

    ``EstimateCreateItemTaxInput`` accepts only ``salesTaxId``; the invoice
    equivalent also takes ``amount``. Sharing the normalizer and trimming here
    keeps one code path for both.
    """
    trimmed = []
    for item in items:
        entry = dict(item)
        if entry.get("taxes"):
            entry["taxes"] = [{"salesTaxId": t["salesTaxId"]} for t in entry["taxes"]]
        trimmed.append(entry)
    return trimmed


def normalize_discounts(
    discounts: Optional[Sequence[Dict[str, Any]]],
    *,
    context: str,
) -> Optional[List[Dict[str, Any]]]:
    """Validate discount entries for invoices and estimates.

    A discount is either FIXED with an ``amount`` or PERCENTAGE with a
    ``percentage``; the type is inferred when the caller omits it.
    """
    if not discounts:
        return None
    normalized = []
    for index, discount in enumerate(discounts):
        if not isinstance(discount, dict):
            raise WaveError(f"{context} discount {index + 1} must be an object.")
        amount = discount.get("amount")
        percentage = discount.get("percentage")
        discount_type = (
            discount.get("discountType")
            or discount.get("discount_type")
            or ("PERCENTAGE" if percentage is not None else "FIXED")
        )
        discount_type = str(discount_type).upper()
        if discount_type not in ("FIXED", "PERCENTAGE"):
            raise WaveError(
                f"{context} discount {index + 1} has discountType "
                f"'{discount_type}'; expected FIXED or PERCENTAGE."
            )
        if discount_type == "FIXED" and amount is None:
            raise WaveError(
                f"{context} discount {index + 1} is FIXED but has no amount."
            )
        if discount_type == "PERCENTAGE" and percentage is None:
            raise WaveError(
                f"{context} discount {index + 1} is PERCENTAGE but has no percentage."
            )
        entry: Dict[str, Any] = {"discountType": discount_type}
        if discount.get("name") is not None:
            entry["name"] = discount["name"]
        if amount is not None:
            entry["amount"] = str(amount)
        if percentage is not None:
            entry["percentage"] = str(percentage)
        normalized.append(entry)
    return normalized


def normalize_recipients(to: Any, *, context: str) -> List[str]:
    """Accept a single address or a list, and return a non-empty list."""
    if isinstance(to, str):
        recipients = [to]
    elif isinstance(to, (list, tuple)):
        recipients = [str(r) for r in to if r]
    else:
        raise WaveError(
            f"{context} needs a recipient email address, or a list of them."
        )
    if not recipients:
        raise WaveError(f"{context} needs at least one recipient email address.")
    return recipients


def decimal_str(value: Any) -> str:
    """Render a money/decimal argument as a string.

    Wave's ``Decimal`` scalar accepts strings, and passing one avoids the
    binary-float rounding that turns 0.1 + 0.2 into 0.30000000000000004.
    """
    return str(value)
