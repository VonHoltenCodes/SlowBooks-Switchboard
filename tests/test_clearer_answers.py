"""Answers that say what SlowBooks Pro did, read from the app and never
guessed: a DELETE that deactivates or deletes, where a payment's money went,
an expense with no paid-from account, and the accounts a new item falls back
to."""

import json

import pytest
from test_protocol import INIT, INITIALIZED, _modern, _req

from sbplive import operations, tools
from sbplive.protocol import Session
from sbplive.server import INSTRUCTIONS, build_registry

LINE = {"description": "work", "quantity": 1, "rate": "100.00"}


# -- delete: deleted or deactivated ------------------------------------------------


@pytest.mark.parametrize(
    "answer, outcome",
    [
        ({"message": "Customer deactivated"}, "deactivated"),
        ({"message": "Item deactivated"}, "deactivated"),
        ({"status": "deactivated", "id": 3}, "deactivated"),
        ({"message": "Account deleted"}, "deleted"),
        ({"status": "deleted"}, "deleted"),
        ({"deleted": True}, "deleted"),
        ({"deleted": 7}, None),
        ({"ok": True}, None),
        ([], None),
    ],
)
def test_the_outcome_is_read_from_the_app_s_answer(answer, outcome):
    assert operations.delete_outcome(answer) == outcome


def test_deleting_a_customer_says_it_was_made_inactive(api, books):
    cust = tools.create_customer(api, {"name": "Gone Co"}, asked_user=True)
    out = operations.delete_operation(
        api, "/api/customers/{customer_id}", {"customer_id": cust["id"]}
    )
    assert out == {"message": "Customer deactivated", "result": "deactivated"}
    assert books.records["/api/customers"][cust["id"]]["is_active"] is False


def test_deleting_an_account_says_deleted(api, books):
    acct = books._store(
        "/api/accounts",
        {"account_number": "6999", "name": "Spare", "account_type": "expense"},
    )
    out = operations.delete_operation(
        api, "/api/accounts/{account_id}", {"account_id": acct["id"]}
    )
    assert out["result"] == "deleted"


def test_the_delete_tool_says_so(api):
    desc = {t["name"]: t["description"] for t in build_registry(api).definitions()}
    assert "make it inactive" in desc["delete_operation"]
    assert "deleted or deactivated" in desc["delete_operation"]


# -- payments: where the money went ------------------------------------------------


def _invoice(api):
    cust = tools.create_customer(api, {"name": "Pay Co"}, asked_user=True)
    inv = tools.create_invoice(
        api,
        {"customer_id": cust["id"], "date": "2026-11-19", "lines": [LINE]},
        asked_user=True,
    )
    return cust, inv


def _payment(cust, inv, **extra):
    return {
        "customer_id": cust["id"],
        "date": "2026-11-19",
        "amount": "40.00",
        "allocations": [{"invoice_id": inv["id"], "amount": "40.00"}],
        **extra,
    }


def test_a_payment_with_no_deposit_account_waits_in_undeposited_funds(
    api, books, seed_accounts
):
    cust, inv = _invoice(api)
    out = tools.record_payment(api, _payment(cust, inv), "money-1")
    assert out["deposit_to_account_id"] is None
    assert out["money_to"] == {
        "account_id": seed_accounts["1200"].id,
        "account": "1200 - Undeposited Funds",
    }
    assert "not in a bank account yet" in out["money_note"]
    assert "Make Deposits" in out["money_note"]


def test_a_payment_into_a_named_account_says_which(api, seed_accounts):
    cust, inv = _invoice(api)
    body = _payment(cust, inv, deposit_to_account_id=seed_accounts["1000"].id)
    out = tools.record_payment(api, body, "money-2")
    assert out["money_to"]["account"] == "1000 - Checking"
    assert out["money_note"] == "The payment went into 1000 - Checking."


def test_a_bill_payment_names_the_bank_it_came_from(api, seed_accounts):
    vendor = tools.create_vendor(api, {"name": "Paid Vendor"}, asked_user=True)
    body = {"vendor_id": vendor["id"], "date": "2026-11-19", "amount": 10}
    out = tools.record_bill_payment(api, body, "money-3")
    assert out["money_to"]["account"] == "1000 - Checking"
    assert "its default bank account" in out["money_note"]
    out = operations.post_operation(
        api,
        "/api/bill-payments",
        body={**body, "pay_from_account_id": seed_accounts["1000"].id},
        idempotency_key="money-4",
    )
    assert out["money_note"] == "The payment was paid from 1000 - Checking."


# -- expenses: asked first, paid-from never guessed ---------------------------------


def _expense(seed_accounts, **extra):
    return {
        "date": "2026-11-19",
        "amount": "25.00",
        "expense_account_id": seed_accounts["6000"].id,
        **extra,
    }


def test_an_expense_without_paid_from_suggests_the_form_s_account(
    api, books, seed_accounts
):
    for asked in (False, True):
        out = tools.create_expense(
            api, _expense(seed_accounts, payee="Hardware Store"), asked_user=asked
        )
        assert out["created"] is False and out["ask_first"] is True
        assert out["suggested"] == {
            "paid_from_account_id": seed_accounts["1000"].id,
            "account": "1000 - Checking",
        }
        assert "would start at 1000 - Checking" in out["next"]
    assert books.records["/api/expenses"] == {}


def test_the_settings_default_deposit_account_is_the_suggestion(
    api, books, seed_accounts
):
    savings = books._store(
        "/api/accounts",
        {
            "account_number": "1010",
            "name": "Savings",
            "account_type": "asset",
            "bank_kind": "savings",
        },
    )
    books.settings["default_deposit_account_id"] = str(savings["id"])
    out = operations.post_operation(
        api, "/api/expenses", body=_expense(seed_accounts), asked_user=True
    )
    assert out["suggested"]["account"] == "1010 - Savings"


def test_a_bare_expense_asks_and_a_full_one_is_made(api, books, seed_accounts):
    paid = {"paid_from_account_id": seed_accounts["1000"].id}
    held = tools.create_expense(api, _expense(seed_accounts, **paid))
    assert held["ask_first"] is True
    assert [e["label"] for e in held["empty"]] == [
        "Payee or vendor",
        "Memo",
        "Reference (check number)",
    ]
    made = tools.create_expense(
        api, _expense(seed_accounts, payee="Hardware Store", **paid)
    )
    assert made["id"] and made["left_blank"] == ["Memo", "Reference (check number)"]


# -- items: the accounts the app falls back to ------------------------------------


def test_a_stocked_item_names_the_cost_of_goods_account(api):
    made = tools.create_item(
        api,
        {"name": "Fence post", "item_type": "product", "rate": "12"},
        asked_user=True,
    )
    values = {d["label"]: d["value"] for d in made["defaulted"]}
    assert "4000 - Sales" in values["Income Account"]
    assert values["Expense or COGS Account"].startswith(
        "the cost of its sales posts to 5000 - Cost of Goods Sold"
    )


def test_an_item_with_its_accounts_has_no_fallback(api, seed_accounts):
    made = tools.create_item(
        api,
        {
            "name": "Labor",
            "item_type": "service",
            "income_account_id": seed_accounts["4000"].id,
            "expense_account_id": seed_accounts["6000"].id,
        },
        asked_user=True,
    )
    assert made["defaulted"] == []


@pytest.mark.parametrize("era", ["2025-11-25", "2026-07-28"])
def test_both_eras_carry_the_money_note(api, era):
    cust, inv = _invoice(api)
    session = Session(build_registry(api), INSTRUCTIONS)
    call = {
        "name": "record_payment",
        "arguments": {"data": _payment(cust, inv), "key": f"era-{era}"},
    }
    if era == "2025-11-25":
        session.handle_message(INIT)
        session.handle_message(INITIALIZED)
        reply = session.handle_message(_req(3, "tools/call", call))
    else:
        reply = session.handle_message(_modern(3, "tools/call", call))
    made = json.loads(reply["result"]["content"][0]["text"])
    assert made["money_to"]["account"] == "1200 - Undeposited Funds"
