# Wave Accounting MCP Server

An MCP server for [Wave Accounting](https://www.waveapps.com) that covers Wave's public GraphQL API in full: **every mutation (42) and every root query (11)**, exposed as 73 tools.

Invoices, estimates, customers, products, sales taxes, payments, the chart of accounts, and double-entry bookkeeping transactions are all reachable from an MCP client.

## What's covered

| Area | Tools |
|------|-------|
| **Businesses** | List, get, set session default, invoice/estimate branding |
| **Chart of accounts** | List, get, create, patch, archive, plus the type/subtype taxonomy |
| **Customers** | List, get, create, patch, delete |
| **Vendors** | List, get (read-only — see [API limitations](#wave-api-limitations)) |
| **Products** | List, get, create, patch, archive |
| **Sales taxes** | List, get, create, patch (including scheduled rate changes), archive |
| **Invoices** | List, get, create, patch, clone, approve, send, mark sent, delete |
| **Invoice payments** | Get, create, patch, delete, email receipt |
| **Estimates** | List, get, create, patch, clone, approve, send, mark sent, mark accepted, reset acceptance, generate PDF, convert to invoice, delete |
| **Estimate deposits** | Get, create, update, delete, email receipt |
| **Transactions** | Single, bulk, and deposit-with-fees bookkeeping entries |
| **Reference data** | User, currencies, countries, provinces, OAuth application |
| **Convenience** | Receipt-to-expense and payment-to-income, with account name matching |

Every read tool supports `response_format` (`markdown` for a compact summary, `json` for the complete record) and offset pagination via `page` / `page_size`, or `fetch_all=true` to walk every page.

## Installation

```bash
git clone https://github.com/oliverames/wave_mcp.git
cd wave_mcp
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Getting an access token

Wave uses OAuth2 bearer tokens. Create an application and generate a token in the [Wave developer portal](https://developer.waveapps.com/hc/en-us/articles/360020948171). Then:

```bash
cp .env.example .env
```

and set `WAVE_ACCESS_TOKEN` in `.env`.

| Variable | Required | Purpose |
|----------|----------|---------|
| `WAVE_ACCESS_TOKEN` | Yes | OAuth2 bearer token |
| `WAVE_BUSINESS_ID` | No | Default business, so tools can omit `business_id` |
| `WAVE_MCP_LOG_LEVEL` | No | `DEBUG`, `INFO` (default), `WARNING`, `ERROR` |

| `WAVE_MCP_TIMEOUT` | No | Per-request timeout in seconds (default 20) |
| `WAVE_MCP_TOTAL_BUDGET` | No | Total seconds for one call including retries (default 50) |

## Client configuration

The server speaks stdio, so any MCP client can launch it. Both paths must be absolute, and `command` should point at the venv's Python so dependencies resolve.

### Claude Code / Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows:

```json
{
  "mcpServers": {
    "wave_mcp": {
      "command": "/absolute/path/to/wave_mcp/.venv/bin/python",
      "args": ["/absolute/path/to/wave_mcp/mcp_server.py"],
      "env": {
        "WAVE_ACCESS_TOKEN": "your_token_here"
      }
    }
  }
}
```

Restart the client after saving.

### Codex CLI

Register it with the `codex mcp` command, which writes to `~/.codex/config.toml` atomically:

```bash
codex mcp add wave_mcp --env WAVE_ACCESS_TOKEN=your_token_here -- /absolute/path/to/wave_mcp/.venv/bin/python /absolute/path/to/wave_mcp/mcp_server.py
```

Or write the TOML yourself:

```toml
[mcp_servers.wave_mcp]
command = "/absolute/path/to/wave_mcp/.venv/bin/python"
args = ["/absolute/path/to/wave_mcp/mcp_server.py"]
startup_timeout_sec = 10
tool_timeout_sec = 60

[mcp_servers.wave_mcp.env]
WAVE_ACCESS_TOKEN = "your_token_here"
WAVE_BUSINESS_ID = "your_business_id"
```

Verify with `codex mcp list` or `codex mcp get wave_mcp`. The CLI and the VS Code extension share this file.

Three Codex-specific notes, all verified against Codex CLI 0.145.0:

- **Startup fits the default timeout.** Codex allows 10 seconds (`startup_timeout_sec`); this server completes `initialize` and `tools/list` in about 0.5s because the Wave client is built lazily on first use, not at import.
- **Retries fit the tool timeout.** Codex kills a tool call at 60 seconds (`tool_timeout_sec`). The client caps all attempts for one request at `WAVE_MCP_TOTAL_BUDGET` (default 50s) and abandons a retry it cannot finish in time, so you get Wave's real error rather than a client-side timeout. Raise both together if you need longer.
- **Codex uses tools and `instructions`, not resources.** It reads the server `instructions` field returned at initialize, which this server provides. The seven `wave://` resources below are ignored by Codex; nothing is resource-only, so no capability is lost.

Long `fetch_all=true` sweeps over thousands of records can still exceed 60 seconds. Prefer explicit `page` / `page_size` on large data sets, or raise `tool_timeout_sec` alongside `WAVE_MCP_TOTAL_BUDGET`.

## Resources

Alongside the tools, the server exposes read-only JSON resources for grounding context. Clients that support resources (Claude Desktop) can attach them directly; clients that do not (Codex) reach the same data through tools.

| URI | Contents |
|-----|----------|
| `wave://businesses` | Every reachable business |
| `wave://accounts` | Default business's chart of accounts |
| `wave://customers` | Customers with balances |
| `wave://vendors` | Vendors |
| `wave://products` | Products and services |
| `wave://sales-taxes` | Sales taxes and rate history |
| `wave://account-taxonomy` | Account types and subtypes |

All except `wave://businesses` read the default business, so set one first.

## Getting started

Wave scopes almost everything to a business, so start there:

```
List my Wave businesses, then set the first one as the default.
```

After that, tools can omit `business_id`. Any tool still accepts an explicit `business_id` to override the default.

## Usage examples

**Record a receipt**

```
Log a $45.99 expense from Office Depot on 2026-03-15 for office supplies,
paid from Business Checking.
```

**Bill a customer end to end**

```
Create an invoice for Acme Corp with 10 hours of consulting at $150/hour,
due in 30 days. Approve it and email it to billing@acme.com.
```

**Quote, then convert**

```
Create an estimate for Acme Corp for the website redesign package with a
25% deposit, send it to them, and convert it to an invoice once accepted.
```

**Split a transaction**

```
Record a $100 withdrawal from checking on 2026-07-01: $60 to fuel and
$40 to meals.
```

**Reconcile a processor payout**

```
A Stripe payout of $97 landed in checking on 2026-07-02: $100 of consulting
income less a $3 processing fee.
```

**Review the books**

```
Show me every unpaid invoice over $500, sorted by amount due.
```

## Tool reference

Names are prefixed `wave_` so they do not collide with other MCP servers.

<details>
<summary><b>Businesses and reference data</b></summary>

| Tool | Purpose |
|------|---------|
| `wave_list_businesses` | List reachable businesses |
| `wave_get_business` | Full business detail |
| `wave_set_default_business` | Set the session default |
| `wave_get_invoice_estimate_settings` | Accent color and logo |
| `wave_get_user` | Account the token belongs to |
| `wave_get_oauth_application` | Application that issued the token |
| `wave_list_currencies` / `wave_get_currency` | Supported currencies |
| `wave_list_countries` / `wave_get_country` | Countries and their provinces |
| `wave_get_province` | One province or state |
| `wave_list_account_types` | The five top-level account types |
| `wave_list_account_subtypes` | Subtypes — needed by `wave_create_account` |

</details>

<details>
<summary><b>Accounts, customers, vendors, products, taxes</b></summary>

| Tool | Purpose |
|------|---------|
| `wave_list_accounts` / `wave_get_account` | Chart of accounts, with balances |
| `wave_create_account` / `wave_patch_account` / `wave_archive_account` | Manage accounts |
| `wave_list_customers` / `wave_get_customer` | Customers, with outstanding balances |
| `wave_create_customer` / `wave_patch_customer` / `wave_delete_customer` | Manage customers |
| `wave_list_vendors` / `wave_get_vendor` | Vendors (read-only) |
| `wave_list_products` / `wave_get_product` | Products and services |
| `wave_create_product` / `wave_patch_product` / `wave_archive_product` | Manage products |
| `wave_list_sales_taxes` / `wave_get_sales_tax` | Sales taxes and rate history |
| `wave_create_sales_tax` / `wave_patch_sales_tax` / `wave_archive_sales_tax` | Manage taxes |

</details>

<details>
<summary><b>Invoices and payments</b></summary>

| Tool | Purpose |
|------|---------|
| `wave_list_invoices` / `wave_get_invoice` | Invoices, with items and payments |
| `wave_create_invoice` / `wave_patch_invoice` / `wave_clone_invoice` | Build invoices |
| `wave_approve_invoice` | Move a draft into the books |
| `wave_send_invoice` | **Sends real email** |
| `wave_mark_invoice_sent` | Record delivery made outside Wave |
| `wave_delete_invoice` | Delete an invoice |
| `wave_get_invoice_payment` | One payment |
| `wave_create_invoice_payment` / `wave_patch_invoice_payment` / `wave_delete_invoice_payment` | Record payments |
| `wave_send_invoice_payment_receipt` | **Sends real email** |

</details>

<details>
<summary><b>Estimates and deposits</b></summary>

| Tool | Purpose |
|------|---------|
| `wave_list_estimates` / `wave_get_estimate` | Estimates, with history and deposits |
| `wave_create_estimate` / `wave_patch_estimate` / `wave_clone_estimate` | Build estimates |
| `wave_approve_estimate` | Approve a draft |
| `wave_send_estimate` | **Sends real email** |
| `wave_mark_estimate_sent` / `wave_mark_estimate_accepted` | Record offline delivery and acceptance |
| `wave_reset_estimate_acceptance` | Undo an acceptance |
| `wave_send_estimate_acceptance_email` | **Sends real email** |
| `wave_generate_estimate_pdf` | Render a PDF |
| `wave_convert_estimate_to_invoice` | Turn an accepted estimate into an invoice |
| `wave_delete_estimate` | Delete an estimate |
| `wave_get_estimate_payment` | One deposit payment |
| `wave_create_estimate_deposit_payment` / `wave_update_estimate_deposit_payment` / `wave_delete_estimate_payment` | Record deposits |
| `wave_send_estimate_deposit_receipt` | **Sends real email** |

</details>

<details>
<summary><b>Bookkeeping transactions</b></summary>

| Tool | Purpose |
|------|---------|
| `wave_create_money_transaction` | One expense, income, or transfer |
| `wave_create_money_transactions` | Bulk import, applied atomically |
| `wave_create_deposit_transaction` | A deposit whose net differs from gross because of fees |
| `wave_create_expense_from_receipt` | Expense, with the account matched from a category word |
| `wave_create_income_from_payment` | Income, with the account matched from a category word |

</details>

## How transactions work

Wave is double-entry, so `wave_create_money_transaction` has two sides:

- The **anchor** is the account money physically moved through — a bank account or credit card — with a `direction` of `DEPOSIT` or `WITHDRAWAL`.
- The **line items** are the categories the money is attributed to. Their amounts must total the anchor amount.

A $50 office-supplies expense paid from checking is one anchor (checking, `WITHDRAWAL`, `50.00`) and one line item (Office Supplies, `50.00`). A split is the same anchor with more line items. The server checks the totals before calling Wave and reports the exact discrepancy if they disagree, since Wave's own error names neither figure.

Every transaction carries an `external_id`. Wave deduplicates on it, so passing a stable value of your own makes retries safe. One is generated when you omit it.

## Wave API limitations

These are constraints in Wave's API, not gaps in this server. Each was confirmed against the live schema.

- **Transactions cannot be read back.** Wave can create money transactions but exposes no query to list them — there is no `transactions` connection on `Business`. Review them in the Wave web app.
- **Vendors are read-only.** The schema has no `vendorCreate`, `vendorPatch`, or `vendorDelete`. Add vendors under Purchases → Vendors in the web app.
- **Money transactions cannot reference a vendor.** `wave_create_expense_from_receipt` records the vendor name in the description instead.
- **`wave_patch_estimate` needs fields you are not changing.** Wave marks `customerId`, `status`, `title`, `estimateDate`, `currency`, `exchangeRate`, and `dueDate` as required on the patch input. Read the estimate first and pass its current values back, or they will be overwritten.
- **`wave_patch_account` needs the account's current `sequence`** as an optimistic-concurrency check. Read it with `wave_get_account` first.
- **Line items must reference a product.** Invoices and estimates cannot carry free-text lines; create a product first if you need one.
- **`wave_create_deposit_transaction` returns no ID.** Wave's payload has no transaction field, so there is nothing to reference afterward.
- **Deposit transaction amounts are `Float`,** not `Decimal` like everywhere else, so they carry binary floating-point representation. Every other money field in this server is sent as a string to keep it exact.
- **Bills, receipts, payroll, and reports have no API.** Wave's public schema does not expose them.
- **No file attachments.** Receipt images and PDFs cannot be uploaded.
- **Rate limits are tight** — roughly 2 concurrent requests. The client retries 429s with exponential backoff, honouring `Retry-After`.
- **Un-archiving is web-app only.** Archive mutations exist; their inverse does not.

## Development

```bash
.venv/bin/python -m pytest tests/ -v      # 51 tests, no network access
.venv/bin/python -m pyflakes wave_mcp/    # lint
```

The test suite stubs the transport, so it covers input validation, pagination, error mapping, retry budgeting, tool metadata, and every rendering path without touching Wave.

Because Wave validates a GraphQL document *before* it checks authentication, every query in this repo can be schema-checked without a token: an `UNAUTHENTICATED` response means the document is valid, while `GRAPHQL_VALIDATION_FAILED` means it is not. All 72 documents are verified this way against the live endpoint.

`evaluation/` holds ten read-only questions in the mcp-builder evaluation format for measuring how well a model drives these tools. The answers are placeholders — see [evaluation/README.md](evaluation/README.md), which explains why they cannot be filled in without a real Wave business.

### Project structure

```
wave_mcp/
├── mcp_server.py           # Entry point (unchanged path, for existing configs)
├── wave_mcp/
│   ├── client.py           # GraphQL transport: auth, retries, budget, pagination
│   ├── errors.py           # Exception hierarchy
│   ├── fragments.py        # Reusable GraphQL fragments
│   ├── formatting.py       # Markdown and JSON rendering
│   ├── resources.py        # Read-only wave:// resources
│   ├── runtime.py          # FastMCP instance, tool decorator, shared types
│   ├── server.py           # Assembly and stdio transport
│   └── tools/              # One module per API domain
├── evaluation/             # mcp-builder evaluation questions
└── tests/test_tools.py
```

### Field coverage

Seven schema fields are selected nowhere, all deliberately: `internalId` on four types and `Invoice.anonymousId` are Wave's legacy internal identifiers rather than the API `id`; `InvoicePayment.originInvoicePayment` is a self-reference that would nest without bound; and `OAuthApplication.extraData` is an opaque blob. Everything else on every type this server touches is selected.

## Upgrading from 1.x

Tools were renamed to a consistent `wave_` namespace. Old names map as follows:

| 1.x | 2.x |
|-----|-----|
| `list_businesses` | `wave_list_businesses` |
| `set_business` | `wave_set_default_business` |
| `get_expense_accounts` | `wave_list_accounts` with `types=["EXPENSE"]` |
| `get_income_accounts` | `wave_list_accounts` with `types=["INCOME"]` |
| `search_vendor` | `wave_list_vendors` with `name_contains` |
| `search_customer` | `wave_list_customers` with `name_contains` |
| `debug_accounts` | `wave_list_accounts` with `fetch_all=true` |
| `create_expense_from_receipt` | `wave_create_expense_from_receipt` |
| `create_income_from_payment` | `wave_create_income_from_payment` |

One behavioural change is worth calling out. The 1.x account matcher contained a hardcoded rule for apartment numbers 142–146, specific to the original author's rental properties, which would silently mis-categorize anyone else's income. The generic matching — exact, prefix, per-word, then a synonym table — is kept, and the hardcoded rule is gone. The matcher now also refuses to guess below 55% confidence, listing the real account names instead of quietly picking the first one.

## Troubleshooting

**"Wave rejected the access token"** — tokens expire. Generate a fresh one in the developer portal and update `WAVE_ACCESS_TOKEN`.

**"No business selected"** — call `wave_list_businesses`, then `wave_set_default_business`, or set `WAVE_BUSINESS_ID`.

**Server not appearing in the client** — both paths in the config must be absolute, and `command` should point at the venv's Python so dependencies resolve.

**"Wave rejected the query as invalid"** — that indicates a bug in this server rather than in your input. Please open an issue.

## License

MIT — see [LICENSE](LICENSE).

Forked from [vinnividivicci/wave_mcp](https://github.com/vinnividivicci/wave_mcp).
