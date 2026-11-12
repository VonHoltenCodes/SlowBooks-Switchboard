"""OAuth sign-in on the remote door, over a real socket on 127.0.0.1 with the
fake app's own sign-in behind it: the metadata, the 401, DCR and Client ID
Metadata Documents, the sign-in page, codes with PKCE, tokens in both eras,
refresh rotation and reuse, expiry, the lockout, the non-administrator
refusal, and no password kept anywhere. Plus the module's pieces alone.

Every secret here is fake (*_FAKE, *_PW); every address is 127.0.0.1."""

import json
import os
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fakeapi import CLERK_PW, OWNER_PW
from oauthflow import (
    CLAUDE_CALLBACK,
    auth_params,
    authorize,
    call,
    pending_of,
    pkce,
    query_of,
    register,
    sign_in,
    submit,
    token,
)

from sbplive import logs, oauth, remote
from sbplive.app_signin import NOT_ADMIN, AppSignIn
from sbplive.client import ApiClient
from sbplive.oauth_store_file import FileStore
from sbplive.protocol import (
    META_CAPABILITIES,
    META_VERSION,
    MODERN_VERSIONS,
    PROTOCOL_VERSION,
)
from sbplive.server import INSTRUCTIONS, build_registry

SECRET_FAKE = "sbc_test_" + "5" * 40
MODERN = MODERN_VERSIONS[0]
OWNER, CLERK = "owner", "clerk"  # the fake app's user names


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


class OAuthDoor:
    """remote.Door with OAuth on, behind the real socket server, signing in
    against the fake app over HTTP."""

    def __init__(self, books, app_url, store=None, fetch=oauth.fetch_document):
        self.clock = Clock()
        reg = build_registry(ApiClient(books.transport))
        self.door = remote.Door(reg, INSTRUCTIONS, SECRET_FAKE)
        self.httpd = remote.make_server(self.door, "127.0.0.1", 0)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.resource = self.base + "/mcp"
        self.app = AppSignIn(app_url, role_phrase="a bookkeeper")
        self.store = store if store is not None else oauth.MemoryStore(self.clock)
        self.server = oauth.OAuthServer(
            self.base,
            self.store,
            self.app.verify,
            fields=self.app.fields,
            about=self.app.about,
            fetch=fetch,
            clock=self.clock,
        )
        self.door.oauth = self.server
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def mcp(self, message, bearer=None, headers=None):
        hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
        if bearer:
            hdrs["Authorization"] = f"Bearer {bearer}"
        hdrs.update(headers or {})
        status, out, data = call(
            self.base, "POST", "/mcp", json.dumps(message).encode(), hdrs
        )
        return status, out, (json.loads(data) if data else None)

    def modern_call(self, bearer, tool="context"):
        msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": tool,
                "arguments": {},
                "_meta": {META_VERSION: MODERN, META_CAPABILITIES: {}},
            },
        }
        return self.mcp(
            msg,
            bearer,
            {
                "MCP-Protocol-Version": MODERN,
                "Mcp-Method": "tools/call",
                "Mcp-Name": tool,
            },
        )

    def initialize(self, bearer):
        msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        }
        return self.mcp(msg, bearer)


@pytest.fixture
def owner_only(books):
    books.add_user(OWNER, OWNER_PW, "admin")
    return books


@pytest.fixture
def door(owner_only, fake_server):
    running = OAuthDoor(owner_only, fake_server.url)
    yield running
    running.close()


def _company(reply):
    return json.loads(reply["result"]["content"][0]["text"])["company"]


# -- metadata and the 401 -------------------------------------------------------


def test_metadata_documents(door):
    for path in (
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/oauth-protected-resource",
    ):
        status, headers, data = call(door.base, "GET", path)
        assert status == 200 and headers["content-type"] == "application/json"
        prm = json.loads(data)
        assert prm["resource"] == door.resource
        assert prm["authorization_servers"] == [door.base]
        assert prm["bearer_methods_supported"] == ["header"]
        assert prm["scopes_supported"] == ["books"]  # not offline_access
    status, _, data = call(door.base, "GET", "/.well-known/oauth-authorization-server")
    asm = json.loads(data)
    assert asm["issuer"] == door.base
    assert asm["authorization_endpoint"] == door.base + "/oauth/authorize"
    assert asm["token_endpoint"] == door.base + "/oauth/token"
    assert asm["registration_endpoint"] == door.base + "/oauth/register"
    assert asm["code_challenge_methods_supported"] == ["S256"]
    assert asm["token_endpoint_auth_methods_supported"] == ["none"]
    assert asm["client_id_metadata_document_supported"] is True
    assert asm["authorization_response_iss_parameter_supported"] is True
    assert asm["grant_types_supported"] == ["authorization_code", "refresh_token"]
    assert asm["scopes_supported"] == ["books", "offline_access"]
    # Other well-known paths are still nothing.
    assert call(door.base, "GET", "/.well-known/openid-configuration")[0] == 404
    assert call(door.base, "POST", "/.well-known/oauth-authorization-server")[0] == 405


def test_the_401_shape(door):
    status, headers, body = door.mcp({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert status == 401 and body is None
    assert headers["www-authenticate"] == (
        f'Bearer resource_metadata="{door.base}/.well-known/oauth-protected-'
        'resource/mcp", scope="books"'
    )
    status, headers, _ = door.mcp(
        {"jsonrpc": "2.0", "id": 1, "method": "ping"}, bearer="sbo_at_" + "x" * 43
    )
    assert status == 401
    assert headers["www-authenticate"].startswith('Bearer error="invalid_token", ')
    # A GET without a token is the 401 too, not a 405: it starts a sign-in.
    assert call(door.base, "GET", "/mcp")[0] == 401


def test_a_missing_token_is_not_a_failed_sign_in(door):
    for _ in range(15):
        assert door.mcp({"jsonrpc": "2.0", "id": 1, "method": "ping"})[0] == 401
    assert door.modern_call(SECRET_FAKE)[0] == 200  # not locked out


def test_the_connector_secret_still_works(door):
    status, _, reply = door.modern_call(SECRET_FAKE)
    assert status == 200 and _company(reply) == "Acme Fence Test Co"


# -- the whole walk ---------------------------------------------------------------


def test_the_full_flow_with_dcr(door, owner_only):
    status, reg = register(door.base, [CLAUDE_CALLBACK], name="Claude")
    assert status == 201
    assert reg["token_endpoint_auth_method"] == "none"
    assert "client_secret" not in reg
    client_id = reg["client_id"]

    verifier, challenge = pkce()
    params = auth_params(client_id, CLAUDE_CALLBACK, challenge, door.resource)
    status, headers, page = authorize(door.base, params)
    assert status == 200
    csp = headers["content-security-policy"]
    assert "default-src 'none'" in csp and "script-src" not in csp
    assert "form-action 'self' https://claude.ai" in csp
    assert "frame-ancestors 'none'" in csp
    assert "<script" not in page
    assert "Let Claude work the books of Acme Fence Test Co as a bookkeeper?" in page
    assert "you go back to claude.ai" in page
    assert "calls itself Claude" in page
    assert 'type="password" name="password"' in page
    assert 'name="username"' not in page  # one user: the password alone

    status, headers, _ = submit(door.base, pending_of(page), {"password": OWNER_PW})
    assert status == 303
    location = headers["location"]
    assert location.startswith(CLAUDE_CALLBACK + "?")
    got = query_of(location)
    assert got["state"] == params["state"]
    assert got["iss"] == door.base
    assert got["code"].startswith("sbo_code_")

    exchange = {
        "grant_type": "authorization_code",
        "code": got["code"],
        "redirect_uri": CLAUDE_CALLBACK,
        "client_id": client_id,
        "resource": door.resource,
    }
    # A wrong verifier: refused, and the code is burned with it.
    status, headers, answer = token(door.base, dict(exchange, code_verifier=pkce()[0]))
    assert status == 400 and answer["error"] == "invalid_grant"
    assert headers["cache-control"] == "no-store"
    status, _, answer = token(door.base, dict(exchange, code_verifier=verifier))
    assert status == 400 and answer["error"] == "invalid_grant"

    # Again, with the right verifier from the start.
    answer, client_id, _, _ = sign_in(
        door.base, {"password": OWNER_PW}, resource=door.resource, client_id=client_id
    )
    assert answer["token_type"] == "Bearer" and answer["expires_in"] == 3600
    assert answer["scope"] == "books offline_access"
    access, refresh = answer["access_token"], answer["refresh_token"]

    # The bearer in the 2026-07-28 door ...
    status, _, reply = door.modern_call(access)
    assert status == 200 and _company(reply) == "Acme Fence Test Co"
    # ... and in a 2025-11-25 session, which belongs to this sign-in.
    status, headers, _ = door.initialize(access)
    assert status == 200
    sid = headers["mcp-session-id"]
    listed = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    status, _, reply = door.mcp(listed, access, {"Mcp-Session-Id": sid})
    assert status == 200 and len(reply["result"]["tools"]) == 21
    status, _, _ = door.mcp(listed, SECRET_FAKE, {"Mcp-Session-Id": sid})
    assert status == 404  # another sign-in cannot use this session

    # Refresh rotates: a new pair, the old refresh token spent.
    status, _, rotated = token(
        door.base,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": client_id,
            "resource": door.resource,
        },
    )
    assert status == 200
    assert rotated["refresh_token"] != refresh
    assert rotated["access_token"] != access
    assert door.modern_call(rotated["access_token"])[0] == 200
    # The session stays with the sign-in through the rotation.
    status, _, _ = door.mcp(listed, rotated["access_token"], {"Mcp-Session-Id": sid})
    assert status == 200

    # The old refresh token again: refused, and the whole family ends.
    status, _, answer = token(
        door.base,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": client_id,
        },
    )
    assert status == 400 and answer["error"] == "invalid_grant"
    assert door.modern_call(rotated["access_token"])[0] == 401
    status, _, answer = token(
        door.base,
        {
            "grant_type": "refresh_token",
            "refresh_token": rotated["refresh_token"],
            "client_id": client_id,
        },
    )
    assert answer["error"] == "invalid_grant"


def test_expiry(door):
    answer, client_id, _, _ = sign_in(
        door.base, {"password": OWNER_PW}, resource=door.resource
    )
    door.clock.now += 3601
    assert door.modern_call(answer["access_token"])[0] == 401
    status, _, fresh = token(
        door.base,
        {
            "grant_type": "refresh_token",
            "refresh_token": answer["refresh_token"],
            "client_id": client_id,
        },
    )
    assert status == 200
    assert door.modern_call(fresh["access_token"])[0] == 200
    door.clock.now += 31 * 86400
    status, _, answer = token(
        door.base,
        {
            "grant_type": "refresh_token",
            "refresh_token": fresh["refresh_token"],
            "client_id": client_id,
        },
    )
    assert status == 400 and answer["error"] == "invalid_grant"


def test_a_code_expires_and_is_used_once(door):
    status, reg = register(door.base, [CLAUDE_CALLBACK])
    verifier, challenge = pkce()
    params = auth_params(reg["client_id"], CLAUDE_CALLBACK, challenge, door.resource)
    _, _, page = authorize(door.base, params)
    _, headers, _ = submit(door.base, pending_of(page), {"password": OWNER_PW})
    code = query_of(headers["location"])["code"]
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": CLAUDE_CALLBACK,
        "client_id": reg["client_id"],
        "code_verifier": verifier,
    }
    door.clock.now += 601
    status, _, answer = token(door.base, form)
    assert status == 400 and answer["error"] == "invalid_grant"

    _, _, page = authorize(door.base, params)
    _, headers, _ = submit(door.base, pending_of(page), {"password": OWNER_PW})
    form["code"] = query_of(headers["location"])["code"]
    status, _, first = token(door.base, form)
    assert status == 200
    status, _, answer = token(door.base, form)  # the same code again
    assert answer["error"] == "invalid_grant"
    # ... and what it bought is revoked with it.
    assert door.modern_call(first["access_token"])[0] == 401


def test_no_refresh_token_without_offline_access(door):
    status, reg = register(door.base, [CLAUDE_CALLBACK])
    verifier, challenge = pkce()
    params = auth_params(
        reg["client_id"], CLAUDE_CALLBACK, challenge, door.resource, scope="books"
    )
    _, _, page = authorize(door.base, params)
    _, headers, _ = submit(door.base, pending_of(page), {"password": OWNER_PW})
    status, _, answer = token(
        door.base,
        {
            "grant_type": "authorization_code",
            "code": query_of(headers["location"])["code"],
            "redirect_uri": CLAUDE_CALLBACK,
            "client_id": reg["client_id"],
            "code_verifier": verifier,
        },
    )
    assert status == 200 and "refresh_token" not in answer
    assert answer["scope"] == "books"


# -- client registration and redirect URIs ---------------------------------------


def test_loopback_redirects_match_on_any_port(door):
    for registered, used in (
        ("http://127.0.0.1/callback", "http://127.0.0.1:61234/callback"),
        ("http://localhost:3118/callback", "http://localhost:5001/callback"),
    ):
        status, reg = register(door.base, [registered])
        assert status == 201, reg
        answer, _, _, page = sign_in(
            door.base,
            {"password": OWNER_PW},
            resource=door.resource,
            redirect=used,
            client_id=reg["client_id"],
        )
        assert answer["access_token"]
        # The extra warning for a loopback-only client.
        assert "is a program on your own computer, not a website" in page


@pytest.mark.parametrize(
    "uri",
    [
        "https://evil.example/callback",
        "http://claude.ai/api/mcp/auth_callback",
        "https://claude.ai/api/mcp/auth_callback/extra",
        "http://127.0.0.1/other",
        "http://192.168.1.5/callback",
    ],
)
def test_a_redirect_uri_not_accepted_is_refused_at_registration(door, uri):
    status, answer = register(door.base, [uri])
    assert status == 400 and answer["error"] == "invalid_redirect_uri"


def test_a_redirect_uri_not_registered_is_refused_at_authorization(door):
    _, reg = register(door.base, [CLAUDE_CALLBACK])
    _, challenge = pkce()
    for uri in ("https://evil.example/callback", "http://localhost:4000/callback"):
        params = auth_params(reg["client_id"], uri, challenge, door.resource)
        status, headers, page = authorize(door.base, params)
        assert status == 400 and "location" not in headers
        assert "not one this door accepts" in page
    params = auth_params(
        "sbo_client_unknown", CLAUDE_CALLBACK, challenge, door.resource
    )
    status, headers, page = authorize(door.base, params)
    assert status == 400 and "location" not in headers


@pytest.mark.parametrize(
    "over, error",
    [
        ({"code_challenge": None}, "invalid_request"),
        ({"code_challenge_method": "plain"}, "invalid_request"),
        ({"code_challenge_method": None}, "invalid_request"),
        ({"response_type": "token"}, "unsupported_response_type"),
        ({"resource": "https://elsewhere.example/mcp"}, "invalid_target"),
        ({"scope": "books admin"}, "invalid_scope"),
    ],
)
def test_authorization_errors_go_back_on_the_redirect(door, over, error):
    _, reg = register(door.base, [CLAUDE_CALLBACK])
    _, challenge = pkce()
    params = auth_params(reg["client_id"], CLAUDE_CALLBACK, challenge, door.resource)
    params.update(over)
    params = {k: v for k, v in params.items() if v is not None}
    status, headers, _ = authorize(door.base, params)
    assert status == 303
    got = query_of(headers["location"])
    assert got["error"] == error and got["state"] == params["state"]
    assert got["iss"] == door.base


def test_dcr_refuses_what_it_does_not_offer(door):
    for extra in (
        {"grant_types": ["client_credentials"]},
        {"response_types": ["token"]},
        {"token_endpoint_auth_method": "private_key_jwt"},
    ):
        status, answer = register(door.base, [CLAUDE_CALLBACK], **extra)
        assert status == 400 and answer["error"] == "invalid_client_metadata"
    status, _, _ = call(
        door.base,
        "POST",
        "/oauth/register",
        b"redirect_uris=x",
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert status == 400


class _DocServer:
    """A client's metadata document, served on 127.0.0.1 (the door fetches
    https://cimd.test/... through a fetch that points there)."""

    def __init__(self, doc):
        self.doc, self.hits = doc, 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits += 1
                data = json.dumps(outer.doc).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def fetch(self, url):
        assert url.startswith("https://cimd.test/")
        return oauth.fetch_document(
            url.replace("https://cimd.test", self.url), public_only=False
        )

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


CIMD_URL = "https://cimd.test/oauth/client.json"


def test_the_flow_with_a_client_id_metadata_document(owner_only, fake_server):
    docs = _DocServer(
        {
            "client_id": CIMD_URL,
            "client_name": "Claude Code",
            "redirect_uris": ["http://localhost/callback", "http://127.0.0.1/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
    )
    running = OAuthDoor(owner_only, fake_server.url, fetch=docs.fetch)
    try:
        answer, client_id, _, page = sign_in(
            running.base,
            {"password": OWNER_PW},
            resource=running.resource,
            redirect="http://localhost:48213/callback",
            client_id=CIMD_URL,
        )
        assert client_id == CIMD_URL
        assert "published at cimd.test (it calls itself Claude Code)" in page
        assert "localhost:48213 is a program on your own computer" in page
        assert running.modern_call(answer["access_token"])[0] == 200
        # Fetched once, then believed for a day.
        sign_in(
            running.base,
            {"password": OWNER_PW},
            resource=running.resource,
            redirect="http://127.0.0.1:5999/callback",
            client_id=CIMD_URL,
        )
        assert docs.hits == 1
        # A redirect the document does not list is refused on the page.
        _, challenge = pkce()
        params = auth_params(CIMD_URL, CLAUDE_CALLBACK, challenge, running.resource)
        status, headers, _ = authorize(running.base, params)
        assert status == 400 and "location" not in headers
    finally:
        running.close()
        docs.close()


def test_a_document_that_is_not_its_own_client_id_is_refused(owner_only, fake_server):
    docs = _DocServer(
        {"client_id": "https://other.test/x.json", "redirect_uris": [CLAUDE_CALLBACK]}
    )
    running = OAuthDoor(owner_only, fake_server.url, fetch=docs.fetch)
    try:
        _, challenge = pkce()
        params = auth_params(CIMD_URL, CLAUDE_CALLBACK, challenge, running.resource)
        status, headers, page = authorize(running.base, params)
        assert status == 400 and "location" not in headers
        assert "client_id is not its own address" in page
    finally:
        running.close()
        docs.close()


def test_the_default_fetcher_refuses_http_and_private_addresses():
    with pytest.raises(oauth.DocumentError, match="https"):
        oauth.fetch_document("http://example.com/client.json")
    for url in ("https://127.0.0.1/client.json", "https://localhost/client.json"):
        with pytest.raises(oauth.DocumentError, match="public"):
            oauth.fetch_document(url)


# -- the sign-in -------------------------------------------------------------------


def test_a_wrong_password_and_the_lockout(door, owner_only):
    _, reg = register(door.base, [CLAUDE_CALLBACK])
    _, challenge = pkce()
    params = auth_params(reg["client_id"], CLAUDE_CALLBACK, challenge, door.resource)
    _, _, page = authorize(door.base, params)
    pending = pending_of(page)
    for _ in range(10):
        status, headers, page = submit(door.base, pending, {"password": "wrong-pw"})
        assert status == 401 and "location" not in headers
        assert oauth.SENTENCE_BAD_SIGNIN in page
    tries = len(owner_only.sign_ins)
    status, headers, page = submit(door.base, pending, {"password": OWNER_PW})
    assert status == 429 and int(headers["retry-after"]) > 0
    assert oauth.SENTENCE_LOCKED in page
    assert len(owner_only.sign_ins) == tries  # the app was not even asked
    door.clock.now += 601  # the lock and the page both run out
    _, _, page = authorize(door.base, params)
    status, headers, _ = submit(door.base, pending_of(page), {"password": OWNER_PW})
    assert status == 303 and "code=" in headers["location"]


def test_a_non_administrator_is_refused(books, fake_server):
    books.add_user(OWNER, OWNER_PW, "admin")
    books.add_user(CLERK, CLERK_PW, "bookkeeper")
    running = OAuthDoor(books, fake_server.url)
    try:
        _, reg = register(running.base, [CLAUDE_CALLBACK])
        _, challenge = pkce()
        params = auth_params(
            reg["client_id"], CLAUDE_CALLBACK, challenge, running.resource
        )
        _, _, page = authorize(running.base, params)
        assert 'name="username"' in page  # several users: the name too
        pending = pending_of(page)
        status, headers, page = submit(
            running.base, pending, {"username": CLERK, "password": CLERK_PW}
        )
        assert status == 403 and "location" not in headers
        assert NOT_ADMIN in page
        assert books.logouts == 0  # no sign-out: it would end the admin's own
        status, headers, _ = submit(
            running.base, pending, {"username": OWNER, "password": OWNER_PW}
        )
        assert status == 303 and "code=" in headers["location"]
    finally:
        running.close()


def test_deny_replay_and_another_site(door):
    _, reg = register(door.base, [CLAUDE_CALLBACK])
    _, challenge = pkce()
    params = auth_params(reg["client_id"], CLAUDE_CALLBACK, challenge, door.resource)
    _, _, page = authorize(door.base, params)
    status, headers, _ = submit(door.base, pending_of(page), {}, action="deny")
    assert status == 303
    assert query_of(headers["location"])["error"] == "access_denied"

    _, _, page = authorize(door.base, params)
    pending = pending_of(page)
    status, _, _ = submit(
        door.base, pending, {"password": OWNER_PW}, origin="https://evil.example"
    )
    assert status == 403
    assert submit(door.base, pending, {"password": OWNER_PW})[0] == 303
    status, _, page = submit(door.base, pending, {"password": OWNER_PW})
    assert status == 400 and oauth.SENTENCE_EXPIRED_PAGE in page  # no replay
    door.clock.now += 601
    _, _, page = authorize(door.base, params)
    pending = pending_of(page)
    door.clock.now += 601
    status, _, page = submit(door.base, pending, {"password": OWNER_PW})
    assert status == 400  # a page left open too long


def test_the_token_endpoint_takes_a_form_only(door):
    status, _, answer = token(
        door.base,
        {"grant_type": "refresh_token", "refresh_token": "x", "client_id": "y"},
        content_type="application/json",
    )
    assert status == 400 and answer["error"] == "invalid_request"
    status, _, answer = token(door.base, {"grant_type": "password", "client_id": "y"})
    assert answer["error"] == "unsupported_grant_type"
    status, _, answer = token(door.base, {"grant_type": "refresh_token"})
    assert status == 401 and answer["error"] == "invalid_client"
    status, _, data = call(
        door.base,
        "POST",
        "/oauth/token",
        b"grant_type=a&grant_type=b&client_id=c",
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert json.loads(data)["error"] == "invalid_request"
    assert call(door.base, "GET", "/oauth/token")[0] == 405


# -- nothing kept that should not be -------------------------------------------


def test_no_password_or_token_stored_or_logged(
    owner_only, fake_server, home, monkeypatch
):
    monkeypatch.setenv("SBPLIVE_LOG", "DEBUG")
    path = logs.setup()
    store_path = home / "oauth.json"
    running = OAuthDoor(owner_only, fake_server.url, store=FileStore(store_path))
    try:
        answer, _, got, _ = sign_in(
            running.base, {"password": OWNER_PW}, resource=running.resource
        )
        submit(running.base, "nonsense", {"password": "wrong-" + OWNER_PW})
        assert running.modern_call(answer["access_token"])[0] == 200
    finally:
        running.close()
    stored = store_path.read_text()
    logged = path.read_text()
    for secret in (
        OWNER_PW,
        answer["access_token"],
        answer["refresh_token"],
        got["code"],
    ):
        assert secret not in stored
        assert secret not in logged
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(store_path).st_mode) == 0o600
    assert "the sign-in (/oauth/authorize)" in logged


# -- the module's pieces ----------------------------------------------------------


def test_s256_is_rfc_7636_appendix_b():
    assert (
        oauth.s256("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


@pytest.mark.parametrize(
    "requested, registered, same",
    [
        ("http://127.0.0.1:5000/callback", "http://127.0.0.1/callback", True),
        ("http://localhost:1/callback", "http://localhost:2/callback", True),
        ("http://[::1]:9/callback", "http://[::1]/callback", True),
        ("http://localhost:1/callback", "http://127.0.0.1/callback", False),
        ("http://localhost:1/other", "http://localhost/callback", False),
        ("https://localhost:1/callback", "https://localhost/callback", False),
        ("https://claude.ai/api/mcp/auth_callback", CLAUDE_CALLBACK, True),
        ("https://claude.ai:444/api/mcp/auth_callback", CLAUDE_CALLBACK, False),
    ],
)
def test_redirect_matches(requested, registered, same):
    assert oauth.redirect_matches(requested, registered) is same


@pytest.mark.parametrize(
    "raw, out",
    [
        ("https://Books.Example.com/", "https://books.example.com"),
        ("https://books.example.com:8443", "https://books.example.com:8443"),
        ("http://127.0.0.1:8743", "http://127.0.0.1:8743"),
        ("http://localhost", "http://localhost"),
    ],
)
def test_public_url(raw, out):
    assert oauth.check_public_url(raw) == out


@pytest.mark.parametrize(
    "raw",
    [
        "http://books.example.com",
        "https://books.example.com/mcp",
        "books.example.com",
        "https://x.example?a=1",
        "ftp://x.example",
    ],
)
def test_public_url_refused(raw):
    with pytest.raises(ValueError):
        oauth.check_public_url(raw)


def test_the_file_store_sweeps_reloads_and_stays_private(home):
    clock = Clock()
    path = home / "store" / "oauth.json"
    fs = FileStore(path, clock=clock)
    fs.put("access", "a", {"family": "f1"}, 10)
    fs.put("access", "b", {"family": "f2"}, 100)
    fs.put("refresh", "c", {"family": "f1"}, 100)
    assert fs.get("access", "a") == {"family": "f1"}
    again = FileStore(path, clock=clock)
    assert again.get("refresh", "c") == {"family": "f1"}
    assert again.delete_where("refresh", "family", "f1") == 1
    clock.now += 11
    assert fs.get("access", "a") is None
    fs.put("pending", "p", {}, 5)  # a write sweeps
    assert '"a"' not in path.read_text()
    fs.delete("access", "b")
    assert FileStore(path, clock=clock).get("access", "b") is None
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700


def test_memory_store_is_the_same_interface():
    clock = Clock()
    ms = oauth.MemoryStore(clock)
    ms.put("k", "1", {"x": 1, "f": "a"}, 5)
    ms.put("k", "2", {"x": 2, "f": "a"}, 5)
    record = ms.get("k", "1")
    record["x"] = 99  # a copy, not the stored record
    assert ms.get("k", "1")["x"] == 1
    assert ms.delete_where("k", "f", "a") == 2
    ms.put("k", "3", {}, 5)
    clock.now += 6
    assert ms.get("k", "3") is None


def test_the_log_mask_covers_oauth_tokens():
    import logging

    record = logging.LogRecord(
        "x", logging.INFO, "", 0, "got %s", ("sbo_at_abc123",), None
    )
    logs._MaskSecrets().filter(record)
    assert "sbo_at_" not in record.getMessage()
