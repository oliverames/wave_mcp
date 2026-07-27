"""Estimate (quote) tools covering the full lifecycle.

Draft -> approve -> send -> customer accepts -> convert to invoice. Wave gives
each step its own mutation, and estimates additionally support deposits, PDF
generation, and an acceptance history.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import fragments as f
from ..formatting import kv_block, listing, money, render, success, table
from ..runtime import business_id_or_default, get_client, mcp
from .common import (
    DEFAULT_ESTIMATE_SORT,
    compact,
    decimal_str,
    normalize_discounts,
    normalize_line_items,
    normalize_recipients,
    require_items,
    strip_estimate_item_taxes,
)

LIST_ESTIMATES = f.build(
    """
    query ListEstimates(
      $businessId: ID!
      $page: Int!
      $pageSize: Int!
      $sort: EstimateSort!
      $status: EstimateListStatusFilter
      $customerId: ID
      $currency: CurrencyCode
      $estimateDateStart: Date
      $estimateDateEnd: Date
      $modifiedAtAfter: DateTime
      $modifiedAtBefore: DateTime
      $estimateNumber: String
      $amountDue: Decimal
    ) {
      business(id: $businessId) {
        id
        estimates(
          page: $page
          pageSize: $pageSize
          sort: $sort
          status: $status
          customerId: $customerId
          currency: $currency
          estimateDateStart: $estimateDateStart
          estimateDateEnd: $estimateDateEnd
          modifiedAtAfter: $modifiedAtAfter
          modifiedAtBefore: $modifiedAtBefore
          estimateNumber: $estimateNumber
          amountDue: $amountDue
        ) {
          pageInfo { ...PageInfoFields }
          edges {
            node {
              id
              status
              title
              estimateNumber
              poNumber
              estimateDate
              dueDate
              viewUrl
              pdfUrl
              createdAt
              modifiedAt
              lastSentAt
              depositStatus
              depositPaymentStatus
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

GET_ESTIMATE = f.build(
    """
    query GetEstimate(
      $businessId: ID!
      $id: ID!
      $embedAttachments: Boolean
      $embedHistory: Boolean
      $embedDepositPayments: Boolean
    ) {
      business(id: $businessId) {
        id
        estimate(
          id: $id
          embedAttachments: $embedAttachments
          embedHistory: $embedHistory
          embedDepositPayments: $embedDepositPayments
        ) {
          ...EstimateFields
          attachments { id fileName fileSize downloadUrl }
          history { entityId entityType state name email timestamp }
          payments { ...EstimatePaymentFields }
        }
      }
    }
    """,
    *f.ESTIMATE_SET,
    f.ESTIMATE_PAYMENT,
)

CREATE_ESTIMATE = f.build(
    f"""
    mutation CreateEstimate($input: EstimateCreateInput!) {{
      estimateCreate(input: $input) {{
        didSucceed
        {f.INPUT_ERRORS}
        estimate {{ ...EstimateFields }}
      }}
    }}
    """,
    *f.ESTIMATE_SET,
)

PATCH_ESTIMATE = f.build(
    f"""
    mutation PatchEstimate($input: EstimatePatchInput!) {{
      estimatePatch(input: $input) {{
        didSucceed
        {f.INPUT_ERRORS}
        estimate {{ ...EstimateFields }}
      }}
    }}
    """,
    *f.ESTIMATE_SET,
)

CLONE_ESTIMATE = f.build(
    f"""
    mutation CloneEstimate($input: EstimateCloneInput!) {{
      estimateClone(input: $input) {{
        didSucceed
        {f.INPUT_ERRORS}
        estimate {{ ...EstimateFields }}
      }}
    }}
    """,
    *f.ESTIMATE_SET,
)

APPROVE_ESTIMATE = f"""
mutation ApproveEstimate($input: EstimateApproveInput!) {{
  estimateApprove(input: $input) {{
    didSucceed
    {f.INPUT_ERRORS}
    estimate {{ id status estimateNumber viewUrl pdfUrl }}
  }}
}}
"""

SEND_ESTIMATE = f"""
mutation SendEstimate($input: EstimateSendInput!) {{
  estimateSend(input: $input) {{
    didSucceed
    {f.INPUT_ERRORS}
    estimate {{ id status estimateNumber lastSentAt lastSentVia viewUrl }}
  }}
}}
"""

MARK_SENT_ESTIMATE = f"""
mutation MarkEstimateSent($input: EstimateMarkSentInput!) {{
  estimateMarkSent(input: $input) {{
    didSucceed
    {f.INPUT_ERRORS}
    estimate {{ id status estimateNumber lastSentAt lastSentVia }}
  }}
}}
"""

MARK_ACCEPTED_ESTIMATE = f"""
mutation MarkEstimateAccepted($input: EstimateMarkAcceptedInput!) {{
  estimateMarkAccepted(input: $input) {{
    didSucceed
    {f.INPUT_ERRORS}
    estimate {{ id status estimateNumber }}
  }}
}}
"""

RESET_ACCEPTANCE_ESTIMATE = f"""
mutation ResetEstimateAcceptance($input: EstimateResetAcceptanceInput!) {{
  estimateResetAcceptance(input: $input) {{
    didSucceed
    {f.INPUT_ERRORS}
    estimate {{ id status estimateNumber }}
  }}
}}
"""

SEND_ACCEPTANCE_EMAIL = f"""
mutation SendEstimateAcceptanceEmail($input: EstimateSendAcceptanceCustomerEmailInput!) {{
  estimateSendAcceptanceCustomerEmail(input: $input) {{
    didSucceed
    {f.INPUT_ERRORS}
    estimate {{ id status estimateNumber }}
  }}
}}
"""

GENERATE_PDF = f"""
mutation GenerateEstimatePdf($input: EstimateGeneratePdfInput!) {{
  estimateGeneratePdf(input: $input) {{
    didSucceed
    {f.INPUT_ERRORS}
    pdfUrl
  }}
}}
"""

CONVERT_TO_INVOICE = f"""
mutation ConvertEstimateToInvoice($input: ConvertEstimateToInvoiceInput!) {{
  convertEstimateToInvoice(input: $input) {{
    didSucceed
    {f.INPUT_ERRORS}
    invoiceId
  }}
}}
"""

DELETE_ESTIMATE = f"""
mutation DeleteEstimate($input: EstimateDeleteInput!) {{
  estimateDelete(input: $input) {{
    didSucceed
    {f.INPUT_ERRORS}
  }}
}}
"""

ESTIMATE_COLUMNS = [
    ("Number", "estimateNumber"),
    ("Customer", lambda r: (r.get("customer") or {}).get("name", "-")),
    ("Status", "status"),
    ("Date", "estimateDate"),
    ("Expires", "dueDate"),
    ("Total", lambda r: money(r.get("total"))),
    ("ID", "id"),
]


def _estimate_detail(estimate: Dict[str, Any]) -> str:
    header = (
        f"**Estimate {estimate.get('estimateNumber')} - {estimate.get('status')}**\n\n"
    )
    body = kv_block(
        [
            ("ID", f"`{estimate['id']}`"),
            ("Title", estimate.get("title")),
            ("Subhead", estimate.get("subhead")),
            ("Customer", (estimate.get("customer") or {}).get("name")),
            ("Customer email", (estimate.get("customer") or {}).get("email")),
            ("PO number", estimate.get("poNumber")),
            ("Estimate date", estimate.get("estimateDate")),
            ("Expires", estimate.get("dueDate")),
            ("Currency", (estimate.get("currency") or {}).get("code")),
            ("Subtotal", money(estimate.get("subtotal"))),
            ("Discount", money(estimate.get("discountTotal"))),
            ("Tax", money(estimate.get("taxTotal"))),
            ("Total", money(estimate.get("total"))),
            ("Paid", money(estimate.get("amountPaid"))),
            ("Amount due", money(estimate.get("amountDue"))),
            ("Deposit required", estimate.get("depositStatus")),
            ("Deposit value", estimate.get("depositValue")),
            ("Deposit unit", estimate.get("depositUnit")),
            ("Deposit total", money(estimate.get("depositTotal"))),
            ("Deposit payment status", estimate.get("depositPaymentStatus")),
            ("Memo", estimate.get("memo")),
            ("Footer", estimate.get("footer")),
            ("Last sent", estimate.get("lastSentAt")),
            ("Last viewed", estimate.get("lastViewedAt")),
            ("View URL", estimate.get("viewUrl")),
            ("PDF URL", estimate.get("pdfUrl")),
            ("Created", estimate.get("createdAt")),
            ("Modified", estimate.get("modifiedAt")),
        ]
    )

    sections = [header + body]

    items = estimate.get("items") or []
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
                ],
            )
        )

    history = estimate.get("history") or []
    if history:
        sections.append(
            "\n**Acceptance history**\n\n"
            + table(
                history,
                [
                    ("When", "timestamp"),
                    ("State", "state"),
                    ("Who", "name"),
                    ("Email", "email"),
                    ("Type", "entityType"),
                ],
            )
        )

    payments = [p for p in (estimate.get("payments") or []) if p]
    if payments:
        sections.append(
            "\n**Deposit payments**\n\n"
            + table(
                payments,
                [
                    ("Date", "paymentDate"),
                    ("Amount", "amount"),
                    ("Method", "paymentMethod"),
                    ("State", "state"),
                    ("ID", "id"),
                ],
            )
        )

    return "\n".join(sections)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def wave_list_estimates(
    business_id: Optional[str] = None,
    status: Optional[str] = None,
    customer_id: Optional[str] = None,
    currency: Optional[str] = None,
    estimate_number: Optional[str] = None,
    amount_due: Optional[str] = None,
    estimate_date_start: Optional[str] = None,
    estimate_date_end: Optional[str] = None,
    modified_after: Optional[str] = None,
    modified_before: Optional[str] = None,
    sort: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    fetch_all: bool = False,
    response_format: str = "markdown",
) -> str:
    """List estimates (quotes), filtered by status, customer, or date range.

    Args:
        business_id: Business to read. Defaults to the session business.
        status: DRAFT, SENT, VIEWED, ACCEPTED, APPROVED, CONVERTED, EXPIRED,
            REJECTED, ACTIVE, PAID, PARTIAL, UNPAID.
        customer_id: Only estimates for this customer.
        currency: Currency code, e.g. "USD".
        estimate_number: Exact estimate number match.
        amount_due: Exact outstanding amount match.
        estimate_date_start: Earliest estimate date, YYYY-MM-DD.
        estimate_date_end: Latest estimate date, YYYY-MM-DD.
        modified_after: ISO 8601 timestamp; only estimates changed after it.
        modified_before: ISO 8601 timestamp; only estimates changed before it.
        sort: A single value such as "ESTIMATE_DATE_DESC" or "TOTAL_DESC".
            Defaults to ESTIMATE_DATE_DESC.
        page: 1-based page number.
        page_size: Records per page (1-200).
        fetch_all: Walk every page.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    result = await client.paginate(
        LIST_ESTIMATES,
        compact(
            {
                "businessId": resolved,
                "sort": sort.upper() if sort else DEFAULT_ESTIMATE_SORT,
                "status": status.upper() if status else None,
                "customerId": customer_id,
                "currency": currency.upper() if currency else None,
                "estimateNumber": estimate_number,
                "amountDue": decimal_str(amount_due) if amount_due is not None else None,
                "estimateDateStart": estimate_date_start,
                "estimateDateEnd": estimate_date_end,
                "modifiedAtAfter": modified_after,
                "modifiedAtBefore": modified_before,
            }
        ),
        path=("business", "estimates"),
        page=page,
        page_size=page_size,
        fetch_all=fetch_all,
    )

    def as_markdown() -> str:
        return listing(result, "Estimates", ESTIMATE_COLUMNS)

    return render(result, response_format, as_markdown)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def wave_get_estimate(
    estimate_id: str,
    business_id: Optional[str] = None,
    include_attachments: bool = True,
    include_history: bool = True,
    include_deposit_payments: bool = True,
    response_format: str = "markdown",
) -> str:
    """Get one estimate in full: line items, deposits, acceptance history.

    Args:
        estimate_id: The Wave estimate ID.
        business_id: Business the estimate belongs to. Defaults to the session business.
        include_attachments: Include attached files.
        include_history: Include the acceptance and rejection audit trail.
        include_deposit_payments: Include deposit payments recorded against it.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    data = await client.execute(
        GET_ESTIMATE,
        {
            "businessId": resolved,
            "id": estimate_id,
            "embedAttachments": include_attachments,
            "embedHistory": include_history,
            "embedDepositPayments": include_deposit_payments,
        },
    )
    estimate = (data.get("business") or {}).get("estimate")
    if not estimate:
        return f"No estimate found with ID `{estimate_id}` in this business."
    return render(estimate, response_format, lambda: _estimate_detail(estimate))


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def wave_create_estimate(
    customer_id: str,
    items: List[Dict[str, Any]],
    business_id: Optional[str] = None,
    title: Optional[str] = None,
    subhead: Optional[str] = None,
    estimate_number: Optional[str] = None,
    po_number: Optional[str] = None,
    estimate_date: Optional[str] = None,
    due_date: Optional[str] = None,
    currency: Optional[str] = None,
    exchange_rate: Optional[str] = None,
    memo: Optional[str] = None,
    footer: Optional[str] = None,
    discounts: Optional[List[Dict[str, Any]]] = None,
    deposit_status: Optional[str] = None,
    deposit_value: Optional[str] = None,
    deposit_unit: Optional[str] = None,
    disable_credit_card_payments: Optional[bool] = None,
    disable_bank_payments: Optional[bool] = None,
    disable_amex_payments: Optional[bool] = None,
    require_terms_of_service_agreement: Optional[bool] = None,
    response_format: str = "markdown",
) -> str:
    """Create an estimate (quote).

    Line items follow the same shape as invoices and must reference a product:
    `{"productId": "...", "quantity": 2, "unitPrice": "150.00"}`. Unlike
    invoices, `unitPrice` is required on each line.

    To ask for a deposit, set `deposit_status` to ENABLED_OPTIONAL or
    ENABLED_MANDATORY along with `deposit_value` and `deposit_unit`.

    Estimates are always created as DRAFT.

    Args:
        customer_id: Customer to quote. Required.
        items: Line items, each with productId and unitPrice. Required.
        business_id: Business to create in. Defaults to the session business.
        title: Heading on the estimate. Defaults to "Estimate".
        subhead: Text under the title.
        estimate_number: Your own number. Wave assigns the next one if omitted.
        po_number: Customer purchase order number.
        estimate_date: Issue date, YYYY-MM-DD. Defaults to today.
        due_date: Expiry date, YYYY-MM-DD.
        currency: Currency code. Defaults to the customer or business currency.
        exchange_rate: Rate to the business currency.
        memo: Note shown to the customer.
        footer: Footer text.
        discounts: e.g. `[{"discountType": "PERCENTAGE", "percentage": "10"}]`.
        deposit_status: DISABLED, ENABLED_OPTIONAL, or ENABLED_MANDATORY.
        deposit_value: Deposit amount or percentage, e.g. "25".
        deposit_unit: AMOUNT or PERCENTAGE.
        disable_credit_card_payments: Turn off card payments.
        disable_bank_payments: Turn off bank payments.
        disable_amex_payments: Turn off Amex specifically.
        require_terms_of_service_agreement: Require terms acceptance.
        response_format: "markdown" or "json".
    """
    client = get_client()
    resolved = business_id_or_default(business_id)
    line_items = strip_estimate_item_taxes(
        normalize_line_items(
            require_items(items, "estimate line item"),
            context="Estimate",
            allow_name=True,
        )
    )
    payload = compact(
        {
            "businessId": resolved,
            "customerId": customer_id,
            "status": "DRAFT",
            "items": line_items,
            "title": title,
            "subhead": subhead,
            "estimateNumber": estimate_number,
            "poNumber": po_number,
            "estimateDate": estimate_date,
            "dueDate": due_date,
            "currency": currency.upper() if currency else None,
            "exchangeRate": (
                decimal_str(exchange_rate) if exchange_rate is not None else None
            ),
            "memo": memo,
            "footer": footer,
            "discounts": normalize_discounts(discounts, context="Estimate"),
            "depositStatus": deposit_status.upper() if deposit_status else None,
            "depositValue": (
                decimal_str(deposit_value) if deposit_value is not None else None
            ),
            "depositUnit": deposit_unit.upper() if deposit_unit else None,
            "disableCreditCardPayments": disable_credit_card_payments,
            "disableBankPayments": disable_bank_payments,
            "disableAmexPayments": disable_amex_payments,
            "requireTermsOfServiceAgreement": require_terms_of_service_agreement,
        }
    )
    result = await client.mutate(
        CREATE_ESTIMATE, {"input": payload}, root_field="estimateCreate"
    )
    estimate = result.get("estimate") or {}
    return render(
        estimate,
        response_format,
        lambda: success(
            f"Created estimate **{estimate.get('estimateNumber')}** ({estimate.get('status')}).",
            [
                ("ID", f"`{estimate.get('id')}`"),
                ("Customer", (estimate.get("customer") or {}).get("name")),
                ("Total", money(estimate.get("total"))),
                ("Expires", estimate.get("dueDate")),
                ("View URL", estimate.get("viewUrl")),
            ],
        )
        + "\n\nIt is a draft. Call wave_approve_estimate, then wave_send_estimate to email it.",
    )


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def wave_patch_estimate(
    estimate_id: str,
    customer_id: str,
    status: str,
    title: str,
    estimate_date: str,
    currency: str,
    exchange_rate: str,
    due_date: str,
    items: Optional[List[Dict[str, Any]]] = None,
    subhead: Optional[str] = None,
    estimate_number: Optional[str] = None,
    po_number: Optional[str] = None,
    memo: Optional[str] = None,
    footer: Optional[str] = None,
    discounts: Optional[List[Dict[str, Any]]] = None,
    deposit_status: Optional[str] = None,
    deposit_value: Optional[str] = None,
    deposit_unit: Optional[str] = None,
    disable_credit_card_payments: Optional[bool] = None,
    disable_bank_payments: Optional[bool] = None,
    disable_amex_payments: Optional[bool] = None,
    require_terms_of_service_agreement: Optional[bool] = None,
    response_format: str = "markdown",
) -> str:
    """Update an estimate.

    Wave's estimate patch is unusual: customer_id, status, title,
    estimate_date, currency, exchange_rate, and due_date are all mandatory even
    when unchanged. Read the estimate first with `wave_get_estimate` and pass
    its current values for anything you are not changing, or those fields will
    be overwritten.

    Supplying `items` replaces every line item.

    Args:
        estimate_id: The estimate to update. Required.
        customer_id: Customer. Required by Wave even if unchanged.
        status: Current or new status. Required by Wave even if unchanged.
        title: Title. Required by Wave even if unchanged.
        estimate_date: Issue date, YYYY-MM-DD. Required by Wave even if unchanged.
        currency: Currency code. Required by Wave even if unchanged.
        exchange_rate: Exchange rate. Required by Wave even if unchanged.
        due_date: Expiry date, YYYY-MM-DD. Required by Wave even if unchanged.
        items: Replacement line items. Replaces all existing items.
        subhead: New subhead.
        estimate_number: New estimate number.
        po_number: New PO number.
        memo: New customer-facing memo.
        footer: New footer.
        discounts: Replacement discount list.
        deposit_status: DISABLED, ENABLED_OPTIONAL, ENABLED_MANDATORY.
        deposit_value: Deposit amount or percentage.
        deposit_unit: AMOUNT or PERCENTAGE.
        disable_credit_card_payments: Turn card payments off or on.
        disable_bank_payments: Turn bank payments off or on.
        disable_amex_payments: Turn Amex off or on.
        require_terms_of_service_agreement: Require terms acceptance.
        response_format: "markdown" or "json".
    """
    client = get_client()
    payload = compact(
        {
            "id": estimate_id,
            "customerId": customer_id,
            "status": status.upper(),
            "title": title,
            "estimateDate": estimate_date,
            "currency": currency.upper(),
            "exchangeRate": decimal_str(exchange_rate),
            "dueDate": due_date,
            "items": (
                strip_estimate_item_taxes(
                    normalize_line_items(items, context="Estimate", allow_name=True)
                )
                if items is not None
                else None
            ),
            "subhead": subhead,
            "estimateNumber": estimate_number,
            "poNumber": po_number,
            "memo": memo,
            "footer": footer,
            "discounts": normalize_discounts(discounts, context="Estimate"),
            "depositStatus": deposit_status.upper() if deposit_status else None,
            "depositValue": (
                decimal_str(deposit_value) if deposit_value is not None else None
            ),
            "depositUnit": deposit_unit.upper() if deposit_unit else None,
            "disableCreditCardPayments": disable_credit_card_payments,
            "disableBankPayments": disable_bank_payments,
            "disableAmexPayments": disable_amex_payments,
            "requireTermsOfServiceAgreement": require_terms_of_service_agreement,
        }
    )
    result = await client.mutate(
        PATCH_ESTIMATE, {"input": payload}, root_field="estimatePatch"
    )
    estimate = result.get("estimate") or {}
    return render(
        estimate,
        response_format,
        lambda: success(
            f"Updated estimate **{estimate.get('estimateNumber')}**.",
            [
                ("ID", f"`{estimate.get('id')}`"),
                ("Status", estimate.get("status")),
                ("Total", money(estimate.get("total"))),
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
async def wave_clone_estimate(
    estimate_id: str,
    response_format: str = "markdown",
) -> str:
    """Copy an estimate into a new draft.

    Args:
        estimate_id: The estimate to copy.
        response_format: "markdown" or "json".
    """
    client = get_client()
    result = await client.mutate(
        CLONE_ESTIMATE, {"input": {"estimateId": estimate_id}}, root_field="estimateClone"
    )
    estimate = result.get("estimate") or {}
    return render(
        estimate,
        response_format,
        lambda: success(
            f"Cloned into draft estimate **{estimate.get('estimateNumber')}**.",
            [
                ("New ID", f"`{estimate.get('id')}`"),
                ("Status", estimate.get("status")),
                ("Total", money(estimate.get("total"))),
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
async def wave_approve_estimate(
    estimate_id: str,
    response_format: str = "markdown",
) -> str:
    """Approve a draft estimate so it can be sent to the customer.

    This does not notify anyone: use `wave_send_estimate` for that.

    Args:
        estimate_id: The draft estimate to approve.
        response_format: "markdown" or "json".
    """
    client = get_client()
    result = await client.mutate(
        APPROVE_ESTIMATE,
        {"input": {"estimateId": estimate_id}},
        root_field="estimateApprove",
    )
    estimate = result.get("estimate") or {}
    return render(
        estimate,
        response_format,
        lambda: success(
            f"Approved estimate **{estimate.get('estimateNumber')}**.",
            [
                ("ID", f"`{estimate.get('id')}`"),
                ("Status", estimate.get("status")),
                ("View URL", estimate.get("viewUrl")),
            ],
        )
        + "\n\nThe customer has not been notified. Call wave_send_estimate to email it.",
    )


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def wave_send_estimate(
    estimate_id: str,
    to: Any,
    subject: Optional[str] = None,
    message: Optional[str] = None,
    attach_pdf: bool = False,
    cc_myself: Optional[bool] = None,
    from_address: Optional[str] = None,
    hide_grand_total: bool = False,
    include_attachments: bool = False,
    response_format: str = "markdown",
) -> str:
    """Email an estimate to a customer through Wave.

    This sends real email to real recipients, so confirm the addresses before
    calling it. Sending is not reversible.

    Args:
        estimate_id: The estimate to send. Required.
        to: Recipient email address, or a list of them. Required.
        subject: Email subject. Wave supplies a default.
        message: Body text. Wave supplies a default.
        attach_pdf: Attach the estimate PDF.
        cc_myself: Send a copy to the business email.
        from_address: Reply-to address; must be verified in Wave.
        hide_grand_total: Omit the grand total from the email body.
        include_attachments: Include files attached to the estimate.
        response_format: "markdown" or "json".
    """
    client = get_client()
    recipients = normalize_recipients(to, context="wave_send_estimate")
    payload = compact(
        {
            "estimateId": estimate_id,
            "to": recipients,
            "subject": subject,
            "message": message,
            "attachPDF": attach_pdf,
            "ccMyself": cc_myself,
            "fromAddress": from_address,
            "hideGrandTotal": hide_grand_total,
            "includeAttachments": include_attachments,
        }
    )
    result = await client.mutate(
        SEND_ESTIMATE, {"input": payload}, root_field="estimateSend"
    )
    estimate = result.get("estimate") or {}
    return render(
        estimate,
        response_format,
        lambda: success(
            f"Sent estimate **{estimate.get('estimateNumber')}** to {', '.join(recipients)}.",
            [
                ("ID", f"`{estimate.get('id')}`"),
                ("Status", estimate.get("status")),
                ("Sent at", estimate.get("lastSentAt")),
                ("View URL", estimate.get("viewUrl")),
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
async def wave_mark_estimate_sent(
    estimate_id: str,
    send_method: str = "MARKED_SENT",
    sent_at: Optional[str] = None,
    response_format: str = "markdown",
) -> str:
    """Record that an estimate was delivered outside Wave. Sends no email.

    Args:
        estimate_id: The estimate to mark. Required.
        send_method: MARKED_SENT, EXPORT_PDF, SHARED_LINK, GMAIL, OUTLOOK,
            YAHOO, WAVE, NOT_SENT, SKIPPED. Defaults to MARKED_SENT.
        sent_at: When it was sent, ISO 8601. Defaults to now.
        response_format: "markdown" or "json".
    """
    client = get_client()
    payload = compact(
        {
            "estimateId": estimate_id,
            "sendMethod": send_method.upper(),
            "sentAt": sent_at,
        }
    )
    result = await client.mutate(
        MARK_SENT_ESTIMATE, {"input": payload}, root_field="estimateMarkSent"
    )
    estimate = result.get("estimate") or {}
    return render(
        estimate,
        response_format,
        lambda: success(
            f"Marked estimate **{estimate.get('estimateNumber')}** as sent. No email was sent.",
            [("ID", f"`{estimate.get('id')}`"), ("Status", estimate.get("status"))],
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
async def wave_mark_estimate_accepted(
    estimate_id: str,
    response_format: str = "markdown",
) -> str:
    """Record that the customer accepted an estimate.

    Use this when acceptance happened offline. An accepted estimate can be
    turned into an invoice with `wave_convert_estimate_to_invoice`.

    Args:
        estimate_id: The estimate the customer accepted.
        response_format: "markdown" or "json".
    """
    client = get_client()
    result = await client.mutate(
        MARK_ACCEPTED_ESTIMATE,
        {"input": {"estimateId": estimate_id}},
        root_field="estimateMarkAccepted",
    )
    estimate = result.get("estimate") or {}
    return render(
        estimate,
        response_format,
        lambda: success(
            f"Marked estimate **{estimate.get('estimateNumber')}** as accepted.",
            [("ID", f"`{estimate.get('id')}`"), ("Status", estimate.get("status"))],
        )
        + "\n\nCall wave_convert_estimate_to_invoice to bill it.",
    )


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def wave_reset_estimate_acceptance(
    estimate_id: str,
    response_format: str = "markdown",
) -> str:
    """Undo an estimate's acceptance, returning it to an unaccepted state.

    This discards the recorded acceptance, including who accepted and when.

    Args:
        estimate_id: The estimate to reset.
        response_format: "markdown" or "json".
    """
    client = get_client()
    result = await client.mutate(
        RESET_ACCEPTANCE_ESTIMATE,
        {"input": {"estimateId": estimate_id}},
        root_field="estimateResetAcceptance",
    )
    estimate = result.get("estimate") or {}
    return render(
        estimate,
        response_format,
        lambda: success(
            f"Reset acceptance on estimate **{estimate.get('estimateNumber')}**.",
            [("ID", f"`{estimate.get('id')}`"), ("Status", estimate.get("status"))],
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
async def wave_send_estimate_acceptance_email(
    estimate_id: str,
    response_format: str = "markdown",
) -> str:
    """Email the customer a confirmation that their estimate was accepted.

    This sends real email to the customer on file.

    Args:
        estimate_id: The accepted estimate.
        response_format: "markdown" or "json".
    """
    client = get_client()
    result = await client.mutate(
        SEND_ACCEPTANCE_EMAIL,
        {"input": {"estimateId": estimate_id}},
        root_field="estimateSendAcceptanceCustomerEmail",
    )
    estimate = result.get("estimate") or {}
    return render(
        estimate,
        response_format,
        lambda: success(
            f"Sent an acceptance confirmation for estimate **{estimate.get('estimateNumber')}**.",
            [("ID", f"`{estimate.get('id')}`"), ("Status", estimate.get("status"))],
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
async def wave_generate_estimate_pdf(
    estimate_id: str,
    response_format: str = "markdown",
) -> str:
    """Generate a PDF of an estimate and return its download URL.

    Args:
        estimate_id: The estimate to render.
        response_format: "markdown" or "json".
    """
    client = get_client()
    result = await client.mutate(
        GENERATE_PDF,
        {"input": {"estimateId": estimate_id}},
        root_field="estimateGeneratePdf",
    )
    pdf_url = result.get("pdfUrl")
    return render(
        {"pdfUrl": pdf_url},
        response_format,
        lambda: success(
            f"Generated a PDF for estimate `{estimate_id}`.", [("PDF URL", pdf_url)]
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
async def wave_convert_estimate_to_invoice(
    estimate_id: str,
    response_format: str = "markdown",
) -> str:
    """Turn an accepted estimate into an invoice.

    Wave copies the customer, line items, and totals onto a new invoice and
    marks the estimate CONVERTED. The invoice starts as a draft; approve and
    send it with `wave_approve_invoice` and `wave_send_invoice`.

    Args:
        estimate_id: The estimate to convert.
        response_format: "markdown" or "json".
    """
    client = get_client()
    result = await client.mutate(
        CONVERT_TO_INVOICE,
        {"input": {"estimateId": estimate_id}},
        root_field="convertEstimateToInvoice",
    )
    invoice_id = result.get("invoiceId")
    return render(
        {"invoiceId": invoice_id},
        response_format,
        lambda: success(
            f"Converted estimate `{estimate_id}` into an invoice.",
            [("New invoice ID", f"`{invoice_id}`")],
        )
        + "\n\nCall wave_get_invoice to review it, then wave_approve_invoice to finalize.",
    )


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def wave_delete_estimate(estimate_id: str) -> str:
    """Delete an estimate. This cannot be undone.

    Args:
        estimate_id: The estimate to delete.
    """
    client = get_client()
    await client.mutate(
        DELETE_ESTIMATE,
        {"input": {"estimateId": estimate_id}},
        root_field="estimateDelete",
    )
    return f"Deleted estimate `{estimate_id}`."
