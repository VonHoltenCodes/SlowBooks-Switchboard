"""The protocol loop with scripted messages, in both eras.

The handshake era (2025-11-25): the handshake, a revision mismatch, the order
rules, malformed input, unknown methods, notifications, batches, a tool error
and a very large result. The modern era (2026-07-28): server/discover,
tools/list and tools/call with the version in _meta, an unsupported version,
a missing version or capabilities, and sessions that mix the two."""

import io
import json

import pytest

from sbplive import registry as reg_mod
from sbplive.client import ApiClient
from sbplive.protocol import (
    CACHE_SCOPE,
    CACHE_TTL_MS,
    HANDSHAKE_VERSIONS,
    INVALID_PARAMS,
    INVALID_REQUEST,
    META_CAPABILITIES,
    META_CLIENT_INFO,
    META_SERVER_INFO,
    META_VERSION,
    METHOD_NOT_FOUND,
    MODERN_VERSIONS,
    PARSE_ERROR,
    PROTOCOL_VERSION,
    SUPPORTED_VERSIONS,
    UNSUPPORTED_PROTOCOL_VERSION,
    Session,
    encode,
    serve_stdio,
)
from sbplive.server import INSTRUCTIONS, build_registry

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "scripted", "version": "0"},
    },
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def _req(id_, method, params=None):
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


@pytest.fixture
def session(api):
    return Session(build_registry(api), INSTRUCTIONS)


@pytest.fixture
def ready(session):
    assert "result" in session.handle_message(INIT)
    assert session.handle_message(INITIALIZED) is None
    return session


def test_the_revisions_are_listed_once():
    assert PROTOCOL_VERSION == "2025-11-25"  # the answer to initialize
    assert HANDSHAKE_VERSIONS == (PROTOCOL_VERSION,)
    assert MODERN_VERSIONS == ("2026-07-28",)
    assert SUPPORTED_VERSIONS == ("2026-07-28", "2025-11-25")  # newest first
    assert UNSUPPORTED_PROTOCOL_VERSION == -32022


def test_handshake(session):
    reply = session.handle_message(INIT)
    assert reply["id"] == 1 and reply["jsonrpc"] == "2.0"
    result = reply["result"]
    assert result["protocolVersion"] == PROTOCOL_VERSION
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "sbplive"
    assert result["instructions"] == INSTRUCTIONS
    assert session.handle_message(INITIALIZED) is None
    assert session.ready


@pytest.mark.parametrize("asked", ["2024-11-05", "2025-06-18", "2099-01-01", "1.0"])
def test_another_revision_is_offered_the_pinned_one(session, asked):
    msg = json.loads(json.dumps(INIT))
    msg["params"]["protocolVersion"] = asked
    assert session.handle_message(msg)["result"]["protocolVersion"] == (
        PROTOCOL_VERSION
    )


@pytest.mark.parametrize(
    "params", [None, {}, {"protocolVersion": 5}, {"protocolVersion": ""}]
)
def test_initialize_without_a_version_is_invalid_params(session, params):
    reply = session.handle_message(_req(1, "initialize", params))
    assert reply["error"]["code"] == INVALID_PARAMS
    assert not session.initialized


def test_initialize_twice_is_refused(ready):
    reply = ready.handle_message(dict(INIT, id=2))
    assert reply["error"]["code"] == INVALID_REQUEST
    assert "already initialized" in reply["error"]["message"]


@pytest.mark.parametrize("method", ["tools/list", "tools/call", "server/discover"])
def test_requests_with_no_handshake_and_no_version_are_refused(session, method):
    """With neither initialize nor a version in _meta, the only era that could
    serve the request is the modern one, which calls it malformed: -32602."""
    reply = session.handle_message(_req(7, method, {"name": "context"}))
    assert reply["id"] == 7
    assert reply["error"]["code"] == INVALID_PARAMS
    if method != "server/discover":
        assert "send initialize first" in reply["error"]["message"]
    assert META_VERSION in reply["error"]["message"]


def test_ping_works_before_and_after_initialize(session):
    assert session.handle_message(_req("p1", "ping")) == {
        "jsonrpc": "2.0",
        "id": "p1",
        "result": {},
    }
    session.handle_message(INIT)
    assert session.handle_message(_req("p2", "ping"))["result"] == {}


def test_a_discover_without_a_version_then_the_handshake(session):
    """An old-style probe with no _meta is not a modern request: -32602, never
    -32022, and the handshake that follows works as before."""
    reply = session.handle_message(_req(1, "server/discover"))
    assert reply["error"]["code"] == INVALID_PARAMS
    assert "result" in session.handle_message(INIT)


def test_unknown_method(ready):
    reply = ready.handle_message(_req(3, "resources/list"))
    assert reply == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {
            "code": METHOD_NOT_FOUND,
            "message": "Method not found: resources/list",
        },
    }


@pytest.mark.parametrize(
    "method", ["notifications/cancelled", "notifications/whatever", "tools/list"]
)
def test_notifications_are_never_answered(ready, method):
    assert ready.handle_message({"jsonrpc": "2.0", "method": method}) is None


def test_responses_from_the_client_are_ignored(ready):
    assert ready.handle_message({"jsonrpc": "2.0", "id": 9, "result": {}}) is None


@pytest.mark.parametrize(
    "line, code",
    [
        ("{not json", PARSE_ERROR),
        (b"\xff\xfe{}", PARSE_ERROR),
        ("42", INVALID_REQUEST),
        ('"text"', INVALID_REQUEST),
        ('{"id": 1, "method": "ping"}', INVALID_REQUEST),  # no jsonrpc
        ('{"jsonrpc": "1.0", "id": 1, "method": "ping"}', INVALID_REQUEST),
        ('{"jsonrpc": "2.0", "id": null, "method": "ping"}', INVALID_REQUEST),
        ('{"jsonrpc": "2.0", "id": true, "method": "ping"}', INVALID_REQUEST),
        ('{"jsonrpc": "2.0", "id": 1.5, "method": "ping"}', INVALID_REQUEST),
        ('{"jsonrpc": "2.0", "id": 1, "method": 5}', INVALID_REQUEST),
        ('{"jsonrpc": "2.0", "id": 1}', INVALID_REQUEST),
        (
            '{"jsonrpc": "2.0", "id": 1, "method": "ping", "params": [1]}',
            INVALID_PARAMS,
        ),
    ],
)
def test_malformed_input(session, line, code):
    reply = session.handle_line(line)
    assert reply["error"]["code"] == code
    assert reply["error"]["message"]


def test_malformed_reply_keeps_a_readable_id(session):
    reply = session.handle_line('{"jsonrpc": "1.0", "id": 4, "method": "ping"}')
    assert reply["id"] == 4
    reply = session.handle_line("{nope")
    assert reply["id"] is None


def test_blank_lines_are_skipped(session):
    assert session.handle_line("\n") is None
    assert session.handle_line("   ") is None


def test_batches_are_refused_in_this_revision(ready):
    batch = json.dumps([_req(1, "ping"), _req(2, "ping")])
    reply = ready.handle_line(batch)
    assert reply["id"] is None
    assert reply["error"]["code"] == INVALID_REQUEST
    assert "batches" in reply["error"]["message"]


def test_tools_list(ready):
    tools = ready.handle_message(_req(2, "tools/list"))["result"]["tools"]
    assert "nextCursor" not in tools
    names = [t["name"] for t in tools]
    assert len(names) == len(set(names)) == 21
    for name in ("find_operations", "describe_operation", "call_operation"):
        assert name in names
    for t in tools:
        assert t["description"] and t["title"]
        assert t["inputSchema"]["type"] == "object"
        assert t["inputSchema"]["additionalProperties"] is False
        assert set(t["annotations"]) >= {
            "readOnlyHint",
            "destructiveHint",
            "idempotentHint",
            "openWorldHint",
        }
        for prop in t["inputSchema"]["properties"].values():
            assert prop.get("description"), (t["name"], prop)


def test_tools_list_with_an_unknown_cursor(ready):
    reply = ready.handle_message(_req(2, "tools/list", {"cursor": "abc"}))
    assert reply["error"]["code"] == INVALID_PARAMS


def test_tools_call_read(ready):
    reply = ready.handle_message(_req(3, "tools/call", {"name": "context"}))
    result = reply["result"]
    assert result["isError"] is False
    data = json.loads(result["content"][0]["text"])
    assert data["company"] == "Acme Fence Test Co"
    assert ", " not in result["content"][0]["text"]  # compact JSON


def test_tools_call_unknown_tool_is_a_protocol_error(ready):
    reply = ready.handle_message(_req(4, "tools/call", {"name": "nope"}))
    assert reply["error"] == {"code": INVALID_PARAMS, "message": "Unknown tool: nope"}


@pytest.mark.parametrize(
    "params", [{}, {"name": 5}, {"name": "context", "arguments": []}]
)
def test_tools_call_malformed_is_a_protocol_error(ready, params):
    reply = ready.handle_message(_req(4, "tools/call", params))
    assert reply["error"]["code"] == INVALID_PARAMS


@pytest.mark.parametrize(
    "args, words",
    [
        ({}, "needs kind"),
        ({"kind": 5}, "kind must be text"),
        ({"kind": "customers", "limit": "5"}, "limit must be a whole number"),
        ({"kind": "customers", "limit": True}, "limit must be a whole number"),
        ({"kind": "customers", "colour": "red"}, "does not take colour"),
    ],
)
def test_bad_arguments_are_a_tool_error_with_a_sentence(ready, args, words):
    reply = ready.handle_message(
        _req(5, "tools/call", {"name": "list_records", "arguments": args})
    )
    result = reply["result"]
    assert result["isError"] is True
    assert words in result["content"][0]["text"]


def test_api_refusal_is_passed_on_as_a_tool_error(ready, api):
    reply = ready.handle_message(
        _req(
            6,
            "tools/call",
            {
                "name": "create_customer",
                "arguments": {"data": {"name": "X", "bogus_field": 1}},
            },
        )
    )
    text = reply["result"]["content"][0]["text"]
    assert reply["result"]["isError"] is True
    assert text == (
        "SlowBooks Pro did not accept the data: "
        "bogus_field: Extra inputs are not permitted."
    )


def test_app_not_running_is_one_sentence():
    def down(*a):
        from sbplive.client import ApiError

        raise ApiError(0, "SlowBooks Pro did not answer at http://127.0.0.1:1")

    session = Session(build_registry(ApiClient(down)))
    session.handle_message(INIT)
    result = session.handle_message(_req(1, "tools/call", {"name": "context"}))[
        "result"
    ]
    assert result["isError"] is True
    assert result["content"][0]["text"] == (
        "SlowBooks Pro did not answer at http://127.0.0.1:1. "
        "Open SlowBooks Pro, then try again."
    )


def test_a_bug_in_a_tool_is_a_tool_error_not_a_crash(ready, monkeypatch):
    def boom(client):
        raise KeyError("x")

    monkeypatch.setattr(ready.registry["context"], "fn", boom)
    result = ready.handle_message(_req(1, "tools/call", {"name": "context"}))["result"]
    assert result["isError"] is True
    assert "internal error running context" in result["content"][0]["text"]
    assert ready.handle_message(_req(2, "ping"))["result"] == {}


def test_a_very_large_list_is_cut_to_whole_records(ready, books, monkeypatch):
    monkeypatch.setattr(reg_mod, "MAX_RESULT_CHARS", 2000)
    for n in range(200):
        books.records["/api/customers"][10_000 + n] = {
            "id": 10_000 + n,
            "name": f"Invented Customer {n:03d}",
        }
    result = ready.handle_message(
        _req(
            1,
            "tools/call",
            {"name": "list_records", "arguments": {"kind": "customers", "limit": 500}},
        )
    )["result"]
    assert result["isError"] is False
    first, note = result["content"]
    rows = json.loads(first["text"])  # still valid JSON
    assert 0 < len(rows) < 200 and len(first["text"]) <= 2000
    assert f"first {len(rows)} of 200 records" in note["text"]
    assert "Narrow it" in note["text"]


def test_a_very_large_object_is_cut_with_a_sentence(ready, monkeypatch):
    monkeypatch.setattr(reg_mod, "MAX_RESULT_CHARS", 500)
    result = ready.handle_message(
        _req(
            1,
            "tools/call",
            {"name": "api_schema", "arguments": {"path": "/api/invoices"}},
        )
    )["result"]
    first, note = result["content"]
    assert len(first["text"]) == 500
    assert "cut at 500 of" in note["text"] and "Narrow it" in note["text"]


def test_encode_is_one_line():
    line = encode({"jsonrpc": "2.0", "id": 1, "result": {"text": "a\nb c"}})
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    assert json.loads(line)["result"]["text"] == "a\nb c"


def test_serve_stdio_end_to_end(session):
    lines = [
        json.dumps(INIT),
        json.dumps(INITIALIZED),
        "",
        json.dumps(_req(2, "tools/list")),
        "garbage",
        json.dumps(_req(3, "tools/call", {"name": "list_reports"})),
    ]
    stdin = io.BytesIO(("\n".join(lines) + "\n").encode())
    stdout = io.BytesIO()
    assert serve_stdio(session, stdin, stdout) == 0
    replies = [json.loads(x) for x in stdout.getvalue().splitlines()]
    assert [r.get("id") for r in replies] == [1, 2, None, 3]
    assert replies[2]["error"]["code"] == PARSE_ERROR
    assert replies[3]["result"]["isError"] is False


# -- the modern era (2026-07-28) ---------------------------------------------

MODERN = MODERN_VERSIONS[0]


def _meta(version=MODERN, **extra):
    meta = {
        META_VERSION: version,
        META_CAPABILITIES: {},
        META_CLIENT_INFO: {"name": "scripted-modern", "version": "0"},
    }
    meta.update(extra)
    return meta


def _modern(id_, method, params=None, meta=None):
    params = dict(params or {})
    params["_meta"] = _meta() if meta is None else meta
    return _req(id_, method, params)


def _assert_modern_result(reply):
    result = reply["result"]
    assert result["resultType"] == "complete"
    assert result["_meta"][META_SERVER_INFO]["name"] == "sbplive"
    return result


def test_discover(session):
    reply = session.handle_message(_modern("d1", "server/discover"))
    assert reply["id"] == "d1"
    result = _assert_modern_result(reply)
    assert result["supportedVersions"] == list(SUPPORTED_VERSIONS)
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["instructions"] == INSTRUCTIONS
    assert result["ttlMs"] == CACHE_TTL_MS and result["cacheScope"] == CACHE_SCOPE
    assert not session.initialized  # stateless: nothing was opened


def test_modern_tools_list_matches_the_handshake_list(session, api):
    modern = _assert_modern_result(session.handle_message(_modern(2, "tools/list")))
    other = Session(build_registry(api), INSTRUCTIONS)
    other.handle_message(INIT)
    legacy = other.handle_message(_req(2, "tools/list"))["result"]
    assert modern["tools"] == legacy["tools"]
    assert modern["ttlMs"] == CACHE_TTL_MS and modern["cacheScope"] == "public"
    assert "resultType" not in legacy and "ttlMs" not in legacy


def test_modern_tools_call_read_and_write(session, books):
    reply = session.handle_message(_modern(3, "tools/call", {"name": "context"}))
    result = _assert_modern_result(reply)
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"])["company"] == ("Acme Fence Test Co")
    assert "ttlMs" not in result  # a call is not cacheable
    reply = session.handle_message(
        _modern(
            4,
            "tools/call",
            {
                "name": "create_customer",
                "arguments": {"data": {"name": "Modern Era Co"}},
            },
        )
    )
    made = json.loads(_assert_modern_result(reply)["content"][0]["text"])
    assert made["name"] == "Modern Era Co"


def test_modern_tool_error_is_still_a_result(session):
    reply = session.handle_message(
        _modern(5, "tools/call", {"name": "list_records", "arguments": {}})
    )
    result = _assert_modern_result(reply)
    assert result["isError"] is True and "needs kind" in result["content"][0]["text"]


def test_modern_unknown_tool_is_a_protocol_error(session):
    reply = session.handle_message(_modern(6, "tools/call", {"name": "nope"}))
    assert reply["error"] == {"code": INVALID_PARAMS, "message": "Unknown tool: nope"}


@pytest.mark.parametrize("asked", ["1900-01-01", "2099-01-01", "2025-06-18"])
def test_unsupported_version(session, asked):
    reply = session.handle_message(_modern(7, "tools/list", meta=_meta(asked)))
    assert reply["id"] == 7
    err = reply["error"]
    assert err["code"] == UNSUPPORTED_PROTOCOL_VERSION
    assert err["data"] == {"supported": list(SUPPORTED_VERSIONS), "requested": asked}
    assert err["message"].startswith("Unsupported protocol version")


def test_the_handshake_revision_per_request_is_unsupported(session):
    """2025-11-25 is spoken, but only through initialize: per request it is
    -32022, whose supported list sends a dual-era client to the handshake."""
    reply = session.handle_message(
        _modern(8, "server/discover", meta=_meta(PROTOCOL_VERSION))
    )
    assert reply["error"]["code"] == UNSUPPORTED_PROTOCOL_VERSION
    assert "through initialize" in reply["error"]["message"]
    assert PROTOCOL_VERSION in reply["error"]["data"]["supported"]


@pytest.mark.parametrize(
    "meta, words",
    [
        ({META_VERSION: MODERN}, META_CAPABILITIES),  # capabilities missing
        ({META_VERSION: MODERN, META_CAPABILITIES: []}, META_CAPABILITIES),
        ({META_VERSION: 20260728, META_CAPABILITIES: {}}, "version string"),
        ({META_VERSION: "", META_CAPABILITIES: {}}, "version string"),
        (
            {META_VERSION: MODERN, META_CAPABILITIES: {}, META_CLIENT_INFO: "x"},
            META_CLIENT_INFO,
        ),
        ("not an object", "_meta is an object"),
    ],
)
def test_malformed_modern_meta_is_invalid_params(session, meta, words):
    reply = session.handle_message(_modern(9, "tools/list", meta=meta))
    assert reply["error"]["code"] == INVALID_PARAMS
    assert words in reply["error"]["message"]


def test_missing_version_is_invalid_params_not_unsupported(session):
    meta = _meta()
    del meta[META_VERSION]
    reply = session.handle_message(_modern(10, "tools/call", {"name": "context"}, meta))
    assert reply["error"]["code"] == INVALID_PARAMS


@pytest.mark.parametrize("method", ["ping", "resources/list", "subscriptions/listen"])
def test_methods_not_in_the_modern_era(session, method):
    """ping was removed in 2026-07-28; the others are not offered."""
    reply = session.handle_message(_modern(11, method))
    assert reply["error"] == {
        "code": METHOD_NOT_FOUND,
        "message": f"Method not found: {method}",
    }


def test_unsupported_version_is_checked_before_the_method(session):
    reply = session.handle_message(_modern(12, "nope/nope", meta=_meta("1900-01-01")))
    assert reply["error"]["code"] == UNSUPPORTED_PROTOCOL_VERSION


def test_initialize_with_modern_meta_is_still_the_handshake(session):
    """initialize is not a modern method: whatever its _meta, it opens the
    handshake era for the process."""
    msg = json.loads(json.dumps(INIT))
    msg["params"]["_meta"] = _meta()
    assert session.handle_message(msg)["result"]["protocolVersion"] == (
        PROTOCOL_VERSION
    )
    assert session.initialized


def test_legacy_meta_does_not_make_a_request_modern(ready):
    """A handshake-era request may carry _meta (a progress token): with no
    version in it, it is served as 2025-11-25 and carries no resultType."""
    reply = ready.handle_message(
        _req(13, "tools/call", {"name": "context", "_meta": {"progressToken": 1}})
    )
    assert reply["result"]["isError"] is False
    assert "resultType" not in reply["result"]


def test_mixed_session_modern_first_then_the_handshake(session):
    """A dual-era client that probed, or a modern request, then initialize:
    the handshake still opens, and modern requests keep working after it."""
    assert "result" in session.handle_message(_modern(1, "server/discover"))
    assert "result" in session.handle_message(_modern(2, "tools/list"))
    reply = session.handle_message(dict(INIT, id=3))
    assert reply["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert session.handle_message(INITIALIZED) is None
    legacy = session.handle_message(_req(4, "tools/list"))["result"]
    assert "resultType" not in legacy
    _assert_modern_result(session.handle_message(_modern(5, "tools/list")))
    # discover with no version is malformed whatever the handshake state
    reply = session.handle_message(_req(6, "server/discover"))
    assert reply["error"]["code"] == INVALID_PARAMS


def test_mixed_session_handshake_first_then_modern(ready):
    """After initialize, a request with a version in _meta is served by its
    own _meta alone, statelessly; the handshake stays open beside it."""
    _assert_modern_result(ready.handle_message(_modern(2, "server/discover")))
    result = _assert_modern_result(
        ready.handle_message(_modern(3, "tools/call", {"name": "context"}))
    )
    assert result["isError"] is False
    # an unsupported version is refused per request, even in a handshake
    reply = ready.handle_message(_modern(4, "tools/list", meta=_meta("1900-01-01")))
    assert reply["error"]["code"] == UNSUPPORTED_PROTOCOL_VERSION
    # and the handshake session is untouched by any of it
    assert ready.initialized and ready.ready
    assert ready.handle_message(_req(5, "ping"))["result"] == {}
    assert "resultType" not in ready.handle_message(_req(6, "tools/list"))["result"]
    # a second initialize is still refused
    reply = ready.handle_message(dict(INIT, id=7))
    assert reply["error"]["code"] == INVALID_REQUEST


def test_modern_session_over_stdio(session):
    lines = [
        json.dumps(_modern(1, "server/discover")),
        json.dumps(_modern(2, "tools/list")),
        json.dumps(_modern(3, "tools/call", {"name": "list_reports"})),
        json.dumps(_modern(4, "tools/list", meta=_meta("1900-01-01"))),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/cancelled",
                    "params": {"requestId": 3}}),
    ]  # fmt: skip
    stdin = io.BytesIO(("\n".join(lines) + "\n").encode())
    stdout = io.BytesIO()
    assert serve_stdio(session, stdin, stdout) == 0
    replies = [json.loads(x) for x in stdout.getvalue().splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3, 4]
    assert [r.get("result", {}).get("resultType") for r in replies] == [
        "complete",
        "complete",
        "complete",
        None,
    ]
    assert replies[3]["error"]["code"] == UNSUPPORTED_PROTOCOL_VERSION
