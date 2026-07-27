"""Payment tools for invoices and estimate deposits.

Wave keeps two separate payment models:

* Invoice payments -- money received against an invoice, which reduces its
  amount due and posts to the bank account you name.
* Estimate deposit payments -- money taken up front against an estimate, before
  any invoice exists.
"""

from __future__ import annotations

from typing import Any, Optional

from .. import fragments as f
from ..formatting import kv_block, render, success
from ..runtime import business_id_or_default, get_client, mcp
from .common import compact, decimal_str, normalize_recipients

GET_INVOICE_PAYMENT = f.build(
    """
    query GetInvoicePayment($businessId: ID!, $id: ID!) {
      business(id: $businessId) {
        id
        invoicePayment(id: $id) {
          ...InvoicePaymentFields
          invoice { id invoiceNumber status }
        }
      }
    }
    """,
    f.INVOICE_PAYMENT,
)

CREATE_INVOICE_PAYMENT = f.build(
    f"""
    mutation CreateInvoicePayment($input: InvoicePaymentCreateManualInput!) {{
      invoicePaymentCreateManual(input: $input) {{
        didSucceed
        {f.INPUT_ERRORS}
        invoicePayment {{
          ...InvoicePaymentFields
          invoice {{ id invoiceNumber status }}
        }}
      }}
    }}
    """,
    f.INVOICE_PAYMENT,
)

PATCH_INVOICE_PAYMENT = f.build(
    f"""
    mutation PatchInvoicePayment($input: InvoicePaymentPatchInput!) {{
      invoicePaymentPatch(input: $input) {{
        didSucceed
        {f.INPUT_ERRORS}
        invoicePayment {{
          ...InvoicePaymentFields
          invoice {{ id invoiceNumber status }}
        }}
      }}
    }}
    """,
    f.INVOICE_PAYMENT,
)

DELETE_INVOICE_PAYMENT = f"""
mutation DeleteInvoicePayment($input: InvoicePaymentDeleteInput!) {{
  invoicePaymentDelete(input: $input) {{
    didSucceed
    {f.INPUT_ERRORS}
  }}
}}
"""

SEND_INVOICE_RECEIPT = f"""
mutation SendInvoicePaymentReceipt($input: InvoicePaymentReceiptSendInput!) {{
  invoicePaymentReceiptSend(input: $input) {{
    didSucceed
    {f.INPUT_ERRORS}
  }}
}}
"""

GET_ESTIMATE_PAYMENT = f.build(
    """
    query GetEstimatePayment($businessId: ID!, $id: ID!) {
      business(id: $businessId) {
        id
        estimatePayment(id: $id) {
          ...EstimatePaymentFields
          estimate { id estimateNumber status }
        }
      }
    }
    """,
    f.ESTIMATE_PAYMENT,
)

CREATE_ESTIMATE_PAYMENT = f.build(
    f"""
    mutation CreateEstimateDepositPayment(
      $input: EstimateDepositPaymentCreateManualInput!
    ) {{
      estimateDepositPaymentCreateManual(input: $input) {{
        didSucceed
        {f.INPUT_ERRORS}
        estimatePayment {{ ...EstimatePaymentFields }}
      }}
    }}
    """,
    f.ESTIMATE_PAYMENT,
)

UPDATE_ESTIMATE_PAYMENT = f.build(
    f"""
    mutation UpdateEstimateDepositPayment(
      $input: EstimateDepositPaymentUpdateManualInput!
    ) {{
      estimateDepositPaymentUpdateManual(input: $input) {{
        didSucceed
        {f.INPUT_ERRORS}
        estimatePayment {{ ...EstimatePaymentFields }}
      }}
    }}
    """,
    f.ESTIMATE_PAYMENT,
)

DELETE_ESTIMATE_PAYMENT = f"""
mutation DeleteEstimatePayment($input: EstimatePaymentDeleteInput!) {{
  estimatePaymentDelete(input: $input) {{
    didSucceed
    {f.INPUT_ERRORS}
  }}
}}
"""

SEND_ESTIMATE_RECEIPT = f"""
mutation SendEstimateDepositReceipt(
  $input: EstimateDepositPaymentReceiptSendInput!
) {{
  estimateDepositPaymentReceiptSend(input: $input) {{
    didSucceed
    {f.INPUT_ERRORS}
  }}
}}
"""


def _payment_detail(payment: dict, *, kind: str) -> str:
    parent = payment.get("invoice") or payment.get("estimate") or {}
    parent_number = parent.get("invoiceNumber") or parent.get("estimateNumber")
    return f"**{kind} payment**\n\n" + kv_block(
        [
            ("ID", f"`{payment['id']}`"),
            ("Amount", payment.get("amount")),
            ("Payment date", payment.get("paymentDate")),
            ("Method", payment.get("paymentMethod")),
            ("Memo", payment.get("memo")),
            ("Deposited to", (payment.get("account") or {}).get("name")),
            ("Customer", (payment.get("customer") or {}).get("name")),
            ("Applied to", parent_number),
            ("Parent status", parent.get("status")),
            ("State", payment.get("state")),
            ("Origin", payment.get("origin")),
            ("Provider", payment.get("paymentProvider")),
            ("Exchange rate", payment.get("exchangeRate")),
            ("Transaction ID", payment.get("transactionId")),
            ("Confirmation code", payment.get("confirmationCode")),
            ("Created", payment.get("createdAt")),
            ("Modified", payment.get("modifiedAt")),
        ]
    )


# ------------------------------------------------------------- invoice payments


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def wave_get_invoice_payment(
    payment_id: str,
    business_id: Optional[str] = None,
    response_format: str = "markdown",
) -> str:
    """Get one invoice payment by ID.

    To see every payment on an invoice, call `wave_get_invoice` instead: it
    returns them all.

    Args:
        payment_id: The Wave invoice payment ID.
        business_id: Business the payment belongs to. Defaults to the session business.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    data = await client.execute(
        GET_INVOICE_PAYMENT, {"businessId": resolved, "id": payment_id}
    )
    payment = (data.get("business") or {}).get("invoicePayment")
    if not payment:
        return f"No invoice payment found with ID `{payment_id}` in this business."
    return render(
        payment, response_format, lambda: _payment_detail(payment, kind="Invoice")
    )


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def wave_create_invoice_payment(
    invoice_id: str,
    payment_account_id: str,
    amount: str,
    payment_date: str,
    payment_method: str = "UNSPECIFIED",
    exchange_rate: str = "1",
    memo: Optional[str] = None,
    response_format: str = "markdown",
) -> str:
    """Record a manual payment against an invoice.

    This posts money to the account you name and reduces the invoice's amount
    due. It records a payment you already received; it does not charge anyone.

    Find `payment_account_id` with `wave_list_accounts` filtered to subtypes
    ["CASH_AND_BANK"].

    Args:
        invoice_id: The invoice being paid. Required.
        payment_account_id: Bank or cash account the money landed in. Required.
        amount: Amount received, e.g. "250.00". Required.
        payment_date: Date received, YYYY-MM-DD. Required.
        payment_method: CASH, CHEQUE, CREDIT_CARD, BANK_TRANSFER, PAYPAL,
            OTHER, or UNSPECIFIED. Defaults to UNSPECIFIED.
        exchange_rate: Rate to the business currency. Defaults to "1".
        memo: Note on the payment, such as a cheque number.
        response_format: "markdown" or "json".
    """
    client = get_client()
    payload = compact(
        {
            "invoiceId": invoice_id,
            "paymentAccountId": payment_account_id,
            "amount": decimal_str(amount),
            "paymentDate": payment_date,
            "paymentMethod": payment_method.upper(),
            "exchangeRate": decimal_str(exchange_rate),
            "memo": memo,
        }
    )
    result = await client.mutate(
        CREATE_INVOICE_PAYMENT,
        {"input": payload},
        root_field="invoicePaymentCreateManual",
    )
    payment = result.get("invoicePayment") or {}
    invoice = payment.get("invoice") or {}
    return render(
        payment,
        response_format,
        lambda: success(
            f"Recorded a {payment.get('amount', amount)} payment on invoice "
            f"**{invoice.get('invoiceNumber')}**.",
            [
                ("Payment ID", f"`{payment.get('id')}`"),
                ("Payment date", payment.get("paymentDate")),
                ("Method", payment.get("paymentMethod")),
                ("Deposited to", (payment.get("account") or {}).get("name")),
                ("Invoice status", invoice.get("status")),
            ],
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
async def wave_patch_invoice_payment(
    payment_id: str,
    payment_account_id: Optional[str] = None,
    amount: Optional[str] = None,
    payment_date: Optional[str] = None,
    payment_method: Optional[str] = None,
    exchange_rate: Optional[str] = None,
    memo: Optional[str] = None,
    response_format: str = "markdown",
) -> str:
    """Update a recorded invoice payment. Only the fields you supply change.

    Changing the amount re-derives the invoice's amount due and may move it
    between PARTIAL, PAID, and OVERPAID.

    Args:
        payment_id: The payment to update. Required.
        payment_account_id: Move the payment to a different bank account.
        amount: Corrected amount.
        payment_date: Corrected date, YYYY-MM-DD.
        payment_method: CASH, CHEQUE, CREDIT_CARD, BANK_TRANSFER, PAYPAL,
            OTHER, UNSPECIFIED.
        exchange_rate: Corrected exchange rate.
        memo: New memo.
        response_format: "markdown" or "json".
    """
    client = get_client()
    payload = compact(
        {
            "id": payment_id,
            "paymentAccountId": payment_account_id,
            "amount": decimal_str(amount) if amount is not None else None,
            "paymentDate": payment_date,
            "paymentMethod": payment_method.upper() if payment_method else None,
            "exchangeRate": (
                decimal_str(exchange_rate) if exchange_rate is not None else None
            ),
            "memo": memo,
        }
    )
    if len(payload) == 1:
        return "Nothing to update. Supply at least one field to change."

    result = await client.mutate(
        PATCH_INVOICE_PAYMENT, {"input": payload}, root_field="invoicePaymentPatch"
    )
    payment = result.get("invoicePayment") or {}
    invoice = payment.get("invoice") or {}
    return render(
        payment,
        response_format,
        lambda: success(
            f"Updated payment on invoice **{invoice.get('invoiceNumber')}**.",
            [
                ("Payment ID", f"`{payment.get('id')}`"),
                ("Amount", payment.get("amount")),
                ("Payment date", payment.get("paymentDate")),
                ("Invoice status", invoice.get("status")),
            ],
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
async def wave_delete_invoice_payment(payment_id: str) -> str:
    """Delete a recorded invoice payment. This cannot be undone.

    The invoice's amount due goes back up by the deleted amount.

    Args:
        payment_id: The payment to delete.
    """
    client = get_client()
    await client.mutate(
        DELETE_INVOICE_PAYMENT,
        {"input": {"id": payment_id}},
        root_field="invoicePaymentDelete",
    )
    return (
        f"Deleted invoice payment `{payment_id}`. The invoice's amount due has "
        "increased by that amount."
    )


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def wave_send_invoice_payment_receipt(
    invoice_id: str,
    payment_id: str,
    to: Any,
    subject: Optional[str] = None,
    message: Optional[str] = None,
    attach_pdf: Optional[bool] = None,
    cc_myself: Optional[bool] = None,
    from_address: Optional[str] = None,
) -> str:
    """Email a payment receipt to a customer.

    This sends real email to real recipients, so confirm the addresses first.

    Args:
        invoice_id: The invoice the payment belongs to. Required.
        payment_id: The payment to receipt. Required.
        to: Recipient email address, or a list of them. Required.
        subject: Email subject. Wave supplies a default.
        message: Body text. Wave supplies a default.
        attach_pdf: Attach the receipt as a PDF.
        cc_myself: Send a copy to the business email.
        from_address: Reply-to address; must be verified in Wave.
    """
    client = get_client()
    recipients = normalize_recipients(to, context="wave_send_invoice_payment_receipt")
    payload = compact(
        {
            "invoiceId": invoice_id,
            "invoicePaymentId": payment_id,
            "to": recipients,
            "subject": subject,
            "message": message,
            "attachPdf": attach_pdf,
            "ccMyself": cc_myself,
            "fromAddress": from_address,
        }
    )
    await client.mutate(
        SEND_INVOICE_RECEIPT,
        {"input": payload},
        root_field="invoicePaymentReceiptSend",
    )
    return f"Sent a payment receipt for invoice `{invoice_id}` to {', '.join(recipients)}."


# ------------------------------------------------------ estimate deposit payments


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def wave_get_estimate_payment(
    payment_id: str,
    business_id: Optional[str] = None,
    response_format: str = "markdown",
) -> str:
    """Get one estimate deposit payment by ID.

    To see every deposit on an estimate, call `wave_get_estimate` with
    `include_deposit_payments=true`.

    Args:
        payment_id: The Wave estimate payment ID.
        business_id: Business the payment belongs to. Defaults to the session business.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    data = await client.execute(
        GET_ESTIMATE_PAYMENT, {"businessId": resolved, "id": payment_id}
    )
    payment = (data.get("business") or {}).get("estimatePayment")
    if not payment:
        return f"No estimate payment found with ID `{payment_id}` in this business."
    return render(
        payment, response_format, lambda: _payment_detail(payment, kind="Estimate deposit")
    )


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def wave_create_estimate_deposit_payment(
    estimate_id: str,
    amount: str,
    payment_date: str,
    payment_method: str = "OTHER",
    payment_account_id: Optional[str] = None,
    memo: Optional[str] = None,
    response_format: str = "markdown",
) -> str:
    """Record a deposit received against an estimate.

    Use this for money taken up front, before the estimate becomes an invoice.
    It records a payment you already received; it does not charge anyone.

    Args:
        estimate_id: The estimate the deposit applies to. Required.
        amount: Amount received, e.g. "500.00". Required.
        payment_date: Date received, YYYY-MM-DD. Required.
        payment_method: CASH, CHEQUE, CREDIT_CARD, BANK_TRANSFER, PAYPAL, or
            OTHER. Defaults to OTHER.
        payment_account_id: Bank or cash account the money landed in.
        memo: Note on the deposit.
        response_format: "markdown" or "json".
    """
    client = get_client()
    payload = compact(
        {
            "estimateId": estimate_id,
            "amount": decimal_str(amount),
            "paymentDate": payment_date,
            "paymentMethod": payment_method.upper(),
            "paymentAccountId": payment_account_id,
            "memo": memo,
        }
    )
    result = await client.mutate(
        CREATE_ESTIMATE_PAYMENT,
        {"input": payload},
        root_field="estimateDepositPaymentCreateManual",
    )
    payment = result.get("estimatePayment") or {}
    return render(
        payment,
        response_format,
        lambda: success(
            f"Recorded a {payment.get('amount', amount)} deposit on estimate `{estimate_id}`.",
            [
                ("Payment ID", f"`{payment.get('id')}`"),
                ("Payment date", payment.get("paymentDate")),
                ("Method", payment.get("paymentMethod")),
                ("State", payment.get("state")),
            ],
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
async def wave_update_estimate_deposit_payment(
    payment_id: str,
    estimate_id: str,
    amount: Optional[str] = None,
    payment_date: Optional[str] = None,
    payment_method: Optional[str] = None,
    payment_account_id: Optional[str] = None,
    memo: Optional[str] = None,
    response_format: str = "markdown",
) -> str:
    """Update a recorded estimate deposit. Only the fields you supply change.

    Wave requires the estimate ID alongside the payment ID.

    Args:
        payment_id: The deposit payment to update. Required.
        estimate_id: The estimate it belongs to. Required by Wave.
        amount: Corrected amount.
        payment_date: Corrected date, YYYY-MM-DD.
        payment_method: CASH, CHEQUE, CREDIT_CARD, BANK_TRANSFER, PAYPAL, OTHER.
        payment_account_id: Move the deposit to a different bank account.
        memo: New memo.
        response_format: "markdown" or "json".
    """
    client = get_client()
    payload = compact(
        {
            "id": payment_id,
            "estimateId": estimate_id,
            "amount": decimal_str(amount) if amount is not None else None,
            "paymentDate": payment_date,
            "paymentMethod": payment_method.upper() if payment_method else None,
            "paymentAccountId": payment_account_id,
            "memo": memo,
        }
    )
    if len(payload) == 2:
        return "Nothing to update. Supply at least one field to change."

    result = await client.mutate(
        UPDATE_ESTIMATE_PAYMENT,
        {"input": payload},
        root_field="estimateDepositPaymentUpdateManual",
    )
    payment = result.get("estimatePayment") or {}
    return render(
        payment,
        response_format,
        lambda: success(
            f"Updated deposit payment on estimate `{estimate_id}`.",
            [
                ("Payment ID", f"`{payment.get('id')}`"),
                ("Amount", payment.get("amount")),
                ("Payment date", payment.get("paymentDate")),
            ],
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
async def wave_delete_estimate_payment(payment_id: str) -> str:
    """Delete a recorded estimate deposit payment. This cannot be undone.

    Args:
        payment_id: The deposit payment to delete.
    """
    client = get_client()
    await client.mutate(
        DELETE_ESTIMATE_PAYMENT,
        {"input": {"id": payment_id}},
        root_field="estimatePaymentDelete",
    )
    return f"Deleted estimate deposit payment `{payment_id}`."


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def wave_send_estimate_deposit_receipt(
    estimate_id: str,
    payment_id: str,
    to: Any,
    subject: Optional[str] = None,
    message: Optional[str] = None,
    attach_pdf: Optional[bool] = None,
    cc_myself: Optional[bool] = None,
    from_address: Optional[str] = None,
) -> str:
    """Email a deposit receipt to a customer.

    This sends real email to real recipients, so confirm the addresses first.

    Args:
        estimate_id: The estimate the deposit belongs to. Required.
        payment_id: The deposit payment to receipt. Required.
        to: Recipient email address, or a list of them. Required.
        subject: Email subject. Wave supplies a default.
        message: Body text. Wave supplies a default.
        attach_pdf: Attach the receipt as a PDF.
        cc_myself: Send a copy to the business email.
        from_address: Reply-to address; must be verified in Wave.
    """
    client = get_client()
    recipients = normalize_recipients(to, context="wave_send_estimate_deposit_receipt")
    payload = compact(
        {
            "estimateId": estimate_id,
            "estimatePaymentId": payment_id,
            "to": recipients,
            "subject": subject,
            "message": message,
            "attachPdf": attach_pdf,
            "ccMyself": cc_myself,
            "fromAddress": from_address,
        }
    )
    await client.mutate(
        SEND_ESTIMATE_RECEIPT,
        {"input": payload},
        root_field="estimateDepositPaymentReceiptSend",
    )
    return f"Sent a deposit receipt for estimate `{estimate_id}` to {', '.join(recipients)}."
