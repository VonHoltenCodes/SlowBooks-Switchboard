"""Every tool's annotations, pinned in one table (registry.py says the
reading: every tool that writes the books is destructive, since nothing a
ledger write does is only additive; idempotent where the same call again
changes nothing more; nothing is open-world)."""

from sbplive.server import build_registry

# tool: (readOnlyHint, destructiveHint, idempotentHint, openWorldHint)
TABLE = {
    "context": (True, False, True, False),
    "search": (True, False, True, False),
    "list_records": (True, False, True, False),
    "get_record": (True, False, True, False),
    "list_reports": (True, False, True, False),
    "report": (True, False, True, False),
    "api_schema": (True, False, True, False),
    "api_get": (True, False, True, False),
    "find_operations": (True, False, True, False),
    "describe_operation": (True, False, True, False),
    "post_operation": (False, True, False, False),
    "update_operation": (False, True, False, False),
    "delete_operation": (False, True, True, False),
    "create_customer": (False, True, False, False),
    "create_vendor": (False, True, False, False),
    "create_item": (False, True, False, False),
    "create_invoice": (False, True, False, False),
    "create_bill": (False, True, False, False),
    "create_expense": (False, True, False, False),
    "create_journal_entry": (False, True, False, False),
    "record_payment": (False, True, True, False),
    "record_bill_payment": (False, True, True, False),
    "void_record": (False, True, True, False),
}
HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")


def test_every_tool_s_annotations_are_the_table(api):
    listed = {t["name"]: t for t in build_registry(api).definitions()}
    assert set(listed) == set(TABLE)
    for name, want in TABLE.items():
        a = listed[name]["annotations"]
        assert tuple(a[h] for h in HINTS) == want, name
        assert a["title"] == listed[name]["title"] and a["title"]
        assert set(a) == {"title", *HINTS}, name


def test_only_the_reads_skip_the_prompt(api):
    # Claude asks before a destructive tool and lets a read-only one run.
    for t in build_registry(api).definitions():
        a = t["annotations"]
        assert a["readOnlyHint"] != a["destructiveHint"], t["name"]


# -- a server failure, said clearly and without its insides ------------------------

import pytest

from sbplive.client import ApiClient, ApiError
from sbplive.registry import Registry, Tool, error_sentence


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        "",
        None,
        "Internal Server Error",
        "<html><body><h1>500</h1><pre>Traceback (most recent call last)</pre></body></html>",
        'Traceback (most recent call last):\n  File "app/main.py", line 9',
        {"detail": None},
        {"detail": "x" * 400},
    ],
)
def test_a_500_with_nothing_usable_is_a_clear_sentence(payload):
    client = ApiClient(lambda *a: (500, payload))
    with pytest.raises(ApiError) as exc:
        client.get("/api/customers")
    said = error_sentence(exc.value)
    assert said.startswith(
        "SlowBooks Pro failed while doing this (HTTP 500) and gave no reason."
    )
    assert "whether it was made is not known" in said
    assert "check with search or list_records before trying again" in said
    for leak in ("Traceback", "<", "main.py", "xxxx", "the request failed"):
        assert leak not in said


def test_a_500_that_says_why_passes_the_sentence_on():
    client = ApiClient(
        lambda *a: (500, {"detail": "The company file is locked by a backup"})
    )
    with pytest.raises(ApiError) as exc:
        client.get("/api/customers")
    assert error_sentence(exc.value).startswith(
        "SlowBooks Pro failed while doing this (HTTP 500): The company file is "
        "locked by a backup."
    )


def test_a_500_reaches_the_assistant_as_a_tool_error(api):
    def boom(client):
        raise ApiError(500, "")

    reg = Registry(api)
    reg.add(Tool(name="boom", title="Boom", description="d", fn=boom,
                 annotations={}))  # fmt: skip
    out = reg.call("boom", {})
    assert out["isError"] is True
    assert "gave no reason" in out["content"][0]["text"]
