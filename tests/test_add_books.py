"""Several companies from one store: add-books, remove-books, books, status,
setup and disconnect --forget against several companies of one fake app
over HTTP, the stdio server and the self-hosted door over them, and a store
made by setup in 1.0.0 working as it is.

Every token is made with secrets.token_urlsafe (fakeapi.new_token). Nothing
here touches this machine's real configuration: conftest gives every test
its own home, and no claude command is ever found."""

import json
import os
import re
import stat
import subprocess
import sys

import pytest
from conftest import HOME_VARS, home_env
from fakeapi import FakeInstall, FakeInstallServer, new_token

from sbplive import app_signin, books, cli, hosts, oauth_door, store
from sbplive.config import (
    HANDLE_RE,
    NOT_SET_UP,
    Config,
    ConfigError,
    hash_token,
    load_config,
)
from sbplive.server import build_registry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BARE = [sys.executable, "-S", "-E"]

HARBOR, SMITH, LAKE = "harbor-books.db", "smith-plumbing.db", "lake-dental.db"
FILES = {HARBOR: "Harbor Books", SMITH: "Smith Plumbing", LAKE: "Lake Dental"}

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="file modes and SIGINT are POSIX here"
)


@pytest.fixture(autouse=True)
def no_claude(monkeypatch):
    """No claude command and no Claude Desktop: setup and disconnect leave
    every host alone."""
    monkeypatch.setattr(hosts, "find_claude", lambda: None)
    monkeypatch.setattr(hosts, "MAC_APPS", ())


@pytest.fixture
def install(spec):
    made = FakeInstall(spec, FILES)
    with FakeInstallServer(made) as server:
        made.url = server.url
        yield made


def run(capsys, *argv) -> tuple:
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def token_file(tmp_path, token: str):
    """A file holding token, as a person saves one to pass to add-books."""
    path = tmp_path / f"pasted-{hash_token(token)[:12]}"
    path.write_text(token + "\n")
    return path


def lay_out_1_0_0(url: str, token: str) -> None:
    """The store exactly as setup in 1.0.0 left it: the token file and
    settings.json with the address alone, both mode 600, the folder 700."""
    folder = store.config_dir()
    folder.mkdir(parents=True)
    folder.chmod(0o700)
    (folder / "token").write_text(token + "\n")
    (folder / "token").chmod(0o600)
    (folder / "settings.json").write_text(json.dumps({"url": url}, indent=2) + "\n")
    (folder / "settings.json").chmod(0o600)


def add(capsys, install, tmp_path, file, token=None, *more):
    token = token or install.token_of(file)
    return run(
        capsys,
        "add-books",
        "--file",
        file,
        "--token-file",
        str(token_file(tmp_path, token)),
        *more,
    )


def rows() -> list:
    return json.loads(store.settings_path().read_text())["books"]


def handle_of(file: str) -> str:
    return next(r["books"] for r in rows() if r["file"] == file)


def bare_env(home) -> dict:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("SBPLIVE_", "SLOWBOOKS_")) and k not in HOME_VARS
    }
    env.update(home_env(home))
    return env


def _rpc(id_, method, params=None):
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


def stdio(home, calls: list) -> list:
    """python -S -E -m sbplive over stdio: initialize, then each
    (tool, arguments) as tools/call; the tool results in order."""
    from sbplive.protocol import PROTOCOL_VERSION

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
    ]
    for n, (name, arguments) in enumerate(calls, 2):
        script.append(_rpc(n, "tools/call", {"name": name, "arguments": arguments}))
    r = subprocess.run(
        BARE + ["-m", "sbplive"],
        cwd=ROOT,
        env=bare_env(home),
        input="\n".join(script) + "\n",
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    replies = [json.loads(line) for line in r.stdout.splitlines()]
    assert [x["id"] for x in replies] == list(range(1, len(calls) + 2))
    return [x["result"] for x in replies[1:]]


def texts(result) -> list:
    return [c["text"] for c in result["content"]]


# -- a store made by 1.0.0 -----------------------------------------------------------


def test_a_1_0_0_store_works_with_no_new_setup(install, home, capsys):
    """Set up with 1.0.0 (one token, no list of companies): the server, status
    and books answer as before, no company header is sent, no handle is
    needed, and the store is not touched."""
    token = install.token_of(HARBOR)
    lay_out_1_0_0(install.url, token)
    before = {
        p.name: p.read_bytes() for p in store.config_dir().iterdir() if p.is_file()
    }

    config = load_config()
    assert config.books == {} and config.company == "" and config.token == token

    ctx, made = stdio(
        home,
        [
            ("context", {}),
            (
                "create_customer",
                {"data": {"name": "Still Works Co"}, "asked_user": True},
            ),
        ],
    )
    assert not ctx["isError"] and len(ctx["content"]) == 1  # no "Books:" line
    facts = json.loads(ctx["content"][0]["text"])
    assert facts["company"] == "Harbor Books"
    assert "books" not in facts
    assert json.loads(made["content"][0]["text"])["name"] == "Still Works Co"
    customers = install.companies[HARBOR].records["/api/customers"].values()
    assert any(c["name"] == "Still Works Co" for c in customers)

    code, out = run(capsys, "status")
    assert code == 0
    assert f"SlowBooks Switchboard is set up for {install.url}." in out
    assert "the company is Harbor Books" in out
    code, out = run(capsys, "books")
    assert code == 0 and "one company's token, from setup" in out

    assert install.requests and all(named == () for _, named, _, _ in install.requests)
    after = {
        p.name: p.read_bytes()
        for p in store.config_dir().iterdir()
        if p.is_file() and p.name != store.LOG_NAME
    }
    assert after == before
    assert not store.books_dir().exists()


def test_environment_token_file_is_one_company_whatever_the_store(
    install, home, capsys, tmp_path, monkeypatch
):
    lay_out_1_0_0(install.url, install.token_of(HARBOR))
    assert add(capsys, install, tmp_path, SMITH)[0] == 0
    path = tmp_path / "env-token"
    path.write_text(install.token_of(LAKE) + "\n")
    path.chmod(0o600)
    monkeypatch.setenv("SBPLIVE_TOKEN_FILE", str(path))
    config = load_config()
    assert config.books == {} and config.token == install.token_of(LAKE)


# -- add-books ------------------------------------------------------------------------


def test_the_first_add_moves_the_setup_token_then_adds(install, home, capsys, tmp_path):
    lay_out_1_0_0(install.url, install.token_of(HARBOR))
    code, out = add(capsys, install, tmp_path, SMITH)
    assert code == 0, out
    harbor, smith = handle_of(HARBOR), handle_of(SMITH)
    assert HANDLE_RE.fullmatch(harbor) and harbor.startswith("harbor-books~")
    assert HANDLE_RE.fullmatch(smith) and smith.startswith("smith-plumbing~")
    assert f"The token setup stored, for Harbor Books, is now kept as {harbor}." in out
    assert f"Added Smith Plumbing's books as {smith} (bookkeeper)." in out

    # setup's file stays as it was (Switchboard 1.0.0 still reads it); each
    # company's token is its own private file
    assert store.token_path().read_text() == install.token_of(HARBOR) + "\n"
    folder = store.books_dir()
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    for handle, file in ((harbor, HARBOR), (smith, SMITH)):
        path = store.book_token_path(handle)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.read_text() == install.token_of(file) + "\n"
    settings = store.settings_path().read_text()
    assert install.token_of(HARBOR) not in settings
    assert install.token_of(SMITH) not in settings
    assert json.loads(settings)["url"] == install.url
    assert rows() == [
        {
            "books": harbor,
            "file": HARBOR,
            "name": "Harbor Books",
            "token_sha256": hash_token(install.token_of(HARBOR)),
        },
        {
            "books": smith,
            "file": SMITH,
            "name": "Smith Plumbing",
            "token_sha256": hash_token(install.token_of(SMITH)),
        },
    ]

    config = load_config()
    assert list(config.books) == [harbor, smith]
    assert config.company == HARBOR and config.token == install.token_of(HARBOR)
    assert install.token_of(SMITH) not in repr(config)

    # the new company's token was only ever sent with its own header
    for token, named, _method, path in install.requests:
        if token == install.token_of(SMITH):
            assert named == (SMITH,), path


def test_add_books_without_setup_first(install, home, capsys, tmp_path):
    code, out = add(capsys, install, tmp_path, LAKE, None, "--url", install.url)
    assert code == 0, out
    assert [r["file"] for r in rows()] == [LAKE]
    assert load_config().company == LAKE


def test_a_token_from_another_company_is_a_mismatch(install, home, capsys, tmp_path):
    """Harbor Books is the company the app serves by default: its token given
    for Smith Plumbing is refused there, and asked plainly says where it
    answers."""
    code, out = run(
        capsys,
        "add-books",
        "--file",
        SMITH,
        "--url",
        install.url,
        "--token-file",
        str(token_file(tmp_path, install.token_of(HARBOR))),
    )
    assert code == 1
    assert (
        "That token answers from harbor-books.db, not smith-plumbing.db. "
        "Nothing was saved." in out
    )
    assert not store.settings_path().exists() and not store.books_dir().exists()


def test_an_answer_from_other_books_is_a_mismatch(install, home, capsys, tmp_path):
    install.stamp[SMITH] = LAKE
    code, out = add(capsys, install, tmp_path, SMITH, None, "--url", install.url)
    assert code == 1
    assert (
        "That token answers from lake-dental.db, not smith-plumbing.db. "
        "Nothing was saved." in out
    )
    assert not store.books_dir().exists()


def test_a_token_already_saved_is_refused(install, home, capsys, tmp_path):
    lay_out_1_0_0(install.url, install.token_of(HARBOR))
    # setup's own token, given for another file
    code, out = add(capsys, install, tmp_path, LAKE, install.token_of(HARBOR))
    assert code == 1
    assert "That token is already saved for Harbor Books. Nothing was saved." in out
    assert store.token_path().exists() and not store.books_dir().exists()
    assert add(capsys, install, tmp_path, SMITH)[0] == 0
    kept = store.settings_path().read_bytes()
    code, out = add(capsys, install, tmp_path, LAKE, install.token_of(SMITH))
    assert code == 1
    assert "That token is already saved for Smith Plumbing. Nothing was saved." in out
    assert store.settings_path().read_bytes() == kept


def test_the_same_company_twice_is_refused(install, home, capsys, tmp_path):
    lay_out_1_0_0(install.url, install.token_of(HARBOR))
    assert add(capsys, install, tmp_path, SMITH)[0] == 0
    install.companies[SMITH].token = new_token()
    code, out = add(capsys, install, tmp_path, SMITH)
    assert code == 1
    smith = handle_of(SMITH)
    assert f"are already kept as {smith}" in out
    assert f"sbplive remove-books {smith}" in out
    assert out.rstrip().endswith("Nothing was saved.")


def test_an_older_app_is_refused(install, home, capsys, tmp_path):
    install.older = True
    code, out = add(capsys, install, tmp_path, SMITH, None, "--url", install.url)
    assert code == 1
    assert (
        "This SlowBooks Pro is older than 2.32.0-beta.2 and cannot keep companies "
        "apart for a token. Update it to add a second company. Nothing was saved."
        in out
    )
    assert not store.books_dir().exists()


def test_an_older_app_leaves_the_setup_token_where_it_is(
    install, home, capsys, tmp_path
):
    lay_out_1_0_0(install.url, install.token_of(HARBOR))
    before = store.settings_path().read_bytes()
    install.older = True
    code, out = add(capsys, install, tmp_path, SMITH)
    assert code == 1
    assert "The token setup stored is checked before a second company" in out
    assert "older than 2.32.0-beta.2" in out
    assert store.token_path().read_text() == install.token_of(HARBOR) + "\n"
    assert store.settings_path().read_bytes() == before
    assert not store.books_dir().exists()


def test_only_a_bookkeeper_token_is_added(install, home, capsys, tmp_path):
    install.companies[SMITH].role = "readonly"
    code, out = add(capsys, install, tmp_path, SMITH, None, "--url", install.url)
    assert code == 1
    assert "needs a bookkeeper token" in out and "Smith Plumbing" in out
    assert out.rstrip().endswith("Nothing was saved.")
    assert not store.books_dir().exists()


def test_a_company_not_served_is_refused(install, home, capsys, tmp_path):
    install.not_served.add(SMITH)
    code, out = add(capsys, install, tmp_path, SMITH, None, "--url", install.url)
    assert code == 1
    assert "does not serve smith-plumbing.db right now" in out


def test_a_file_name_that_is_not_a_company_file(install, home, capsys, tmp_path):
    for bad in ("Smith Plumbing", "../smith-plumbing.db", "smith-plumbing", ""):
        code, out = add(capsys, install, tmp_path, bad, install.token_of(SMITH))
        assert code == 2, bad
        assert "--file must name the company's file" in out


def test_another_address_is_refused_once_one_is_kept(install, home, capsys, tmp_path):
    lay_out_1_0_0(install.url, install.token_of(HARBOR))
    code, out = add(
        capsys, install, tmp_path, SMITH, None, "--url", "http://127.0.0.1:9"
    )
    assert code == 2
    assert f"The books here are kept for {install.url}" in out
    assert store.token_path().exists() and not store.books_dir().exists()


# -- remove-books, books, status, setup, disconnect --forget --------------------------


def test_remove_books(install, home, capsys, tmp_path):
    lay_out_1_0_0(install.url, install.token_of(HARBOR))
    assert add(capsys, install, tmp_path, SMITH)[0] == 0
    harbor, smith = handle_of(HARBOR), handle_of(SMITH)

    code, out = run(capsys, "remove-books", smith)
    assert code == 0
    assert (
        "Removed Smith Plumbing. Its token still works in SlowBooks Pro until you "
        "turn it off there (Settings, API Tokens)." in out
    )
    assert not store.book_token_path(smith).exists()
    assert [r["books"] for r in rows()] == [harbor]

    code, out = run(capsys, "remove-books", "smith~aaaaaa")
    assert code == 1
    assert (
        f"No company's books are kept here as 'smith~aaaaaa'. These are: {harbor}."
        in out
    )
    assert out.rstrip().endswith("Nothing was changed.")

    code, out = run(capsys, "remove-books", harbor)
    assert code == 0 and "No company's books are kept here now." in out
    with pytest.raises(ConfigError) as caught:
        load_config()
    assert str(caught.value) == NOT_SET_UP


def test_books_and_status_name_every_company(install, home, capsys, tmp_path):
    lay_out_1_0_0(install.url, install.token_of(HARBOR))
    assert add(capsys, install, tmp_path, SMITH)[0] == 0
    assert add(capsys, install, tmp_path, LAKE)[0] == 0
    harbor, smith, lake = handle_of(HARBOR), handle_of(SMITH), handle_of(LAKE)

    code, out = run(capsys, "books")
    assert code == 0
    assert f"3 companies' books are kept for {install.url}:" in out
    assert (
        f"- Harbor Books ({harbor}, harbor-books.db) answers, and the token works "
        "as a bookkeeper" in out
    )
    assert f"- Smith Plumbing ({smith}, smith-plumbing.db) answers" in out
    assert f"- Lake Dental ({lake}, lake-dental.db) answers" in out

    install.companies[LAKE].token = new_token()  # turned off there
    install.not_served.add(SMITH)
    code, out = run(capsys, "status")
    assert code == 1
    assert (
        f"SlowBooks Switchboard is set up for {install.url}, with 3 companies' "
        "books:" in out
    )
    assert f"- Harbor Books ({harbor}, harbor-books.db) answers" in out
    assert (
        f"- Smith Plumbing ({smith}): SlowBooks Pro at {install.url} does not "
        "serve smith-plumbing.db right now." in out
    )
    assert f"- Lake Dental ({lake}): SlowBooks Pro no longer accepts its token" in out
    for file in FILES:
        assert install.token_of(file) not in out


def test_setup_keeps_the_companies(install, home, capsys, tmp_path):
    lay_out_1_0_0(install.url, install.token_of(HARBOR))
    assert add(capsys, install, tmp_path, SMITH)[0] == 0
    kept = store.settings_path().read_bytes()
    code, out = run(
        capsys,
        "setup",
        "--yes",
        "--url",
        install.url,
        "--token-file",
        str(token_file(tmp_path, install.token_of(LAKE))),
    )
    # the token given is not taken without a word: it would look stored
    assert code == 2
    assert "keeps 2 companies' books" in out and "stores no token" in out
    assert "--token-file was not used" in out and "Nothing was changed." in out
    assert "Done." not in out
    assert store.settings_path().read_bytes() == kept
    # another address is refused: one connection reaches one SlowBooks Pro
    other = install.url.rsplit(":", 1)[0] + ":1"
    code, out = run(capsys, "setup", "--yes", "--url", other)
    assert code == 2 and "--url was not used" in out
    assert "Done." not in out
    assert store.settings_path().read_bytes() == kept
    # the address the companies are kept for, given again, is no conflict
    code, out = run(capsys, "setup", "--yes", "--url", install.url)
    assert code == 0 and "--url was not used" not in out
    assert "keeps 2 companies' books" in out and "Done." in out
    assert store.settings_path().read_bytes() == kept
    # with neither, setup keeps the companies and only connects Claude
    code, out = run(capsys, "setup", "--yes")
    assert code == 0
    assert "keeps 2 companies' books" in out and "stores no token" in out
    assert "Done." in out
    assert store.settings_path().read_bytes() == kept
    # setup's first token is the one 1.0.0 stored, still as it was
    assert store.token_path().read_text() == install.token_of(HARBOR) + "\n"


def test_disconnect_forget_removes_every_company(install, home, capsys, tmp_path):
    lay_out_1_0_0(install.url, install.token_of(HARBOR))
    assert add(capsys, install, tmp_path, SMITH)[0] == 0
    code, out = run(capsys, "disconnect", "--forget")
    assert code == 0
    assert "The stored tokens were deleted, every company's." in out
    assert not store.books_dir().exists()
    assert not store.settings_path().exists()
    assert not store.token_path().exists()


# -- what the store checks -------------------------------------------------------------


def test_a_damaged_list_is_refused(install, home, capsys, tmp_path):
    lay_out_1_0_0(install.url, install.token_of(HARBOR))
    assert add(capsys, install, tmp_path, SMITH)[0] == 0
    good = json.loads(store.settings_path().read_text())
    for change in (
        lambda d: d["books"].append(dict(d["books"][0])),  # a handle twice
        lambda d: d["books"][1].update(file=HARBOR),  # a file twice
        lambda d: d["books"][1].update(books="Smith Plumbing"),
        lambda d: d["books"][1].update(file="../x.db"),
        lambda d: d["books"][1].update(token_sha256=d["books"][0]["token_sha256"]),
        lambda d: d.update(books="all"),
    ):
        data = json.loads(json.dumps(good))
        change(data)
        store.save_settings(data)
        with pytest.raises(ConfigError) as caught:
            load_config()
        assert "cannot be read" in str(caught.value)
    store.save_settings(good)
    smith = handle_of(SMITH)
    store.book_token_path(smith).write_text(install.token_of(LAKE) + "\n")
    with pytest.raises(ConfigError) as caught:
        load_config()
    assert "does not hold the token added for Smith Plumbing's books" in str(
        caught.value
    )
    store.book_token_path(smith).unlink()
    with pytest.raises(ConfigError) as caught:
        load_config()
    assert f"sbplive remove-books {smith}" in str(caught.value)
    store.save_book_token(smith, install.token_of(SMITH))
    store.book_token_path(smith).chmod(0o644)
    with pytest.raises(ConfigError) as caught:
        load_config()
    assert "can be read by other users" in str(caught.value)


# -- the tools over the kept companies --------------------------------------------------


def test_the_stdio_server_works_every_company(install, home, capsys, tmp_path):
    lay_out_1_0_0(install.url, install.token_of(HARBOR))
    assert add(capsys, install, tmp_path, SMITH)[0] == 0
    harbor, smith = handle_of(HARBOR), handle_of(SMITH)
    install.requests.clear()

    overview, refused, made, facts = stdio(
        home,
        [
            ("context", {}),
            ("create_customer", {"data": {"name": "Pipe Co"}, "asked_user": True}),
            (
                "create_customer",
                {"data": {"name": "Pipe Co"}, "asked_user": True, "books": smith},
            ),
            ("context", {"books": harbor}),
        ],
    )
    listed = json.loads(overview["content"][0]["text"])
    assert [b["books"] for b in listed["books"]] == [harbor, smith]
    assert refused["isError"]
    assert texts(refused) == [
        (
            "This connection reaches 2 companies' books, so every call names one "
            "with books. Call context to see them, then call create_customer "
            f"again with books set to one of: {harbor}, {smith}. Nothing was done."
        )
    ]
    assert texts(made)[0] == f"Books: Smith Plumbing ({smith})."
    assert json.loads(texts(made)[1])["name"] == "Pipe Co"
    assert texts(facts)[0] == f"Books: Harbor Books ({harbor})."
    assert json.loads(texts(facts)[1])["company"] == "Harbor Books"

    smith_names = [
        c["name"] for c in install.companies[SMITH].records["/api/customers"].values()
    ]
    harbor_names = [
        c["name"] for c in install.companies[HARBOR].records["/api/customers"].values()
    ]
    assert smith_names == ["Pipe Co"] and harbor_names == []
    for token, named, _method, path in install.requests:
        for file in (HARBOR, SMITH):
            if token == install.token_of(file) and path != "/openapi.json":
                assert named == (file,), path


def _registry(install):
    """The registry over this store's companies, in-process over HTTP, as
    server.run builds it."""
    return build_registry(books.provider(load_config()))


def test_a_company_added_while_claude_runs_is_seen(install, home, capsys, tmp_path):
    lay_out_1_0_0(install.url, install.token_of(HARBOR))
    assert add(capsys, install, tmp_path, SMITH)[0] == 0
    reg = _registry(install)
    first = json.loads(reg.call("context", {})["content"][0]["text"])
    assert len(first["books"]) == 2
    assert add(capsys, install, tmp_path, LAKE)[0] == 0
    lake = handle_of(LAKE)
    now = json.loads(reg.call("context", {})["content"][0]["text"])
    assert [b["name"] for b in now["books"]] == [
        "Harbor Books",
        "Smith Plumbing",
        "Lake Dental",
    ]
    answer = reg.call("context", {"books": lake})
    assert not answer["isError"]
    assert json.loads(texts(answer)[1])["company"] == "Lake Dental"

    smith = handle_of(SMITH)
    assert run(capsys, "remove-books", smith)[0] == 0
    gone = reg.call("context", {"books": smith})
    assert gone["isError"]
    assert texts(gone)[-1].startswith(
        f"This connection does not reach the books '{smith}'."
    )


def test_a_refused_token_is_not_sent_again(install, home, capsys, tmp_path):
    lay_out_1_0_0(install.url, install.token_of(HARBOR))
    assert add(capsys, install, tmp_path, SMITH)[0] == 0
    smith = handle_of(SMITH)
    reg = _registry(install)
    old = install.token_of(SMITH)
    install.companies[SMITH].token = new_token()  # turned off in that company
    lost = (
        "Smith Plumbing no longer accepts this connection: its token was turned "
        "off there, or the person who made it is no longer an administrator "
        "there. To use it again, the person makes a new token in Smith Plumbing "
        f"and runs sbplive remove-books {smith}, then sbplive add-books with it. "
        "Nothing was done."
    )
    first = reg.call("search", {"q": "pipe", "books": smith})
    assert first["isError"] and texts(first)[-1] == lost
    sent = len(install.requests)
    again = reg.call("search", {"q": "pipe", "books": smith})
    assert again["isError"] and texts(again)[-1] == lost
    assert len(install.requests) == sent  # nothing was sent
    overview = json.loads(reg.call("context", {})["content"][0]["text"])
    assert overview["books"][1] == {
        "books": smith,
        "name": "Smith Plumbing",
        "answers": False,
        "why": books.TURNED_OFF,
    }
    assert all(t != old for t, *_ in install.requests[sent:])


def test_a_handle_from_another_store_is_refused(
    install, home, capsys, tmp_path, monkeypatch
):
    """Two people (two stores) add the same company: each gets a handle of
    its own, and neither's handle reaches anything through the other's."""
    lay_out_1_0_0(install.url, install.token_of(HARBOR))
    assert add(capsys, install, tmp_path, SMITH)[0] == 0
    mine = handle_of(SMITH)
    reg = _registry(install)

    other = tmp_path / "other-home"
    other.mkdir()
    for name, value in home_env(other).items():
        monkeypatch.setenv(name, value)
    second = new_token()
    install.companies[SMITH].token = second  # its own token there
    lay_out_1_0_0(install.url, install.token_of(HARBOR))
    assert add(capsys, install, tmp_path, SMITH, second)[0] == 0
    theirs = handle_of(SMITH)
    assert theirs != mine

    answer = reg.call("context", {"books": theirs})
    assert answer["isError"]
    assert texts(answer)[-1].startswith(
        f"This connection does not reach the books '{theirs}'."
    )


def test_books_added_for_another_address_are_not_reached(
    install, home, capsys, tmp_path, spec
):
    """disconnect --forget, then add-books for another SlowBooks Pro, while
    Claude runs: the running connection still sends to the address it
    started with, so it reaches none of the new companies, and no new token
    goes anywhere."""
    lay_out_1_0_0(install.url, install.token_of(HARBOR))
    assert add(capsys, install, tmp_path, SMITH)[0] == 0
    reg = _registry(install)
    assert len(json.loads(texts(reg.call("context", {}))[0])["books"]) == 2

    other = FakeInstall(spec, FILES)
    with FakeInstallServer(other) as server:
        other.url = server.url
        assert other.url != install.url
        assert run(capsys, "disconnect", "--forget")[0] == 0
        assert add(capsys, other, tmp_path, LAKE, None, "--url", other.url)[0] == 0
        lake = handle_of(LAKE)
        assert load_config().base_url == other.url  # a new start would use it
        here, there = len(install.requests), len(other.requests)

        answer = reg.call("context", {"books": lake})
        assert answer["isError"]
        assert texts(answer)[-1].startswith(
            f"This connection does not reach the books '{lake}'."
        )
        assert texts(reg.call("context", {})) == [
            "This connection reaches no company's books yet. Nothing was done."
        ]
        assert len(install.requests) == here
        assert len(other.requests) == there


# -- the self-hosted door stays one company ---------------------------------------------


def test_the_door_serves_only_the_first_company(install, home, capsys, tmp_path):
    from test_standalone import ServeHttp, _post

    from sbplive.protocol import META_CAPABILITIES, META_VERSION, MODERN_VERSIONS

    lay_out_1_0_0(install.url, install.token_of(HARBOR))
    assert add(capsys, install, tmp_path, SMITH)[0] == 0
    smith = handle_of(SMITH)
    door = ServeHttp(bare_env(home))
    try:
        assert (
            "The self-hosted sign-in door serves only Harbor Books in this version; "
            "the other companies are reachable from Claude Desktop" in door.text
        )
        secret = re.search(
            r"^\s+(sbc_[A-Za-z0-9_\-]+)\s*$", door.text, re.MULTILINE
        ).group(1)
        meta = {META_VERSION: MODERN_VERSIONS[0], META_CAPABILITIES: {}}

        def call(arguments, id_):
            return _post(
                door.url,
                {
                    "jsonrpc": "2.0",
                    "id": id_,
                    "method": "tools/call",
                    "params": {
                        "name": "context",
                        "arguments": arguments,
                        "_meta": meta,
                    },
                },
                {
                    "Authorization": f"Bearer {secret}",
                    "MCP-Protocol-Version": MODERN_VERSIONS[0],
                    "Mcp-Method": "tools/call",
                    "Mcp-Name": "context",
                },
            )[2]

        plain = call({}, 1)["result"]
        assert len(plain["content"]) == 1
        assert json.loads(plain["content"][0]["text"])["company"] == "Harbor Books"
        other = call({"books": smith}, 2)["result"]
        assert other["isError"]
        assert "It reaches one company's books" in other["content"][-1]["text"]
    finally:
        door.stop()
    for token, named, _method, path in install.requests:
        if token == install.token_of(SMITH):
            assert path == "/api/whoami"  # only add-books asked with it


def test_the_door_signs_in_to_its_own_company(install):
    config = Config(install.url, new_token(), 30.0, company=SMITH)
    door = oauth_door.make(config, "https://books.example.com")
    assert door.app.company == SMITH
    assert door.app._headers()["X-SlowBooks-Company"] == SMITH
    plain = app_signin.AppSignIn(install.url)
    assert "X-SlowBooks-Company" not in plain._headers()
