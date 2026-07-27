"""Reusable GraphQL fragments, one per Wave entity.

Keeping selection sets in one place means a field is spelled correctly once
rather than once per query, and it keeps every tool's output shape consistent.

``Money`` is always selected in full: ``value`` is the display string, while
``raw`` and ``minorUnitValue`` give the amount in minor units for arithmetic
that must stay exact.
"""

from __future__ import annotations

PAGE_INFO = """
fragment PageInfoFields on OffsetPageInfo {
  currentPage
  totalPages
  totalCount
}
"""

MONEY = """
fragment MoneyFields on Money {
  raw
  minorUnitValue
  value
  currency { code symbol }
}
"""

ADDRESS = """
fragment AddressFields on Address {
  addressLine1
  addressLine2
  city
  postalCode
  province { code name }
  country { code name }
}
"""

CURRENCY = """
fragment CurrencyFields on Currency {
  code
  symbol
  name
  plural
  exponent
}
"""

ACCOUNT = """
fragment AccountFields on Account {
  id
  name
  description
  displayId
  classicId
  isArchived
  sequence
  balance
  balanceInBusinessCurrency
  normalBalanceType
  currency { code symbol }
  type { name value normalBalanceType }
  subtype { name value archivable systemCreated }
}
"""

CUSTOMER = """
fragment CustomerFields on Customer {
  id
  name
  firstName
  lastName
  displayId
  email
  mobile
  phone
  fax
  tollFree
  website
  internalNotes
  isArchived
  createdAt
  modifiedAt
  currency { code symbol }
  address { ...AddressFields }
  shippingDetails {
    name
    phone
    instructions
    address { ...AddressFields }
  }
  outstandingAmount { ...MoneyFields }
  overdueAmount { ...MoneyFields }
}
"""

VENDOR = """
fragment VendorFields on Vendor {
  id
  name
  firstName
  lastName
  displayId
  email
  mobile
  phone
  fax
  tollFree
  website
  internalNotes
  isArchived
  createdAt
  modifiedAt
  currency { code symbol }
  address { ...AddressFields }
  shippingDetails {
    name
    phone
    instructions
    address { ...AddressFields }
  }
}
"""

PRODUCT = """
fragment ProductFields on Product {
  id
  name
  description
  unitPrice
  isSold
  isBought
  isArchived
  createdAt
  modifiedAt
  incomeAccount { id name }
  expenseAccount { id name }
  defaultSalesTaxes { id name abbreviation rate }
}
"""

SALES_TAX = """
fragment SalesTaxFields on SalesTax {
  id
  name
  abbreviation
  description
  taxNumber
  showTaxNumberOnInvoices
  rate
  rates { effective rate }
  isCompound
  isRecoverable
  isArchived
  createdAt
  modifiedAt
}
"""

INVOICE_PAYMENT = """
fragment InvoicePaymentFields on InvoicePayment {
  id
  amount
  paymentDate
  paymentMethod
  memo
  exchangeRate
  displayExchangeRate
  origin
  state
  transactionType
  paymentProvider
  transactionId
  confirmationCode
  institutionName
  authorizerName
  accountNumberLast3
  accountingTransactionId
  paymentMethodId
  active
  readonlyUrl
  createdAt
  modifiedAt
  businessCurrency { code symbol }
  invoiceCurrency { code symbol }
  paymentCurrency { code symbol }
  account { id name }
  customer { id name }
  paymentDetails { cardType lastFour cardExpiryMonth cardExpiryYear cardSource }
}
"""

# Discounts are interfaces; both concrete types must be spread explicitly.
INVOICE_DISCOUNT = """
fragment InvoiceDiscountFields on InvoiceDiscount {
  name
  createdAt
  modifiedAt
  ... on FixedInvoiceDiscount { amount }
  ... on PercentageInvoiceDiscount { percentage }
}
"""

ESTIMATE_DISCOUNT = """
fragment EstimateDiscountFields on EstimateDiscount {
  name
  createdAt
  modifiedAt
  ... on FixedEstimateDiscount { amount }
  ... on PercentageEstimateDiscount { percentage }
}
"""

INVOICE = """
fragment InvoiceFields on Invoice {
  id
  status
  title
  subhead
  invoiceNumber
  poNumber
  invoiceDate
  dueDate
  memo
  footer
  pdfUrl
  viewUrl
  exchangeRate
  createdAt
  modifiedAt
  lastSentAt
  lastSentVia
  lastViewedAt
  requireTermsOfServiceAgreement
  disableCreditCardPayments
  disableBankPayments
  disableAmexPayments
  itemTitle
  unitTitle
  priceTitle
  amountTitle
  hideName
  hideDescription
  hideUnit
  hidePrice
  hideAmount
  currency { code symbol }
  customer { id name email }
  subtotal { ...MoneyFields }
  taxTotal { ...MoneyFields }
  discountTotal { ...MoneyFields }
  total { ...MoneyFields }
  amountDue { ...MoneyFields }
  amountPaid { ...MoneyFields }
  discounts { ...InvoiceDiscountFields }
  source {
    ... on Estimate { id }
    ... on NewEstimate { id }
    ... on RecurringInvoice { id }
  }
  invoiceReminders {
    id
    daysDelta
    sent
    sentManually
    issueDate
  }
  items {
    id
    description
    quantity
    price
    unitPrice
    account { id name }
    product { id name }
    subtotal { ...MoneyFields }
    total { ...MoneyFields }
    taxes {
      amount { ...MoneyFields }
      rate
      salesTax { id name abbreviation }
    }
  }
  attachments { id fileName fileSize filePath downloadUrl uploadStatusUpdatedAt }
}
"""

ESTIMATE = """
fragment EstimateFields on AREstimate {
  id
  status
  title
  subhead
  estimateNumber
  poNumber
  estimateDate
  dueDate
  memo
  footer
  pdfUrl
  viewUrl
  exchangeRate
  createdAt
  modifiedAt
  lastSentAt
  lastSentVia
  lastViewedAt
  requireTermsOfServiceAgreement
  disableCreditCardPayments
  disableBankPayments
  disableAmexPayments
  itemTitle
  unitTitle
  priceTitle
  amountTitle
  hideName
  hideDescription
  hideUnit
  hidePrice
  hideAmount
  depositStatus
  depositUnit
  depositValue
  depositPaymentStatus
  currency { code symbol }
  customer { id name email }
  subtotal { ...MoneyFields }
  taxTotal { ...MoneyFields }
  discountTotal { ...MoneyFields }
  total { ...MoneyFields }
  amountDue { ...MoneyFields }
  amountPaid { ...MoneyFields }
  depositTotal { ...MoneyFields }
  discounts { ...EstimateDiscountFields }
  items {
    id
    description
    quantity
    unitPrice
    account { id name }
    product { id name }
    subtotal { ...MoneyFields }
    total { ...MoneyFields }
    taxes {
      amount { ...MoneyFields }
      salesTax { id name abbreviation }
    }
  }
}
"""

ESTIMATE_PAYMENT = """
fragment EstimatePaymentFields on EstimatePayment {
  id
  amount
  paymentDate
  paymentMethod
  memo
  paymentAccountId
  origin
  state
  transactionType
  paymentProvider
  transactionId
  confirmationCode
  originPaymentId
  paymentMethodId
  active
  readonlyUrl
  createdAt
  modifiedAt
  currency { code symbol }
  paymentDetails { cardType lastFour cardExpiryMonth cardExpiryYear cardSource }
}
"""

BUSINESS = """
fragment BusinessFields on Business {
  id
  name
  isPersonal
  isClassicAccounting
  isClassicInvoicing
  isArchived
  organizationalType
  timezone
  phone
  fax
  mobile
  tollFree
  website
  emailSendEnabled
  createdAt
  modifiedAt
  currency { code symbol name }
  type { name value }
  subtype { name value }
  address { ...AddressFields }
}
"""

INPUT_ERRORS = "inputErrors { path message code }"


def build(document: str, *fragments: str) -> str:
    """Concatenate an operation with the fragments it references.

    Fragments are deduplicated so a caller can list a dependency (``MONEY``)
    without worrying whether another fragment already pulled it in.
    """
    seen: set[str] = set()
    parts = [document.strip()]
    for fragment in fragments:
        cleaned = fragment.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            parts.append(cleaned)
    return "\n\n".join(parts)


# Common fragment bundles, so call sites stay short.
MONEY_SET = (MONEY,)
ADDRESS_SET = (ADDRESS,)
CUSTOMER_SET = (CUSTOMER, ADDRESS, MONEY)
VENDOR_SET = (VENDOR, ADDRESS)
INVOICE_SET = (INVOICE, INVOICE_DISCOUNT, MONEY)
ESTIMATE_SET = (ESTIMATE, ESTIMATE_DISCOUNT, MONEY)
BUSINESS_SET = (BUSINESS, ADDRESS)
