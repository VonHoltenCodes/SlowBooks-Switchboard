"""Sales tax on a new sales document (tools.sales_tax): the app's forms
start at the company's Default Tax Rate (the API too from SlowBooks Pro
2.32.0; before it, a missing tax_rate was 0%), so Switchboard does what the
form does and says so; a tax_rate the assistant gives, 0 included, is sent
as given. A document the app charges no tax (a non-taxable customer, its own
tax_exempt, a tax-exempt job from 2.32.0) is answered as untaxed, never as
taxed at the default. Both protocol eras."""

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


# ---------------------------------------------------------------------------
# No sales tax charged: the answer says so and why, and never that the
# default rate was applied. books.exemptions answers as SlowBooks Pro 2.32.0
# does (the saved document's tax_exempt and amounts); without it the fake is
# 2.31.x, whose answer has neither.
# ---------------------------------------------------------------------------

UNTAXED_SOURCE = "company default (6.25%); no sales tax was charged"
UNTAXED_ROW = {
    "label": "Sales tax rate",
    "value": "6.25% (the company's default; no sales tax was charged)",
}
TAXED_ROW = {"label": "Sales tax rate", "value": "6.25% (the company's default)"}
KINDS = ["/api/invoices", "/api/estimates", "/api/sales-receipts", "/api/credit-memos"]
SWITCHED = KINDS[:3]  # a credit memo has no switch and no exempt job


@pytest.fixture
def books232(books):
    books.exemptions = True
    return books


def _document(customer, **extra):
    return {"customer_id": customer["id"], "date": "2026-11-19", "lines": LINE, **extra}


def _make(api, route, body):
    return operations.post_operation(api, route, body=body, asked_user=True)


def _saved(made, route):
    return made["invoice"] if route == "/api/sales-receipts" else made


def _untaxed(made, why):
    """The answer says no tax was charged and why, in all three places, and
    nowhere that the default rate was applied as tax."""
    assert made["tax_rate_source"] == UNTAXED_SOURCE
    note = made["tax_note"]
    assert f"{why}, so no sales tax is charged." in note
    assert "Tell the user that no sales tax was charged and why" in note
    assert "not that 6.25% was applied" in note
    assert "used the company's default sales tax rate" not in note
    rows = [d for d in made["defaulted"] if d["label"] == "Sales tax rate"]
    assert rows == [UNTAXED_ROW]
    assert "Sales tax rate" not in made["left_blank"]


def _taxed(made):
    assert made["tax_rate_source"] == "company default (6.25%)"
    assert "used the company's default sales tax rate, 6.25%" in made["tax_note"]
    assert "no sales tax" not in made["tax_note"]
    assert TAXED_ROW in made["defaulted"] and UNTAXED_ROW not in made["defaulted"]


@pytest.mark.parametrize("route", SWITCHED)
def test_an_exempt_job_with_no_rate_says_no_tax_was_charged(
    api, books232, customer, route
):
    job = books232.add_job(customer["id"], "Town Hall Fence", is_taxable=False)
    made = _make(api, route, _document(customer, job_id=job["id"]))
    # the form keeps the default rate on it; the app charges no tax
    assert _sent(books232, route)["tax_rate"] == 0.0625
    assert "tax_exempt" not in _sent(books232, route)
    saved = _saved(made, route)
    assert saved["tax_exempt"] is True and saved["tax_amount"] == "0.00"
    _untaxed(made, "the job Town Hall Fence is tax exempt")


def test_create_invoice_for_an_exempt_job_says_no_tax_was_charged(
    api, books232, customer
):
    job = books232.add_job(customer["id"], "Town Hall Fence", is_taxable=False)
    made = tools.create_invoice(
        api, _invoice(customer, terms="Net 15", job_id=job["id"])
    )
    assert made["tax_amount"] == "0.00"
    _untaxed(made, "the job Town Hall Fence is tax exempt")


@pytest.mark.parametrize("route", SWITCHED)
@pytest.mark.parametrize("given", [0, 0.05])
def test_an_exempt_job_with_a_rate_given_gets_no_note(
    api, books232, customer, route, given
):
    job = books232.add_job(customer["id"], "Town Hall Fence", is_taxable=False)
    body = _document(customer, job_id=job["id"], tax_rate=given)
    made = _make(api, route, body)
    assert _sent(books232, route)["tax_rate"] == given
    assert "tax_rate_source" not in made and "tax_note" not in made
    assert all(d["label"] != "Sales tax rate" for d in made.get("defaulted", []))


@pytest.mark.parametrize("route", SWITCHED)
def test_a_taxable_job_is_taxed_at_the_default_and_says_so(
    api, books232, customer, route
):
    job = books232.add_job(customer["id"], "Garden Gate", is_taxable=True)
    made = _make(api, route, _document(customer, job_id=job["id"]))
    saved = _saved(made, route)
    assert saved["tax_exempt"] is False and saved["tax_amount"] == "6.25"
    _taxed(made)


@pytest.mark.parametrize("route", SWITCHED)
def test_an_exempt_job_switched_back_on_is_taxed(api, books232, customer, route):
    """tax_exempt false on the request overrides the job: the document is
    taxed, and the answer says the default was used."""
    job = books232.add_job(customer["id"], "Town Hall Fence", is_taxable=False)
    made = _make(api, route, _document(customer, job_id=job["id"], tax_exempt=False))
    assert _saved(made, route)["tax_amount"] == "6.25"
    _taxed(made)


def test_a_credit_memo_is_not_exempt_by_its_job(api, books232, customer):
    """A credit memo has no switch: an exempt job does not untax it, so the
    answer must not say it did."""
    job = books232.add_job(customer["id"], "Town Hall Fence", is_taxable=False)
    made = _make(api, "/api/credit-memos", _document(customer, job_id=job["id"]))
    assert made["tax_amount"] == "6.25"
    _taxed(made)
    assert all(c[1] != f"/api/jobs/{job['id']}" for c in books232.calls)


@pytest.mark.parametrize("route", KINDS)
def test_a_non_taxable_customer_says_no_tax_was_charged(
    api, books232, exempt_customer, route
):
    made = _make(api, route, _document(exempt_customer))
    assert _sent(books232, route)["tax_rate"] == 0.0625
    assert _saved(made, route)["tax_amount"] == "0.00"
    _untaxed(made, "Reseller Co is non-taxable (reseller or exempt)")


@pytest.mark.parametrize("route", SWITCHED)
def test_a_document_sent_tax_exempt_says_no_tax_was_charged(
    api, books232, customer, route
):
    made = _make(api, route, _document(customer, tax_exempt=True))
    assert _sent(books232, route)["tax_exempt"] is True
    assert _saved(made, route)["tax_amount"] == "0.00"
    _untaxed(made, "the document is marked tax exempt")


@pytest.mark.parametrize("route", SWITCHED)
def test_a_job_that_cannot_be_read_still_says_no_tax_was_charged(
    api, books232, customer, route, monkeypatch
):
    """The saved document says it is exempt; with the job unreadable the
    answer gives that as the reason."""
    job = books232.add_job(customer["id"], "Town Hall Fence", is_taxable=False)
    real = books232.handle

    def handle(method, path, *a, **kw):
        if method == "GET" and path.startswith("/api/jobs/"):
            return 403, {"detail": "Your role doesn't allow this action"}
        return real(method, path, *a, **kw)

    monkeypatch.setattr(books232, "handle", handle)
    made = _make(api, route, _document(customer, job_id=job["id"]))
    _untaxed(made, "SlowBooks Pro saved the document as tax exempt")


def test_a_saved_document_with_tax_on_it_is_never_called_untaxed(
    api, books232, customer, monkeypatch
):
    """The saved document is the evidence: tax on it means it was taxed,
    whatever the request suggested."""
    real = books232.handle

    def handle(method, path, *a, **kw):
        status, payload = real(method, path, *a, **kw)
        if method == "POST" and path == "/api/invoices":
            payload = {k: v for k, v in payload.items() if k != "tax_exempt"}
            payload["tax_amount"] = "6.25"
        return status, payload

    monkeypatch.setattr(books232, "handle", handle)
    made = _make(api, "/api/invoices", _document(customer, tax_exempt=True))
    _taxed(made)


@pytest.mark.parametrize("route", KINDS)
def test_an_older_app_with_no_exemption_fields_is_taxed_at_the_default(
    api, books, customer, route
):
    """SlowBooks Pro 2.31.x: its jobs have no is_taxable and its answer no
    tax_exempt, so a job never untaxes a document."""
    job = books.add_job(customer["id"], "Town Hall Fence")
    assert "is_taxable" not in job
    made = _make(api, route, _document(customer, job_id=job["id"]))
    assert _sent(books, route)["tax_rate"] == 0.0625
    assert "tax_exempt" not in made and "tax_amount" not in made
    _taxed(made)


@pytest.mark.parametrize("route", KINDS)
def test_an_older_app_and_a_non_taxable_customer_says_no_tax_was_charged(
    api, books, exempt_customer, route
):
    made = _make(api, route, _document(exempt_customer))
    assert "tax_exempt" not in made
    _untaxed(made, "Reseller Co is non-taxable (reseller or exempt)")


def test_an_exempt_job_and_no_default_rate_does_not_ask(api, books232, customer):
    books232.settings["default_tax_rate"] = "0"
    job = books232.add_job(customer["id"], "Town Hall Fence", is_taxable=False)
    made = tools.create_invoice(
        api, _invoice(customer, terms="Net 15", job_id=job["id"])
    )
    assert "Sales tax rate" not in made["left_blank"]
    assert made["tax_rate_source"] == "none: no sales tax applies"
    assert made["tax_note"] == (
        "No tax_rate was given; the job Town Hall Fence is tax exempt, so no "
        "sales tax is charged."
    )


def test_the_apps_note_about_the_job_s_certificate_is_kept_in_front(
    api, books232, customer
):
    theirs = "The exemption certificate for the job Town Hall Fence expired."
    books232.api_tax_note = theirs
    job = books232.add_job(customer["id"], "Town Hall Fence", is_taxable=False)
    made = tools.create_invoice(
        api, _invoice(customer, terms="Net 15", job_id=job["id"])
    )
    assert made["tax_note"].startswith(theirs + " No tax_rate was given")
    _untaxed(made, "the job Town Hall Fence is tax exempt")


MEMO_UNTAXED_SOURCE = "the credited invoice's rate; no sales tax was charged"


def _memo_untaxed(made, why):
    """A credit memo for an invoice that gave back no tax says so and why,
    and nowhere that tax went back."""
    assert made["tax_amount"] == "0.00"
    assert made["tax_rate_source"] == MEMO_UNTAXED_SOURCE
    note = made["tax_note"]
    assert f"{why}, so no sales tax is given back." in note
    assert "Tell the user that no sales tax was credited and why" in note
    assert "the tax goes back the way it was charged" not in note
    assert all(d["label"] != "Sales tax rate" for d in made["defaulted"])


@pytest.mark.parametrize("with_job", [False, True])
def test_a_credit_memo_for_an_exempt_invoice_says_no_tax_goes_back(
    api, books232, customer, with_job
):
    job = books232.add_job(customer["id"], "Town Hall Fence", is_taxable=False)
    inv = tools.create_invoice(
        api, _invoice(customer, terms="Net 15", job_id=job["id"])
    )
    extra = {"job_id": job["id"]} if with_job else {}
    body = _document(customer, original_invoice_id=inv["id"], **extra)
    made = operations.post_operation(api, "/api/credit-memos", body=body)
    assert "tax_rate" not in _sent(books232, "/api/credit-memos")
    assert made["tax_rate"] == "0.0625"  # the invoice's rate is on the memo
    _memo_untaxed(made, "the invoice being credited is tax exempt")


def test_a_credit_memo_for_a_non_taxable_customer_s_invoice_says_no_tax_goes_back(
    api, books232, exempt_customer
):
    inv = tools.create_invoice(api, _invoice(exempt_customer, terms="Net 15"))
    body = _document(exempt_customer, original_invoice_id=inv["id"])
    made = operations.post_operation(api, "/api/credit-memos", body=body)
    _memo_untaxed(made, "Reseller Co is non-taxable (reseller or exempt)")


def test_a_credit_memo_for_a_taxed_invoice_keeps_the_invoice_s_note(
    api, books232, customer
):
    inv = tools.create_invoice(api, _invoice(customer, terms="Net 15"))
    body = _document(customer, original_invoice_id=inv["id"])
    made = operations.post_operation(api, "/api/credit-memos", body=body)
    assert made["tax_amount"] == "6.25"
    assert made["tax_rate_source"] == "the credited invoice's rate"
    assert "the tax goes back the way it was charged" in made["tax_note"]


def test_a_credit_memo_that_gave_tax_back_is_never_called_untaxed(
    api, books232, customer, monkeypatch
):
    """The saved memo decides: with tax on it, the invoice's switch (changed
    since, or misread) does not make the answer say none went back."""
    job = books232.add_job(customer["id"], "Town Hall Fence", is_taxable=False)
    inv = tools.create_invoice(
        api, _invoice(customer, terms="Net 15", job_id=job["id"])
    )
    real = books232.handle

    def handle(method, path, *a, **kw):
        status, answer = real(method, path, *a, **kw)
        if method == "POST" and path == "/api/credit-memos":
            answer = {**answer, "tax_amount": "6.25"}
        return status, answer

    monkeypatch.setattr(books232, "handle", handle)
    body = _document(customer, original_invoice_id=inv["id"])
    made = operations.post_operation(api, "/api/credit-memos", body=body)
    assert made["tax_rate_source"] == "the credited invoice's rate"
    assert "the tax goes back the way it was charged" in made["tax_note"]


def test_a_credit_memo_for_an_invoice_that_cannot_be_read_keeps_the_note(
    api, books232, customer, monkeypatch
):
    job = books232.add_job(customer["id"], "Town Hall Fence", is_taxable=False)
    inv = tools.create_invoice(
        api, _invoice(customer, terms="Net 15", job_id=job["id"])
    )
    real = books232.handle

    def handle(method, path, *a, **kw):
        if method == "GET" and path == f"/api/invoices/{inv['id']}":
            return 403, {"detail": "Your role doesn't allow this action"}
        return real(method, path, *a, **kw)

    monkeypatch.setattr(books232, "handle", handle)
    body = _document(customer, original_invoice_id=inv["id"])
    made = operations.post_operation(api, "/api/credit-memos", body=body)
    assert made["tax_rate_source"] == "the credited invoice's rate"


def test_a_credit_memo_before_2_32_is_answered_as_it_was(api, books, customer):
    """2.31.x has no exempt invoice: nothing on it says tax_exempt."""
    inv = tools.create_invoice(api, _invoice(customer, terms="Net 15"))
    body = _document(customer, original_invoice_id=inv["id"])
    made = operations.post_operation(api, "/api/credit-memos", body=body)
    assert made["tax_rate_source"] == "the credited invoice's rate"
    assert "the tax goes back the way it was charged" in made["tax_note"]


# ---------------------------------------------------------------------------
# The company's default could not be read: no rate is sent, and the answer
# says what the saved document carries, never that a missing rate is 0%
# (true before 2.32.0 only; from 2.32.0 the API uses the default itself).
# ---------------------------------------------------------------------------

UNREAD = (
    "No tax_rate was given and the company's Default Tax Rate could not be "
    "read, so no rate was sent"
)


@pytest.fixture
def no_settings(books, monkeypatch):
    real = books.handle

    def handle(method, path, *a, **kw):
        if path == "/api/settings":
            return 500, {"detail": "Internal Server Error"}
        return real(method, path, *a, **kw)

    monkeypatch.setattr(books, "handle", handle)


@pytest.mark.parametrize("route", SWITCHED)
def test_an_unreadable_default_on_2_32_says_the_rate_the_app_used(
    api, books232, customer, no_settings, route
):
    made = _make(api, route, _document(customer))
    assert "tax_rate" not in _sent(books232, route)
    assert _saved(made, route)["tax_amount"] == "6.25"
    assert made["tax_rate_source"] == (
        "SlowBooks Pro's default (6.25%); the company's default rate could "
        "not be read"
    )
    assert made["tax_note"] == (
        f"{UNREAD} and SlowBooks Pro used its own default, 6.25%. Tell the "
        "user; if this sale is taxed at another rate, change the document's "
        "tax_rate (a fraction: 0.0159 is 1.59%)."
    )
    assert "0% sales tax rate" not in made["tax_note"]
    assert "Sales tax rate" not in made["left_blank"]
    row = {"label": "Sales tax rate", "value": "6.25% (SlowBooks Pro's default)"}
    assert row in made["defaulted"]


@pytest.mark.parametrize("route", SWITCHED)
def test_an_unreadable_default_and_an_exempt_job_do_not_ask_for_a_rate(
    api, books232, customer, no_settings, route
):
    job = books232.add_job(customer["id"], "Town Hall Fence", is_taxable=False)
    made = _make(api, route, _document(customer, job_id=job["id"]))
    assert _saved(made, route)["tax_amount"] == "0.00"
    assert made["tax_rate_source"] == (
        "none: the company's default rate could not be read; no sales tax "
        "was charged"
    )
    assert made["tax_note"] == (
        f"{UNREAD}; the job Town Hall Fence is tax exempt, so no sales tax is "
        "charged. Tell the user that no sales tax was charged and why."
    )
    assert "Ask the user" not in made["tax_note"]
    assert "Sales tax rate" not in made["left_blank"]
    assert all(d["label"] != "Sales tax rate" for d in made["defaulted"])


def test_an_unreadable_default_and_a_job_that_did_not_untax_say_the_rate(
    api, books232, customer, no_settings
):
    """tax_exempt false and tax on the saved document: the job did not
    decide, so the answer says the rate and never that none was charged."""
    job = books232.add_job(customer["id"], "Town Hall Fence", is_taxable=False)
    real = books232.handle

    def handle(method, path, *a, **kw):
        status, answer = real(method, path, *a, **kw)
        if method == "POST" and path == "/api/invoices":
            answer = {**answer, "tax_exempt": False, "tax_amount": "6.25"}
        return status, answer

    books232.handle = handle
    made = _make(api, "/api/invoices", _document(customer, job_id=job["id"]))
    assert "no sales tax" not in made["tax_note"]
    assert "SlowBooks Pro used its own default, 6.25%" in made["tax_note"]
    assert "Sales tax rate" not in made["left_blank"]


def test_an_unreadable_default_before_2_32_says_zero_and_asks(
    api, books, customer, no_settings
):
    real = books.handle

    def handle(method, path, *a, **kw):
        status, answer = real(method, path, *a, **kw)
        if method == "POST" and path == "/api/invoices":
            # 2.31.x takes a missing rate as 0% and answers it
            answer = {**answer, "tax_rate": "0.0000", "tax_amount": "0.00"}
        return status, answer

    books.handle = handle
    made = tools.create_invoice(api, _invoice(customer, terms="Net 15"))
    assert made["tax_note"] == (
        f"{UNREAD} and the document has a 0% sales tax rate. Ask the user "
        "what rate applies."
    )
    assert "Sales tax rate" in made["left_blank"]


def test_an_unreadable_default_and_no_rate_on_the_answer_asks(
    api, books, customer, no_settings
):
    made = tools.create_invoice(api, _invoice(customer, terms="Net 15"))
    assert made["tax_note"] == f"{UNREAD}. Ask the user what rate applies."
    assert "Sales tax rate" in made["left_blank"]


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
