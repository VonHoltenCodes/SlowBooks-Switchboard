"""The remote door (serve-http), end to end over a real socket on 127.0.0.1
and an ephemeral port, with the fake app: both eras, a tool call, the
connector secret (missing, wrong, the failure count), the Origin rules,
every other path 404, a large result and requests at the same time. Plus
the pieces on their own: the secret store, the failure count, the sessions.

Every secret here is fake (SECRET_FAKE); every address is 127.0.0.1."""

import http.client
import json
import os
import socket
import stat
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fakeapi import TOKEN_FAKE

from sbplive import logs, registry, remote, store
from sbplive.client import ApiClient
from sbplive.config import ConfigError
from sbplive.protocol import (
    META_CAPABILITIES,
    META_VERSION,
    MODERN_VERSIONS,
    PROTOCOL_VERSION,
    SUPPORTED_VERSIONS,
)
from sbplive.server import INSTRUCTIONS, build_registry

SECRET_FAKE = "sbc_test_" + "1" * 40
OTHER_SECRET_FAKE = "sbc_test_" + "2" * 40
MODERN = MODERN_VERSIONS[0]
META = {META_VERSION: MODERN, META_CAPABILITIES: {}}


class Running:
    """A Door behind the real socket server, in a thread."""

    def __init__(self, books, **door_args):
        reg = build_registry(ApiClient(books.transport))
        self.door = remote.Door(reg, INSTRUCTIONS, SECRET_FAKE, **door_args)
        self.httpd = remote.make_server(self.door, "127.0.0.1", 0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def request(self, method, path="/mcp", body=None, headers=None, auth=True):
        hdrs = {"Accept": "application/json, text/event-stream"}
        if body is not None:
            hdrs["Content-Type"] = "application/json"
        if auth:
            hdrs["Authorization"] = f"Bearer {SECRET_FAKE}"
        hdrs.update(headers or {})
        hdrs = {k: v for k, v in hdrs.items() if v is not None}
        raw = None
        if body is not None:
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.request(method, path, body=raw, headers=hdrs)
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, dict(resp.getheaders()), data
        finally:
            conn.close()

    def rpc(self, message, headers=None, **kw):
        status, hdrs, data = self.request("POST", body=message, headers=headers, **kw)
        return status, hdrs, (json.loads(data) if data else None)

    def modern(self, id_, method, params=None, headers=None):
        params = dict(params or {})
        params["_meta"] = dict(META)
        hdrs = {"MCP-Protocol-Version": MODERN, "Mcp-Method": method}
        if method == "tools/call":
            hdrs["Mcp-Name"] = params.get("name")
        hdrs.update(headers or {})
        msg = {"jsonrpc": "2.0", "id": id_, "method": method, "params": params}
        return self.rpc(msg, hdrs)


@pytest.fixture
def door(books):
    running = Running(books)
    yield running
    running.close()


def _init(id_=1):
    return {
        "jsonrpc": "2.0",
        "id": id_,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    }


def _req(id_, method, params=None):
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def _handshake(door):
    status, hdrs, _reply = door.rpc(_init())
    assert status == 200
    sid = hdrs["Mcp-Session-Id"]
    status, _, body = door.request(
        "POST",
        body={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Mcp-Session-Id": sid, "MCP-Protocol-Version": PROTOCOL_VERSION},
    )
    assert status == 202 and body == b""
    return sid


# -- 2026-07-28 ------------------------------------------------------------------


def test_modern_discover_list_and_call(door):
    status, hdrs, reply = door.modern(1, "server/discover")
    assert status == 200
    assert hdrs["Content-Type"] == "application/json"
    assert "Mcp-Session-Id" not in hdrs  # stateless: no session minted
    result = reply["result"]
    assert result["supportedVersions"] == list(SUPPORTED_VERSIONS)
    assert result["resultType"] == "complete"

    status, _, reply = door.modern(2, "tools/list")
    assert status == 200 and len(reply["result"]["tools"]) == 21
    assert reply["result"]["cacheScope"] == "public"

    status, _, reply = door.modern(
        3, "tools/call", {"name": "context", "arguments": {}}
    )
    assert status == 200
    ctx = json.loads(reply["result"]["content"][0]["text"])
    assert ctx["company"] == "Acme Fence Test Co"


def test_modern_write_through_the_door(door, books):
    status, _, reply = door.modern(
        1,
        "tools/call",
        {"name": "create_customer", "arguments": {"data": {"name": "Remote Co"}}},
    )
    assert status == 200 and reply["result"]["isError"] is False
    assert any(
        r["name"] == "Remote Co" for r in books.records["/api/customers"].values()
    )


def test_modern_session_header_is_ignored(door):
    status, hdrs, _reply = door.modern(
        1, "tools/list", headers={"Mcp-Session-Id": "nonsense"}
    )
    assert status == 200 and "Mcp-Session-Id" not in hdrs


@pytest.mark.parametrize(
    "headers, what",
    [
        ({"MCP-Protocol-Version": None}, "MCP-Protocol-Version header is missing"),
        ({"MCP-Protocol-Version": "2025-11-25"}, "does not match the body's version"),
        ({"Mcp-Method": None}, "Mcp-Method header is missing"),
        ({"Mcp-Method": "tools/list"}, "does not match the body's method"),
        ({"Mcp-Name": None}, "Mcp-Name header is missing"),
        ({"Mcp-Name": "search"}, "does not match the body's name"),
        ({"Mcp-Name": "=?base64?not base64?="}, "does not match the body's name"),
    ],
)
def test_modern_header_mismatch_is_400_32020(door, headers, what):
    status, _, reply = door.modern(
        7, "tools/call", {"name": "context", "arguments": {}}, headers=headers
    )
    assert status == 400
    assert reply["id"] == 7
    assert reply["error"]["code"] == -32020
    assert what in reply["error"]["message"]


def test_modern_mcp_name_may_be_base64(door):
    status, _, reply = door.modern(
        1,
        "tools/call",
        {"name": "context", "arguments": {}},
        headers={"Mcp-Name": "=?base64?Y29udGV4dA==?="},  # "context"
    )
    assert status == 200 and reply["result"]["isError"] is False


def test_modern_unsupported_version_is_400_32022(door):
    msg = _req(
        1, "tools/list", {"_meta": {META_VERSION: "1900-01-01", META_CAPABILITIES: {}}}
    )
    status, _, reply = door.rpc(
        msg, {"MCP-Protocol-Version": "1900-01-01", "Mcp-Method": "tools/list"}
    )
    assert status == 400
    assert reply["error"]["code"] == -32022
    assert reply["error"]["data"] == {
        "supported": list(SUPPORTED_VERSIONS),
        "requested": "1900-01-01",
    }


def test_modern_unknown_method_is_404_32601(door):
    status, _, reply = door.modern(1, "ping")  # gone in 2026-07-28
    assert status == 404 and reply["error"]["code"] == -32601


def test_modern_header_without_modern_body_is_400(door):
    status, _, reply = door.rpc(
        _req(1, "tools/list"),
        {"MCP-Protocol-Version": MODERN, "Mcp-Method": "tools/list"},
    )
    assert status == 400 and reply["error"]["code"] == -32602


# -- 2025-11-25 --------------------------------------------------------------------


def test_handshake_session_list_call_and_delete(door, books):
    sid = _handshake(door)
    hdrs = {"Mcp-Session-Id": sid, "MCP-Protocol-Version": PROTOCOL_VERSION}
    status, _, reply = door.rpc(_req(2, "tools/list"), hdrs)
    assert status == 200 and len(reply["result"]["tools"]) == 21
    assert "resultType" not in reply["result"]  # the handshake era's shape
    status, _, reply = door.rpc(
        _req(
            3,
            "tools/call",
            {"name": "create_vendor", "arguments": {"data": {"name": "Legacy Supply"}}},
        ),
        hdrs,
    )
    assert status == 200 and reply["result"]["isError"] is False
    status, _, reply = door.rpc(_req(4, "ping"), hdrs)
    assert status == 200 and reply["result"] == {}
    # an unknown tool in a session is a JSON-RPC error at 200, as before
    status, _, reply = door.rpc(_req(5, "tools/call", {"name": "nope"}), hdrs)
    assert status == 200 and reply["error"]["code"] == -32602

    status, _, _ = door.request("DELETE", headers={"Mcp-Session-Id": sid})
    assert status == 200
    status, _, reply = door.rpc(_req(6, "tools/list"), hdrs)
    assert status == 404 and reply["id"] == 6
    status, _, _ = door.request("DELETE", headers={"Mcp-Session-Id": sid})
    assert status == 404


def test_handshake_initialize_answer_and_session_header(door):
    status, hdrs, reply = door.rpc(_init())
    assert status == 200
    assert reply["result"]["protocolVersion"] == PROTOCOL_VERSION
    sid = hdrs["Mcp-Session-Id"]
    assert len(sid) >= 32 and all(0x21 <= ord(c) <= 0x7E for c in sid)
    _, hdrs2, _ = door.rpc(_init())
    assert hdrs2["Mcp-Session-Id"] != sid  # one session per initialize


def test_handshake_without_session_is_400_and_unknown_session_404(door):
    status, _, reply = door.rpc(_req(1, "tools/list"))
    assert status == 400 and reply["error"]["code"] == -32602
    assert "initialize" in reply["error"]["message"]
    status, _, reply = door.rpc(_req(2, "ping"))  # ping needs no session
    assert status == 200 and reply["result"] == {}
    status, _, reply = door.rpc(_req(3, "tools/list"), {"Mcp-Session-Id": "gone"})
    assert status == 404


def test_handshake_wrong_version_header_is_400(door):
    sid = _handshake(door)
    status, _, reply = door.rpc(
        _req(1, "tools/list"),
        {"Mcp-Session-Id": sid, "MCP-Protocol-Version": "2024-11-05"},
    )
    assert status == 400 and reply["error"]["code"] == -32600


def test_bad_initialize_opens_no_session(door):
    msg = _init()
    del msg["params"]["protocolVersion"]
    status, hdrs, _reply = door.rpc(msg)
    assert status == 400 and "Mcp-Session-Id" not in hdrs
    assert len(door.door.sessions) == 0


def test_notification_for_unknown_session_is_404_else_202(door):
    note = {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {}}
    assert door.request("POST", body=note)[0] == 202
    assert door.request("POST", body=note, headers={"Mcp-Session-Id": "x"})[0] == 404
    response = {"jsonrpc": "2.0", "id": 9, "result": {}}
    assert door.request("POST", body=response)[0] == 202


# -- the HTTP layer -----------------------------------------------------------------


def test_get_and_other_methods_are_405(door):
    for method in ("GET", "PUT", "PATCH", "OPTIONS"):
        status, hdrs, _body = door.request(method)
        assert status == 405, method
        assert hdrs["Allow"] == "POST, DELETE"
    status, hdrs, _ = door.request("DELETE")  # no session to end
    assert status == 405


def test_parse_errors_batches_and_media_types(door):
    status, _, body = door.request("POST", body=b"{not json")
    assert status == 400 and json.loads(body)["error"]["code"] == -32700
    status, _, body = door.request("POST", body=[_init()])
    assert status == 400 and json.loads(body)["error"]["code"] == -32600
    status, _, _ = door.request(
        "POST", body=_init(), headers={"Content-Type": "text/plain"}
    )
    assert status == 415
    status, _, _ = door.request("POST", body=_init(), headers={"Accept": "text/html"})
    assert status == 406
    status, _hdrs, _ = door.request("POST", body=_init(), headers={"Accept": None})
    assert status == 200  # no Accept means anything


def test_too_large_a_body_is_413(door, monkeypatch):
    monkeypatch.setattr(remote, "MAX_BODY", 1000)
    status, _, _ = door.request("POST", body=b"{" + b" " * 5000 + b"}")
    assert status == 413


def _raw(port, data: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=30) as s:
        s.sendall(data)
        chunks = []
        while True:
            got = s.recv(65536)
            if not got:
                break
            chunks.append(got)
        return b"".join(chunks)


def test_a_chunked_body_is_read_and_no_length_is_411(door):
    body = json.dumps(_init()).encode()
    head = (
        "POST /mcp HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
        f"Authorization: Bearer {SECRET_FAKE}\r\nConnection: close\r\n"
    ).encode()
    chunked = head + b"Transfer-Encoding: chunked\r\n\r\n"
    chunked += b"%x\r\n" % 10 + body[:10] + b"\r\n"
    chunked += b"%x\r\n" % (len(body) - 10) + body[10:] + b"\r\n0\r\n\r\n"
    answer = _raw(door.port, chunked)
    assert answer.startswith(b"HTTP/1.1 200")
    assert b"Mcp-Session-Id" in answer
    answer = _raw(door.port, head + b"\r\n")
    assert answer.startswith(b"HTTP/1.1 411")


def test_server_header_names_nothing_of_python(door):
    _, hdrs, _ = door.request("GET")
    assert "Python" not in hdrs.get("Server", "")
    assert hdrs["Cache-Control"] == "no-store"


# -- only the endpoint ----------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/index.html",
        "/api/settings",
        "/api/customers",
        "/openapi.json",
        "/health",
        "/.well-known/oauth-protected-resource",
        "/mcp/extra",
        "/mcpx",
        "/MCP",
        "/../mcp",
    ],
)
def test_every_other_path_is_404_even_signed_in(door, books, path):
    calls = len(books.calls)
    for method in ("GET", "POST"):
        status, _, body = door.request(method, path, body=_init())
        assert status == 404 and body == b""
    assert len(books.calls) == calls  # nothing reached the app


def test_endpoint_accepts_a_trailing_slash_and_a_query(door):
    assert door.request("POST", "/mcp/", body=_init())[0] == 200
    assert door.request("POST", "/mcp?x=1", body=_init())[0] == 200


# -- sign-in ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "Bearer",
        f"Bearer {OTHER_SECRET_FAKE}",
        f"Bearer {SECRET_FAKE}x",
        f"Bearer {SECRET_FAKE[:-1]}",
        f"Basic {SECRET_FAKE}",
        SECRET_FAKE,
        f"Bearer {TOKEN_FAKE}",  # the API token is not the connector secret
    ],
)
def test_wrong_or_missing_secret_is_401_with_no_detail(door, value):
    status, hdrs, body = door.request(
        "POST", body=_init(), headers={"Authorization": value}, auth=False
    )
    assert status == 401
    assert body == b""
    assert hdrs["WWW-Authenticate"] == "Bearer"
    assert "Mcp-Session-Id" not in hdrs


def test_bearer_scheme_is_case_insensitive(door):
    status, _, _ = door.request(
        "POST", body=_init(), headers={"Authorization": f"bearer {SECRET_FAKE}"}
    )
    assert status == 200


def test_get_without_secret_is_401_not_405(door):
    assert door.request("GET", auth=False)[0] == 401


def test_failures_from_one_address_are_limited(door):
    wrong = {"Authorization": f"Bearer {OTHER_SECRET_FAKE}"}
    for _ in range(remote.MAX_FAILURES):
        assert door.request("POST", body=_init(), headers=wrong, auth=False)[0] == 401
    status, hdrs, _ = door.request("POST", body=_init(), headers=wrong, auth=False)
    assert status == 429 and int(hdrs["Retry-After"]) > 0
    # locked out: the right secret is refused too, from that address
    assert door.request("POST", body=_init())[0] == 429
    # another visitor arriving through cloudflared (which connects from
    # this computer and names the visitor in CF-Connecting-IP) is not
    status, _, _ = door.request(
        "POST", body=_init(), headers={"CF-Connecting-IP": "203.0.113.9"}
    )
    assert status == 200


def test_cf_connecting_ip_is_believed_only_from_this_computer():
    headers = _Headers({"CF-Connecting-IP": "203.0.113.9"})
    assert remote.client_address("127.0.0.1", headers) == "203.0.113.9"
    assert remote.client_address("::1", headers) == "203.0.113.9"
    assert remote.client_address("192.168.1.50", headers) == "192.168.1.50"
    bad = _Headers({"CF-Connecting-IP": "not an address"})
    assert remote.client_address("127.0.0.1", bad) == "127.0.0.1"


class _Headers(dict):
    def get_all(self, name, default=None):
        return [self[name]] if name in self else default


def test_failure_limit_window_and_success():
    now = [1000.0]
    limit = remote.FailureLimit(limit=3, window=60, clock=lambda: now[0])
    for _ in range(3):
        assert limit.wait("a") == 0
        limit.failed("a")
    assert limit.wait("a") == 60
    assert limit.wait("b") == 0
    now[0] += 59
    assert 0 < limit.wait("a") <= 1
    now[0] += 1
    assert limit.wait("a") == 0  # the oldest failure left the window
    limit.failed("b")
    limit.succeeded("b")
    assert limit.wait("b") == 0


def test_failure_limit_forgets_the_oldest_addresses():
    limit = remote.FailureLimit(limit=1, window=60, max_tracked=2)
    for addr in ("a", "b", "c"):
        limit.failed(addr)
    assert limit.wait("a") == 0 and limit.wait("c") > 0


# -- Origin -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "origin, allowed",
    [
        (None, True),
        ("http://localhost:5173", True),
        ("http://127.0.0.1", True),
        ("http://[::1]:8080", True),
        ("https://claude.ai", True),
        ("https://claude.com", True),
        ("https://evil.example", False),
        ("https://claude.ai.evil.example", False),
        ("http://localhost.evil.example", False),
        ("null", False),
        ("file://", False),
    ],
)
def test_origin_rules(door, origin, allowed):
    status, _, body = door.request("POST", body=_init(), headers={"Origin": origin})
    if allowed:
        assert status == 200
    else:
        assert status == 403
        reply = json.loads(body)
        assert reply["id"] is None and reply["error"]["code"] == -32600


def test_allow_origin_adds_one(books):
    running = Running(
        books, allowed_origins=(remote.check_origin_arg("https://Books.Example.com/"),)
    )
    try:
        ok = running.request(
            "POST", body=_init(), headers={"Origin": "https://books.example.com"}
        )
        assert ok[0] == 200
        no = running.request(
            "POST", body=_init(), headers={"Origin": "https://other.example.com"}
        )
        assert no[0] == 403
    finally:
        running.close()


@pytest.mark.parametrize(
    "raw", ["books.example.com", "https://x.example/path", "ftp://x"]
)
def test_allow_origin_must_be_an_origin(raw):
    with pytest.raises(ConfigError):
        remote.check_origin_arg(raw)


# -- large and concurrent ------------------------------------------------------------------


def test_a_large_result_arrives_whole(door, books):
    for n in range(600):
        books.records["/api/customers"][20_000 + n] = {
            "id": 20_000 + n,
            "name": f"Invented Customer {n:04d} " + "x" * 80,
        }
    status, _hdrs, reply = door.modern(
        1,
        "tools/call",
        {"name": "list_records", "arguments": {"kind": "customers", "limit": 1000}},
    )
    assert status == 200
    text = reply["result"]["content"][0]["text"]
    assert len(text) > registry.MAX_RESULT_CHARS // 2
    json.loads(text)  # whole, valid JSON


def test_requests_at_the_same_time(door, books):
    sid = _handshake(door)

    def one(n):
        if n % 2:
            status, _, reply = door.modern(
                n, "tools/call", {"name": "context", "arguments": {}}
            )
        else:
            status, _, reply = door.rpc(
                _req(
                    n,
                    "tools/call",
                    {
                        "name": "create_customer",
                        "arguments": {"data": {"name": f"Parallel {n}"}},
                    },
                ),
                {"Mcp-Session-Id": sid},
            )
        return n, status, reply

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(one, range(1, 49)))
    for n, status, reply in results:
        assert status == 200 and reply["id"] == n
        assert reply["result"]["isError"] is False
    names = {r["name"] for r in books.records["/api/customers"].values()}
    assert {f"Parallel {n}" for n in range(2, 49, 2)} <= names


# -- sessions ------------------------------------------------------------------------------


def test_sessions_expire_and_are_limited():
    now = [0.0]
    table = remote.Sessions(limit=2, idle=10, clock=lambda: now[0])
    a, b = table.add("A"), table.add("B")
    assert a and b and a != b
    assert table.add("C") is None  # full
    now[0] = 5
    assert table.get(a) == "A"  # touched at 5
    now[0] = 12
    assert table.get(b) is None  # idle since 0
    assert table.get(a) == "A"
    assert table.add("C") is not None  # b's place was freed
    assert table.close(a) and not table.close(a)


def test_too_many_sessions_is_503(books):
    running = Running(books, sessions=remote.Sessions(limit=1))
    try:
        assert running.rpc(_init())[0] == 200
        status, hdrs, _reply = running.rpc(_init(2))
        assert status == 503 and "Mcp-Session-Id" not in hdrs
    finally:
        running.close()


# -- the connector secret ----------------------------------------------------------------


def test_secret_is_made_once_kept_private_and_replaced(home):
    secret, made = remote.ensure_secret()
    assert made and secret.startswith("sbc_") and len(secret) >= 40
    path = store.secret_path()
    assert path.parent == store.config_dir()  # beside the API token
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    again, made = remote.ensure_secret()
    assert again == secret and not made
    newer, made = remote.ensure_secret(replace=True)
    assert made and newer != secret
    assert remote.read_secret() == newer


def test_secret_readable_by_others_or_malformed_is_refused(home):
    store.save_secret(SECRET_FAKE)
    if os.name != "nt":
        os.chmod(store.secret_path(), 0o644)
        with pytest.raises(ConfigError, match="chmod 600"):
            remote.read_secret()
    store.save_secret("not-a-secret")
    with pytest.raises(ConfigError, match="--new-secret"):
        remote.read_secret()


def test_disconnect_forget_removes_the_secret_too(home):
    store.save_secret(SECRET_FAKE)
    assert store.secret_path() in store.forget()


def test_log_has_no_secret_no_path_and_no_body(home, books, monkeypatch):
    monkeypatch.setenv("SBPLIVE_LOG", "DEBUG")
    log_file = logs.setup()
    running = Running(books)
    try:
        running.request("POST", "/mcp", body=_init())
        running.request(
            "POST",
            "/mcp",
            body=_req(
                2,
                "tools/call",
                {"name": "search", "arguments": {"q": "Secretive Body Words"}},
            ),
        )
        running.request("POST", f"/mcp/{OTHER_SECRET_FAKE}", body=_init())
        running.request(
            "POST",
            body=_init(),
            headers={"Authorization": f"Bearer {OTHER_SECRET_FAKE}"},
        )
    finally:
        running.close()
    for handler in logs.logging.getLogger(logs.LOGGER).handlers:
        handler.flush()
    text = log_file.read_text()
    assert "POST the endpoint" in text
    assert "sbc_" not in text and SECRET_FAKE[4:] not in text
    assert OTHER_SECRET_FAKE[4:] not in text
    assert "Secretive Body Words" not in text
    assert "/mcp" not in text
    logs.setup(to_file=False)


def test_log_filter_masks_a_connector_secret():
    record = logs.logging.LogRecord("x", 20, "f", 1, "saw %s", (SECRET_FAKE,), None)
    logs._MaskSecrets().filter(record)
    assert SECRET_FAKE not in record.getMessage()
