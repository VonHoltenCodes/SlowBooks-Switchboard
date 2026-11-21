"""Ask before a bare create: ask once, then allow.

A create of one of the main records that carries only what the API requires
comes back as a normal result marked ask_first, with the empty fields worth
asking about, and nothing is written; the same call with asked_user writes
it; a record that already fills half its fields writes at once. Every
create's answer carries left_blank. Updates, payments and voids are not
touched, and the gate lives here, not in the API."""

import json

import pytest
from test_protocol import INIT, INITIALIZED, _assert_modern_result, _modern, _req

from sbplive import operations, tools
from sbplive.protocol import Session
from sbplive.registry import DESTRUCTIVE, WRITE
from sbplive.server import INSTRUCTIONS, build_registry

GATED_TOOLS = {
    "create_customer": "/api/customers",
    "create_vendor": "/api/vendors",
    "create_item": "/api/items",
    "create_invoice": "/api/invoices",
    "create_bill": "/api/bills",
    "create_expense": "/api/expenses",
}


def _count(books, route):
    return len(books.records[route])


def test_the_rule_list_and_the_enough_rule():
    assert set(tools.ASK_FIRST) == {
        "/api/customers",
        "/api/vendors",
        "/api/items",
        "/api/employees",
        "/api/accounts",
        "/api/invoices",
        "/api/bills",
        "/api/estimates",
        "/api/purchase-orders",
        "/api/expenses",
    }
    needs = {r: rule.need for r, rule in tools.ASK_FIRST.items()}
    assert needs == {
        "/api/customers": 3,
        "/api/vendors": 3,
        "/api/items": 2,
        "/api/employees": 4,
        "/api/accounts": 1,
        "/api/invoices": 1,
        "/api/bills": 1,
        "/api/estimates": 1,
        "/api/purchase-orders": 1,
        "/api/expenses": 2,
    }


def test_a_bare_customer_is_turned_back_and_nothing_is_written(api, books):
    before = _count(books, "/api/customers")
    out = tools.create_customer(api, {"name": "Bare Name Co"})
    assert out["created"] is False and out["ask_first"] is True
    assert out["record"] == "customer"
    assert [e["label"] for e in out["empty"]] == [
        "Company",
        "Email",
        "Phone",
        "Billing address",
        "Terms",
        "Sales Tax (taxable or exempt)",
    ]
    assert {"label": "Phone", "field": "phone or mobile"} in out["empty"]
    assert "Do not make any up" in out["next"]
    assert "create_customer again" in out["next"]
    assert "asked_user: true" in out["next"]
    assert _count(books, "/api/customers") == before
    assert not [c for c in books.calls if c[0] == "POST"]


def test_the_second_call_with_the_flag_writes_and_says_what_is_blank(api, books):
    tools.create_customer(api, {"name": "Second Call Co"})
    made = tools.create_customer(
        api, {"name": "Second Call Co", "email": "ap@example.test"}, asked_user=True
    )
    assert made["id"] and made["name"] == "Second Call Co"
    # Left out and still empty, apart from what the app filled with its
    # default: those are named with the value it used.
    assert made["left_blank"] == ["Company", "Phone", "Billing address"]
    assert made["defaulted"] == [
        {"label": "Terms", "value": "Net 30"},
        {"label": "Sales Tax (taxable or exempt)", "value": "taxable"},
    ]
    names = [c["name"] for c in books.records["/api/customers"].values()]
    assert names.count("Second Call Co") == 1


def test_a_full_record_writes_the_first_time(api, books):
    full = {
        "name": "Full Record Co",
        "company": "Full Record Company LLC",
        "email": "books@example.test",
        "phone": "555-0100",
        "bill_address1": "1 Main St",
        "terms": "Net 15",
        "is_taxable": False,
    }
    made = tools.create_customer(api, full)
    assert made["id"] and made["left_blank"] == [] and made["defaulted"] == []


def test_half_the_fields_is_enough_and_one_less_asks(api, books):
    three = {"name": "Three Co", "email": "a@example.test", "phone": "1", "terms": "x"}
    made = tools.create_customer(api, three)
    assert made["left_blank"] == ["Company", "Billing address"]
    assert made["defaulted"] == [
        {"label": "Sales Tax (taxable or exempt)", "value": "taxable"}
    ]
    two = {"name": "Two Co", "email": "a@example.test", "phone": "1"}
    assert tools.create_customer(api, two)["ask_first"] is True
    # blank text is not a value
    blank = {"name": "Blank Co", "email": "  ", "phone": "", "terms": None}
    assert tools.create_customer(api, blank)["ask_first"] is True


def test_vendor_and_item(api, books):
    assert tools.create_vendor(api, {"name": "V"})["record"] == "vendor"
    item = {"name": "Consulting", "item_type": "service", "rate": "150"}
    asked = tools.create_item(api, item)
    assert asked["ask_first"] is True
    assert [e["label"] for e in asked["empty"]] == [
        "Description",
        "Cost",
        "Income Account",
        "Expense or COGS Account",
    ]
    made = tools.create_item(api, {**item, "description": "Hourly consulting"})
    # No account given: what the app will post to, read from the chart.
    assert made["left_blank"] == ["Cost"]
    assert made["defaulted"] == [
        {
            "label": "Income Account",
            "value": "sales of this item post to 4000 - Sales (SlowBooks Pro's "
            "default income account) unless you set one",
        },
        {
            "label": "Expense or COGS Account",
            "value": "a bill or expense line for it posts to the vendor's default "
            "expense account; with none, SlowBooks Pro refuses the line until an "
            "account is chosen",
        },
    ]


def test_documents_ask_when_their_lines_say_nothing(api, books, seed_accounts):
    cust = tools.create_customer(api, {"name": "Doc Co"}, asked_user=True)
    bare = {"customer_id": cust["id"], "date": "2026-11-19", "lines": [{"amount": 5}]}
    before = _count(books, "/api/invoices")
    asked = tools.create_invoice(api, bare)
    assert asked["ask_first"] is True and asked["record"] == "invoice"
    assert [e["label"] for e in asked["empty"]] == [
        "Terms or Due Date",
        "PO #",
        "An item or description on each line",
    ]
    assert _count(books, "/api/invoices") == before
    described = dict(bare, lines=[{"description": "Fence repair", "amount": 5}])
    made = tools.create_invoice(api, described)
    assert made["left_blank"] == ["PO #"]
    assert made["defaulted"] == [
        {"label": "Terms or Due Date", "value": "terms Net 30, due date 2026-12-19"},
        {"label": "Sales tax rate", "value": "6.25% (the company's default)"},
    ]
    # one line without either is not "each line"
    mixed = dict(bare, lines=[{"description": "a", "amount": 1}, {"amount": 2}])
    assert tools.create_invoice(api, mixed)["ask_first"] is True
    vendor = tools.create_vendor(api, {"name": "Doc Vendor"}, asked_user=True)
    bill = {"vendor_id": vendor["id"], "date": "2026-11-19", "lines": [{"amount": 9}]}
    assert tools.create_bill(api, bill)["ask_first"] is True
    made = tools.create_bill(api, dict(bill, bill_number="B-7"))
    assert made["left_blank"] == ["An item or description on each line"]
    assert made["defaulted"] == [
        {"label": "Terms or Due Date", "value": "terms Net 30, due date 2026-12-19"}
    ]
    # a bill number the app made up is a default too
    made = tools.create_bill(api, dict(bill, terms="Net 15"), asked_user=True)
    assert {"label": "Bill Number", "value": "AUTO-1"} in made["defaulted"]
    # the app's answer also names the payment term by id: only the words show
    answer = {"terms": "Net 30", "due_date": "2026-12-19", "payment_term_id": 3}
    assert tools._defaulted_value("Terms or Due Date", tools._TERMS[1], answer) == (
        "terms Net 30, due date 2026-12-19"
    )
    assert tools._defaulted_value("Terms", ("payment_term_id",), answer) == "3"
    assert "tax_rate_source" not in made  # not a sales document


def test_a_call_missing_what_the_api_requires_goes_to_the_api(api, books):
    # The API's own sentence says what is missing; asking first would only
    # make the person answer questions for a record that cannot be saved.
    reg = build_registry(api)
    out = reg.call("create_customer", {"data": {"company": "No Name Co"}})
    assert out["isError"] is True and "name" in out["content"][0]["text"]
    out = reg.call("create_customer", {"data": {"name": "X", "bogus_field": 1}})
    assert out["isError"] is True
    assert "bogus_field" in out["content"][0]["text"]


def test_post_operation_asks_on_the_create_routes_only(api, books):
    reg = build_registry(api)
    before = _count(books, "/api/customers")
    out = reg.call(
        "post_operation", {"path": "/api/customers", "body": {"name": "Op Co"}}
    )
    assert out["isError"] is False
    held = json.loads(out["content"][0]["text"])
    assert held["ask_first"] is True and "post_operation again" in held["next"]
    assert _count(books, "/api/customers") == before
    out = reg.call(
        "post_operation",
        {"path": "/api/customers", "body": {"name": "Op Co"}, "asked_user": True},
    )
    made = json.loads(out["content"][0]["text"])
    assert made["name"] == "Op Co" and "Email" in made["left_blank"]
    assert _count(books, "/api/customers") == before + 1
    # A route that is not a create of a main record is sent as before.
    vendor = tools.create_vendor(api, {"name": "Pay Co"}, asked_user=True)
    pay = operations.post_operation(
        api,
        "/api/bill-payments",
        body={"vendor_id": vendor["id"], "date": "2026-11-19", "amount": "1.00"},
        idempotency_key="ask-first-0001",
    )
    assert "left_blank" not in pay and "ask_first" not in pay


def test_post_operation_still_checks_its_parameters_when_it_asks(api):
    with pytest.raises(ValueError, match="has no parameter"):
        operations.post_operation(
            api, "/api/customers", params={"colour": 1}, body={"name": "P"}
        )


def test_every_affected_tool_carries_the_flag_and_keeps_its_annotations(api):
    listed = {t["name"]: t for t in build_registry(api).definitions()}
    for name in [*GATED_TOOLS, "post_operation"]:
        prop = listed[name]["inputSchema"]["properties"]["asked_user"]
        assert prop["type"] == "boolean" and prop["default"] is False
        assert "asked the user" in prop["description"]
        assert "asked_user" not in listed[name]["inputSchema"].get("required", [])
        assert "ask_first" in listed[name]["description"]
        assert "left_blank" in listed[name]["description"]
    for name in GATED_TOOLS:
        assert listed[name]["annotations"] == {"title": listed[name]["title"], **WRITE}
    assert listed["post_operation"]["annotations"] == {
        "title": "Create or act through the API",
        **DESTRUCTIVE,
    }
    for name in ("create_journal_entry", "update_operation"):
        assert "asked_user" not in listed[name]["inputSchema"]["properties"]
    assert "Never invent a value for a record" in INSTRUCTIONS
    assert "left_blank" in INSTRUCTIONS


@pytest.mark.parametrize("era", ["2025-11-25", "2026-07-28"])
def test_both_eras(api, books, era):
    session = Session(build_registry(api), INSTRUCTIONS)
    call = {"name": "create_vendor", "arguments": {"data": {"name": "Era Co"}}}
    flagged = {
        "name": "create_vendor",
        "arguments": {"data": {"name": "Era Co"}, "asked_user": True},
    }
    if era == "2025-11-25":
        assert "result" in session.handle_message(INIT)
        session.handle_message(INITIALIZED)
        first = session.handle_message(_req(2, "tools/call", call))["result"]
        second = session.handle_message(_req(3, "tools/call", flagged))["result"]
    else:
        first = _assert_modern_result(
            session.handle_message(_modern(2, "tools/call", call))
        )
        second = _assert_modern_result(
            session.handle_message(_modern(3, "tools/call", flagged))
        )
    assert first["isError"] is False
    assert json.loads(first["content"][0]["text"])["ask_first"] is True
    assert second["isError"] is False
    made = json.loads(second["content"][0]["text"])
    assert made["name"] == "Era Co" and made["left_blank"]
    names = [v["name"] for v in books.records["/api/vendors"].values()]
    assert names.count("Era Co") == 1
