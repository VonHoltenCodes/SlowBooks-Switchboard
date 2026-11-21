"""One connection, several companies' books: the books argument, how a call
picks its books before anything is sent, the line naming the books on every
result, a refused token, the served-header check, and calls on two threads
that never cross."""

import json
import re
import threading

import pytest
from bookskit import ManyBooks
from fakeapi import UNSERVED, FakeInstall, new_token

from sbplive import operations, tools
from sbplive.client import (
    ApiClient,
    ApiError,
    SpecHolder,
    Unconfirmed,
)
from sbplive.registry import BOOKS_PROPERTY, SingleBooks, choose
from sbplive.server import INSTRUCTIONS, build_registry

THREE = {
    "harbor-books.db": "Harbor Books",
    "smith-plumbing.db": "Smith Plumbing",
    "lake-dental.db": "Lake Dental",
}
HANDLE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}~[a-z2-7]{6}$")


@pytest.fixture
def install(spec):
    return FakeInstall(spec, THREE)


@pytest.fixture
def many(install):
    return ManyBooks.of(install)


@pytest.fixture
def reg(many):
    return build_registry(many)


def _texts(result) -> list:
    return [c["text"] for c in result["content"]]


def _body(result):
    return json.loads(result["content"][-1]["text"])


def _sent(install) -> int:
    return len(install.requests)


# ---------------------------------------------------------------------------
# the argument
# ---------------------------------------------------------------------------


def test_every_tool_takes_books_and_none_requires_it(reg):
    for t in reg.definitions():
        schema = t["inputSchema"]
        assert schema["properties"]["books"] == BOOKS_PROPERTY
        assert "books" not in schema.get("required", [])
        assert schema["additionalProperties"] is False
        assert "enum" not in schema["properties"]["books"]


def test_the_handles_are_well_formed(many):
    for e in many.entries():
        assert HANDLE.fullmatch(e["books"])


def test_the_instructions_say_how_books_are_named():
    assert "Call context first: it lists them." in INSTRUCTIONS
    assert "never chooses the books" in INSTRUCTIONS


# ---------------------------------------------------------------------------
# picking the books, before anything is sent
# ---------------------------------------------------------------------------


def test_no_books_on_three_companies_is_refused_and_sends_nothing(reg, many, install):
    result = reg.call("create_invoice", {"data": {"customer_id": 1}})
    assert result["isError"] is True
    handles = ", ".join(e["books"] for e in many.entries())
    assert _texts(result) == [
        (
            "This connection reaches 3 companies' books, so every call names one "
            "with books. Call context to see them, then call create_invoice again "
            f"with books set to one of: {handles}. Nothing was done."
        )
    ]
    assert _sent(install) == 0 and many.picks == []


def test_context_without_books_lists_them_and_reads_no_books(reg, many, install):
    result = reg.call("context", {})
    assert result["isError"] is False
    assert len(result["content"]) == 1  # no books named, so no line naming them
    out = _body(result)
    assert out == {
        "books": [
            {"books": e["books"], "name": e["name"], "answers": True, "why": ""}
            for e in many.entries()
        ],
        "next": "Every call names one of these with books. Ids, keys and "
        "answers belong to one company's books only.",
    }
    assert _sent(install) == 0


def test_an_unknown_handle_is_refused(reg, many, install):
    result = reg.call("search", {"q": "x", "books": "smith~aaaaaa"})
    handles = ", ".join(e["books"] for e in many.entries())
    assert _texts(result) == [
        (
            "This connection does not reach the books 'smith~aaaaaa'. It reaches: "
            f"{handles}. Nothing was done."
        )
    ]
    assert result["isError"] is True and _sent(install) == 0


@pytest.mark.parametrize(
    "given", ["Smith Plumbing", "smith plumbing", "smith-plumbing.db", "smith-plumbing"]
)
def test_a_name_or_file_is_refused_with_the_handle(reg, many, install, given):
    result = reg.call("search", {"q": "x", "books": given})
    handle = many.handle("smith-plumbing.db")
    assert _texts(result) == [
        (
            f"Name the books by their handle, {handle}, not by the company's name "
            "or file (names repeat and change). Nothing was done."
        )
    ]
    assert _sent(install) == 0


def test_books_must_be_text(reg, install):
    result = reg.call("search", {"q": "x", "books": 3})
    assert result["isError"] is True
    assert _texts(result) == ["search: books must be text. Nothing was done."]
    assert _sent(install) == 0


def test_one_company_may_leave_books_out(spec):
    install = FakeInstall(spec, {"harbor-books.db": "Harbor Books"})
    one = ManyBooks.of(install)
    reg = build_registry(one)
    result = reg.call("context", {})
    assert result["isError"] is False
    assert len(result["content"]) == 1  # one company: nothing names it
    out = _body(result)
    assert out["company"] == "Harbor Books"
    assert out["books"] == one.handle("harbor-books.db")
    assert out["company_file"] == "harbor-books.db"
    assert "companies" not in out
    # naming it works; naming another does not, and sends nothing
    again = reg.call("context", {"books": one.handle("harbor-books.db")})
    assert again["isError"] is False
    sent = _sent(install)
    other = reg.call("context", {"books": "harbor-books~aaaaaa"})
    assert other["isError"] is True and _sent(install) == sent


def test_a_single_token_needs_no_handle(api, books):
    reg = build_registry(api)
    assert isinstance(reg.books, SingleBooks)
    out = _body(reg.call("context", {}))
    assert "books" not in out and "companies" not in out
    sent = len(books.calls)
    result = reg.call("context", {"books": "acme~aaaaaa"})
    assert result["isError"] is True
    assert _texts(result) == [
        (
            "This connection does not reach the books 'acme~aaaaaa'. It reaches "
            "one company's books, which need no handle: leave books out. Nothing "
            "was done."
        )
    ]
    assert len(books.calls) == sent


def test_choose_counts_the_connection_not_what_answers_now(many):
    entries = many.entries()
    entries[1]["answers"] = False
    with pytest.raises(Exception) as caught:
        choose(entries[:1], 3, None, "search")
    assert "3 companies' books" in caught.value.sentence


def test_the_provider_refusing_is_said_with_the_books(reg, many, install):
    many.row("lake-dental.db")["off"] = "refused"
    handle = many.handle("lake-dental.db")
    result = reg.call("search", {"q": "x", "books": handle})
    assert result["isError"] is True
    assert _texts(result) == [
        f"Books: Lake Dental ({handle}).",
        "Lake Dental no longer accepts this connection. Nothing was done.",
    ]
    assert _sent(install) == 0


def test_arguments_are_checked_after_the_books(reg, many, install):
    handle = many.handle("harbor-books.db")
    result = reg.call("search", {"books": handle})
    assert _texts(result) == [f"Books: Harbor Books ({handle}).", "search needs q."]
    assert _sent(install) == 0


# ---------------------------------------------------------------------------
# the line naming the books
# ---------------------------------------------------------------------------


def test_every_result_starts_by_naming_its_books(reg, many, install):
    handle = many.handle("smith-plumbing.db")
    result = reg.call("context", {"books": handle})
    assert result["isError"] is False
    assert result["content"][0] == {
        "type": "text",
        "text": f"Books: Smith Plumbing ({handle}).",
    }
    out = _body(result)
    assert out["company"] == "Smith Plumbing"
    assert out["books"] == handle and out["company_file"] == "smith-plumbing.db"
    assert "Books:" not in result["content"][1]["text"]
    assert "companies" not in out


def test_an_error_starts_by_naming_its_books_too(reg, many):
    handle = many.handle("lake-dental.db")
    result = reg.call(
        "get_record", {"kind": "customers", "record_id": 99, "books": handle}
    )
    assert result["isError"] is True
    assert _texts(result)[0] == f"Books: Lake Dental ({handle})."
    assert "found nothing" in _texts(result)[1]


def test_a_write_lands_in_the_books_it_names(reg, many, install):
    handle = many.handle("lake-dental.db")
    result = reg.call(
        "create_customer",
        {"data": {"name": "Pine Dental Supply"}, "asked_user": True, "books": handle},
    )
    assert result["isError"] is False, result
    made = [
        r["name"]
        for r in install.companies["lake-dental.db"].records["/api/customers"].values()
    ]
    assert made == ["Pine Dental Supply"]
    for file in ("harbor-books.db", "smith-plumbing.db"):
        assert install.companies[file].records["/api/customers"] == {}


# ---------------------------------------------------------------------------
# a refused token
# ---------------------------------------------------------------------------


def test_a_401_tells_the_provider_and_says_who(reg, many, install):
    install.companies["smith-plumbing.db"].token = new_token()
    handle = many.handle("smith-plumbing.db")
    result = reg.call("search", {"q": "x", "books": handle})
    assert result["isError"] is True
    assert _texts(result) == [
        f"Books: Smith Plumbing ({handle}).",
        (
            "Smith Plumbing no longer accepts this connection: its token was turned "
            "off there, or the person who added it is no longer an administrator "
            "there. Tell the person; it can be used again only when it is added "
            "again. Nothing was done."
        ),
    ]
    assert many.lost_calls == [handle]
    # the provider now refuses it before sending
    sent = _sent(install)
    again = reg.call("search", {"q": "x", "books": handle})
    assert again["isError"] is True and _sent(install) == sent
    # the others still answer
    ok = reg.call("search", {"q": "x", "books": many.handle("harbor-books.db")})
    assert ok["isError"] is False


def test_a_401_on_a_single_token_keeps_its_sentence():
    def refusing(method, path, params, body, headers):
        return 401, {"detail": "Invalid or revoked API token"}

    reg = build_registry(ApiClient(refusing))
    result = reg.call("search", {"q": "x"})
    assert "make a new one under Settings, API Tokens" in _texts(result)[0]


def test_a_provider_failing_on_lost_still_answers(reg, many, install, monkeypatch):
    def broken(books):
        raise RuntimeError("provider bug")

    monkeypatch.setattr(many, "lost", broken)
    install.companies["harbor-books.db"].token = new_token()
    result = reg.call("search", {"q": "x", "books": many.handle("harbor-books.db")})
    assert "no longer accepts this connection" in _texts(result)[1]


# ---------------------------------------------------------------------------
# which company answered
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stamp", [None, "harbor-books.db", ""])
def test_an_answer_not_from_the_named_books_stops_the_call(reg, many, install, stamp):
    install.stamp["smith-plumbing.db"] = stamp
    handle = many.handle("smith-plumbing.db")
    result = reg.call(
        "create_invoice",
        {
            "data": {
                "customer_id": 1,
                "date": "2026-11-20",
                "lines": [{"description": "Valve", "amount": "40.00"}],
            },
            "asked_user": True,
            "books": handle,
        },
    )
    assert result["isError"] is True
    assert _texts(result) == [
        f"Books: Smith Plumbing ({handle}).",
        (
            "SlowBooks Pro did not confirm it answered from Smith Plumbing's books, "
            "so this call stopped. Tell the person; do not retry."
        ),
    ]
    # the read before the write was caught: the write was never sent
    assert not [r for r in install.requests if r[2] == "POST"]
    assert install.companies["smith-plumbing.db"].records["/api/invoices"] == {}


def _client(answer, **kw):
    seen = []

    def send(method, path, params, body, headers):
        seen.append(dict(headers or {}))
        return answer(path)

    return ApiClient(send, **kw), seen


def test_the_client_checks_the_served_header():
    def ok(path):
        return 200, {"ok": True}, {"X-SlowBooks-Company-Served": "a.db"}

    client, _ = _client(ok, company="a.db", expect_served="a.db")
    assert client.get("/api/settings") == {"ok": True}

    def missing(path):
        return 200, {"ok": True}, {}

    client, _ = _client(missing, company="a.db", expect_served="a.db")
    with pytest.raises(Unconfirmed):
        client.get("/api/settings")

    def two_tuple(path):
        return 200, {"ok": True}

    client, _ = _client(two_tuple, company="a.db", expect_served="a.db")
    with pytest.raises(Unconfirmed):
        client.get("/api/settings")

    def other(path):
        return 404, {"detail": "Not Found"}, {"x-slowbooks-company-served": "b.db"}

    client, _ = _client(other, company="a.db", expect_served="a.db")
    with pytest.raises(Unconfirmed):
        client.get("/api/customers/1")


def test_refusals_before_the_token_carry_no_served_header():
    def refused(path):
        return 401, {"detail": "Invalid or revoked API token"}, {}

    client, _ = _client(refused, company="a.db", expect_served="a.db")
    with pytest.raises(ApiError) as caught:
        client.get("/api/settings")
    assert caught.value.status == 401 and caught.value.stamped is False

    def document(path):
        return 200, {"paths": {}}, {}

    client, _ = _client(document, company="a.db", expect_served="a.db")
    assert client.openapi() == {"paths": {}}
    assert client.get("/api/auth/status") == {"paths": {}}


def test_unconfirmed_is_not_an_api_error():
    # so no tool that reads past a refused read can go on to its write
    assert not issubclass(Unconfirmed, ApiError)


def test_a_company_not_served_now_is_said_by_name(reg, many, install):
    install.not_served.add("lake-dental.db")
    handle = many.handle("lake-dental.db")
    result = reg.call("search", {"q": "x", "books": handle})
    assert _texts(result) == [
        f"Books: Lake Dental ({handle}).",
        (
            "Lake Dental is not served by this SlowBooks Pro server right now. Ask "
            "its administrator. Nothing was done."
        ),
    ]
    assert install.requests[-1][1] == ("lake-dental.db",)
    assert many.lost_calls == []
    assert UNSERVED.endswith("Nothing was done.")


def test_a_locked_company_gives_the_apps_own_sentence(reg, many):
    locked = "This company is locked. Unlock it in SlowBooks Pro first"

    def answer(method, path, params, body, headers):
        return 503, {"detail": locked}, {}

    row = many.row("harbor-books.db")
    row["client"] = ApiClient(answer, company=row["file"], expect_served=row["file"])
    result = reg.call("search", {"q": "x", "books": row["books"]})
    assert _texts(result)[1] == f"{locked}. Nothing was done."


# ---------------------------------------------------------------------------
# the company header
# ---------------------------------------------------------------------------


def test_case_variants_of_the_company_header_are_stripped():
    def ok(path):
        return 200, {}, {"X-SlowBooks-Company-Served": "a.db"}

    client, seen = _client(ok, company="a.db", expect_served="a.db")
    client.request(
        "POST",
        "/api/customers",
        body={},
        headers={
            "x-slowbooks-company": "b.db",
            "X-SLOWBOOKS-COMPANY": "c.db",
            "Idempotency-Key": "k-1",
        },
    )
    assert seen[-1] == {"X-SlowBooks-Company": "a.db", "Idempotency-Key": "k-1"}
    # a client for no named company sends none at all
    plain, seen = _client(lambda path: (200, {}))
    plain.request("GET", "/api/settings", headers={"X-SlowBooks-Company": "b.db"})
    assert seen[-1] == {}


def test_post_operation_cannot_set_the_company_header(api):
    assert "x-slowbooks-company" in operations._OWN_HEADERS
    with pytest.raises(ValueError, match="has no parameter"):
        operations.build_request(
            api, "POST", "/api/customers", {"X-SlowBooks-Company": "b.db"}, {}, None
        )


def test_a_token_never_travels_with_another_companys_header(reg, many, install):
    for file in THREE:
        reg.call("context", {"books": many.handle(file)})
        reg.call("search", {"q": "a", "books": many.handle(file)})
    tokened = [r for r in install.requests if r[3] != "/openapi.json"]
    assert tokened
    for token, named, _method, _path in tokened:
        assert len(named) == 1
        assert token == install.token_of(named[0])


# ---------------------------------------------------------------------------
# next steps name the books
# ---------------------------------------------------------------------------


def _not_answering(file):
    def send(method, path, params, body, headers):
        raise ApiError(
            0, "SlowBooks Pro did not answer at http://127.0.0.1:9: timed out"
        )

    return ApiClient(send, company=file, expect_served=file)


def test_an_unknown_outcome_names_the_same_books(reg, many):
    row = many.row("smith-plumbing.db")
    row["client"] = _not_answering(row["file"])
    handle = row["books"]
    paid = reg.call(
        "record_payment",
        {
            "data": {"customer_id": 1, "date": "2026-11-20", "amount": "5.00"},
            "key": "pay-1",
            "books": handle,
        },
    )
    assert (
        f"Retry with the same key, the same data and the same books ({handle}); "
        "the API books it once." in _texts(paid)[1]
    )
    entry = reg.call(
        "create_journal_entry",
        {
            "data": {"date": "2026-11-20", "description": "x", "lines": []},
            "books": handle,
        },
    )
    assert (
        f"Check with list_records or search, with books: {handle}, before retrying."
        in (_texts(entry)[1])
    )


def test_one_company_keeps_the_plain_next_steps(api):
    def down(method, path, params, body, headers):
        raise ApiError(
            0, "SlowBooks Pro did not answer at http://127.0.0.1:9: timed out"
        )

    reg = build_registry(ApiClient(down))
    paid = reg.call(
        "record_payment",
        {
            "data": {"customer_id": 1, "date": "2026-11-20", "amount": "5.00"},
            "key": "k",
        },
    )
    assert "Retry with the same key and the same data; the API books it once." in (
        _texts(paid)[0]
    )


def test_ask_first_names_the_books(reg, many):
    handle = many.handle("harbor-books.db")
    result = reg.call(
        "create_customer", {"data": {"name": "Gull Marine"}, "books": handle}
    )
    assert result["isError"] is False
    out = _body(result)
    assert out["ask_first"] is True
    assert (
        f"call create_customer again with what they gave, asked_user: true and "
        f"books: {handle}, or with asked_user: true and books: {handle} alone"
        in out["next"]
    )


def test_an_expense_without_its_account_names_the_books(reg, many):
    handle = many.handle("lake-dental.db")
    result = reg.call(
        "create_expense",
        {"data": {"date": "2026-11-20", "amount": "9.00"}, "books": handle},
    )
    out = _body(result)
    assert out["next"].endswith(
        f"call create_expense again with paid_from_account_id and books: {handle}."
    )


# ---------------------------------------------------------------------------
# one document for the whole connection
# ---------------------------------------------------------------------------


def test_one_openapi_fetch_for_every_company(reg, many, install):
    for file in THREE:
        reg.call("find_operations", {"query": "invoice", "books": many.handle(file)})
        reg.call("list_reports", {"books": many.handle(file)})
    fetched = [r for r in install.requests if r[3] == "/openapi.json"]
    assert len(fetched) == 1


def test_clients_share_a_holder_only_when_given_one(spec):
    calls = []

    def send(method, path, params, body, headers):
        calls.append(path)
        return 200, spec

    holder = SpecHolder()
    a, b = ApiClient(send, spec_holder=holder), ApiClient(send, spec_holder=holder)
    tools._spec(a)
    tools._spec(b)
    assert calls == ["/openapi.json"]
    tools._spec(ApiClient(send))
    assert calls == ["/openapi.json", "/openapi.json"]


# ---------------------------------------------------------------------------
# two threads, two companies
# ---------------------------------------------------------------------------


def test_two_threads_on_two_books_never_cross(reg, many, install):
    pairs = [
        ("harbor-books.db", "Harbor Books"),
        ("smith-plumbing.db", "Smith Plumbing"),
    ]
    wrong = []
    barrier = threading.Barrier(2)

    def run(file, name):
        handle = many.handle(file)
        barrier.wait()
        for i in range(40):
            result = reg.call("context", {"books": handle})
            out = _body(result)
            if (
                result["content"][0]["text"] != f"Books: {name} ({handle})."
                or out["company"] != name
                or out["books"] != handle
            ):
                wrong.append((file, i, out))
            made = reg.call(
                "create_customer",
                {"data": {"name": f"{name} {i}"}, "asked_user": True, "books": handle},
            )
            if made["isError"]:
                wrong.append((file, i, made))

    threads = [threading.Thread(target=run, args=p) for p in pairs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert wrong == []
    for file, name in pairs:
        names = {
            r["name"]
            for r in install.companies[file].records["/api/customers"].values()
        }
        assert names == {f"{name} {i}" for i in range(40)}
    for token, named, _m, path in install.requests:
        if path != "/openapi.json":
            assert token == install.token_of(named[0])
    assert tools.CALLED.get() is None
