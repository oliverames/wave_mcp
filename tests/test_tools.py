"""Tests for the Wave MCP server.

Wave's API is not exercised here. The transport is stubbed so the tests can
cover what stubbing cannot hide: input validation, pagination, error mapping,
and every markdown rendering path.

Run with:
    python -m pytest tests/ -v
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wave_mcp.client import WaveClient  # noqa: E402
from wave_mcp.errors import (  # noqa: E402
    WaveAuthError,
    WaveConfigError,
    WaveError,
    WaveGraphQLError,
    WaveMutationError,
)
from wave_mcp.runtime import reset_client, set_client  # noqa: E402
from wave_mcp.tools import common  # noqa: E402


class StubClient(WaveClient):
    """A WaveClient whose transport returns canned payloads."""

    def __init__(self, responses: Optional[List[Dict[str, Any]]] = None):
        super().__init__("stub-token", business_id="biz-1")
        self.responses = list(responses or [])
        self.calls: List[Dict[str, Any]] = []

    async def execute(self, query, variables=None, *, operation_name=None):
        self.calls.append({"query": query, "variables": variables or {}})
        if not self.responses:
            return {}
        return self.responses.pop(0)


def money(value: str, code: str = "USD") -> Dict[str, Any]:
    return {"raw": int(float(value) * 100), "value": value, "currency": {"code": code, "symbol": "$"}}


def install(responses: List[Dict[str, Any]]) -> StubClient:
    client = StubClient(responses)
    set_client(client)
    return client


@pytest.fixture(autouse=True)
def _reset():
    yield
    reset_client()


# ------------------------------------------------------------------- input prep


class TestNormalizers:
    def test_line_item_requires_product(self):
        with pytest.raises(WaveError, match="missing productId"):
            common.normalize_line_items([{"quantity": 1}], context="Invoice")

    def test_line_item_coerces_numbers_to_strings(self):
        [item] = common.normalize_line_items(
            [{"productId": "p1", "quantity": 2, "unitPrice": 19.99}], context="Invoice"
        )
        assert item == {"productId": "p1", "quantity": "2", "unitPrice": "19.99"}

    def test_tax_accepts_bare_id_or_object(self):
        [item] = common.normalize_line_items(
            [{"productId": "p1", "taxes": ["tax-1", {"salesTaxId": "tax-2", "amount": 3}]}],
            context="Invoice",
        )
        assert item["taxes"] == [
            {"salesTaxId": "tax-1"},
            {"salesTaxId": "tax-2", "amount": "3"},
        ]

    def test_estimate_taxes_drop_amount(self):
        items = common.normalize_line_items(
            [{"productId": "p1", "taxes": [{"salesTaxId": "t1", "amount": 5}]}],
            context="Estimate",
        )
        assert common.strip_estimate_item_taxes(items)[0]["taxes"] == [{"salesTaxId": "t1"}]

    def test_discount_type_is_inferred(self):
        assert common.normalize_discounts([{"percentage": 10}], context="Invoice") == [
            {"discountType": "PERCENTAGE", "percentage": "10"}
        ]
        assert common.normalize_discounts([{"amount": 25}], context="Invoice") == [
            {"discountType": "FIXED", "amount": "25"}
        ]

    def test_fixed_discount_without_amount_is_rejected(self):
        with pytest.raises(WaveError, match="FIXED but has no amount"):
            common.normalize_discounts(
                [{"discountType": "FIXED"}], context="Invoice"
            )

    def test_recipients_accept_string_or_list(self):
        assert common.normalize_recipients("a@b.com", context="t") == ["a@b.com"]
        assert common.normalize_recipients(["a@b.com", "c@d.com"], context="t") == [
            "a@b.com",
            "c@d.com",
        ]

    def test_empty_recipients_rejected(self):
        with pytest.raises(WaveError, match="at least one recipient"):
            common.normalize_recipients([], context="t")

    def test_empty_address_becomes_none(self):
        assert common.optional_address() is None
        assert common.optional_address(city="Burlington") == {"city": "Burlington"}


# ------------------------------------------------------------------ client core


class TestClient:
    def test_missing_token_is_actionable(self, monkeypatch):
        monkeypatch.delenv("WAVE_ACCESS_TOKEN", raising=False)
        with pytest.raises(WaveConfigError, match="WAVE_ACCESS_TOKEN"):
            WaveClient.from_env()

    def test_business_id_required_when_unset(self):
        client = WaveClient("t")
        with pytest.raises(WaveConfigError, match="No business selected"):
            client.require_business_id()

    def test_explicit_business_id_wins(self):
        client = WaveClient("t", business_id="default")
        assert client.require_business_id("explicit") == "explicit"
        assert client.require_business_id() == "default"

    def test_none_values_are_stripped_not_sent_as_null(self):
        from wave_mcp.client import _strip_none

        assert _strip_none({"a": 1, "b": None, "c": {"d": None, "e": 2}}) == {
            "a": 1,
            "c": {"e": 2},
        }

    def test_unauthenticated_maps_to_auth_error(self):
        body = {
            "errors": [
                {"message": "expired", "extensions": {"code": "UNAUTHENTICATED"}}
            ]
        }
        with pytest.raises(WaveAuthError, match="rejected the access token"):
            WaveClient._raise_for_graphql_errors(body)

    def test_validation_failure_is_flagged_as_server_bug(self):
        body = {
            "errors": [
                {
                    "message": 'Cannot query field "x"',
                    "extensions": {"code": "GRAPHQL_VALIDATION_FAILED"},
                }
            ]
        }
        with pytest.raises(WaveGraphQLError, match="bug in the MCP server"):
            WaveClient._raise_for_graphql_errors(body)

    def test_failed_mutation_raises_with_field_detail(self):
        client = StubClient(
            [
                {
                    "invoiceCreate": {
                        "didSucceed": False,
                        "inputErrors": [
                            {
                                "path": ["customerId"],
                                "message": "not found",
                                "code": "NOT_FOUND",
                            }
                        ],
                    }
                }
            ]
        )
        with pytest.raises(WaveMutationError) as exc:
            asyncio.run(client.mutate("mutation {}", {}, root_field="invoiceCreate"))
        assert "customerId: not found [NOT_FOUND]" in str(exc.value)

    def test_pagination_reports_next_page(self):
        client = StubClient(
            [
                {
                    "business": {
                        "customers": {
                            "pageInfo": {"currentPage": 1, "totalPages": 3, "totalCount": 120},
                            "edges": [{"node": {"id": "c1", "name": "Acme"}}],
                        }
                    }
                }
            ]
        )
        result = asyncio.run(
            client.paginate("q", {}, path=("business", "customers"), page=1, page_size=50)
        )
        assert result["has_more"] is True
        assert result["next_page"] == 2
        assert result["total_count"] == 120

    def test_fetch_all_walks_every_page(self):
        pages = [
            {
                "business": {
                    "customers": {
                        "pageInfo": {"currentPage": n, "totalPages": 3, "totalCount": 3},
                        "edges": [{"node": {"id": f"c{n}"}}],
                    }
                }
            }
            for n in (1, 2, 3)
        ]
        client = StubClient(pages)
        result = asyncio.run(
            client.paginate("q", {}, path=("business", "customers"), fetch_all=True)
        )
        assert result["count"] == 3
        assert result["has_more"] is False
        assert [c["id"] for c in result["items"]] == ["c1", "c2", "c3"]

    def test_page_size_is_clamped_to_wave_maximum(self):
        client = StubClient(
            [
                {
                    "business": {
                        "customers": {
                            "pageInfo": {"currentPage": 1, "totalPages": 1},
                            "edges": [],
                        }
                    }
                }
            ]
        )
        result = asyncio.run(
            client.paginate("q", {}, path=("business", "customers"), page_size=9999)
        )
        assert result["page_size"] == 200
        assert client.calls[0]["variables"]["pageSize"] == 200

    def test_null_nodes_are_skipped(self):
        client = StubClient(
            [
                {
                    "business": {
                        "customers": {
                            "pageInfo": {"currentPage": 1, "totalPages": 1},
                            "edges": [{"node": None}, {"node": {"id": "c1"}}],
                        }
                    }
                }
            ]
        )
        result = asyncio.run(client.paginate("q", {}, path=("business", "customers")))
        assert result["items"] == [{"id": "c1"}]


# ----------------------------------------------------------------- read tools


class TestReadTools:
    def test_list_businesses_renders_a_table(self):
        install(
            [
                {
                    "businesses": {
                        "pageInfo": {"currentPage": 1, "totalPages": 1, "totalCount": 1},
                        "edges": [
                            {
                                "node": {
                                    "id": "biz-1",
                                    "name": "Acme Inc",
                                    "isPersonal": False,
                                    "isArchived": False,
                                    "currency": {"code": "USD"},
                                    "type": {"name": "Service Provider"},
                                }
                            }
                        ],
                    }
                }
            ]
        )
        from wave_mcp.tools.businesses import wave_list_businesses

        out = asyncio.run(wave_list_businesses())
        assert "Acme Inc" in out and "biz-1" in out and "| Name |" in out

    def test_json_format_returns_parseable_json(self):
        install(
            [
                {
                    "businesses": {
                        "pageInfo": {"currentPage": 1, "totalPages": 1, "totalCount": 1},
                        "edges": [{"node": {"id": "biz-1", "name": "Acme Inc"}}],
                    }
                }
            ]
        )
        from wave_mcp.tools.businesses import wave_list_businesses

        payload = json.loads(asyncio.run(wave_list_businesses(response_format="json")))
        assert payload["items"][0]["name"] == "Acme Inc"
        assert payload["total_count"] == 1

    def test_invoice_detail_renders_items_and_payments(self):
        install(
            [
                {
                    "business": {
                        "invoice": {
                            "id": "inv-1",
                            "status": "PARTIAL",
                            "invoiceNumber": "INV-42",
                            "title": "Invoice",
                            "invoiceDate": "2026-07-01",
                            "dueDate": "2026-07-31",
                            "currency": {"code": "USD", "symbol": "$"},
                            "customer": {"id": "c1", "name": "Acme", "email": "a@b.com"},
                            "subtotal": money("100.00"),
                            "taxTotal": money("13.00"),
                            "discountTotal": money("0.00"),
                            "total": money("113.00"),
                            "amountPaid": money("50.00"),
                            "amountDue": money("63.00"),
                            "items": [
                                {
                                    "id": "li-1",
                                    "description": "Consulting",
                                    "quantity": "1",
                                    "unitPrice": "100.00",
                                    "product": {"id": "p1", "name": "Consulting"},
                                    "total": money("100.00"),
                                    "taxes": [{"salesTax": {"abbreviation": "HST"}}],
                                }
                            ],
                            "payments": [
                                {
                                    "id": "pay-1",
                                    "paymentDate": "2026-07-15",
                                    "amount": "50.00",
                                    "paymentMethod": "CHEQUE",
                                    "account": {"name": "Checking"},
                                }
                            ],
                        }
                    }
                }
            ]
        )
        from wave_mcp.tools.invoices import wave_get_invoice

        out = asyncio.run(wave_get_invoice("inv-1"))
        assert "INV-42" in out
        assert "$63.00 USD" in out          # amount due formatted from Money
        assert "Consulting" in out           # line item table
        assert "**Payments**" in out         # payment table
        assert "HST" in out

    def test_missing_record_returns_a_helpful_message(self):
        install([{"business": {"invoice": None}}])
        from wave_mcp.tools.invoices import wave_get_invoice

        out = asyncio.run(wave_get_invoice("nope"))
        assert "No invoice found" in out

    def test_account_subtypes_filter_locally(self):
        install(
            [
                {
                    "accountSubtypes": [
                        {
                            "name": "Expense",
                            "value": "EXPENSE",
                            "systemCreated": False,
                            "archivable": True,
                            "type": {"value": "EXPENSE"},
                        },
                        {
                            "name": "Retained Earnings",
                            "value": "RETAINED_EARNINGS",
                            "systemCreated": True,
                            "archivable": False,
                            "type": {"value": "EQUITY"},
                        },
                    ]
                }
            ]
        )
        from wave_mcp.tools.reference import wave_list_account_subtypes

        out = asyncio.run(wave_list_account_subtypes(account_type="EXPENSE"))
        assert "EXPENSE" in out and "RETAINED_EARNINGS" not in out


# ---------------------------------------------------------------- write tools


class TestWriteTools:
    def test_create_invoice_sends_normalized_input(self):
        client = install(
            [
                {
                    "invoiceCreate": {
                        "didSucceed": True,
                        "inputErrors": None,
                        "invoice": {
                            "id": "inv-1",
                            "invoiceNumber": "INV-1",
                            "status": "DRAFT",
                            "customer": {"name": "Acme"},
                            "total": money("113.00"),
                            "amountDue": money("113.00"),
                            "viewUrl": "https://wave/inv-1",
                        },
                    }
                }
            ]
        )
        from wave_mcp.tools.invoices import wave_create_invoice

        out = asyncio.run(
            wave_create_invoice(
                customer_id="c1",
                items=[{"productId": "p1", "quantity": 1, "unitPrice": 100}],
            )
        )
        sent = client.calls[0]["variables"]["input"]
        assert sent["businessId"] == "biz-1"
        assert sent["items"] == [{"productId": "p1", "quantity": "1", "unitPrice": "100"}]
        assert "still a draft" in out
        assert "wave_approve_invoice" in out

    def test_transaction_must_balance(self):
        install([])
        from wave_mcp.tools.transactions import wave_create_money_transaction

        with pytest.raises(WaveError, match="does not balance"):
            asyncio.run(
                wave_create_money_transaction(
                    anchor_account_id="bank",
                    direction="WITHDRAWAL",
                    amount="100.00",
                    date="2026-07-01",
                    description="Split",
                    line_items=[
                        {"accountId": "a1", "amount": "60.00"},
                        {"accountId": "a2", "amount": "30.00"},
                    ],
                )
            )

    def test_balanced_split_transaction_is_accepted(self):
        client = install(
            [
                {
                    "moneyTransactionCreate": {
                        "didSucceed": True,
                        "inputErrors": None,
                        "transaction": {"id": "txn-1"},
                    }
                }
            ]
        )
        from wave_mcp.tools.transactions import wave_create_money_transaction

        out = asyncio.run(
            wave_create_money_transaction(
                anchor_account_id="bank",
                direction="withdrawal",
                amount="100.00",
                date="2026-07-01",
                description="Split",
                line_items=[
                    {"accountId": "a1", "amount": "60.00"},
                    {"accountId": "a2", "amount": "40.00"},
                ],
            )
        )
        sent = client.calls[0]["variables"]["input"]
        assert sent["anchor"]["direction"] == "WITHDRAWAL"   # lowercase was normalized
        assert sent["externalId"]                             # generated when omitted
        assert len(sent["lineItems"]) == 2
        assert "txn-1" in out

    def test_supplied_external_id_is_preserved_for_idempotency(self):
        client = install(
            [
                {
                    "moneyTransactionCreate": {
                        "didSucceed": True,
                        "transaction": {"id": "txn-1"},
                    }
                }
            ]
        )
        from wave_mcp.tools.transactions import wave_create_money_transaction

        asyncio.run(
            wave_create_money_transaction(
                anchor_account_id="bank",
                direction="DEPOSIT",
                amount="10.00",
                date="2026-07-01",
                description="x",
                external_id="my-key-1",
                line_items=[{"accountId": "a1", "amount": "10.00"}],
            )
        )
        assert client.calls[0]["variables"]["input"]["externalId"] == "my-key-1"

    def test_patch_with_no_fields_is_refused_without_calling_wave(self):
        client = install([])
        from wave_mcp.tools.customers import wave_patch_customer

        out = asyncio.run(wave_patch_customer("c1"))
        assert "Nothing to update" in out
        assert client.calls == []

    def test_send_invoice_normalizes_a_single_recipient(self):
        client = install(
            [
                {
                    "invoiceSend": {
                        "didSucceed": True,
                        "invoice": {
                            "id": "inv-1",
                            "invoiceNumber": "INV-1",
                            "status": "SENT",
                            "lastSentAt": "2026-07-27T10:00:00Z",
                        },
                    }
                }
            ]
        )
        from wave_mcp.tools.invoices import wave_send_invoice

        out = asyncio.run(wave_send_invoice("inv-1", to="a@b.com"))
        assert client.calls[0]["variables"]["input"]["to"] == ["a@b.com"]
        assert "a@b.com" in out

    def test_set_default_business_validates_before_switching(self):
        client = install([{"business": None}])
        from wave_mcp.tools.businesses import wave_set_default_business

        out = asyncio.run(wave_set_default_business("bad-id"))
        assert "unchanged" in out
        assert client.business_id == "biz-1"


# ------------------------------------------------------------ account matching


class TestAccountMatching:
    ACCOUNTS = [
        {"id": "a1", "name": "Meals and Entertainment"},
        {"id": "a2", "name": "Motor Vehicle - Fuel"},
        {"id": "a3", "name": "Office Supplies"},
    ]

    def test_synonym_maps_everyday_word_to_account(self):
        from wave_mcp.tools.legacy import EXPENSE_SYNONYMS, _match_account

        account, score, _ = _match_account(
            "food", self.ACCOUNTS, EXPENSE_SYNONYMS, kind="expense"
        )
        assert account["id"] == "a1"
        assert score >= 0.8

    def test_substring_beats_fuzzy(self):
        from wave_mcp.tools.legacy import EXPENSE_SYNONYMS, _match_account

        account, _, _ = _match_account(
            "office supplies", self.ACCOUNTS, EXPENSE_SYNONYMS, kind="expense"
        )
        assert account["id"] == "a3"

    def test_weak_match_refuses_rather_than_guessing(self):
        from wave_mcp.tools.legacy import EXPENSE_SYNONYMS, _match_account

        with pytest.raises(WaveError) as exc:
            _match_account(
                "cryptocurrency mining rig",
                self.ACCOUNTS,
                EXPENSE_SYNONYMS,
                kind="expense",
            )
        # The error must list the real options so the caller can choose.
        assert "Office Supplies" in str(exc.value)
        assert "wave_create_money_transaction" in str(exc.value)

    def test_no_accounts_is_reported_clearly(self):
        from wave_mcp.tools.legacy import EXPENSE_SYNONYMS, _match_account

        with pytest.raises(WaveError, match="no active expense accounts"):
            _match_account("food", [], EXPENSE_SYNONYMS, kind="expense")


# ---------------------------------------------------------------- registration


class TestRegistration:
    def test_every_tool_is_registered_and_described(self):
        from wave_mcp.server import build_server

        tools = asyncio.run(build_server().list_tools())
        names = {t.name for t in tools}

        assert len(tools) >= 70
        # Spot-check one tool per domain.
        for expected in [
            "wave_list_businesses",
            "wave_list_accounts",
            "wave_create_customer",
            "wave_create_invoice",
            "wave_send_invoice",
            "wave_create_estimate",
            "wave_convert_estimate_to_invoice",
            "wave_create_invoice_payment",
            "wave_create_money_transaction",
            "wave_list_sales_taxes",
            "wave_list_vendors",
            "wave_get_user",
        ]:
            assert expected in names, f"{expected} is not registered"

        for tool in tools:
            assert tool.name.startswith("wave_"), f"{tool.name} lacks the wave_ prefix"
            assert tool.description, f"{tool.name} has no description"
            assert tool.annotations is not None, f"{tool.name} has no annotations"

    def test_destructive_tools_are_annotated_as_such(self):
        from wave_mcp.server import build_server

        tools = {t.name: t for t in asyncio.run(build_server().list_tools())}
        for name in [
            "wave_delete_invoice",
            "wave_delete_customer",
            "wave_delete_estimate",
            "wave_archive_account",
        ]:
            assert tools[name].annotations.destructiveHint is True, name

    def test_read_tools_are_annotated_read_only(self):
        from wave_mcp.server import build_server

        tools = {t.name: t for t in asyncio.run(build_server().list_tools())}
        for name in ["wave_list_invoices", "wave_get_invoice", "wave_list_accounts"]:
            assert tools[name].annotations.readOnlyHint is True, name
