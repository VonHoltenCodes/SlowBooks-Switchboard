"""setup, status and disconnect against a temporary home laid out as each
platform lays it out (sys.platform and the environment monkeypatched), the
fake app over HTTP, and a fake `claude` command. Nothing here touches this
machine's real configuration: conftest's autouse fixture gives every test
its own home."""

import json
import os
import stat
import sys
from pathlib import Path

import pytest
from fakeapi import COMPANY, TOKEN_FAKE

from sbplive import cli, hosts, store

TOKEN2_FAKE = "sbp_test_" + "2" * 32
WRONG_FAKE = "sbp_test_" + "9" * 32

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="the fake claude command is a POSIX script"
)

OTHER = {
    "globalShortcut": "Ctrl+Space",
    "mcpServers": {
        "notes": {"command": "/opt/notes/bin/notes-mcp", "args": ["--quiet"]},
        "weather": {
            "command": "npx",
            "args": ["-y", "weather-mcp"],
            "env": {"UNITS": "imperial", "ÄPFEL": "ü"},
        },
    },
    "preferences": {"theme": "dark", "nested": [1, 2.5, None, True]},
}

FAKE_CLAUDE = r'''#!{python}
"""A stand-in for the claude command: records its arguments and keeps the
user-scope servers in $HOME/.claude.json, as Claude Code does."""
import json, os, sys
home = os.environ["HOME"]
with open(os.path.join(home, "claude-calls.jsonl"), "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
path = os.path.join(home, ".claude.json")
try:
    data = json.load(open(path))
except OSError:
    data = {{}}
servers = data.setdefault("mcpServers", {{}})
args = sys.argv[1:]
if args[:2] == ["mcp", "add"]:
    name = args[4]
    rest = args[args.index("--") + 1:]
    servers[name] = {{"type": "stdio", "command": rest[0], "args": rest[1:]}}
elif args[:2] == ["mcp", "remove"]:
    if args[-1] not in servers:
        print("No MCP server found", file=sys.stderr)
        sys.exit(1)
    del servers[args[-1]]
json.dump(data, open(path, "w"))
'''


@pytest.fixture
def fake_claude(home, monkeypatch):
    path = home / "bin" / "claude"
    path.parent.mkdir()
    path.write_text(FAKE_CLAUDE.format(python=sys.executable))
    path.chmod(0o755)
    monkeypatch.setattr(hosts, "find_claude", lambda: str(path))
    monkeypatch.setattr(hosts, "MAC_APPS", ())
    return home / "claude-calls.jsonl"


def calls(log: Path) -> list:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


@pytest.fixture
def token_path(tmp_path):
    path = tmp_path / "pasted-token"
    path.write_text(TOKEN_FAKE + "\n")
    return path


def run(capsys, *argv) -> tuple:
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def setup_args(server, token_path, *more):
    return (
        "setup",
        "--yes",
        "--url",
        server.url,
        "--token-file",
        str(token_path),
        *more,
    )


def desktop_layout(platform: str, home: Path) -> list:
    """Make Claude Desktop's folders as installed on the platform; return
    the configuration files setup should write."""
    if platform == "linux":
        folder = home / ".config" / "Claude"
        folder.mkdir(parents=True)
        return [folder / hosts.DESKTOP_FILE]
    if platform == "darwin":
        folder = home / "Library" / "Application Support" / "Claude"
        folder.mkdir(parents=True)
        return [folder / hosts.DESKTOP_FILE]
    plain = home / "AppData" / "Roaming" / "Claude"
    plain.mkdir(parents=True)
    packaged = (
        home
        / "AppData"
        / "Local"
        / "Packages"
        / "Claude_pzs8sxrjxfjjc"
        / "LocalCache"
        / "Roaming"
        / "Claude"
    )
    packaged.mkdir(parents=True)
    return [plain / hosts.DESKTOP_FILE, packaged / hosts.DESKTOP_FILE]


STORE_DIRS = {
    "linux": Path(".config") / "sbplive",
    "darwin": Path("Library") / "Application Support" / "sbplive",
    "win32": Path("AppData") / "Roaming" / "sbplive",
}


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_setup_status_disconnect_on_each_platform(
    platform, home, monkeypatch, capsys, fake_server, token_path, fake_claude
):
    monkeypatch.setattr(store, "platform", lambda: platform)
    files = desktop_layout(platform, home)
    files[0].write_text(json.dumps(OTHER, indent=4, ensure_ascii=False))
    original = files[0].read_text()

    code, out = run(capsys, *setup_args(fake_server, token_path))
    assert code == 0, out
    assert f"the company is {COMPANY}" in out
    assert "the token works as a bookkeeper" in out
    assert TOKEN_FAKE not in out

    # the token store, for this user only
    folder = home / STORE_DIRS[platform]
    assert store.config_dir() == folder
    assert (folder / "token").read_text() == TOKEN_FAKE + "\n"
    assert stat.S_IMODE((folder / "token").stat().st_mode) == 0o600
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert json.loads((folder / "settings.json").read_text()) == {
        "url": fake_server.url
    }

    # Claude Desktop: every other key kept, the backup is the file as it was
    data = json.loads(files[0].read_text())
    entry = data["mcpServers"].pop("sbplive")
    assert data == OTHER
    assert entry["command"] == sys.executable
    assert entry["args"] == ["-m", "sbplive"]
    assert TOKEN_FAKE not in files[0].read_text()
    backups = list(files[0].parent.glob(hosts.DESKTOP_FILE + ".bak-sbplive-*"))
    assert [b.read_text() for b in backups] == [original]
    for extra in files[1:]:  # the Windows package's copy, created
        assert json.loads(extra.read_text())["mcpServers"]["sbplive"] == entry
    assert "Claude Desktop is connected. The file as it was is saved as" in out

    # Claude Code, in the user scope
    made = calls(fake_claude)
    assert made[0] == ["mcp", "remove", "--scope", "user", "sbplive"]
    assert made[1][:5] == ["mcp", "add", "--scope", "user", "sbplive"]
    assert made[1][made[1].index("--") + 1 :] == [
        sys.executable,
        "-m",
        "sbplive",
    ]
    assert "Claude Code is connected for your user" in out

    code, out = run(capsys, "status")
    assert code == 0, out
    assert f"SlowBooks Switchboard is set up for {fake_server.url}." in out
    assert f"the company is {COMPANY}" in out
    assert f"Claude Desktop is connected ({files[0]})." in out
    assert "Claude Code is connected for your user." in out
    assert f"The log is at {folder / 'sbplive.log'}." in out

    code, out = run(capsys, "disconnect")
    assert code == 0
    assert json.loads(files[0].read_text()) == OTHER
    assert len(list(files[0].parent.glob(hosts.DESKTOP_FILE + ".bak-sbplive-*"))) == 2
    assert "SlowBooks was taken out of Claude Desktop" in out
    assert "SlowBooks was taken out of Claude Code." in out
    assert (folder / "token").exists()  # kept without --forget

    code, out = run(capsys, "disconnect", "--forget")
    assert not (folder / "token").exists()
    assert not (folder / "settings.json").exists()
    assert "It still works in SlowBooks Pro until it is revoked" in out
    assert "Claude Desktop was not connected" in out


def test_a_second_setup_replaces_the_first(
    home, monkeypatch, capsys, fake_server, token_path, fake_claude, books
):
    monkeypatch.setattr(store, "platform", lambda: "linux")
    (config,) = desktop_layout("linux", home)
    assert run(capsys, *setup_args(fake_server, token_path))[0] == 0
    assert not list(config.parent.glob("*.bak-sbplive-*"))  # no file before
    first = json.loads(config.read_text())

    books.token = TOKEN2_FAKE
    token_path.write_text(TOKEN2_FAKE + "\n")
    code, out = run(capsys, *setup_args(fake_server, token_path))
    assert code == 0, out
    assert store.token_path().read_text() == TOKEN2_FAKE + "\n"
    assert json.loads(config.read_text()) == first  # one entry, the same one
    assert len(list(config.parent.glob("*.bak-sbplive-*"))) == 1
    code_calls = calls(fake_claude)
    assert [c[1] for c in code_calls] == ["remove", "add", "remove", "add"]
    servers = json.loads((home / ".claude.json").read_text())["mcpServers"]
    assert list(servers) == ["sbplive"]


def test_a_refused_token_stores_nothing(
    home, monkeypatch, capsys, fake_server, token_path, fake_claude
):
    monkeypatch.setattr(store, "platform", lambda: "linux")
    (config,) = desktop_layout("linux", home)
    token_path.write_text(WRONG_FAKE + "\n")
    code, out = run(capsys, *setup_args(fake_server, token_path))
    assert code == 1
    assert "did not accept the API token" in out and "Nothing was stored." in out
    assert WRONG_FAKE not in out
    assert not store.has_token() and not config.exists() and not calls(fake_claude)


def test_the_app_not_answering_stores_nothing(home, capsys, token_path, fake_claude):
    code, out = run(
        capsys,
        "setup",
        "--yes",
        "--url",
        "http://127.0.0.1:9",
        "--token-file",
        str(token_path),
    )
    assert code == 1
    assert "did not answer at http://127.0.0.1:9" in out
    assert "Open SlowBooks Pro" in out
    assert not store.has_token()


def test_a_file_that_is_not_json_is_left_alone(
    home, monkeypatch, capsys, fake_server, token_path, fake_claude
):
    monkeypatch.setattr(store, "platform", lambda: "linux")
    (config,) = desktop_layout("linux", home)
    config.write_text('{"mcpServers": {"notes": ')
    code, out = run(capsys, *setup_args(fake_server, token_path))
    assert code == 0
    assert config.read_text() == '{"mcpServers": {"notes": '
    assert "is not valid JSON, so it was left as it is" in out
    assert not list(config.parent.glob("*.bak-sbplive-*"))


def test_no_hosts_installed_says_so(home, capsys, fake_server, token_path, monkeypatch):
    monkeypatch.setattr(store, "platform", lambda: "linux")
    monkeypatch.setattr(hosts, "find_claude", lambda: None)
    code, out = run(capsys, *setup_args(fake_server, token_path))
    assert code == 0
    assert "Claude Desktop is not installed for this user" in out
    assert "Claude Code is not installed here" in out
    assert "claude mcp add --scope user sbplive" in out
    code, out = run(capsys, "status")
    assert "Claude Desktop is not installed for this user." in out
    assert "Claude Code is not installed here." in out


def test_hosts_can_be_left_alone(
    home, monkeypatch, capsys, fake_server, token_path, fake_claude
):
    monkeypatch.setattr(store, "platform", lambda: "linux")
    (config,) = desktop_layout("linux", home)
    code, _ = run(
        capsys,
        *setup_args(fake_server, token_path, "--no-claude-desktop", "--no-claude-code"),
    )
    assert code == 0
    assert not config.exists() and not calls(fake_claude)
    assert store.has_token()


def test_the_token_from_standard_input(
    home, monkeypatch, capsys, fake_server, fake_claude
):
    import io

    monkeypatch.setattr(sys, "stdin", io.StringIO(TOKEN_FAKE + "\n"))
    code, out = run(
        capsys,
        "setup",
        "--yes",
        "--url",
        fake_server.url,
        "--token-file",
        "-",
        "--no-claude-code",
    )
    assert code == 0, out
    assert store.token_path().read_text() == TOKEN_FAKE + "\n"


def test_without_a_terminal_setup_needs_yes(home, monkeypatch, capsys):
    import io

    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    code, out = run(capsys, "setup")
    assert code == 2
    assert "pass --yes with --token-file" in out
    assert not store.has_token()


def test_setup_asks_in_a_terminal(home, monkeypatch, capsys, fake_server, fake_claude):
    """The prompts: the address (Enter for this computer), the token without
    echo, then a yes or no for each host."""
    monkeypatch.setattr(store, "platform", lambda: "linux")
    desktop_layout("linux", home)

    class Tty:
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", Tty())
    answers = iter([fake_server.url, "n", "y"])
    asked = []

    def ask(prompt):
        asked.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", ask)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: TOKEN_FAKE)
    code, out = run(capsys, "setup")
    assert code == 0, out
    assert asked[0].startswith("Where are the books?")
    assert asked[1].startswith("Connect Claude Desktop")
    assert asked[2].startswith("Connect Claude Code")
    assert "Claude Desktop was left as it is." in out
    assert "Claude Code is connected" in out


def test_a_lan_address_warns_and_a_public_one_is_refused(
    home, capsys, token_path, monkeypatch
):
    from sbplive import probe

    def nobody(config):
        raise probe.ProbeError(f"SlowBooks Pro did not answer at {config.base_url}.")

    monkeypatch.setattr(hosts, "find_claude", lambda: None)
    monkeypatch.setattr(probe, "check", nobody)
    code, out = run(
        capsys,
        "setup",
        "--yes",
        "--url",
        "http://192.168.0.250:9",
        "--token-file",
        str(token_path),
    )
    assert "the API token and the books travel unencrypted" in out
    assert code == 1  # nothing answers there
    code, out = run(
        capsys,
        "setup",
        "--yes",
        "--url",
        "http://8.8.4.4:3001",
        "--token-file",
        str(token_path),
    )
    assert code == 2
    assert "must start with https://" in out


def test_status_before_setup(home, capsys, monkeypatch):
    monkeypatch.setattr(hosts, "find_claude", lambda: None)
    code, out = run(capsys, "status")
    assert code == 1
    assert "is not set up on this computer for this user" in out
    assert "The log is at" in out


def test_the_role_is_named(home, capsys, fake_server, token_path, books, monkeypatch):
    monkeypatch.setattr(hosts, "find_claude", lambda: None)
    for role, words in (
        ("readonly", "the token is read-only"),
        ("accountant", "the token works as an accountant"),
    ):
        books.role = role
        code, out = run(capsys, *setup_args(fake_server, token_path))
        assert code == 0, out
        assert words in out


def test_the_entry_runs_a_frozen_build_by_its_own_path(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/opt/sbs/sbplive")
    assert hosts.desktop_entry() == {
        "command": "/opt/sbs/sbplive",
        "args": [],
    }
    assert hosts.code_add_args()[-2:] == ["--", "/opt/sbs/sbplive"]


def test_the_entry_from_a_checkout_names_it(monkeypatch):
    entry = hosts.desktop_entry()
    root = str(Path(hosts.__file__).resolve().parent.parent)
    assert entry["env"] == {"PYTHONPATH": root}
    assert hosts.code_add_args()[5:7] == ["-e", f"PYTHONPATH={root}"]


def test_the_log_holds_no_token(home, capsys, fake_server, token_path, monkeypatch):
    monkeypatch.setenv("SBPLIVE_LOG", "DEBUG")
    monkeypatch.setattr(hosts, "find_claude", lambda: None)
    assert run(capsys, *setup_args(fake_server, token_path))[0] == 0
    text = store.log_path().read_text()
    assert "GET /api/settings -> 200" in text
    assert TOKEN_FAKE not in text and "Bearer" not in text


def test_the_log_masks_anything_shaped_like_a_token(home):
    import logging

    from sbplive import logs

    path = logs.setup()
    logging.getLogger("sbplive").error("oops %s", "Authorization: Bearer " + TOKEN_FAKE)
    assert TOKEN_FAKE not in path.read_text()
    assert "[hidden]" in path.read_text()
