"""End to end: the real SlowBooks Pro, started from a checkout of the
product, with SlowBooks Switchboard set up against it and driven over stdio.

The product runs on 127.0.0.1 at an ephemeral port, with its data directory,
database, settings key and home directory all under the test's tmp_path,
the way its own test suite and launcher isolate it (SLOWBOOKS_DATA_DIR,
SLOWBOOKS_ENV_FILE, DATABASE_URL, APP_HOST, APP_PORT). Nothing is read from
or written to ~/.slowbookspro, and the checkout itself is only read (no
bytecode is written there). The database is migrated and its chart of
accounts seeded as the product's Docker entrypoint does. The test makes the company's password, makes a
bookkeeper API token through the app's own route, runs `setup --yes`, then
calls context, a find_operations + call_operation read, create_customer, a
keyed payment twice, and two admin routes to hear the API's refusal.

Skipped, with the reason, when there is no product checkout, the checkout
is not at the pinned tag, or the interpreter running the tests lacks the
product's requirements. Point
SBPLIVE_PRODUCT_DIR at a checkout to run it elsewhere (default:
~/Development/bookkeeper)."""

import json
import os
import site
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

import pytest
from conftest import HOME_VARS, home_env
from test_standalone import ROOT

from sbplive.protocol import PROTOCOL_VERSION

PRODUCT = Path(
    os.environ.get("SBPLIVE_PRODUCT_DIR")
    or os.path.expanduser("~/Development/bookkeeper")
)
PINNED_TAG = "v2.31.8"
# The ports this machine's other services use; never these.
RESERVED_PORTS = {3000, 3001, 8000, 8080, 8123, 8765}
ADMIN_PW = "e2e-invented-pw-4417"  # the test company's first-run password
CLERK_PW = "e2e-invented-pw-5520"  # a bookkeeper person the OAuth test adds
COMPANY = "Larkspur Test Fencing"
PAY_KEY = "e2e-pay-0001"


def _skip_reason() -> str | None:
    if not (PRODUCT / "app" / "main.py").is_file():
        return f"no SlowBooks Pro checkout at {PRODUCT}"
    check = subprocess.run(
        [sys.executable, "-c", "import fastapi, uvicorn, alembic, sqlalchemy"],
        cwd=str(Path(ROOT).parent),
        capture_output=True,
        text=True,
        check=False,
    )
    if check.returncode != 0:
        return "this Python lacks SlowBooks Pro's requirements (fastapi, uvicorn, ...)"
    tag = subprocess.run(
        ["git", "-C", str(PRODUCT), "describe", "--tags", "--exact-match"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if tag != PINNED_TAG and os.environ.get("SBPLIVE_PRODUCT_ANY_TAG") != "1":
        return (
            f"the checkout at {PRODUCT} is at {tag or 'no tag'}, not the pinned "
            f"{PINNED_TAG} (set SBPLIVE_PRODUCT_ANY_TAG=1 to run it anyway)"
        )
    return None


pytestmark = pytest.mark.skipif(_skip_reason() is not None, reason=_skip_reason() or "")


def _free_port() -> int:
    while True:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port not in RESERVED_PORTS:
            return port


def _product_env(data: Path, home: Path, port: int) -> dict:
    from cryptography.fernet import Fernet  # the product's own requirement

    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("SBPLIVE_", "SLOWBOOKS_", "APP_", "DATABASE_URL"))
        and k not in HOME_VARS
    }
    env.update(home_env(home))
    # The product's packages may be in this user's site-packages, which a
    # different HOME would hide: name it outright.
    paths = [site.getusersitepackages(), env.get("PYTHONPATH", "")]
    env.update(
        PYTHONPATH=os.pathsep.join(p for p in paths if p),
        PYTHONDONTWRITEBYTECODE="1",
        SLOWBOOKS_DATA_DIR=str(data),
        SLOWBOOKS_ENV_FILE=str(data / "none.env"),
        DATABASE_URL=f"sqlite:///{data / 'books.db'}",
        SETTINGS_ENCRYPTION_KEY=Fernet.generate_key().decode("ascii"),
        SESSION_SECRET_KEY="e2e-session-secret-not-for-production",
        APP_DEBUG="true",
        FORCE_HTTPS="false",
        APP_HOST="127.0.0.1",
        APP_PORT=str(port),
        RATE_LIMIT_ENABLED="0",
        SLOWBOOKS_AUTO_ENTER="0",
    )
    return env


@pytest.fixture
def product(tmp_path):
    """The real app, migrated and serving; stopped afterwards whatever happens."""
    data = tmp_path / "product-data"
    data.mkdir()
    home = tmp_path / "product-home"
    home.mkdir()
    port = _free_port()
    env = _product_env(data, home, port)
    migrate = subprocess.run(
        [
            sys.executable,
            "-c",
            "from alembic.config import main; main(argv=['upgrade', 'head'])",
        ],
        cwd=PRODUCT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert migrate.returncode == 0, migrate.stderr[-2000:]
    # The chart of accounts, as the product's Docker entrypoint seeds it.
    seed = subprocess.run(
        [sys.executable, "scripts/seed_database.py"],
        cwd=PRODUCT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert seed.returncode == 0, seed.stderr[-2000:]
    log = open(data / "server.log", "w")  # noqa: SIM115 - closed in finally
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=PRODUCT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 120
        while True:
            try:
                urllib.request.urlopen(url + "/health", timeout=2).close()
                break
            except OSError:
                if proc.poll() is not None or time.monotonic() > deadline:
                    log.flush()
                    pytest.fail(
                        "SlowBooks Pro did not start:\n"
                        + (data / "server.log").read_text()[-3000:]
                    )
                time.sleep(0.2)
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log.close()


def _call(opener, method, url, body=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with opener.open(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def _rpc(id_, method, params=None):
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


def _tool(id_, name, arguments):
    return _rpc(id_, "tools/call", {"name": name, "arguments": arguments})


def test_setup_and_the_tools_against_the_real_app(product, tmp_path, home):
    started = time.monotonic()
    # 1. The company's first run and a bookkeeper token, through the app.
    browser = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar())
    )
    status, answer = _call(
        browser,
        "POST",
        product + "/api/auth/setup",
        {"password": ADMIN_PW, "company_name": COMPANY},
    )
    assert status == 200, answer
    status, made = _call(
        browser,
        "POST",
        product + "/api/tokens",
        {"label": "switchboard-e2e", "role": "bookkeeper"},
    )
    assert status == 201, made
    pasted = tmp_path / "pasted-token"
    pasted.write_text(made["token"] + "\n")

    # 2. setup --yes, in a home of the test's own with Claude Desktop there
    # and no claude command.
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("SBPLIVE_", "SLOWBOOKS_")) and k not in HOME_VARS
    }
    env.update(home_env(home))
    env["PATH"] = os.pathsep.join(
        [str(tmp_path / "no-claude"), os.path.dirname(sys.executable)]
    )
    desktop = home / ".config" / "Claude"
    desktop.mkdir(parents=True)
    setup = subprocess.run(
        [sys.executable, "-m", "sbplive", "setup", "--yes"]
        + ["--url", product, "--token-file", str(pasted)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert setup.returncode == 0, setup.stdout + setup.stderr
    assert f"the company is {COMPANY}" in setup.stdout
    assert "the token works as a bookkeeper" in setup.stdout
    assert made["token"] not in setup.stdout + setup.stderr
    entry = json.loads((desktop / "claude_desktop_config.json").read_text())
    assert entry["mcpServers"]["sbplive"]["args"] == ["-m", "sbplive"]

    # 3. The server, started as Claude Desktop would start it: the entry's
    # command and args, the entry's env, nothing else set.
    server = entry["mcpServers"]["sbplive"]
    run_env = {**env, **server.get("env", {})}
    today = time.strftime("%Y-%m-%d")
    script = [
        _rpc(
            1,
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "e2e", "version": "0"},
            },
        ),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        _tool(2, "context", {}),
        _tool(3, "find_operations", {"query": "list customers", "limit": 5}),
        _tool(4, "call_operation", {"method": "GET", "path": "/api/customers"}),
        _tool(5, "create_customer", {"data": {"name": "Quillfeather Gardens"}}),
    ]
    first = subprocess.run(
        [server["command"], *server["args"]],
        cwd=str(tmp_path),
        env=run_env,
        input="\n".join(script) + "\n",
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert first.returncode == 0, first.stderr
    replies = {r["id"]: r for r in map(json.loads, first.stdout.splitlines())}
    assert replies[1]["result"]["protocolVersion"] == PROTOCOL_VERSION

    def result(reply):
        assert reply["result"]["isError"] is False, reply
        return json.loads(reply["result"]["content"][0]["text"])

    ctx = result(replies[2])
    assert ctx["company"] == COMPANY
    assert ctx["token_role"] == "bookkeeper"
    assert ctx["app_version"]
    found = result(replies[3])
    assert any(r.get("path") == "/api/customers" for r in found)
    assert result(replies[4]) == []  # a new company has no customers
    customer = result(replies[5])
    assert customer["name"] == "Quillfeather Gardens"

    payment = {"customer_id": customer["id"], "date": today, "amount": "125.00"}
    script = script[:2] + [
        _tool(6, "record_payment", {"data": payment, "key": PAY_KEY}),
        _tool(7, "record_payment", {"data": payment, "key": PAY_KEY}),
        _tool(8, "call_operation", {"method": "GET", "path": "/api/users"}),
        _tool(
            9,
            "call_operation",
            {"method": "GET", "path": "/api/company-file/encryption"},
        ),
        _tool(10, "list_records", {"kind": "payments"}),
    ]
    second = subprocess.run(
        [server["command"], *server["args"]],
        cwd=str(tmp_path),
        env=run_env,
        input="\n".join(script) + "\n",
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert second.returncode == 0, second.stderr
    replies = {r["id"]: r for r in map(json.loads, second.stdout.splitlines())}
    paid = result(replies[6])
    again = result(replies[7])
    assert again["id"] == paid["id"]  # the same key books it once
    assert [p["id"] for p in result(replies[10])] == [paid["id"]]
    users = replies[8]["result"]
    assert users["isError"] is True
    assert users["content"][0]["text"] == (
        "SlowBooks Pro refused this: API tokens cannot manage users or tokens."
    )
    encryption = replies[9]["result"]
    assert encryption["isError"] is True
    assert encryption["content"][0]["text"].startswith(
        "SlowBooks Pro refused this: only an administrator"
    )

    # 4. status agrees, and the log holds no token.
    status = subprocess.run(
        [sys.executable, "-m", "sbplive", "status"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert status.returncode == 0, status.stdout
    assert "Claude Desktop is connected" in status.stdout
    log = home / ".config" / "sbplive" / "sbplive.log"
    if log.exists():
        assert made["token"] not in log.read_text()
    print(f"end-to-end took {time.monotonic() - started:.1f} s after the app started")


def _company_and_token(product, tmp_path) -> tuple:
    """The company's first run and a bookkeeper token, through the app;
    the token's file path."""
    browser = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar())
    )
    status, answer = _call(
        browser,
        "POST",
        product + "/api/auth/setup",
        {"password": ADMIN_PW, "company_name": COMPANY},
    )
    assert status == 200, answer
    status, made = _call(
        browser,
        "POST",
        product + "/api/tokens",
        {"label": "switchboard-e2e", "role": "bookkeeper"},
    )
    assert status == 201, made
    pasted = tmp_path / "pasted-token"
    pasted.write_text(made["token"] + "\n")
    return made["token"], pasted, browser


def test_oauth_sign_in_against_the_real_app(product, tmp_path, home):
    """serve-http --public-url against the real app: Claude's OAuth walk
    signed in with the company's administrator password (checked by the
    app's own /api/auth/login), a tool call with the bearer, a bookkeeper's
    sign-in refused, and the password nowhere in the door's files."""
    import re as _re

    from oauthflow import (
        CLAUDE_CALLBACK,
        auth_params,
        authorize,
        pending_of,
        pkce,
        register,
        sign_in,
        submit,
    )
    from test_standalone import ServeHttp

    from sbplive.app_signin import NOT_ADMIN

    _token, pasted, browser = _company_and_token(product, tmp_path)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("SBPLIVE_", "SLOWBOOKS_")) and k not in HOME_VARS
    }
    env.update(home_env(home))
    setup = subprocess.run(
        [sys.executable, "-m", "sbplive", "setup", "--yes"]
        + ["--url", product, "--token-file", str(pasted)]
        + ["--no-claude-desktop", "--no-claude-code"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert setup.returncode == 0, setup.stdout + setup.stderr

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    door = ServeHttp(env, "--port", str(port), "--public-url", base)
    try:
        assert "OAuth sign-in is on" in door.text
        # One person in the books: the page asks for the password alone.
        tokens, _, _, page = sign_in(
            base, {"password": ADMIN_PW}, resource=base + "/mcp"
        )
        assert f"Let Claude work the books of {COMPANY} as a bookkeeper?" in page
        assert 'name="username"' not in page
        msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "context",
                "arguments": {},
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {},
                },
            },
        }
        req = urllib.request.Request(
            base + "/mcp",
            data=json.dumps(msg).encode(),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {tokens['access_token']}",
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": "tools/call",
                "Mcp-Name": "context",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            reply = json.loads(resp.read())
        ctx = json.loads(reply["result"]["content"][0]["text"])
        assert ctx["company"] == COMPANY and ctx["token_role"] == "bookkeeper"

        # The administrator's own browser session still works (the door does
        # not sign out, which would end it). A bookkeeper person: the books now
        # have two users, so the page asks for the name too, and the
        # bookkeeper may not let Claude in.
        status, added = _call(
            browser,
            "POST",
            product + "/api/users",
            {"username": "pat-clerk", "password": CLERK_PW, "role": "bookkeeper"},
        )
        assert status == 201, added
        _, reg = register(base, [CLAUDE_CALLBACK])
        _, challenge = pkce()
        params = auth_params(
            reg["client_id"], CLAUDE_CALLBACK, challenge, base + "/mcp"
        )
        status, _, page = authorize(base, params)
        assert 'name="username"' in page
        pending = pending_of(page)
        status, _, page = submit(
            base, pending, {"username": "pat-clerk", "password": CLERK_PW}
        )
        assert status == 403 and NOT_ADMIN in page
        status, _, page = submit(
            base, pending, {"username": "admin", "password": "not-" + ADMIN_PW}
        )
        assert status == 401
        status, headers, _ = submit(
            base, pending, {"username": "admin", "password": ADMIN_PW}
        )
        assert status == 303 and _re.search(r"[?&]code=sbo_code_", headers["location"])
    finally:
        door.stop()
    folder = home / ".config" / "sbplive"
    for name in ("oauth.json", "sbplive.log"):
        if (folder / name).exists():
            text = (folder / name).read_text()
            assert ADMIN_PW not in text and CLERK_PW not in text
            assert tokens["access_token"] not in text
