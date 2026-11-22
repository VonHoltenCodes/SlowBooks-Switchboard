"""Several companies' books: the edges.

- A 401 ends a company's place on the connection only when SlowBooks Pro's
  gate turned the token away (X-SlowBooks-Token-Refused); a route's own
  401 is that request's refusal, and the sign-in routes are not sent to.
- The companies are kept for one address: SBPLIVE_URL naming another is
  refused, and no token goes there.
- A company is added only under a token SlowBooks Pro ends with its maker
  (one named for an AI assistant).
- add-books leaves setup's own token where it was, so Switchboard 1.0.0
  still reaches that first company; remove-books takes it with that
  company.

Every token is made with secrets.token_urlsafe (fakeapi.new_token)."""

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile

import pytest
from bookskit import ManyBooks
from conftest import home_env
from fakeapi import FakeInstall, FakeInstallServer, new_token

from sbplive import cli, hosts, store
from sbplive.config import ConfigError, load_config
from sbplive.server import build_registry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARBOR, SMITH = "harbor-books.db", "smith-plumbing.db"
FILES = {HARBOR: "Harbor Books", SMITH: "Smith Plumbing"}
# Switchboard 1.0.0, the last version with one token only
ONE_ZERO = "v1.0.0"

posix = pytest.mark.skipif(os.name == "nt", reason="file modes are POSIX here")


@pytest.fixture(autouse=True)
def no_claude(monkeypatch):
    monkeypatch.setattr(hosts, "find_claude", lambda: None)
    monkeypatch.setattr(hosts, "MAC_APPS", ())


@pytest.fixture
def install(spec):
    made = FakeInstall(spec, FILES)
    with FakeInstallServer(made) as server:
        made.url = server.url
        yield made


def _texts(result) -> list:
    return [c["text"] for c in result["content"]]


def _run(capsys, *argv) -> tuple:
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def _token_file(tmp_path, token):
    path = tmp_path / f"pasted-{len(list(tmp_path.iterdir()))}"
    path.write_text(token + "\n")
    return path


def _lay_out_1_0_0(url, token):
    folder = store.config_dir()
    folder.mkdir(parents=True)
    folder.chmod(0o700)
    (folder / "token").write_text(token + "\n")
    (folder / "token").chmod(0o600)
    (folder / "settings.json").write_text(json.dumps({"url": url}) + "\n")
    (folder / "settings.json").chmod(0o600)


def _add(capsys, tmp_path, file, token, *more):
    return _run(
        capsys,
        "add-books",
        "--file",
        file,
        "--token-file",
        str(_token_file(tmp_path, token)),
        *more,
    )


# -- a 401 that is not the gate's --------------------------------------------------


def test_a_routes_own_401_keeps_the_company_on(spec):
    install = FakeInstall(spec, FILES)
    many = ManyBooks.of(install)
    reg = build_registry(many)
    smith = install.companies[SMITH]
    plain = smith.handle

    def webhook_401(method, path, params=None, body=None, headers=None):
        if path == "/api/customers":
            return 401, {"detail": "Signature does not match"}
        return plain(method, path, params, body, headers)

    smith.handle = webhook_401
    handle = many.handle(SMITH)
    result = reg.call("list_records", {"kind": "customers", "books": handle})
    assert result["isError"] is True
    assert _texts(result) == [
        f"Books: Smith Plumbing ({handle}).",
        "SlowBooks Pro refused this: Signature does not match.",
    ]
    assert many.lost_calls == []
    smith.handle = plain
    assert reg.call("search", {"q": "x", "books": handle})["isError"] is False


def test_the_sign_in_routes_are_not_sent_to(spec):
    install = FakeInstall(spec, FILES)
    many = ManyBooks.of(install)
    reg = build_registry(many)
    handle = many.handle(SMITH)
    result = reg.call(
        "post_operation",
        {
            "books": handle,
            "path": "/api/auth/login",
            "body": {"username": "nobody", "password": new_token()},
        },
    )
    assert result["isError"] is True
    assert "is where people sign in to SlowBooks Pro" in _texts(result)[-1]
    assert _texts(result)[-1].endswith("Nothing was done.")
    assert all(path != "/api/auth/login" for *_, path in install.requests)
    assert many.lost_calls == []


# -- one address -----------------------------------------------------------------------


@posix
def test_the_books_go_to_no_other_address(install, home, capsys, tmp_path, monkeypatch):
    _lay_out_1_0_0(install.url, install.token_of(HARBOR))
    assert _add(capsys, tmp_path, SMITH, install.token_of(SMITH))[0] == 0
    assert load_config().base_url == install.url
    monkeypatch.setenv("SBPLIVE_URL", install.url)
    assert len(load_config().books) == 2
    monkeypatch.setenv("SBPLIVE_URL", "http://127.0.0.1:9")
    with pytest.raises(ConfigError) as caught:
        load_config()
    assert f"were added for {install.url}" in str(caught.value)
    assert "sbp_" not in str(caught.value)
    # nor is a company added for another address through the environment
    third = new_token()
    code, out = _add(capsys, tmp_path, SMITH, third)
    assert code == 2 and "Leave SBPLIVE_URL out" in out


# -- a token that ends with its maker ---------------------------------------------------


@posix
def test_a_token_that_outlives_its_maker_is_not_added(install, home, capsys, tmp_path):
    _lay_out_1_0_0(install.url, install.token_of(HARBOR))
    install.companies[SMITH].label = "office script"
    code, out = _add(capsys, tmp_path, SMITH, install.token_of(SMITH))
    assert code == 1
    assert 'That token is named "office script"' in out
    assert 'starts with "AI assistant"' in out and "Nothing was saved." in out
    assert not store.books_dir().exists()
    install.companies[SMITH].label = "AI assistant (Switchboard)"
    assert _add(capsys, tmp_path, SMITH, install.token_of(SMITH))[0] == 0


@posix
def test_setups_token_must_end_with_its_maker_too(install, home, capsys, tmp_path):
    _lay_out_1_0_0(install.url, install.token_of(HARBOR))
    install.companies[HARBOR].label = "Claude Desktop"
    code, out = _add(capsys, tmp_path, SMITH, install.token_of(SMITH))
    assert code == 1
    assert 'The token setup stored is named "Claude Desktop"' in out
    assert "sbplive setup with it" in out
    assert not store.books_dir().exists()
    assert store.token_path().read_text() == install.token_of(HARBOR) + "\n"


# -- going back to 1.0.0 -----------------------------------------------------------------


def _one_zero(tmp_path):
    """Switchboard 1.0.0's sbplive, from this repository's history."""
    if shutil.which("git") is None:
        pytest.skip("git is not here")
    got = subprocess.run(
        ["git", "-C", ROOT, "archive", "--format=tar", ONE_ZERO, "sbplive"],
        capture_output=True,
        check=False,
    )
    if got.returncode != 0:
        pytest.skip("1.0.0 is not in this checkout's history")
    where = tmp_path / "one-zero"
    with tarfile.open(fileobj=io.BytesIO(got.stdout)) as tar:
        tar.extractall(where, filter="data")
    return where


def _stdio(code_dir, home, calls):
    from sbplive.protocol import PROTOCOL_VERSION

    lines = [
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "bare", "version": "0"},
                },
            }
        ),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
    ]
    for n, (name, arguments) in enumerate(calls, 2):
        lines.append(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": n,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                }
            )
        )
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("SBPLIVE_", "SLOWBOOKS_", "PYTHON"))
    }
    env.update(home_env(home))
    r = subprocess.run(
        [sys.executable, "-S", "-E", "-m", "sbplive"],
        cwd=code_dir,
        env=env,
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return r


@posix
def test_switchboard_1_0_0_still_reaches_the_first_company(
    install, home, capsys, tmp_path
):
    _lay_out_1_0_0(install.url, install.token_of(HARBOR))
    assert _add(capsys, tmp_path, SMITH, install.token_of(SMITH))[0] == 0
    old = _one_zero(tmp_path)
    r = _stdio(old, home, [("context", {})])
    assert r.returncode == 0, r.stderr
    replies = [json.loads(line) for line in r.stdout.splitlines()]
    result = replies[-1]["result"]
    assert result["isError"] is False, result
    assert json.loads(result["content"][0]["text"])["company"] == "Harbor Books"


@posix
def test_remove_books_takes_setups_token_with_its_company(
    install, home, capsys, tmp_path
):
    _lay_out_1_0_0(install.url, install.token_of(HARBOR))
    assert _add(capsys, tmp_path, SMITH, install.token_of(SMITH))[0] == 0
    rows = json.loads(store.settings_path().read_text())["books"]
    smith = next(r["books"] for r in rows if r["file"] == SMITH)
    harbor = next(r["books"] for r in rows if r["file"] == HARBOR)
    assert _run(capsys, "remove-books", smith)[0] == 0
    assert store.token_path().exists()  # Harbor's, still on
    assert _run(capsys, "remove-books", harbor)[0] == 0
    assert not store.token_path().exists()
    code, out = _run(capsys, "books")
    assert code == 1 and "not set up" in out
