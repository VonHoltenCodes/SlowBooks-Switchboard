"""Fixtures: the fake API in-process and over HTTP, a token file, and a
home directory of the test's own for everything.

No test may read or write this machine's real configuration: the token
store, the log, Claude Desktop's and Claude Code's settings. The autouse
fixture points every variable those are found by at a temporary home."""

import os
from types import SimpleNamespace

import pytest
from fakeapi import TOKEN_FAKE, FakeBooks, FakeServer, load_spec

from sbplive.client import ApiClient

_SPEC = load_spec()  # read once: the fixture is a megabyte


HOME_VARS = (
    "HOME",
    "USERPROFILE",
    "XDG_CONFIG_HOME",
    "APPDATA",
    "LOCALAPPDATA",
    "CLAUDE_CONFIG_DIR",
)


def home_env(home) -> dict:
    """The variables that place a home directory, all under home."""
    return {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "APPDATA": str(home / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
    }


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A temporary home for the test; the real one is never seen."""
    path = tmp_path / "home"
    path.mkdir()
    for name in HOME_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in home_env(path).items():
        monkeypatch.setenv(name, value)
    for name in [k for k in os.environ if k.startswith(("SBPLIVE_", "SLOWBOOKS_"))]:
        monkeypatch.delenv(name)
    return path


@pytest.fixture
def books():
    return FakeBooks(spec=_SPEC)


@pytest.fixture
def api(books):
    """An ApiClient that reaches the fake API in-process."""
    return ApiClient(books.transport)


@pytest.fixture
def seed_accounts(books):
    """Account number -> an object with the account's id, as the app's
    fixture gave the tests."""
    return {n: SimpleNamespace(id=row["id"]) for n, row in books.accounts.items()}


@pytest.fixture
def spec():
    return _SPEC


@pytest.fixture
def token_file(tmp_path):
    path = tmp_path / "token"
    path.write_text(TOKEN_FAKE + "\n")
    if os.name != "nt":
        path.chmod(0o600)
    return path


@pytest.fixture
def fake_server(books):
    with FakeServer(books) as server:
        yield server


@pytest.fixture
def server_env(fake_server, token_file, home):
    """The environment for python -m sbplive against the fake
    app, with the test's own home."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("SBPLIVE_", "SLOWBOOKS_")) and k not in HOME_VARS
    }
    env.update(home_env(home))
    env["SBPLIVE_URL"] = fake_server.url
    env["SBPLIVE_TOKEN_FILE"] = str(token_file)
    return env
