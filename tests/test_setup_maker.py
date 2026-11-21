"""A new setup takes only a token SlowBooks Pro ends with the administrator
who made it (GET /api/whoami says ends_with_maker), as add-books does; an
app too old to answer /api/whoami is set up as before, with a sentence; and
a store made already, by this version or by Switchboard 1.0.0, is never
held to the rule.

Every token is made with secrets (fakeapi.new_token). Nothing here touches
this machine's real configuration: conftest gives every test its own home,
and the claude command is test_setup's stand-in."""

import json
import os
import sys

import pytest
import test_setup
from fakeapi import FakeBooks, FakeServer, new_token
from test_setup import OTHER, calls, desktop_layout

from sbplive import cli, probe, store
from sbplive.config import hash_token, load_config

# test_setup's stand-in for the claude command, as a fixture here
fake_claude = test_setup.fake_claude

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="the fake claude command is a POSIX script"
)

FILE = "acme-fence-test.db"

# setup's own sentence: it adds no company, and it takes a Read-only or an
# Accountant token as well as a bookkeeper's, so add-books' "A company added
# here needs a bookkeeper token" would not be true of it
REFUSED = (
    'That token is named "office script", so SlowBooks Pro keeps it working '
    "even after the administrator who made it is deactivated or is no longer "
    "an administrator in Acme Fence. Setup takes only a token whose name "
    'starts with "AI assistant" (for example "AI assistant (Switchboard)"), '
    "which SlowBooks Pro turns off when that happens. Make one in Acme Fence "
    "under Settings, API Tokens. Nothing was changed."
)
OLDER = (
    "This SlowBooks Pro cannot tell whether the token ends with the "
    "administrator who made it, and updating SlowBooks Pro will let setup "
    "check."
)


@pytest.fixture
def app(spec):
    """One company's books over HTTP, under a token made with secrets."""
    books = FakeBooks(spec=spec, token=new_token(), company="Acme Fence", file=FILE)
    with FakeServer(books) as server:
        yield server


@pytest.fixture
def desktop(home, monkeypatch, fake_claude):
    """Claude Desktop installed, with a configuration of its own."""
    monkeypatch.setattr(store, "platform", lambda: "linux")
    (path,) = desktop_layout("linux", home)
    path.write_text(json.dumps(OTHER, indent=4))
    return path


def run(capsys, *argv) -> tuple:
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def token_file(tmp_path, token: str):
    path = tmp_path / f"pasted-{hash_token(token)[:12]}"
    path.write_text(token + "\n")
    return path


def setup(capsys, app, tmp_path, token=None):
    argv = ["setup", "--yes", "--url", app.url]
    if token is not False:
        argv += ["--token-file", str(token_file(tmp_path, token or app.books.token))]
    return run(capsys, *argv)


def every_file(home) -> dict:
    """Every file under the home, path -> bytes, but the log, which every
    command opens (it is checked for the token instead)."""
    out = {}
    for root, _dirs, files in os.walk(home):
        for name in files:
            path = os.path.join(root, name)
            with open(path, "rb") as fh:
                out[path] = fh.read()
    log = str(store.log_path())
    if log in out:
        assert b"sbp_" not in out.pop(log)
    return out


def whoami_asked(app) -> int:
    return sum(1 for c in app.books.calls if c[1] == "/api/whoami")


# -- a new setup ------------------------------------------------------------------


def test_a_new_setup_refuses_a_token_that_outlives_its_maker(
    home, capsys, tmp_path, app, desktop, fake_claude
):
    app.books.label = "office script"
    before = every_file(home)
    code, out = setup(capsys, app, tmp_path)
    assert code == 1, out
    assert out.rstrip().endswith(REFUSED)
    assert app.books.token not in out
    assert every_file(home) == before  # no file changed or added
    assert not store.has_token() and not store.settings_path().exists()
    assert not calls(fake_claude)  # Claude Code never asked
    assert whoami_asked(app) == 1


def test_a_new_setup_takes_a_token_named_for_an_ai_assistant(
    home, capsys, tmp_path, app, desktop, fake_claude
):
    app.books.label = "AI assistant (Switchboard)"
    code, out = setup(capsys, app, tmp_path)
    assert code == 0, out
    assert "the company is Acme Fence" in out
    assert "Nothing was changed." not in out and OLDER not in out
    assert store.token_path().read_text() == app.books.token + "\n"
    assert json.loads(desktop.read_text())["mcpServers"]["sbplive"]["command"] == (
        sys.executable
    )
    assert [c[1] for c in calls(fake_claude)] == ["remove", "add"]
    assert whoami_asked(app) == 1


def test_an_older_app_is_set_up_and_says_it_cannot_tell(
    home, capsys, tmp_path, app, desktop, fake_claude
):
    app.books.older = True
    app.books.label = "office script"  # it cannot say so
    code, out = setup(capsys, app, tmp_path)
    assert code == 0, out
    assert out.count(OLDER) == 1
    assert "the company is Acme Fence" in out
    assert "Nothing was" not in out
    assert store.token_path().read_text() == app.books.token + "\n"
    assert "sbplive" in json.loads(desktop.read_text())["mcpServers"]
    assert [c[1] for c in calls(fake_claude)] == ["remove", "add"]


@pytest.mark.parametrize(
    "status,said",
    [
        (500, "refused that token: Internal error. Nothing was saved."),
        (401, "did not accept that token (Not authenticated)."),
    ],
)
def test_whoami_failing_otherwise_stores_nothing(
    home,
    capsys,
    tmp_path,
    app,
    desktop,
    fake_claude,
    monkeypatch,
    status,
    said,
):
    real = app.books._get

    def failing(path, params):
        if path == "/api/whoami":
            detail = "Internal error" if status == 500 else "Not authenticated"
            return status, {"detail": detail}
        return real(path, params)

    monkeypatch.setattr(app.books, "_get", failing)
    before = every_file(home)
    code, out = setup(capsys, app, tmp_path)
    assert code == 1, out
    assert said in out
    assert out.rstrip().endswith("Nothing was saved.")
    assert every_file(home) == before
    assert not calls(fake_claude)


def test_the_app_gone_before_whoami_stores_nothing(
    home, capsys, tmp_path, app, desktop, fake_claude, monkeypatch
):
    real = probe.whoami

    def after_closing(config, file=None):
        app.httpd.shutdown()
        app.httpd.server_close()
        return real(config, file)

    monkeypatch.setattr(probe, "whoami", after_closing)
    before = every_file(home)
    code, out = setup(capsys, app, tmp_path)
    assert code == 1, out
    assert "Open SlowBooks Pro, or check the address, then try again." in out
    assert out.rstrip().endswith("Nothing was saved.")
    assert every_file(home) == before
    assert not calls(fake_claude)


# -- a store made already ----------------------------------------------------------


def lay_out_1_0_0(url: str, token: str) -> None:
    """The store exactly as setup in 1.0.0 left it."""
    folder = store.config_dir()
    folder.mkdir(parents=True)
    folder.chmod(0o700)
    (folder / "token").write_text(token + "\n")
    (folder / "token").chmod(0o600)
    (folder / "settings.json").write_text(json.dumps({"url": url}, indent=2) + "\n")
    (folder / "settings.json").chmod(0o600)


def lay_out_list(url: str, token: str) -> None:
    """A list of one company, as add-books writes it."""
    handle = cli.new_handle(FILE)
    store.save_book_token(handle, token)
    row = {"books": handle, "file": FILE, "name": "Acme Fence"}
    row["token_sha256"] = hash_token(token)
    store.save_settings({"url": url, "books": [row]})


def store_files() -> dict:
    return every_file(store.config_dir())


MODES = {
    "named for another": lambda b: setattr(b, "label", "office script"),
    "named for an AI assistant": lambda b: setattr(
        b, "label", "AI assistant (Switchboard)"
    ),
    "an older app": lambda b: setattr(b, "older", True),
}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("given", ["the kept token", "no token file"])
def test_a_1_0_0_store_is_set_up_again_as_before(
    home, capsys, tmp_path, app, desktop, fake_claude, mode, given
):
    lay_out_1_0_0(app.url, app.books.token)
    MODES[mode](app.books)
    before = store_files()
    code, out = setup(
        capsys, app, tmp_path, False if given == "no token file" else None
    )
    assert code == 0, out
    assert "Nothing was" not in out and OLDER not in out
    assert store_files() == before  # the same token and address, byte for byte
    assert whoami_asked(app) == 0
    assert [c[1] for c in calls(fake_claude)] == ["remove", "add"]
    config = load_config()
    assert config.token == app.books.token and not config.books


# A new token handed to setup over a kept one is a new token, so it is held
# to the rule as on a new setup. A setup run again with no new token is
# never asked.


@pytest.mark.parametrize("role", ["bookkeeper", "readonly", "accountant"])
def test_a_new_token_over_a_kept_one_that_outlives_its_maker_is_refused(
    home, capsys, tmp_path, app, desktop, fake_claude, role
):
    kept = new_token()
    lay_out_1_0_0(app.url, kept)
    app.books.label = "office script"
    app.books.role = role
    before = every_file(home)
    code, out = setup(capsys, app, tmp_path)
    assert code == 1, out
    assert out.rstrip().endswith(REFUSED)
    assert "bookkeeper" not in out
    assert app.books.token not in out and kept not in out
    assert every_file(home) == before  # the kept token and address unchanged
    assert store.token_path().read_text() == kept + "\n"
    assert not calls(fake_claude)
    assert whoami_asked(app) == 1


@pytest.mark.parametrize("role", ["bookkeeper", "readonly", "accountant"])
def test_a_new_token_over_a_kept_one_named_for_an_ai_assistant_replaces_it(
    home, capsys, tmp_path, app, desktop, fake_claude, role
):
    lay_out_1_0_0(app.url, new_token())
    app.books.label = "AI assistant (Switchboard)"
    app.books.role = role
    code, out = setup(capsys, app, tmp_path)
    assert code == 0, out
    assert "Nothing was" not in out and OLDER not in out
    assert store.token_path().read_text() == app.books.token + "\n"
    assert [c[1] for c in calls(fake_claude)] == ["remove", "add"]
    assert whoami_asked(app) == 1


def test_a_new_token_over_a_kept_one_from_an_older_app_replaces_it(
    home, capsys, tmp_path, app, desktop, fake_claude
):
    lay_out_1_0_0(app.url, new_token())
    app.books.older = True
    app.books.label = "office script"  # it cannot say so
    code, out = setup(capsys, app, tmp_path)
    assert code == 0, out
    assert out.count(OLDER) == 1
    assert "Nothing was" not in out
    assert store.token_path().read_text() == app.books.token + "\n"
    assert [c[1] for c in calls(fake_claude)] == ["remove", "add"]


def test_a_stored_token_that_cannot_be_read_counts_as_none_kept(
    home, capsys, tmp_path, app, desktop, fake_claude
):
    lay_out_1_0_0(app.url, new_token())
    store.token_path().write_text("not a token\n")
    app.books.label = "office script"
    before = every_file(home)
    code, out = setup(capsys, app, tmp_path)
    assert code == 1, out
    assert out.rstrip().endswith(REFUSED)
    assert every_file(home) == before


# -- what setup says when the answer is short of something ------------------------


def answering(monkeypatch, app, status, payload):
    """/api/whoami answers status and payload; everything else as before."""
    real = app.books._get

    def short(path, params):
        if path == "/api/whoami":
            return status, payload
        return real(path, params)

    monkeypatch.setattr(app.books, "_get", short)


def who_body(**over) -> dict:
    body = {
        "company_file": FILE,
        "company_name": "Acme Fence",
        "role": "bookkeeper",
        "label": "office script",
        "ends_with_maker": False,
    }
    body.update(over)
    return body


@pytest.mark.parametrize(
    "over,said",
    [
        (
            # a company kept on PostgreSQL has no file and, here, no name
            {"company_file": "", "company_name": ""},
            (
                'That token is named "office script", so SlowBooks Pro keeps it '
                "working even after the administrator who made it is deactivated "
                "or is no longer an administrator there. Setup takes only a token "
                'whose name starts with "AI assistant" (for example "AI assistant '
                '(Switchboard)"), which SlowBooks Pro turns off when that happens. '
                "Make one in SlowBooks Pro under Settings, API Tokens. Nothing was "
                "changed."
            ),
        ),
        (
            {"label": None},
            (
                "SlowBooks Pro keeps that token working even after the "
                "administrator who made it is deactivated or is no longer an "
                "administrator in Acme Fence. Setup takes only a token whose name "
                'starts with "AI assistant" (for example "AI assistant '
                '(Switchboard)"), which SlowBooks Pro turns off when that happens. '
                "Make one in Acme Fence under Settings, API Tokens. Nothing was "
                "changed."
            ),
        ),
        (
            {"company_file": "", "company_name": "", "label": ""},
            (
                "SlowBooks Pro keeps that token working even after the "
                "administrator who made it is deactivated or is no longer an "
                "administrator there. Setup takes only a token whose name starts "
                'with "AI assistant" (for example "AI assistant (Switchboard)"), '
                "which SlowBooks Pro turns off when that happens. Make one in "
                "SlowBooks Pro under Settings, API Tokens. Nothing was changed."
            ),
        ),
    ],
    ids=["no company name", "no label", "neither"],
)
def test_the_refusal_reads_as_sentences_with_parts_missing(
    home, capsys, tmp_path, app, desktop, fake_claude, monkeypatch, over, said
):
    answering(monkeypatch, app, 200, who_body(**over))
    before = every_file(home)
    code, out = setup(capsys, app, tmp_path)
    assert code == 1, out
    assert out.rstrip().endswith(said)
    assert " in ." not in out and "named," not in out and "in  " not in out
    assert every_file(home) == before


def test_a_421_to_whoami_names_no_company_it_was_not_asked_for(
    home, capsys, tmp_path, app, desktop, fake_claude, monkeypatch
):
    answering(monkeypatch, app, 421, {"detail": "Misdirected Request"})
    before = every_file(home)
    code, out = setup(capsys, app, tmp_path)
    assert code == 1, out
    assert "None" not in out
    assert out.rstrip().endswith(
        f"SlowBooks Pro at {app.url} answered that it serves no company right "
        "now. Check that the company is open there, then try again. Nothing was "
        "saved."
    )
    assert every_file(home) == before
    assert not calls(fake_claude)


@pytest.mark.parametrize(
    "body", [[], None, "AI assistant"], ids=["list", "null", "string"]
)
def test_a_whoami_answer_that_is_not_an_object_stores_nothing(
    home, capsys, tmp_path, app, desktop, fake_claude, monkeypatch, body
):
    answering(monkeypatch, app, 200, body)
    before = every_file(home)
    code, out = setup(capsys, app, tmp_path)
    assert code == 1, out
    assert OLDER not in out  # it is no sign of an older app
    assert out.rstrip().endswith(
        f"What {app.url} answered about that token is not an answer SlowBooks "
        "Pro gives. Check that the address is SlowBooks Pro's, then try again. "
        "Nothing was saved."
    )
    assert every_file(home) == before
    assert not calls(fake_claude)


@pytest.mark.parametrize("mode", MODES)
def test_a_list_of_companies_is_left_as_it_is(
    home, capsys, tmp_path, app, desktop, fake_claude, mode
):
    lay_out_list(app.url, app.books.token)
    MODES[mode](app.books)
    before = store_files()
    code, out = setup(capsys, app, tmp_path, False)
    assert code == 0, out
    assert "so setup stores no token" in out
    assert "Nothing was" not in out and OLDER not in out
    assert store_files() == before
    assert whoami_asked(app) == 0
    assert [c[1] for c in calls(fake_claude)] == ["remove", "add"]
