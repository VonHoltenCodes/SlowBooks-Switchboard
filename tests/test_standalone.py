"""The program imports nothing outside the standard library, and runs as
python -m sbplive over stdio, and as serve-http over HTTP,
against a fake app.

Both tests start Python with -S (no site module, so no site-packages and no
user site) and -E (no PYTHONPATH): the only code it can import is the
standard library and this checkout."""

import json
import os
import subprocess
import sys

import pytest

from sbplive.protocol import (
    META_CAPABILITIES,
    META_VERSION,
    MODERN_VERSIONS,
    PROTOCOL_VERSION,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BARE = [sys.executable, "-S", "-E"]

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 10), reason="sys.stdlib_module_names needs 3.10"
)

_CHECK = """
import importlib, pkgutil, sys
import sbplive
# Every module the package holds, whatever this checkout carries.
for found in pkgutil.iter_modules(sbplive.__path__):
    importlib.import_module("sbplive." + found.name)
outside = sorted(
    name for name in sys.modules
    if name.split(".")[0] not in sys.stdlib_module_names
    and name.split(".")[0] not in ("sbplive", "__main__")
)
print(repr(outside))
print("site" in sys.modules)
"""


def test_imports_only_the_standard_library():
    r = subprocess.run(
        BARE + ["-c", _CHECK],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    outside, site_loaded = r.stdout.split("\n")[:2]
    assert outside == "[]"
    assert site_loaded == "False"


def _rpc(id_, method, params=None):
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


def test_handshake_and_calls_over_stdio_with_no_site_packages(server_env):
    script = [
        _rpc(1, "server/discover"),
        _rpc(
            2,
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "bare", "version": "0"},
            },
        ),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        _rpc(3, "tools/list"),
        _rpc(4, "tools/call", {"name": "context", "arguments": {}}),
        _rpc(
            5,
            "tools/call",
            {
                "name": "create_customer",
                "arguments": {"data": {"name": "Bare Python Co"}},
            },
        ),
    ]
    r = subprocess.run(
        BARE + ["-m", "sbplive"],
        cwd=ROOT,
        env=server_env,
        input="\n".join(script) + "\n",
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    replies = [json.loads(line) for line in r.stdout.splitlines()]
    assert [x["id"] for x in replies] == [1, 2, 3, 4, 5]  # stdout is protocol only
    # A probe with no version in _meta is malformed by the modern rule
    # (-32602), and a dual-era client falls back to initialize on it.
    assert replies[0]["error"]["code"] == -32602
    assert replies[1]["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert len(replies[2]["result"]["tools"]) == 21
    ctx = json.loads(replies[3]["result"]["content"][0]["text"])
    assert ctx["company"] == "Acme Fence Test Co"
    made = json.loads(replies[4]["result"]["content"][0]["text"])
    assert made["name"] == "Bare Python Co"


def test_modern_requests_over_stdio_with_no_site_packages(server_env):
    meta = {META_VERSION: MODERN_VERSIONS[0], META_CAPABILITIES: {}}
    script = [
        _rpc(1, "server/discover", {"_meta": meta}),
        _rpc(2, "tools/list", {"_meta": meta}),
        _rpc(3, "tools/call", {"name": "context", "arguments": {}, "_meta": meta}),
    ]
    r = subprocess.run(
        BARE + ["-m", "sbplive"],
        cwd=ROOT,
        env=server_env,
        input="\n".join(script) + "\n",
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    replies = [json.loads(line) for line in r.stdout.splitlines()]
    assert [x["id"] for x in replies] == [1, 2, 3]
    assert replies[0]["result"]["supportedVersions"][0] == MODERN_VERSIONS[0]
    assert len(replies[1]["result"]["tools"]) == 21
    assert all(x["result"]["resultType"] == "complete" for x in replies)
    ctx = json.loads(replies[2]["result"]["content"][0]["text"])
    assert ctx["company"] == "Acme Fence Test Co"


def test_setup_status_and_disconnect_with_no_site_packages(fake_server, home, tmp_path):
    """The new commands, run the way a person runs them, on bare Python."""
    from conftest import HOME_VARS, home_env
    from fakeapi import TOKEN_FAKE

    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("SBPLIVE_", "SLOWBOOKS_")) and k not in HOME_VARS
    }
    env.update(home_env(home))
    env["PATH"] = str(tmp_path / "no-claude-here")  # no claude command
    (home / ".config" / "Claude").mkdir(parents=True)
    pasted = tmp_path / "pasted"
    pasted.write_text(TOKEN_FAKE + "\n")

    def run(*argv, stdin=""):
        return subprocess.run(
            BARE + ["-m", "sbplive", *argv],
            cwd=ROOT,
            env=env,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    r = run("setup", "--yes", "--url", fake_server.url, "--token-file", str(pasted))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Acme Fence Test Co" in r.stdout
    assert "Claude Desktop is connected" in r.stdout
    assert "Claude Code is not installed here" in r.stdout
    r = run("status")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Claude Desktop is connected" in r.stdout
    # the server now runs from the store alone, with no variable set
    script = [
        _rpc(
            1,
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "bare", "version": "0"},
            },
        ),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        _rpc(2, "tools/call", {"name": "context", "arguments": {}}),
    ]
    r = run(stdin="\n".join(script) + "\n")
    assert r.returncode == 0, r.stderr
    replies = [json.loads(line) for line in r.stdout.splitlines()]
    ctx = json.loads(replies[1]["result"]["content"][0]["text"])
    assert ctx["company"] == "Acme Fence Test Co"
    assert ctx["token_role"] == "bookkeeper"
    r = run("disconnect", "--forget")
    assert r.returncode == 0
    assert "The stored token was deleted" in r.stdout
    r = run("--version")
    assert r.stdout.startswith("sbplive ")


class ServeHttp:
    """python -S -E -m sbplive serve-http --port 0, read until
    it says where it listens and that it is ready."""

    def __init__(self, env, *args):
        import queue
        import re
        import threading

        self.proc = subprocess.Popen(
            BARE + ["-m", "sbplive", "serve-http", "--port", "0", *args],
            cwd=ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        lines: queue.Queue = queue.Queue()

        def pump():
            for line in self.proc.stdout:
                lines.put(line)
            lines.put(None)

        threading.Thread(target=pump, daemon=True).start()
        self.out = []
        self.url = None
        while True:
            line = lines.get(timeout=60)
            if line is None:
                raise AssertionError(
                    "serve-http stopped: " + "".join(self.out) + self.proc.stderr.read()
                )
            self.out.append(line)
            found = re.search(r"open at (http://127\.0\.0\.1:\d+/mcp)", line)
            if found:
                self.url = found.group(1)
            if "Ctrl+C" in line:
                break
        self._lines = lines

    @property
    def text(self) -> str:
        return "".join(self.out)

    def stop(self) -> str:
        import signal

        self.proc.send_signal(signal.SIGINT)
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            raise
        rest = []
        while True:
            line = self._lines.get(timeout=30)
            if line is None:
                break
            rest.append(line)
        return "".join(rest)


def _post(url, message, headers):
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        url,
        data=json.dumps(message).encode(),
        method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, dict(resp.headers), json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


@pytest.mark.skipif(os.name == "nt", reason="stopped with SIGINT")
def test_serve_http_with_no_site_packages(server_env, home):
    """serve-http on bare Python: makes and shows the secret once, serves
    both eras with it, refuses without it, keeps it on the next start."""
    import re

    from sbplive import store

    door = ServeHttp(server_env)
    try:
        secret = re.search(
            r"^\s+(sbc_[A-Za-z0-9_\-]+)\s*$", door.text, re.MULTILINE
        ).group(1)
        assert "shown this once" in door.text
        assert "Acme Fence Test Co" in door.text  # it checked the app
        assert store.secret_path().read_text().strip() == secret
        auth = {"Authorization": f"Bearer {secret}"}
        meta = {META_VERSION: MODERN_VERSIONS[0], META_CAPABILITIES: {}}
        status, _, reply = _post(
            door.url,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "context", "arguments": {}, "_meta": meta},
            },
            {
                **auth,
                "MCP-Protocol-Version": MODERN_VERSIONS[0],
                "Mcp-Method": "tools/call",
                "Mcp-Name": "context",
            },
        )
        assert status == 200
        ctx = json.loads(reply["result"]["content"][0]["text"])
        assert ctx["company"] == "Acme Fence Test Co"
        status, hdrs, reply = _post(
            door.url,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "bare", "version": "0"},
                },
            },
            auth,
        )
        assert status == 200 and hdrs["Mcp-Session-Id"]
        status, _, _ = _post(
            door.url, {"jsonrpc": "2.0", "id": 3, "method": "ping"}, {}
        )
        assert status == 401
    finally:
        tail = door.stop()
    assert "closed" in tail
    assert door.proc.returncode == 0
    log_text = store.log_path().read_text() if store.log_path().exists() else ""
    assert secret not in log_text

    again = ServeHttp(server_env)
    try:
        assert secret not in again.text  # shown once only
        assert "not shown again" in again.text
    finally:
        again.stop()


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(os.name == "nt", reason="stopped with SIGINT")
def test_serve_http_oauth_with_no_site_packages(server_env, books, home):
    """serve-http --public-url on bare Python: the whole OAuth walk (DCR, the
    sign-in page, the administrator sign-in against the app, the code, the
    token) and a tool call with the bearer in both eras."""
    from fakeapi import OWNER_PW
    from oauthflow import sign_in

    from sbplive import store

    books.add_user("owner", OWNER_PW, "admin")
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    door = ServeHttp(server_env, "--port", str(port), "--public-url", base)
    try:
        assert "OAuth sign-in is on" in door.text
        assert f"{base}/mcp" in door.text
        answer, _, _, page = sign_in(
            base, {"password": OWNER_PW}, resource=base + "/mcp"
        )
        assert "Acme Fence Test Co as a bookkeeper" in page
        auth = {"Authorization": f"Bearer {answer['access_token']}"}
        meta = {META_VERSION: MODERN_VERSIONS[0], META_CAPABILITIES: {}}
        status, _, reply = _post(
            door.url,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "context", "arguments": {}, "_meta": meta},
            },
            {
                **auth,
                "MCP-Protocol-Version": MODERN_VERSIONS[0],
                "Mcp-Method": "tools/call",
                "Mcp-Name": "context",
            },
        )
        assert status == 200
        ctx = json.loads(reply["result"]["content"][0]["text"])
        assert ctx["company"] == "Acme Fence Test Co"
        status, hdrs, _ = _post(
            door.url,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "bare", "version": "0"},
                },
            },
            auth,
        )
        assert status == 200 and hdrs["Mcp-Session-Id"]
        status, hdrs, _ = _post(
            door.url, {"jsonrpc": "2.0", "id": 3, "method": "ping"}, {}
        )
        assert status == 401 and "resource_metadata=" in hdrs["WWW-Authenticate"]
    finally:
        door.stop()
    assert door.proc.returncode == 0
    kept = (store.config_dir() / "oauth.json").read_text()
    assert OWNER_PW not in kept and answer["access_token"] not in kept
    log_text = store.log_path().read_text() if store.log_path().exists() else ""
    assert OWNER_PW not in log_text


@pytest.mark.skipif(os.name == "nt", reason="stopped with SIGINT")
def test_serve_http_refuses_a_public_url_that_is_not_https(server_env):
    r = subprocess.run(
        BARE
        + ["-m", "sbplive", "serve-http", "--port", "0"]
        + ["--public-url", "http://books.example.com"],
        cwd=ROOT,
        env=server_env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert r.returncode == 2
    assert "does not start with https://" in r.stdout
