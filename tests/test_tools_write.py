"""The write tools (Keith's tests, #655), against the fake API and its seeded
chart of accounts."""

import pytest

from sbplive import tools
from sbplive.client import ApiClient, ApiError
from sbplive.server import build_registry

LINE = {"description": "work", "quantity": 1, "rate": "100.00"}


def _customer_invoice(api):
    cust = tools.create_customer(api, {"name": "Pay Me Co"})
    inv = tools.create_invoice(
        api, {"customer_id": cust["id"], "date": "2026-09-10", "lines": [LINE]}
    )
    return cust, inv


def _bill(api, acct):
    vendor = tools.create_vendor(api, {"name": "Lumber Yard"})
    bill = tools.create_bill(
        api,
        {
            "vendor_id": vendor["id"],
            "date": "2026-09-10",
            "lines": [{"description": "wood", "amount": 100, "account_id": acct}],
        },
    )
    return vendor, bill


def test_create_master_records_and_find_them(api, seed_accounts):
    cust = tools.create_customer(api, {"name": "Made Customer"})
    vend = tools.create_vendor(api, {"name": "Made Vendor"})
    item = tools.create_item(api, {"name": "Made Item", "item_type": "service"})
    for kind, rec in (("customers", cust), ("vendors", vend), ("items", item)):
        assert tools.get_record(api, kind, rec["id"])["name"] == rec["name"]


def test_create_invoice_bill_expense_journal(api, seed_accounts):
    _, inv = _customer_invoice(api)
    assert tools.get_record(api, "invoices", inv["id"])["id"] == inv["id"]
    _, bill = _bill(api, seed_accounts["6000"].id)
    assert tools.get_record(api, "bills", bill["id"])["id"] == bill["id"]
    exp = tools.create_expense(
        api,
        {
            "date": "2026-09-11",
            "amount": "25.00",
            "expense_account_id": seed_accounts["6000"].id,
            "paid_from_account_id": seed_accounts["1000"].id,
        },
    )
    assert exp["id"]
    lines = [
        {"account_id": seed_accounts["6000"].id, "debit": "50.00", "credit": "0"},
        {"account_id": seed_accounts["1000"].id, "debit": "0", "credit": "50.00"},
    ]
    je = tools.create_journal_entry(
        api, {"date": "2026-09-12", "description": "Adjust", "lines": lines}
    )
    assert je["id"]
    lines[1]["credit"] = "40.00"
    with pytest.raises(ApiError) as exc:
        tools.create_journal_entry(
            api, {"date": "2026-09-12", "description": "Off", "lines": lines}
        )
    assert exc.value.status == 400 and exc.value.detail


def test_unknown_field_passes_the_422_on(api, seed_accounts):
    with pytest.raises(ApiError) as exc:
        tools.create_customer(api, {"name": "X", "bogus_field": 1})
    assert exc.value.status == 422 and "bogus_field" in exc.value.detail


def _pay_body(cust, inv, amount="40.00"):
    return {
        "customer_id": cust["id"],
        "date": "2026-09-12",
        "amount": amount,
        "allocations": [{"invoice_id": inv["id"], "amount": amount}],
    }


def test_record_payment_books_once_per_key(api, seed_accounts):
    cust, inv = _customer_invoice(api)
    first = tools.record_payment(api, _pay_body(cust, inv), "k-001")
    again = tools.record_payment(api, _pay_body(cust, inv), "k-001")
    assert again["id"] == first["id"]
    assert len(tools.list_records(api, "payments")) == 1
    with pytest.raises(ApiError) as exc:
        tools.record_payment(api, _pay_body(cust, inv, "50.00"), "k-001")
    assert exc.value.status == 409
    assert "different request" in exc.value.detail


def test_record_bill_payment_books_once_per_key(api, seed_accounts):
    vendor, bill = _bill(api, seed_accounts["6000"].id)
    body = {
        "vendor_id": vendor["id"],
        "date": "2026-09-12",
        "amount": 40,
        "allocations": [{"bill_id": bill["id"], "amount": 40}],
    }
    first = tools.record_bill_payment(api, body, "bk-001")
    again = tools.record_bill_payment(api, body, "bk-001")
    assert again["id"] == first["id"]
    assert len(tools.list_records(api, "bill_payments")) == 1
    voided = tools.void_record(api, "bill_payments", first["id"])
    assert voided["is_voided"] is True


@pytest.mark.parametrize("key", ["", "has space", "x" * 129, "bad/slash", None])
def test_bad_key_raises_before_any_request(key):
    calls = []
    spy = ApiClient(lambda *a: calls.append(a) or (201, {}))
    with pytest.raises(ValueError, match="key must be"):
        tools.record_payment(spy, {}, key)
    with pytest.raises(ValueError, match="key must be"):
        tools.record_bill_payment(spy, {}, key)
    assert calls == []


def test_void_record(api, seed_accounts):
    _, inv = _customer_invoice(api)
    tools.void_record(api, "invoices", inv["id"])
    assert tools.get_record(api, "invoices", inv["id"])["status"] == "void"
    with pytest.raises(ValueError, match="Kinds: invoices"):
        tools.void_record(api, "nope", 1)


def test_void_payment_passes_fee_refunded(api):
    seen = []
    spy = ApiClient(lambda m, p, params, b, h: seen.append((p, params)) or (200, {}))
    tools.void_record(spy, "payments", 7, fee_refunded=True)
    tools.void_record(spy, "payments", 8)
    assert seen == [
        ("/api/payments/7/void", {"fee_refunded": "true"}),
        ("/api/payments/8/void", {}),
    ]


def test_writes_are_audited_as_the_token(api, seed_accounts):
    made = tools.create_customer(api, {"name": "Audited Co"})
    rows = tools.api_get(api, "/api/audit", {"table_name": "customers"})
    mine = [r for r in rows if r["record_id"] == made["id"]]
    assert mine and mine[0]["username"] == "token:mcp-test"


def test_unknown_outcome_sentences():
    def lost(*a):
        raise ApiError(0, "SlowBooks Pro did not answer")

    gone = ApiClient(lost)
    with pytest.raises(ValueError, match="same key and the same data"):
        tools.record_payment(gone, {}, "k-1")
    with pytest.raises(ValueError, match="same key and the same data"):
        tools.record_bill_payment(gone, {}, "k-1")
    with pytest.raises(ValueError, match="list_records or search before retrying"):
        tools.create_invoice(gone, {})
    with pytest.raises(ValueError, match="before retrying"):
        tools.void_record(gone, "invoices", 1)


def test_server_registers_the_tools_with_the_right_hints(api):
    listed = {t["name"]: t for t in build_registry(api).definitions()}
    assert len(listed) == 21  # Keith's eighteen and the three general tools
    hints = {n: t["annotations"] for n, t in listed.items()}
    writes = [n for n, a in hints.items() if not a["readOnlyHint"]]
    assert len(writes) == 11
    assert hints["void_record"]["destructiveHint"] is True
    idem = {n for n in writes if hints[n]["idempotentHint"]}
    assert idem == {"record_payment", "record_bill_payment"}
    assert sorted(n for n in writes if hints[n]["destructiveHint"]) == [
        "call_operation",
        "void_record",
    ]
    assert all(a["openWorldHint"] is False for a in hints.values())
    assert "client" not in listed["record_payment"]["inputSchema"]["properties"]
