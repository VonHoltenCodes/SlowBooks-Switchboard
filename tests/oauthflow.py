"""The OAuth sign-in as Claude's client walks it, by hand, with the standard
library: register, open the sign-in page, submit it, take the code off the
redirect, exchange it. Shared by the door's tests, the contract test, the
no-third-party test and the end-to-end test.

Redirects are never followed: the test reads each Location itself."""

import base64
import hashlib
import json
import re
import secrets
from http.client import HTTPConnection
from urllib.parse import parse_qsl, urlencode, urlsplit

CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"
_PENDING = re.compile(r'name=pending value="([^"]+)"')


def call(base, method, path, body=None, headers=None):
    """(status, headers with lower-case names, body bytes)."""
    parts = urlsplit(base)
    conn = HTTPConnection(parts.hostname, parts.port, timeout=30)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data
    finally:
        conn.close()


def pkce():
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def register(base, redirect_uris, name="Claude", **extra):
    body = json.dumps({"redirect_uris": redirect_uris, "client_name": name, **extra})
    status, _, data = call(
        base,
        "POST",
        "/oauth/register",
        body.encode(),
        {"Content-Type": "application/json"},
    )
    return status, json.loads(data)


def authorize(base, params: dict):
    status, headers, data = call(base, "GET", "/oauth/authorize?" + urlencode(params))
    return status, headers, data.decode("utf-8")


def auth_params(client_id, redirect, challenge, resource, **over):
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect,
        "state": "state-" + secrets.token_hex(4),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": "books offline_access",
        "resource": resource,
    }
    params.update(over)
    return {k: v for k, v in params.items() if v is not None}


def pending_of(page: str) -> str:
    found = _PENDING.search(page)
    assert found, page
    return found.group(1)


def submit(base, pending, fields, action="approve", origin=None):
    form = dict(fields, pending=pending, action=action)
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if origin is not None:
        headers["Origin"] = origin
    status, hdrs, data = call(
        base, "POST", "/oauth/authorize", urlencode(form).encode(), headers
    )
    return status, hdrs, data.decode("utf-8")


def query_of(location: str) -> dict:
    return dict(parse_qsl(urlsplit(location).query))


def token(base, form: dict, content_type="application/x-www-form-urlencoded"):
    body = (
        json.dumps(form).encode()
        if content_type == "application/json"
        else urlencode(form).encode()
    )
    status, headers, data = call(
        base, "POST", "/oauth/token", body, {"Content-Type": content_type}
    )
    return status, headers, json.loads(data)


def sign_in(base, fields, *, resource, redirect=CLAUDE_CALLBACK, client_id=None):
    """The whole walk; returns (token answer, client_id, the callback's
    query, the page's text)."""
    if client_id is None:
        status, reg = register(base, [redirect])
        assert status == 201, reg
        client_id = reg["client_id"]
    verifier, challenge = pkce()
    params = auth_params(client_id, redirect, challenge, resource)
    status, _, page = authorize(base, params)
    assert status == 200, page
    status, hdrs, after = submit(base, pending_of(page), fields)
    assert status == 303, after
    got = query_of(hdrs["location"])
    assert got["state"] == params["state"]
    status, _, answer = token(
        base,
        {
            "grant_type": "authorization_code",
            "code": got["code"],
            "redirect_uri": redirect,
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": resource,
        },
    )
    assert status == 200, answer
    return answer, client_id, got, page
