"""Sales tax on a new sales document (tools.sales_tax): the API takes a
missing tax_rate as 0%, the app's forms start at the company's Default Tax
Rate, so Switchboard does what the form does and says so; a tax_rate the
assistant gives, 0 included, is sent as given. Both protocol eras."""

import json
from decimal import Decimal

import pytest
from test_protocol import INIT, INITIALIZED, _modern, _req

from sbplive import operations, tools
from sbplive.protocol import Session
from sbplive.server import INSTRUCTIONS, build_registry

LINE = [{"description": "Fence repair", "amount": 100}]


@pytest.fixture
def customer(api):
    return tools.create_customer(api, {"name": "Taxed Co"}, asked_user=True)


@pytest.fixture
def exempt_customer(api):
    return tools.create_customer(
        api, {"name": "Reseller Co", "is_taxable": False}, asked_user=True
    )


def _invoice(customer, **extra):
    return {"customer_id": customer["id"], "date": "2026-11-19", "lines": LINE, **extra}


def _sent(books, route):
    return [c[3] for c in books.calls if c[0] == "POST" and c[1] == route][-1]


def test_no_rate_takes_the_company_default_and_says_so(api, books, customer):
    made = tools.create_invoice(api, _invoice(customer, terms="Net 15"))
    assert _sent(books, "/api/invoices")["tax_rate"] == 0.0625
    assert made["tax_rate_source"] == "company default (6.25%)"
    assert "default sales tax rate, 6.25%" in made["tax_note"]
    assert "Tell the user" in made["tax_note"]
    assert {"label": "Sales tax rate", "value": "6.25% (the company's default)"} in (
        made["defaulted"]
    )
    assert "Sales tax rate" not in made["left_blank"]


@pytest.mark.parametrize("given", [0, 0.0, "0", 0.05])
def test_a_rate_the_assistant_gives_is_sent_as_given(api, books, customer, given):
    made = tools.create_invoice(api, _invoice(customer, terms="Net 15", tax_rate=given))
    assert _sent(books, "/api/invoices")["tax_rate"] == given
    assert "tax_rate_source" not in made and "tax_note" not in made
    assert all(d["label"] != "Sales tax rate" for d in made["defaulted"])


def test_a_non_taxable_customer_keeps_the_rate_and_pays_no_tax(
    api, books, exempt_customer
):
    made = tools.create_invoice(api, _invoice(exempt_customer, terms="Net 15"))
    # the form keeps the default rate on it; the app charges no tax
    assert _sent(books, "/api/invoices")["tax_rate"] == 0.0625
    assert "Reseller Co is non-taxable" in made["tax_note"]
    assert "no sales tax is charged" in made["tax_note"]


APP_NOTE = "No exemption certificate is on file for Reseller Co."


def test_the_apps_own_tax_note_is_kept_in_front_of_ours(api, books, exempt_customer):
    """SlowBooks Pro answers a sales document for an exempt customer with no
    certificate on file with a note of its own; the assistant must be handed
    it, not have it written over by the note about the rate."""
    books.api_tax_note = APP_NOTE
    made = tools.create_invoice(api, _invoice(exempt_customer, terms="Net 15"))
    assert made["tax_note"].startswith(APP_NOTE + " ")
    assert "Reseller Co is non-taxable" in made["tax_note"]
    assert made["tax_note"].count(APP_NOTE) == 1


def test_the_apps_tax_note_stands_alone_when_the_assistant_gave_the_rate(
    api, books, exempt_customer
):
    books.api_tax_note = APP_NOTE
    made = tools.create_invoice(
        api, _invoice(exempt_customer, terms="Net 15", tax_rate=0)
    )
    assert made["tax_note"] == APP_NOTE
    assert "tax_rate_source" not in made


def test_a_document_marked_exempt_says_so(api, customer):
    plan = tools.sales_tax(api, "/api/invoices", _invoice(customer, tax_exempt=True))
    assert plan["rate"] == Decimal("0.0625") and plan["ask"] is False
    assert "the document is marked tax exempt" in plan["note"]


def test_no_default_and_a_taxable_customer_asks(api, books, customer):
    books.settings["default_tax_rate"] = "0.0"
    held = tools.create_invoice(api, {**_invoice(customer), "lines": [{"amount": 5}]})
    assert held["ask_first"] is True
    assert {"label": "Sales tax rate", "field": "tax_rate"} in held["empty"]
    assert "Sales tax rate" in held["next"]
    made = tools.create_invoice(api, _invoice(customer), asked_user=True)
    assert "tax_rate" not in _sent(books, "/api/invoices")
    assert "Sales tax rate" in made["left_blank"]
    assert made["tax_rate_source"] == "none: the company's default rate is 0%"
    assert "Ask the user whether this sale is taxed" in made["tax_note"]


def test_no_default_and_a_non_taxable_customer_does_not_ask(
    api, books, exempt_customer
):
    books.settings["default_tax_rate"] = "0"
    made = tools.create_invoice(api, _invoice(exempt_customer, terms="Net 15"))
    assert "Sales tax rate" not in made["left_blank"]
    assert made["tax_rate_source"] == "none: no sales tax applies"


def test_an_unreadable_default_asks(api, books, customer, monkeypatch):
    real = books.handle

    def handle(method, path, *a, **kw):
        if path == "/api/settings":
            return 403, {"detail": "Your role doesn't allow this action"}
        return real(method, path, *a, **kw)

    monkeypatch.setattr(books, "handle", handle)
    made = tools.create_invoice(api, _invoice(customer, terms="Net 15"))
    assert "tax_rate" not in _sent(books, "/api/invoices")
    assert (
        "could not be read" in made["tax_note"]
        and "Sales tax rate" in made["left_blank"]
    )


def test_a_percent_keeps_its_digits():
    assert tools._percent(Decimal("1.59")) == "1.59%"
    assert tools._percent(Decimal("8.875")) == "8.875%"
    assert tools._percent(Decimal("7.0")) == "7%"


def test_a_fine_percent_becomes_a_six_place_fraction(api, books, customer):
    books.settings["default_tax_rate"] = "8.875"
    tools.create_invoice(api, _invoice(customer, terms="Net 15"))
    assert _sent(books, "/api/invoices")["tax_rate"] == 0.08875


@pytest.mark.parametrize("route", ["/api/estimates", "/api/sales-receipts"])
def test_post_operation_documents_take_the_default(api, books, customer, route):
    body = {"customer_id": customer["id"], "date": "2026-11-19", "lines": LINE}
    made = operations.post_operation(api, route, body=body, asked_user=True)
    assert _sent(books, route)["tax_rate"] == 0.0625
    assert made["tax_rate_source"] == "company default (6.25%)"


def test_a_credit_memo_for_an_invoice_takes_that_invoice_s_rate(api, books, customer):
    inv = tools.create_invoice(api, _invoice(customer, terms="Net 15"))
    body = {
        "customer_id": customer["id"],
        "date": "2026-11-20",
        "original_invoice_id": inv["id"],
        "lines": LINE,
    }
    made = operations.post_operation(api, "/api/credit-memos", body=body)
    assert "tax_rate" not in _sent(books, "/api/credit-memos")
    assert made["tax_rate_source"] == "the credited invoice's rate"
    body.pop("original_invoice_id")
    made = operations.post_operation(api, "/api/credit-memos", body=body)
    assert _sent(books, "/api/credit-memos")["tax_rate"] == 0.0625


def _call_args(customer):
    return {
        "name": "create_invoice",
        "arguments": {"data": _invoice(customer, terms="Net 15")},
    }


def test_both_eras_carry_the_note(api, books, customer):
    handshake = Session(build_registry(api), INSTRUCTIONS)
    handshake.handle_message(INIT)
    handshake.handle_message(INITIALIZED)
    modern = Session(build_registry(api), INSTRUCTIONS)
    for reply in (
        handshake.handle_message(_req(5, "tools/call", _call_args(customer))),
        modern.handle_message(_modern(6, "tools/call", _call_args(customer))),
    ):
        result = reply["result"]
        assert result["isError"] is False
        made = json.loads(result["content"][0]["text"])
        assert made["tax_rate_source"] == "company default (6.25%)"
        assert made["defaulted"][-1]["label"] == "Sales tax rate"


def test_the_instructions_name_both_lists_and_the_tax_note():
    assert "defaulted" in INSTRUCTIONS and "left_blank" in INSTRUCTIONS
    assert "tax_note" in INSTRUCTIONS
    reg = build_registry(None)
    text = reg.definitions()
    desc = {t["name"]: t["description"] for t in text}
    assert "Default Tax Rate" in desc["create_invoice"]
    assert "Default Tax Rate" in desc["post_operation"]
    assert "Void" in desc["void_record"] and "invoice" in desc["void_record"]
