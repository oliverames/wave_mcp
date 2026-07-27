"""Invoice tools covering the full lifecycle.

Draft -> approve -> send -> get paid. Wave models each step as its own
mutation, and this module exposes all of them: create, patch, clone, delete,
approve, send, and mark-sent.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import fragments as f
from ..formatting import kv_block, listing, money, render, success, table
from ..runtime import PAGE_SIZE_DEFAULT, PageNumber, PageSize, ResponseFormat, business_id_or_default, get_client, tool
from .common import (
    DEFAULT_INVOICE_SORT,
    compact,
    decimal_str,
    normalize_discounts,
    normalize_line_items,
    normalize_recipients,
    require_items,
)

LIST_INVOICES = f.build(
    """
    query ListInvoices(
      $businessId: ID!
      $page: Int!
      $pageSize: Int!
      $sort: [InvoiceSort!]!
      $status: InvoiceStatus
      $customerId: ID
      $currency: CurrencyCode
      $invoiceDateStart: Date
      $invoiceDateEnd: Date
      $modifiedAtAfter: DateTime
      $modifiedAtBefore: DateTime
      $invoiceNumber: String
      $amountDue: Decimal
    ) {
      business(id: $businessId) {
        id
        invoices(
          page: $page
          pageSize: $pageSize
          sort: $sort
          status: $status
          customerId: $customerId
          currency: $currency
          invoiceDateStart: $invoiceDateStart
          invoiceDateEnd: $invoiceDateEnd
          modifiedAtAfter: $modifiedAtAfter
          modifiedAtBefore: $modifiedAtBefore
          invoiceNumber: $invoiceNumber
          amountDue: $amountDue
        ) {
          pageInfo { ...PageInfoFields }
          edges {
            node {
              id
              status
              title
              invoiceNumber
              poNumber
              invoiceDate
              dueDate
              viewUrl
              pdfUrl
              createdAt
              modifiedAt
              lastSentAt
              currency { code symbol }
              customer { id name email }
              total { ...MoneyFields }
              amountDue { ...MoneyFields }
              amountPaid { ...MoneyFields }
            }
          }
        }
      }
    }
    """,
    f.PAGE_INFO,
    f.MONEY,
)

GET_INVOICE = f.build(
    """
    query GetInvoice($businessId: ID!, $id: ID!) {
      business(id: $businessId) {
        id
        invoice(id: $id) {
          ...InvoiceFields
          payments { ...InvoicePaymentFields }
        }
      }
    }
    """,
    *f.INVOICE_SET,
    f.INVOICE_PAYMENT,
)

CREATE_INVOICE = f.build(
    f"""
    mutation CreateInvoice($input: InvoiceCreateInput!) {{
      invoiceCreate(input: $input) {{
        didSucceed
        {f.INPUT_ERRORS}
        invoice {{ ...InvoiceFields }}
      }}
    }}
    """,
    *f.INVOICE_SET,
)

PATCH_INVOICE = f.build(
    f"""
    mutation PatchInvoice($input: InvoicePatchInput!) {{
      invoicePatch(input: $input) {{
        didSucceed
        {f.INPUT_ERRORS}
        invoice {{ ...InvoiceFields }}
      }}
    }}
    """,
    *f.INVOICE_SET,
)

CLONE_INVOICE = f.build(
    f"""
    mutation CloneInvoice($input: InvoiceCloneInput!) {{
      invoiceClone(input: $input) {{
        didSucceed
        {f.INPUT_ERRORS}
        invoice {{ ...InvoiceFields }}
      }}
    }}
    """,
    *f.INVOICE_SET,
)

APPROVE_INVOICE = f.build(
    f"""
    mutation ApproveInvoice($input: InvoiceApproveInput!) {{
      invoiceApprove(input: $input) {{
        didSucceed
        {f.INPUT_ERRORS}
        invoice {{ id status invoiceNumber viewUrl pdfUrl }}
      }}
    }}
    """,
)

SEND_INVOICE = f.build(
    f"""
    mutation SendInvoice($input: InvoiceSendInput!) {{
      invoiceSend(input: $input) {{
        didSucceed
        {f.INPUT_ERRORS}
        invoice {{ id status invoiceNumber lastSentAt lastSentVia viewUrl }}
      }}
    }}
    """,
)

MARK_SENT_INVOICE = f.build(
    f"""
    mutation MarkInvoiceSent($input: InvoiceMarkSentInput!) {{
      invoiceMarkSent(input: $input) {{
        didSucceed
        {f.INPUT_ERRORS}
        invoice {{ id status invoiceNumber lastSentAt lastSentVia }}
      }}
    }}
    """,
)

DELETE_INVOICE = f"""
mutation DeleteInvoice($input: InvoiceDeleteInput!) {{
  invoiceDelete(input: $input) {{
    didSucceed
    {f.INPUT_ERRORS}
  }}
}}
"""

INVOICE_COLUMNS = [
    ("Number", "invoiceNumber"),
    ("Customer", lambda r: (r.get("customer") or {}).get("name", "-")),
    ("Status", "status"),
    ("Date", "invoiceDate"),
    ("Due", "dueDate"),
    ("Total", lambda r: money(r.get("total"))),
    ("Due amount", lambda r: money(r.get("amountDue"))),
    ("ID", "id"),
]


def _invoice_detail(invoice: Dict[str, Any]) -> str:
    header = f"**Invoice {invoice.get('invoiceNumber')} - {invoice.get('status')}**\n\n"
    body = kv_block(
        [
            ("ID", f"`{invoice['id']}`"),
            ("Title", invoice.get("title")),
            ("Subhead", invoice.get("subhead")),
            ("Customer", (invoice.get("customer") or {}).get("name")),
            ("Customer email", (invoice.get("customer") or {}).get("email")),
            ("PO number", invoice.get("poNumber")),
            ("Invoice date", invoice.get("invoiceDate")),
            ("Due date", invoice.get("dueDate")),
            ("Currency", (invoice.get("currency") or {}).get("code")),
            ("Subtotal", money(invoice.get("subtotal"))),
            ("Discount", money(invoice.get("discountTotal"))),
            ("Tax", money(invoice.get("taxTotal"))),
            ("Total", money(invoice.get("total"))),
            ("Paid", money(invoice.get("amountPaid"))),
            ("Amount due", money(invoice.get("amountDue"))),
            ("Memo", invoice.get("memo")),
            ("Footer", invoice.get("footer")),
            ("Last sent", invoice.get("lastSentAt")),
            ("Last sent via", invoice.get("lastSentVia")),
            ("Last viewed", invoice.get("lastViewedAt")),
            ("View URL", invoice.get("viewUrl")),
            ("PDF URL", invoice.get("pdfUrl")),
            ("Created", invoice.get("createdAt")),
            ("Modified", invoice.get("modifiedAt")),
        ]
    )

    sections = [header + body]

    items = invoice.get("items") or []
    if items:
        sections.append(
            "\n**Line items**\n\n"
            + table(
                items,
                [
                    ("Product", lambda r: (r.get("product") or {}).get("name", "-")),
                    ("Description", "description"),
                    ("Qty", "quantity"),
                    ("Unit price", "unitPrice"),
                    ("Total", lambda r: money(r.get("total"))),
                    (
                        "Taxes",
                        lambda r: ", ".join(
                            (t.get("salesTax") or {}).get("abbreviation", "?")
                            for t in (r.get("taxes") or [])
                        )
                        or "-",
                    ),
                ],
            )
        )

    discounts = invoice.get("discounts") or []
    if discounts:
        sections.append(
            "\n**Discounts**\n\n"
            + table(
                discounts,
                [
                    ("Name", "name"),
                    ("Amount", "amount"),
                    ("Percentage", "percentage"),
                ],
            )
        )

    payments = invoice.get("payments") or []
    if payments:
        sections.append(
            "\n**Payments**\n\n"
            + table(
                [p for p in payments if p],
                [
                    ("Date", "paymentDate"),
                    ("Amount", "amount"),
                    ("Method", "paymentMethod"),
                    ("Account", lambda r: (r.get("account") or {}).get("name", "-")),
                    ("Memo", "memo"),
                    ("ID", "id"),
                ],
            )
        )

    return "\n".join(sections)


@tool(read_only=True)
async def wave_list_invoices(
    business_id: Optional[str] = None,
    status: Optional[str] = None,
    customer_id: Optional[str] = None,
    currency: Optional[str] = None,
    invoice_number: Optional[str] = None,
    amount_due: Optional[str] = None,
    invoice_date_start: Optional[str] = None,
    invoice_date_end: Optional[str] = None,
    modified_after: Optional[str] = None,
    modified_before: Optional[str] = None,
    sort: Optional[List[str]] = None,
    page: PageNumber = 1,
    page_size: PageSize = PAGE_SIZE_DEFAULT,
    fetch_all: bool = False,
    response_format: ResponseFormat = "markdown",
) -> str:
    """List invoices, filtered by status, customer, date range, or amount due.

    To find unpaid invoices use status "UNPAID"; "OVERDUE" narrows that to ones
    past their due date.

    Args:
        business_id: Business to read. Defaults to the session business.
        status: DRAFT, SAVED, UNPAID, SENT, VIEWED, PARTIAL, PAID, OVERDUE, OVERPAID.
        customer_id: Only invoices for this customer.
        currency: Currency code, e.g. "USD".
        invoice_number: Exact invoice number match.
        amount_due: Exact outstanding amount match, e.g. "250.00".
        invoice_date_start: Earliest invoice date, YYYY-MM-DD.
        invoice_date_end: Latest invoice date, YYYY-MM-DD.
        modified_after: ISO 8601 timestamp; only invoices changed after it.
        modified_before: ISO 8601 timestamp; only invoices changed before it.
        sort: e.g. ["INVOICE_DATE_DESC"], ["AMOUNT_DUE_DESC"], ["CUSTOMER_NAME_ASC"].
            Defaults to INVOICE_DATE_DESC.
        page: 1-based page number.
        page_size: Records per page (1-200).
        fetch_all: Walk every page.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    result = await client.paginate(
        LIST_INVOICES,
        compact(
            {
                "businessId": resolved,
                "sort": [s.upper() for s in sort] if sort else DEFAULT_INVOICE_SORT,
                "status": status.upper() if status else None,
                "customerId": customer_id,
                "currency": currency.upper() if currency else None,
                "invoiceNumber": invoice_number,
                "amountDue": decimal_str(amount_due) if amount_due is not None else None,
                "invoiceDateStart": invoice_date_start,
                "invoiceDateEnd": invoice_date_end,
                "modifiedAtAfter": modified_after,
                "modifiedAtBefore": modified_before,
            }
        ),
        path=("business", "invoices"),
        page=page,
        page_size=page_size,
        fetch_all=fetch_all,
    )

    def as_markdown() -> str:
        return listing(result, "Invoices", INVOICE_COLUMNS)

    return render(result, response_format, as_markdown)


@tool(read_only=True)
async def wave_get_invoice(
    invoice_id: str,
    business_id: Optional[str] = None,
    response_format: ResponseFormat = "markdown",
) -> str:
    """Get one invoice in full: line items, taxes, discounts, and payments.

    Args:
        invoice_id: The Wave invoice ID.
        business_id: Business the invoice belongs to. Defaults to the session business.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    data = await client.execute(GET_INVOICE, {"businessId": resolved, "id": invoice_id})
    invoice = (data.get("business") or {}).get("invoice")
    if not invoice:
        return f"No invoice found with ID `{invoice_id}` in this business."
    return render(invoice, response_format, lambda: _invoice_detail(invoice))


@tool()
async def wave_create_invoice(
    customer_id: str,
    items: List[Dict[str, Any]],
    business_id: Optional[str] = None,
    status: str = "DRAFT",
    title: Optional[str] = None,
    subhead: Optional[str] = None,
    invoice_number: Optional[str] = None,
    po_number: Optional[str] = None,
    invoice_date: Optional[str] = None,
    due_date: Optional[str] = None,
    currency: Optional[str] = None,
    exchange_rate: Optional[str] = None,
    memo: Optional[str] = None,
    footer: Optional[str] = None,
    discounts: Optional[List[Dict[str, Any]]] = None,
    disable_credit_card_payments: Optional[bool] = None,
    disable_bank_payments: Optional[bool] = None,
    disable_amex_payments: Optional[bool] = None,
    require_terms_of_service_agreement: Optional[bool] = None,
    response_format: ResponseFormat = "markdown",
) -> str:
    """Create an invoice.

    Every line item must reference a product, so call `wave_list_products`
    first (or `wave_create_product`). Each item is
    `{"productId": "...", "quantity": 2, "unitPrice": "150.00",
    "description": "...", "taxes": ["salesTaxId"]}`; quantity, unitPrice, and
    description fall back to the product's defaults.

    A new invoice is a DRAFT and is not visible to the customer. Approve it
    with `wave_approve_invoice`, then deliver it with `wave_send_invoice`.

    Args:
        customer_id: Customer to bill. Required.
        items: Line items, each needing at least a productId. Required.
        business_id: Business to create in. Defaults to the session business.
        status: DRAFT or SAVED. Defaults to DRAFT.
        title: Heading on the invoice. Defaults to "Invoice".
        subhead: Text under the title.
        invoice_number: Your own number. Wave assigns the next one if omitted.
        po_number: Customer purchase order number.
        invoice_date: Issue date, YYYY-MM-DD. Defaults to today.
        due_date: Payment due date, YYYY-MM-DD.
        currency: Currency code. Defaults to the customer or business currency.
        exchange_rate: Rate to the business currency, for foreign-currency invoices.
        memo: Note shown to the customer.
        footer: Footer text.
        discounts: e.g. `[{"name": "Loyalty", "discountType": "PERCENTAGE",
            "percentage": "10"}]` or `{"discountType": "FIXED", "amount": "25.00"}`.
        disable_credit_card_payments: Turn off card payments on this invoice.
        disable_bank_payments: Turn off bank payments on this invoice.
        disable_amex_payments: Turn off Amex specifically.
        require_terms_of_service_agreement: Make the customer accept terms before paying.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    line_items = normalize_line_items(
        require_items(items, "invoice line item"), context="Invoice"
    )
    payload = compact(
        {
            "businessId": resolved,
            "customerId": customer_id,
            "status": status.upper(),
            "items": line_items,
            "title": title,
            "subhead": subhead,
            "invoiceNumber": invoice_number,
            "poNumber": po_number,
            "invoiceDate": invoice_date,
            "dueDate": due_date,
            "currency": currency.upper() if currency else None,
            "exchangeRate": (
                decimal_str(exchange_rate) if exchange_rate is not None else None
            ),
            "memo": memo,
            "footer": footer,
            "discounts": normalize_discounts(discounts, context="Invoice"),
            "disableCreditCardPayments": disable_credit_card_payments,
            "disableBankPayments": disable_bank_payments,
            "disableAmexPayments": disable_amex_payments,
            "requireTermsOfServiceAgreement": require_terms_of_service_agreement,
        }
    )
    result = await client.mutate(
        CREATE_INVOICE, {"input": payload}, root_field="invoiceCreate"
    )
    invoice = result.get("invoice") or {}

    def as_markdown() -> str:
        return success(
            f"Created invoice **{invoice.get('invoiceNumber')}** ({invoice.get('status')}).",
            [
                ("ID", f"`{invoice.get('id')}`"),
                ("Customer", (invoice.get("customer") or {}).get("name")),
                ("Total", money(invoice.get("total"))),
                ("Amount due", money(invoice.get("amountDue"))),
                ("Due date", invoice.get("dueDate")),
                ("View URL", invoice.get("viewUrl")),
            ],
        ) + (
            "\n\nIt is still a draft. Call wave_approve_invoice to finalize it, "
            "then wave_send_invoice to email it."
            if (invoice.get("status") == "DRAFT")
            else ""
        )

    return render(invoice, response_format, as_markdown)


@tool(idempotent=True)
async def wave_patch_invoice(
    invoice_id: str,
    customer_id: Optional[str] = None,
    status: Optional[str] = None,
    items: Optional[List[Dict[str, Any]]] = None,
    title: Optional[str] = None,
    subhead: Optional[str] = None,
    invoice_number: Optional[str] = None,
    po_number: Optional[str] = None,
    invoice_date: Optional[str] = None,
    due_date: Optional[str] = None,
    currency: Optional[str] = None,
    exchange_rate: Optional[str] = None,
    memo: Optional[str] = None,
    footer: Optional[str] = None,
    discounts: Optional[List[Dict[str, Any]]] = None,
    disable_credit_card_payments: Optional[bool] = None,
    disable_bank_payments: Optional[bool] = None,
    disable_amex_payments: Optional[bool] = None,
    require_terms_of_service_agreement: Optional[bool] = None,
    response_format: ResponseFormat = "markdown",
) -> str:
    """Update an invoice. Only the fields you supply change.

    Supplying `items` replaces every line item, so send the complete list.
    Wave restricts edits to invoices that have payments recorded against them.

    Args:
        invoice_id: The invoice to update. Required.
        customer_id: Reassign to a different customer.
        status: DRAFT or SAVED.
        items: Replacement line items. Replaces all existing items.
        title: New title.
        subhead: New subhead.
        invoice_number: New invoice number.
        po_number: New PO number.
        invoice_date: New issue date, YYYY-MM-DD.
        due_date: New due date, YYYY-MM-DD.
        currency: New currency code.
        exchange_rate: New exchange rate.
        memo: New customer-facing memo.
        footer: New footer.
        discounts: Replacement discount list.
        disable_credit_card_payments: Turn card payments off or on.
        disable_bank_payments: Turn bank payments off or on.
        disable_amex_payments: Turn Amex off or on.
        require_terms_of_service_agreement: Require terms acceptance before payment.
        response_format: "markdown" or "json".
    """
    client = get_client()
    payload = compact(
        {
            "id": invoice_id,
            "customerId": customer_id,
            "status": status.upper() if status else None,
            "items": (
                normalize_line_items(items, context="Invoice")
                if items is not None
                else None
            ),
            "title": title,
            "subhead": subhead,
            "invoiceNumber": invoice_number,
            "poNumber": po_number,
            "invoiceDate": invoice_date,
            "dueDate": due_date,
            "currency": currency.upper() if currency else None,
            "exchangeRate": (
                decimal_str(exchange_rate) if exchange_rate is not None else None
            ),
            "memo": memo,
            "footer": footer,
            "discounts": normalize_discounts(discounts, context="Invoice"),
            "disableCreditCardPayments": disable_credit_card_payments,
            "disableBankPayments": disable_bank_payments,
            "disableAmexPayments": disable_amex_payments,
            "requireTermsOfServiceAgreement": require_terms_of_service_agreement,
        }
    )
    if len(payload) == 1:
        return "Nothing to update. Supply at least one field to change."

    result = await client.mutate(
        PATCH_INVOICE, {"input": payload}, root_field="invoicePatch"
    )
    invoice = result.get("invoice") or {}
    return render(
        invoice,
        response_format,
        lambda: success(
            f"Updated invoice **{invoice.get('invoiceNumber')}**.",
            [
                ("ID", f"`{invoice.get('id')}`"),
                ("Status", invoice.get("status")),
                ("Total", money(invoice.get("total"))),
                ("Amount due", money(invoice.get("amountDue"))),
            ],
        ),
    )


@tool()
async def wave_clone_invoice(
    invoice_id: str,
    response_format: ResponseFormat = "markdown",
) -> str:
    """Copy an invoice into a new draft.

    The copy takes the original's customer, line items, and settings, with a
    fresh invoice number and today's date. Useful for recurring billing.

    Args:
        invoice_id: The invoice to copy.
        response_format: "markdown" or "json".
    """
    client = get_client()
    result = await client.mutate(
        CLONE_INVOICE, {"input": {"invoiceId": invoice_id}}, root_field="invoiceClone"
    )
    invoice = result.get("invoice") or {}
    return render(
        invoice,
        response_format,
        lambda: success(
            f"Cloned into draft invoice **{invoice.get('invoiceNumber')}**.",
            [
                ("New ID", f"`{invoice.get('id')}`"),
                ("Status", invoice.get("status")),
                ("Total", money(invoice.get("total"))),
                ("Invoice date", invoice.get("invoiceDate")),
            ],
        ),
    )


@tool(idempotent=True)
async def wave_approve_invoice(
    invoice_id: str,
    response_format: ResponseFormat = "markdown",
) -> str:
    """Approve a draft invoice, moving it out of DRAFT and into the books.

    An approved invoice can be sent and paid, and it posts to accounts
    receivable. Approving does not notify the customer: use
    `wave_send_invoice` for that.

    Args:
        invoice_id: The draft invoice to approve.
        response_format: "markdown" or "json".
    """
    client = get_client()
    result = await client.mutate(
        APPROVE_INVOICE, {"input": {"invoiceId": invoice_id}}, root_field="invoiceApprove"
    )
    invoice = result.get("invoice") or {}
    return render(
        invoice,
        response_format,
        lambda: success(
            f"Approved invoice **{invoice.get('invoiceNumber')}**.",
            [
                ("ID", f"`{invoice.get('id')}`"),
                ("Status", invoice.get("status")),
                ("View URL", invoice.get("viewUrl")),
            ],
        )
        + "\n\nThe customer has not been notified. Call wave_send_invoice to email it.",
    )


@tool()
async def wave_send_invoice(
    invoice_id: str,
    to: Any,
    subject: Optional[str] = None,
    message: Optional[str] = None,
    attach_pdf: bool = False,
    cc_myself: Optional[bool] = None,
    from_address: Optional[str] = None,
    response_format: ResponseFormat = "markdown",
) -> str:
    """Email an invoice to a customer through Wave.

    This sends real email to real recipients, so confirm the addresses before
    calling it. The invoice must be approved first. Sending is not reversible.

    Args:
        invoice_id: The invoice to send. Required.
        to: Recipient email address, or a list of them. Required.
        subject: Email subject. Wave supplies a default.
        message: Body text. Wave supplies a default.
        attach_pdf: Attach the invoice PDF as well as linking to it.
        cc_myself: Send a copy to the business email.
        from_address: Reply-to address; must be verified in Wave.
        response_format: "markdown" or "json".
    """
    client = get_client()
    recipients = normalize_recipients(to, context="wave_send_invoice")
    payload = compact(
        {
            "invoiceId": invoice_id,
            "to": recipients,
            "subject": subject,
            "message": message,
            "attachPDF": attach_pdf,
            "ccMyself": cc_myself,
            "fromAddress": from_address,
        }
    )
    result = await client.mutate(
        SEND_INVOICE, {"input": payload}, root_field="invoiceSend"
    )
    invoice = result.get("invoice") or {}
    return render(
        invoice,
        response_format,
        lambda: success(
            f"Sent invoice **{invoice.get('invoiceNumber')}** to {', '.join(recipients)}.",
            [
                ("ID", f"`{invoice.get('id')}`"),
                ("Status", invoice.get("status")),
                ("Sent at", invoice.get("lastSentAt")),
                ("Sent via", invoice.get("lastSentVia")),
                ("View URL", invoice.get("viewUrl")),
            ],
        ),
    )


@tool(idempotent=True)
async def wave_mark_invoice_sent(
    invoice_id: str,
    send_method: str = "MARKED_SENT",
    sent_at: Optional[str] = None,
    response_format: ResponseFormat = "markdown",
) -> str:
    """Record that an invoice was delivered outside Wave, without emailing it.

    Use this when you sent the invoice yourself -- by your own email client, or
    on paper -- and want Wave's status to reflect that. No email is sent.

    Args:
        invoice_id: The invoice to mark. Required.
        send_method: How it was delivered: MARKED_SENT, EXPORT_PDF, SHARED_LINK,
            GMAIL, OUTLOOK, YAHOO, WAVE, NOT_SENT, SKIPPED. Defaults to MARKED_SENT.
        sent_at: When it was sent, ISO 8601. Defaults to now.
        response_format: "markdown" or "json".
    """
    client = get_client()
    payload = compact(
        {
            "invoiceId": invoice_id,
            "sendMethod": send_method.upper(),
            "sentAt": sent_at,
        }
    )
    result = await client.mutate(
        MARK_SENT_INVOICE, {"input": payload}, root_field="invoiceMarkSent"
    )
    invoice = result.get("invoice") or {}
    return render(
        invoice,
        response_format,
        lambda: success(
            f"Marked invoice **{invoice.get('invoiceNumber')}** as sent. No email was sent.",
            [
                ("ID", f"`{invoice.get('id')}`"),
                ("Status", invoice.get("status")),
                ("Sent at", invoice.get("lastSentAt")),
                ("Sent via", invoice.get("lastSentVia")),
            ],
        ),
    )


@tool(destructive=True, idempotent=True)
async def wave_delete_invoice(invoice_id: str) -> str:
    """Delete an invoice. This cannot be undone.

    Wave refuses to delete an invoice that has payments recorded against it;
    delete those first with `wave_delete_invoice_payment`.

    Args:
        invoice_id: The invoice to delete.
    """
    client = get_client()
    await client.mutate(
        DELETE_INVOICE, {"input": {"invoiceId": invoice_id}}, root_field="invoiceDelete"
    )
    return f"Deleted invoice `{invoice_id}`."
