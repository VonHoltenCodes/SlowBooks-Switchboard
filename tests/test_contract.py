"""The contract test: the official MCP Python client, pinned in
requirements-dev.txt, starts python -m sbplive over stdio against
a fake app, lists the tools and calls one read and one write, in each of its
three ways of connecting: "2026-07-28" (modern only: no probe, no handshake,
the version in every request's _meta), "auto" (probes server/discover, which
this server answers, so it stays modern) and "legacy" (the initialize
handshake, 2025-11-25).

This is the test that says a protocol change has broken us. It is skipped
when the client is not installed (the fast CI job installs nothing but
pytest); the contract job installs requirements-dev.txt and runs it."""

import asyncio
import json
import sys

import pytest

mcp = pytest.importorskip(
    "mcp",
    reason="the official MCP client is not installed; "
    "pip install -r requirements-dev.txt to run the contract test",
)

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from test_standalone import ROOT

from sbplive.protocol import MODERN_VERSIONS, PROTOCOL_VERSION

KEY = "contract-pay-0001"


def _params(server_env):
    from conftest import HOME_VARS

    env = {
        k: v
        for k, v in server_env.items()
        if k.startswith("SBPLIVE_") or k in HOME_VARS
    }
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "sbplive"],
        env=env,
        cwd=ROOT,
    )


async def _session(server_env, mode, books):
    customer = books._store("/api/customers", {"name": "Contract Test Co"})
    invoice = books._store(
        "/api/invoices", {"customer_id": customer["id"], "date": "2026-09-10"}
    )
    payment = {
        "customer_id": customer["id"],
        "date": "2026-09-12",
        "amount": "40.00",
        "allocations": [{"invoice_id": invoice["id"], "amount": "40.00"}],
    }
    async with Client(_params(server_env), mode=mode, read_timeout_seconds=30) as c:
        version = c.session.protocol_version
        discovered = c.session.discover_result
        identity = c.session.server_info
        listed = await c.list_tools()
        read = await c.call_tool("context", {})
        write = await c.call_tool("record_payment", {"data": payment, "key": KEY})
        again = await c.call_tool("record_payment", {"data": payment, "key": KEY})
        refused = await c.call_tool(
            "call_operation",
            {"method": "POST", "path": "/api/customers", "body": {"nam": "Typo"}},
        )
    return version, discovered, identity, listed, read, write, again, refused


MODERN = MODERN_VERSIONS[0]


@pytest.mark.parametrize(
    "mode, expected",
    [(MODERN, MODERN), ("auto", MODERN), ("legacy", PROTOCOL_VERSION)],
)
def test_official_client_lists_reads_and_writes(server_env, books, mode, expected):
    version, discovered, identity, listed, read, write, again, refused = asyncio.run(
        _session(server_env, mode, books)
    )
    assert version == expected
    if mode == "auto":
        # The probe was answered: the server's own DiscoverResult, not one
        # the client made up.
        assert discovered.supported_versions[0] == MODERN
        assert PROTOCOL_VERSION in discovered.supported_versions
        assert discovered.ttl_ms > 0 and discovered.cache_scope == "public"
    if mode != MODERN:
        # Modern-only mode synthesizes its DiscoverResult, so the server's
        # name is known only once a handshake or the probe has run.
        assert identity.name == "sbplive"

    tools = {t.name: t for t in listed.tools}
    assert len(tools) == 21
    assert {"find_operations", "describe_operation", "call_operation"} <= set(tools)
    assert tools["context"].annotations.read_only_hint is True
    assert tools["void_record"].annotations.destructive_hint is True
    assert tools["record_payment"].annotations.idempotent_hint is True
    assert tools["record_payment"].input_schema["required"] == ["data", "key"]

    assert read.is_error is False
    assert json.loads(read.content[0].text)["company"] == "Acme Fence Test Co"

    assert write.is_error is False
    first = json.loads(write.content[0].text)
    assert first["amount"] == "40.00"
    assert json.loads(again.content[0].text)["id"] == first["id"]
    assert len(books.records["/api/payments"]) == 1  # booked once

    assert refused.is_error is True
    assert "nam: Extra inputs are not permitted" in refused.content[0].text


# -- over Streamable HTTP: serve-http, the remote door ------------------------

SECRET_FAKE = "sbc_contract_" + "3" * 40


@pytest.fixture
def http_door(server_env, home):
    """serve-http on an ephemeral port against the fake app, with a connector
    secret stored beforehand the way serve-http stores one."""
    import os

    from test_standalone import ServeHttp

    from sbplive import store

    store.save_secret(SECRET_FAKE)
    if os.name == "nt":
        pytest.skip("serve-http is stopped with SIGINT here")
    door = ServeHttp(server_env)
    yield door
    door.stop()


async def _http_session(url, mode, books, headers):
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client

    customer = books._store("/api/customers", {"name": "HTTP Contract Co"})
    invoice = books._store(
        "/api/invoices", {"customer_id": customer["id"], "date": "2026-09-10"}
    )
    payment = {
        "customer_id": customer["id"],
        "date": "2026-09-12",
        "amount": "40.00",
        "allocations": [{"invoice_id": invoice["id"], "amount": "40.00"}],
    }
    async with create_mcp_http_client(headers=headers) as http:
        transport = streamable_http_client(url, http_client=http)
        async with Client(transport, mode=mode, read_timeout_seconds=30) as c:
            version = c.session.protocol_version
            listed = await c.list_tools()
            read = await c.call_tool("context", {})
            write = await c.call_tool("record_payment", {"data": payment, "key": KEY})
            again = await c.call_tool("record_payment", {"data": payment, "key": KEY})
    return version, listed, read, write, again


@pytest.mark.parametrize(
    "mode, expected",
    [(MODERN, MODERN), ("auto", MODERN), ("legacy", PROTOCOL_VERSION)],
)
def test_official_client_over_http(http_door, books, mode, expected):
    version, listed, read, write, again = asyncio.run(
        _http_session(
            http_door.url, mode, books, {"Authorization": f"Bearer {SECRET_FAKE}"}
        )
    )
    assert version == expected
    tools = {t.name: t for t in listed.tools}
    assert len(tools) == 21
    assert tools["void_record"].annotations.destructive_hint is True
    assert read.is_error is False
    assert json.loads(read.content[0].text)["company"] == "Acme Fence Test Co"
    first = json.loads(write.content[0].text)
    assert first["amount"] == "40.00"
    assert json.loads(again.content[0].text)["id"] == first["id"]
    assert len(books.records["/api/payments"]) == 1  # booked once


@pytest.mark.parametrize("mode", [MODERN, "legacy"])
def test_official_client_over_http_without_the_secret_fails(http_door, books, mode):
    with pytest.raises(BaseException):  # noqa: B017 - any failure to connect
        asyncio.run(
            _http_session(
                http_door.url,
                mode,
                books,
                {"Authorization": "Bearer sbc_wrong_" + "4" * 40},
            )
        )
    assert books.records["/api/payments"] == {}


# -- OAuth: the official client's own OAuth provider -------------------------
#
# mcp 2.3.0's OAuthClientProvider runs the whole sign-in over Streamable HTTP
# with no browser: its redirect handler is handed the authorization URL, and
# here the handler fetches the sign-in page and submits it (as a person would
# in the browser), keeping the code, state and iss off the redirect; its
# callback handler hands them back. Driven for DCR (serve-http as a person
# starts it, --public-url) in both eras, and for a Client ID Metadata
# Document (the door in-process, the document served on 127.0.0.1).


class _MemoryTokens:
    def __init__(self):
        self.tokens = self.client = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client

    async def set_client_info(self, info):
        self.client = info


def _provider(base, password, client_metadata_url=None):
    from urllib.parse import urlsplit

    from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata
    from oauthflow import call, pending_of, query_of, submit

    seen = {"pages": 0}

    def walk(url):
        parts = urlsplit(url)
        assert f"{parts.scheme}://{parts.netloc}" == base
        status, _, page = call(base, "GET", f"{parts.path}?{parts.query}")
        assert status == 200, page
        seen["pages"] += 1
        pending = pending_of(page.decode("utf-8"))
        status, headers, after = submit(base, pending, {"password": password})
        assert status == 303, after
        return query_of(headers["location"])

    async def redirect_handler(url):
        seen.update(await asyncio.to_thread(walk, url))

    async def callback_handler():
        return AuthorizationCodeResult(
            code=seen["code"], state=seen.get("state"), iss=seen.get("iss")
        )

    provider = OAuthClientProvider(
        server_url=base + "/mcp",
        client_metadata=OAuthClientMetadata(
            redirect_uris=["http://localhost:3118/callback"],
            client_name="Contract test",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
        ),
        storage=_MemoryTokens(),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
        client_metadata_url=client_metadata_url,
    )
    return provider, seen


async def _oauth_session(base, mode, provider):
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client

    async with create_mcp_http_client(auth=provider) as http:
        transport = streamable_http_client(base + "/mcp", http_client=http)
        async with Client(transport, mode=mode, read_timeout_seconds=30) as c:
            version = c.session.protocol_version
            listed = await c.list_tools()
            read = await c.call_tool("context", {})
    return version, listed, read


@pytest.fixture
def oauth_door(server_env, books, home):
    """serve-http --public-url on a port chosen beforehand (the public URL
    names it), against the fake app with one administrator."""
    import os
    import socket

    from fakeapi import OWNER_PW
    from test_standalone import ServeHttp

    if os.name == "nt":
        pytest.skip("serve-http is stopped with SIGINT here")
    books.add_user("owner", OWNER_PW, "admin")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    door = ServeHttp(server_env, "--port", str(port), "--public-url", base)
    door.base = base
    yield door
    door.stop()


@pytest.mark.parametrize(
    "mode, expected",
    [(MODERN, MODERN), ("auto", MODERN), ("legacy", PROTOCOL_VERSION)],
)
def test_official_client_signs_in_with_oauth(oauth_door, books, mode, expected):
    import time

    from fakeapi import OWNER_PW

    provider, seen = _provider(oauth_door.base, OWNER_PW)
    version, listed, read = asyncio.run(_oauth_session(oauth_door.base, mode, provider))
    assert version == expected
    assert seen["pages"] == 1 and seen["iss"] == oauth_door.base
    assert provider.context.client_info.client_id.startswith("sbo_client_")  # DCR
    assert len(listed.tools) == 21
    assert json.loads(read.content[0].text)["company"] == "Acme Fence Test Co"
    first = provider.context.current_tokens
    assert first.refresh_token

    # The access token runs out: the client refreshes (rotation) on its own
    # and goes on, with no second sign-in page.
    provider.context.token_expiry_time = time.time() - 1
    version, listed, read = asyncio.run(_oauth_session(oauth_door.base, mode, provider))
    assert seen["pages"] == 1
    again = provider.context.current_tokens
    assert again.access_token != first.access_token
    assert again.refresh_token != first.refresh_token
    assert json.loads(read.content[0].text)["company"] == "Acme Fence Test Co"


def test_official_client_with_a_wrong_password_gets_no_token(oauth_door, books):
    provider, _ = _provider(oauth_door.base, "not-the-" + "pw")
    with pytest.raises(BaseException):  # noqa: B017 - the walk fails
        asyncio.run(_oauth_session(oauth_door.base, MODERN, provider))
    assert provider.context.current_tokens is None


@pytest.mark.parametrize("mode", [MODERN, "legacy"])
def test_official_client_with_a_client_id_metadata_document(books, fake_server, mode):
    import time

    from fakeapi import OWNER_PW
    from test_oauth import CIMD_URL, OAuthDoor, _DocServer

    books.add_user("owner", OWNER_PW, "admin")
    docs = _DocServer(
        {
            "client_id": CIMD_URL,
            "client_name": "Contract test",
            "redirect_uris": ["http://localhost/callback"],
            "token_endpoint_auth_method": "none",
        }
    )
    door = OAuthDoor(books, fake_server.url, fetch=docs.fetch)
    door.clock.now = time.time()
    try:
        provider, seen = _provider(door.base, OWNER_PW, client_metadata_url=CIMD_URL)
        version, listed, read = asyncio.run(_oauth_session(door.base, mode, provider))
        assert version == (MODERN if mode == MODERN else PROTOCOL_VERSION)
        assert provider.context.client_info.client_id == CIMD_URL  # no DCR
        assert docs.hits == 1 and seen["pages"] == 1
        assert len(listed.tools) == 21
        assert json.loads(read.content[0].text)["company"] == "Acme Fence Test Co"
    finally:
        door.close()
        docs.close()
