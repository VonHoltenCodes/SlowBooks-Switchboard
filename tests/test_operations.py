"""The general tools against the product's own OpenAPI document
(tests/fixtures/openapi-2.31.8.json): find_operations, describe_operation,
api_get and the write tools (post_operation, update_operation,
delete_operation), including operations chosen at random (seeded)."""

import random
import re

import pytest

from sbplive import operations
from sbplive.client import ApiClient, ApiError, reachable
from sbplive.server import build_registry

KEY = "pay-2026-0001"


def send(client, method, path, params=None, body=None, key=None):
    """One call through the tool that sends this method."""
    m = method.lower()
    if m == "get":
        assert body is None and key is None
        return operations.api_get(client, path, params)
    if m == "post":
        return operations.post_operation(client, path, params, body, key)
    if m in ("put", "patch"):
        assert key is None
        return operations.update_operation(client, path, params, body, m)
    assert body is None and key is None
    return operations.delete_operation(client, path, params)


class Spy:
    """A transport that records each request and answers from a script."""

    def __init__(self, spec, answer=(200, {"ok": True})):
        self.spec = spec
        self.answer = answer
        self.sent = []

    def __call__(self, method, path, params, body, headers):
        if path == "/openapi.json":
            return 200, self.spec
        self.sent.append(
            {
                "method": method,
                "path": path,
                "params": params,
                "body": body,
                "headers": headers or {},
            }
        )
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def spy(spec):
    return Spy(spec)


@pytest.fixture
def client(spy):
    return ApiClient(spy)


# ---------------------------------------------------------------------------
# the document is read once per process
# ---------------------------------------------------------------------------


def test_the_document_is_fetched_once(spec):
    fetched = []

    def transport(method, path, params, body, headers):
        fetched.append(path)
        return 200, spec

    c = ApiClient(transport)
    operations.find_operations(c, "invoice")
    operations.describe_operation(c, "GET", "/api/invoices")
    operations.find_operations(c, "bill")
    assert fetched == ["/openapi.json"]


# ---------------------------------------------------------------------------
# find_operations
# ---------------------------------------------------------------------------


def test_find_credit_memos(client):
    rows = operations.find_operations(client, "credit memo")
    paths = [(r["method"], r["path"]) for r in rows if "path" in r]
    assert ("POST", "/api/credit-memos") in paths
    assert all(r["does"] for r in rows if "path" in r)


def test_find_marks_keyed_and_file_operations(client):
    rows = operations.find_operations(client, "payments", limit=50)
    keyed = {r["path"] for r in rows if r.get("idempotency_key")}
    assert "/api/payments" in keyed
    rows = operations.find_operations(client, "profit loss pdf", limit=50)
    pdf = [r for r in rows if r.get("path") == "/api/reports/profit-loss/pdf"]
    assert pdf and "PDF" in pdf[0]["note"]


def test_find_lists_admin_routes_and_only_the_api(client):
    """Match the software: the admin areas are the API's to refuse."""
    for query, path in (
        ("tokens", "/api/tokens"),
        ("users", "/api/users"),
        ("backups restore", "/api/backups/restore"),
        ("encryption", "/api/company-file/encryption"),
    ):
        rows = operations.find_operations(client, query, limit=50)
        assert any(r.get("path") == path for r in rows), (query, rows)
        assert all(reachable(r["path"]) for r in rows if "path" in r)


def test_find_limit_and_more(client):
    rows = operations.find_operations(client, "report", limit=3)
    assert len([r for r in rows if "path" in r]) == 3
    assert "more match" in rows[-1]["more"]


def test_find_nothing_and_an_empty_query(client):
    assert "No operation matches" in operations.find_operations(client, "zqxv")
    with pytest.raises(ValueError, match="at least one word"):
        operations.find_operations(client, " ?? ")


# ---------------------------------------------------------------------------
# describe_operation
# ---------------------------------------------------------------------------


def test_describe_a_payment(client):
    out = operations.describe_operation(client, "post", "/api/payments")
    assert out["method"] == "POST" and out["path"] == "/api/payments"
    assert out["request_body"]["required"] is True
    assert out["request_body"]["schema"] == {"$ref": "PaymentCreate"}
    created = out["schemas"]["PaymentCreate"]
    assert {"customer_id", "date", "amount"} <= set(created["required"])
    assert out["idempotency_key"].startswith("required")
    assert "title" not in created


def test_describe_resolves_nested_schemas(client):
    out = operations.describe_operation(client, "POST", "/api/invoices")
    refs = set(out["schemas"])
    assert "InvoiceCreate" in refs
    # every $ref left in the answer names a schema the answer carries
    text = repr(out)
    for name in re.findall(r"'\$ref': '([^']+)'", text):
        assert name in refs or "schemas_cut" in out


def test_describe_a_concrete_path(client):
    out = operations.describe_operation(client, "GET", "/api/invoices/12")
    assert out["path"] == "/api/invoices/{invoice_id}"
    names = [p["name"] for p in out["parameters"]]
    assert "invoice_id" in names
    assert out["idempotency_key"] == "not taken by this operation"


def test_describe_refusals(client):
    with pytest.raises(ValueError, match="method must be one of"):
        operations.describe_operation(client, "FETCH", "/api/invoices")
    with pytest.raises(ValueError, match="does not take DELETE"):
        operations.describe_operation(client, "DELETE", "/api/invoices")
    with pytest.raises(ValueError, match="Closest: /api/invoices"):
        operations.describe_operation(client, "GET", "/api/invoicez")
    with pytest.raises(ValueError, match="not part of the SlowBooks Pro API"):
        operations.describe_operation(client, "GET", "/health")
    with pytest.raises(ValueError, match="starts with /"):
        operations.describe_operation(client, "GET", "api/invoices")
    with pytest.raises(ValueError, match="query values in params"):
        operations.describe_operation(client, "GET", "/api/invoices?limit=5")


# ---------------------------------------------------------------------------
# api_get and the write tools
# ---------------------------------------------------------------------------


def test_a_get_with_path_and_query(client, spy):
    operations.api_get(client, "/api/customers/{customer_id}", {"customer_id": 5})
    operations.api_get(
        client, "/api/customers", {"active_only": True, "search": "Zephyr"}
    )
    assert spy.sent[0]["path"] == "/api/customers/5"
    assert spy.sent[1]["params"] == {"active_only": "true", "search": "Zephyr"}
    assert spy.sent[1]["body"] is None


def test_call_a_concrete_path(client, spy):
    operations.api_get(client, "/api/invoices/12")
    assert spy.sent[0]["method"] == "GET" and spy.sent[0]["path"] == "/api/invoices/12"


def test_path_values_are_quoted(client, spy):
    operations.api_get(
        client, "/api/customers/{customer_id}", {"customer_id": "1/../tokens"}
    )
    assert spy.sent[0]["path"] == "/api/customers/1%2F..%2Ftokens"


def test_call_refusals_before_anything_is_sent(client, spy):
    with pytest.raises(ValueError, match="has no parameter colour"):
        operations.api_get(client, "/api/customers", {"colour": 1})
    with pytest.raises(ValueError, match="needs customer_id"):
        operations.api_get(client, "/api/customers/{customer_id}")
    with pytest.raises(ValueError, match="takes no request body"):
        operations.post_operation(
            client, "/api/invoices/{invoice_id}/void", {"invoice_id": 1}, {"a": 1}
        )
    with pytest.raises(ValueError, match="idempotency_key must be"):
        operations.post_operation(client, "/api/payments", body={})
    with pytest.raises(ValueError, match="idempotency_key must be"):
        operations.post_operation(
            client, "/api/payments", body={}, idempotency_key="has space"
        )
    with pytest.raises(ValueError, match="must be text, a number"):
        operations.api_get(client, "/api/customers", {"search": {}})
    with pytest.raises(ValueError, match="takes a file upload"):
        operations.post_operation(client, "/api/iif/import")
    assert spy.sent == []


def test_admin_routes_go_to_the_api(client, spy):
    """Match the software: tokens, users, backups, the company file and
    system are sent, and the API's answer is what comes back."""
    for method, path in (
        ("GET", "/api/tokens"),
        ("GET", "/api/users"),
        ("POST", "/api/backups/restore"),
        ("GET", "/api/company-file/encryption"),
        ("GET", "/api/system"),
    ):
        body = {"filename": "x.sql"} if method == "POST" else None
        send(client, method, path, body=body)
        assert spy.sent[-1]["path"] == path


def test_a_file_is_not_returned(client, spy):
    with pytest.raises(ValueError, match="returns a PDF") as exc:
        operations.api_get(client, "/api/reports/profit-loss/pdf")
    assert "JSON from GET /api/reports/profit-loss" in str(exc.value)
    with pytest.raises(ValueError, match="returns a print page"):
        operations.api_get(client, "/api/reports/profit-loss/print")
    assert spy.sent == []


def test_a_payment_carries_its_key(client, spy):
    body = {"customer_id": 1, "date": "2026-09-12", "amount": "40.00"}
    operations.post_operation(client, "/api/payments", body=body, idempotency_key=KEY)
    sent = spy.sent[0]
    assert sent["method"] == "POST" and sent["body"] == body
    assert sent["headers"] == {"Idempotency-Key": KEY}


def test_put_patch_and_delete(client, spy):
    operations.update_operation(
        client, "/api/customers/{customer_id}", {"customer_id": 3}, {"name": "B"}
    )
    deletes = [
        p
        for p, item in client.spec["paths"].items()
        if "delete" in item and reachable(p) and "{" in p
    ]
    target = deletes[0]
    name = re.findall(r"\{([^}]+)\}", target)
    operations.delete_operation(client, target, {n: 9 for n in name})
    operations.update_operation(
        client, "/api/payment-terms/{term_id}", {"term_id": 2}, method="PATCH"
    )
    assert [s["method"] for s in spy.sent] == ["PUT", "DELETE", "PATCH"]
    assert spy.sent[0]["body"] == {"name": "B"}
    assert spy.sent[2]["body"] is None


def test_reads_and_writes_are_separate_tools(client, spy):
    """Each tool sends its own methods; the wrong one names the right tool."""
    with pytest.raises(ValueError, match="A GET is a read: use api_get"):
        operations.update_operation(client, "/api/customers/3", method="GET")
    with pytest.raises(ValueError, match="does not send POST; use post_operation"):
        operations.update_operation(client, "/api/customers", method="POST")
    with pytest.raises(ValueError, match="use delete_operation"):
        operations.update_operation(client, "/api/customers/3", method="DELETE")
    with pytest.raises(ValueError, match=r"takes GET \(api_get\)"):
        operations.post_operation(client, "/api/customers/3")
    with pytest.raises(ValueError, match=r"takes GET \(api_get\)"):
        operations.delete_operation(client, "/api/accounts")
    with pytest.raises(ValueError, match=r"POST \(post_operation\)"):
        operations.update_operation(client, "/api/invoices/12/void")
    with pytest.raises(ValueError, match="takes both PUT"):
        operations.update_operation(client, "/api/payment-terms/2", body={})
    with pytest.raises(ValueError, match=r"takes POST \(post_operation\)"):
        operations.api_get(client, "/api/iif/import")
    assert spy.sent == []


def test_described_operations_name_their_tool(client):
    for method, path, tool in (
        ("GET", "/api/customers", "api_get"),
        ("POST", "/api/customers", "post_operation"),
        ("PUT", "/api/customers/{customer_id}", "update_operation"),
        ("PATCH", "/api/payment-terms/{term_id}", "update_operation"),
    ):
        how = operations.describe_operation(client, method, path)["how_to_call"]
        assert how.startswith(tool), how
    assert (
        "method PATCH"
        in operations.describe_operation(
            client, "PATCH", "/api/payment-terms/{term_id}"
        )["how_to_call"]
    )
    rows = operations.find_operations(client, "customer")
    assert {r["tool"] for r in rows if "tool" in r} >= {"api_get", "post_operation"}


def test_the_apis_refusal_comes_back(client, spy):
    spy.answer = (403, {"detail": "A read-only token cannot change the books."})
    with pytest.raises(ApiError) as exc:
        operations.post_operation(
            client, "/api/customers", body={"name": "A"}, asked_user=True
        )
    assert exc.value.status == 403
    assert exc.value.detail == "A read-only token cannot change the books."


def test_lost_answers_say_what_to_do(client, spy):
    spy.answer = ApiError(0, "SlowBooks Pro did not answer at http://127.0.0.1:1")
    with pytest.raises(ValueError, match="same key and the same data"):
        operations.post_operation(client, "/api/payments", body={}, idempotency_key=KEY)
    with pytest.raises(ValueError, match="before retrying"):
        operations.post_operation(client, "/api/customers", body={})


def test_post_operation_through_the_registry_against_the_fake(api, books):
    reg = build_registry(api)
    out = reg.call(
        "post_operation",
        {"path": "/api/customers", "body": {"name": "Quill Co"}, "asked_user": True},
    )
    assert out["isError"] is False
    out = reg.call(
        "post_operation",
        {"path": "/api/customers", "body": {"nam": "Typo Co"}},
    )
    assert out["isError"] is True
    assert "nam: Extra inputs are not permitted" in out["content"][0]["text"]


# ---------------------------------------------------------------------------
# operations at random, seeded: describe and call build the right request
# ---------------------------------------------------------------------------


def _resolve(spec, schema):
    while isinstance(schema, dict) and "$ref" in schema:
        schema = spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    if isinstance(schema, dict) and "anyOf" in schema:
        options = [s for s in schema["anyOf"] if s.get("type") != "null"]
        return _resolve(spec, options[0]) if options else {}
    return schema or {}


def _sample(spec, schema, rng):
    s = _resolve(spec, schema)
    if "enum" in s:
        return s["enum"][0]
    kind = s.get("type")
    if kind == "integer":
        return rng.randint(1, 999)
    if kind == "number":
        return 12.5
    if kind == "boolean":
        return rng.choice([True, False])
    if kind == "array":
        return [_sample(spec, s.get("items", {}), rng)]
    if s.get("format") == "date":
        return "2026-01-31"
    return "Invented value"


def _expected_query(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return [_expected_query(v) for v in value]
    return value


def _all_operations(spec):
    return [
        (m, p, op)
        for p, item in spec["paths"].items()
        if reachable(p)
        for m, op in item.items()
        if m in operations.METHODS
    ]


@pytest.mark.parametrize("seed", range(8))
def test_random_operations_build_correct_requests(spec, seed):
    rng = random.Random(20261106 + seed)
    for method, template, op in rng.sample(_all_operations(spec), 25):
        spy = Spy(spec)
        client = ApiClient(spy)

        described = operations.describe_operation(client, method, template)
        assert described["method"] == method.upper()
        assert described["path"] == template
        params_doc = [
            operations._resolve_param(client, p) for p in op.get("parameters", [])
        ]
        listed = {p["name"] for p in described.get("parameters", [])}
        assert {p["name"] for p in params_doc if p["in"] in ("path", "query")} <= listed
        assert ("request_body" in described) == ("requestBody" in op)

        params, expected_path, expected_query = {}, template, {}
        for p in params_doc:
            if p["in"] == "path":
                value = _sample(spec, p.get("schema", {}), rng)
                if isinstance(value, (bool, list)) or value == "":
                    value = 7
                params[p["name"]] = value
                expected_path = expected_path.replace(
                    "{" + p["name"] + "}",
                    re.sub(r" ", "%20", str(value)),
                )
            elif p["in"] == "query" and (p.get("required") or rng.random() < 0.3):
                value = _sample(spec, p.get("schema", {}), rng)
                params[p["name"]] = value
                expected_query[p["name"]] = _expected_query(value)
        body = {"memo": "Invented"} if "requestBody" in op else None
        keyed = operations.is_keyed(method, template, op)
        key = KEY if keyed else None
        kind = operations.file_kind(method, template, op)
        content = (op.get("requestBody") or {}).get("content") or {}

        if content and "application/json" not in content:
            with pytest.raises(ValueError, match="takes a file upload"):
                send(client, method, template, params, body, key)
            assert spy.sent == []
            continue
        if kind:
            with pytest.raises(ValueError, match="for a person to open"):
                send(client, method, template, params, body, key)
            assert spy.sent == []
            continue
        send(client, method, template, params, body, key)
        assert len(spy.sent) == 1, (method, template)
        sent = spy.sent[0]
        assert sent["method"] == method.upper()
        assert sent["path"] == expected_path
        assert sent["params"] == expected_query
        assert sent["body"] == body
        assert ("Idempotency-Key" in sent["headers"]) == keyed
        assert described["idempotency_key"].startswith("required") == keyed


def test_the_fixture_is_the_products_document(spec):
    assert spec["info"]["version"] == "2.31.8"
    assert len(_all_operations(spec)) > 700
