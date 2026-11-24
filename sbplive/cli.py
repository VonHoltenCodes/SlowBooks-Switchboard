# ============================================================================
# The commands. With no command the program is the stdio server, so a host's
# configuration is the program alone; `serve` says the same thing aloud.
#
#   sbplive                 the server (what a host runs)
#   sbplive setup           connect to the books, store the token,
#                           connect Claude
#   sbplive status          what is set up and what answers
#   sbplive disconnect      take SlowBooks out of Claude
#   sbplive add-books       add another company's books, under its own
#                           token, to the same connection
#   sbplive remove-books    take one company's books off it
#   sbplive books           the companies kept, each checked
#   sbplive serve-http      the remote door: MCP over HTTP on this
#                           computer, for a tunnel
#
# From a checkout: python -m sbplive <command>.
#
# setup asks only what it must, and every question is also an option, so it
# runs without a terminal with --yes. The token is never an argument: it is
# typed at a prompt that does not echo, or read from --token-file (a path, or
# - for standard input).
# ============================================================================

import argparse
import getpass
import os
import secrets
import sys

from sbplive import __version__, hosts, probe, store, tools
from sbplive.config import (
    DEFAULT_URL,
    FILE_RE,
    MAX_BOOKS,
    Config,
    ConfigError,
    book_rows,
    check_token_text,
    check_url,
    hash_token,
    read_token_file,
)

PROG = "sbplive"


def _say(text: str = "") -> None:
    print(text, flush=True)


def _interactive(args) -> bool:
    return not args.yes and sys.stdin.isatty()


def _ask_yes(question: str, args) -> bool:
    if not _interactive(args):
        return True
    answer = input(f"{question} [Y/n] ").strip().lower()
    return answer in ("", "y", "yes")


def _url(args) -> tuple:
    if args.url:
        raw = args.url
    elif _interactive(args):
        raw = (
            input(
                "Where are the books? Press Enter for this computer "
                f"({DEFAULT_URL}), or type the address of the office's "
                "SlowBooks Pro server: "
            ).strip()
            or DEFAULT_URL
        )
    else:
        raw = DEFAULT_URL
    return check_url(raw)


def _token(args) -> str:
    if args.token_file == "-":
        return check_token_text(sys.stdin.readline(), "Standard input")
    if args.token_file:
        return read_token_file(args.token_file)
    if _interactive(args):
        typed = getpass.getpass(
            "Paste the API token from SlowBooks Pro, Settings, API Tokens (it "
            "starts with sbp_ and is not shown as you type): "
        )
        return check_token_text(typed, "The token you pasted")
    if store.has_token():
        _say("Using the API token already stored for this user.")
        return read_token_file(str(store.token_path()))
    raise ConfigError(
        "No API token was given. Pass --token-file with a file that holds it "
        "(or - to read it from standard input), or run setup in a terminal "
        "to paste it."
    )


def _connect_hosts(args) -> None:
    if args.no_claude_desktop:
        _say("Claude Desktop was left as it is (--no-claude-desktop).")
    else:
        files = hosts.desktop_files()
        if not files:
            _say(
                "Claude Desktop is not installed for this user, so it was not "
                "connected. Install it, then run setup again."
            )
        for path in files:
            if not _ask_yes(f"Connect Claude Desktop ({path})?", args):
                _say("Claude Desktop was left as it is.")
                continue
            try:
                backup = hosts.connect_desktop(path)
            except (hosts.HostError, OSError) as exc:
                _say(f"Claude Desktop was not connected: {exc}")
                continue
            kept = (
                f" The file as it was is saved as {backup}."
                if backup
                else f" Its configuration file {path} was created."
            )
            _say(
                f"Claude Desktop is connected.{kept} Quit Claude Desktop "
                "completely and open it again to see SlowBooks among its tools."
            )
    if args.no_claude_code:
        _say("Claude Code was left as it is (--no-claude-code).")
        return
    claude = hosts.find_claude()
    if not claude:
        _say(
            "Claude Code is not installed here (there is no claude command). "
            f"To connect it later, run: {hosts.shown(hosts.code_add_args())}"
        )
        return
    if not _ask_yes("Connect Claude Code for your user?", args):
        _say("Claude Code was left as it is.")
        return
    try:
        hosts.connect_code(claude)
    except (hosts.HostError, OSError, ValueError) as exc:
        _say(str(exc))
        return
    except Exception as exc:  # noqa: BLE001 - a timeout, a missing file
        _say(f"Claude Code was not connected: {exc}")
        return
    _say("Claude Code is connected for your user, in every project.")


def cmd_setup(args) -> int:
    if not args.yes and not sys.stdin.isatty():
        _say(
            "setup needs a terminal to ask its questions. To run it without "
            "one, pass --yes with --token-file (and --url for another computer)."
        )
        return 2
    _say("SlowBooks Switchboard setup")
    try:
        kept = book_rows()
    except ConfigError as exc:
        _say(str(exc))
        return 2
    other_url = False
    if kept and args.url and not args.token_file:
        # The address the companies are kept for, given again, is no
        # conflicting value: a script that always passes --url still
        # reconnects Claude. Another address is refused.
        try:
            given_url, _ = check_url(args.url, must_resolve=False)
            stored = store.stored_url()
            other_url = not stored or (
                check_url(stored, must_resolve=False)[0] != given_url
            )
        except ConfigError:
            other_url = True
    if kept and (args.token_file or other_url):
        # Taken without a word, a new token (one replacing a revoked one,
        # say) would look stored when it was not.
        given = "--token-file" if args.token_file else "--url"
        _say(
            f"This computer keeps {len(kept)} companies' books for SlowBooks "
            f"Switchboard ({PROG} books lists them), so setup stores no token "
            f"and {given} was not used. To use a new token for a company, run "
            f"{PROG} remove-books with its handle, then {PROG} add-books with "
            "the new token. Nothing was changed."
        )
        return 2
    if kept:
        # The connection already reaches several companies: one token from
        # setup would replace them all, so setup keeps them and only
        # connects Claude.
        _say(
            f"This computer keeps {len(kept)} companies' books for SlowBooks "
            f"Switchboard ({PROG} books lists them), so setup stores no token: "
            f"add a company with {PROG} add-books, or take one off with "
            f"{PROG} remove-books."
        )
        _connect_hosts(args)
        _say(f"Done. To check it later, run: {PROG} status.")
        return 0
    # A new token (none stored, or one other than the token stored) is
    # taken only if SlowBooks Pro ends it with its maker, as add-books does.
    # Setup run again with the token it kept, by this version or by 1.0.0,
    # never asks: a setup that works is left working.
    try:
        url, warning = _url(args)
        if warning:
            _say(warning)
        token = _token(args)
    except ConfigError as exc:
        _say(str(exc))
        return 2
    except (EOFError, KeyboardInterrupt):
        _say("\nSetup stopped; nothing was changed.")
        return 1
    try:
        reached = probe.check(Config(base_url=url, token=token, timeout=30))
    except probe.ProbeError as exc:
        _say(f"{exc} Nothing was stored.")
        return 1
    try:
        new = not store.has_token() or token != read_token_file(str(store.token_path()))
    except ConfigError:
        new = True  # a stored token that cannot be read is no kept one
    older = False
    if new:
        try:
            who = probe.whoami(Config(base_url=url, token=token, timeout=30))
        except probe.OlderApp:
            older = True
        except probe.ProbeError as exc:
            _say(str(exc))
            return 1
        else:
            if not who.ends_with_maker:
                _say(_setup_maker_refusal(who))
                return 1
    _say(reached.sentence())
    if older:
        _say(OLDER_MAKER)
    path = store.save(url, token)
    _say(f"The token is stored in {path}, which only you can read.")
    _connect_hosts(args)
    _say(
        "Done. To check it later, run: "
        f"{PROG} status. To take SlowBooks out of Claude: {PROG} disconnect."
    )
    return 0


def _status_hosts() -> None:
    files = hosts.desktop_files()
    if not files:
        _say("Claude Desktop is not installed for this user.")
    for path in files:
        if hosts.desktop_connected(path):
            _say(f"Claude Desktop is connected ({path}).")
        else:
            _say(f"Claude Desktop is installed but not connected ({path}).")
    if hosts.code_connected():
        _say("Claude Code is connected for your user.")
    elif hosts.find_claude():
        _say("Claude Code is installed but not connected for your user.")
    else:
        _say("Claude Code is not installed here.")


def _say_books(config) -> int:
    """One line per company kept, each checked; 1 when one does not answer."""
    code = 0
    for handle, book in config.books.items():
        ok, sentence = probe.book_sentence(config, handle, book)
        _say(f"- {sentence}")
        if not ok:
            code = 1
    return code


def cmd_status(args) -> int:
    from sbplive.config import load_config

    code = 0
    try:
        config = load_config()
    except ConfigError as exc:
        _say(str(exc))
        config = None
        code = 1
    if config is not None and config.books:
        _say(
            f"SlowBooks Switchboard is set up for {config.base_url}, with "
            f"{len(config.books)} companies' books:"
        )
        code = _say_books(config)
    elif config is not None:
        _say(f"SlowBooks Switchboard is set up for {config.base_url}.")
        try:
            _say(probe.check(config).sentence())
        except probe.ProbeError as exc:
            _say(str(exc))
            code = 1
    _status_hosts()
    _say(f"The log is at {store.log_path()}.")
    return code


def cmd_disconnect(args) -> int:
    for path in hosts.desktop_files():
        try:
            backup = hosts.disconnect_desktop(path)
        except (hosts.HostError, OSError) as exc:
            _say(f"Claude Desktop was not changed: {exc}")
            continue
        if backup is False:
            _say(f"Claude Desktop was not connected ({path}).")
        else:
            _say(
                "SlowBooks was taken out of Claude Desktop. The file as it was "
                f"is saved as {backup}. Quit Claude Desktop and open it again."
            )
    claude = hosts.find_claude()
    if claude:
        if hosts.disconnect_code(claude):
            _say("SlowBooks was taken out of Claude Code.")
        else:
            _say("Claude Code was not connected for your user.")
    elif hosts.code_connected():
        _say(
            "Claude Code still lists SlowBooks, and there is no claude command "
            f"here to remove it. Run: {hosts.shown(hosts.code_remove_args())}"
        )
    if args.forget:
        many = store.books_dir().is_dir()
        removed = store.forget()
        if removed and many:
            _say(
                "The stored tokens were deleted, every company's. Each still "
                "works in SlowBooks Pro until it is revoked there, under "
                "Settings, API Tokens, in that company."
            )
        elif removed:
            _say(
                "The stored token was deleted. It still works in SlowBooks Pro "
                "until it is revoked there, under Settings, API Tokens."
            )
        else:
            _say("No token was stored.")
    return 0


# -- several companies ----------------------------------------------------------

_BASE32 = "abcdefghijklmnopqrstuvwxyz234567"


def new_handle(file: str) -> str:
    """The company file's stem, "~" and six random base32 characters: new
    each time a company is added, and known only to this computer."""
    stem = file[: -len(".db")]
    return stem + "~" + "".join(secrets.choice(_BASE32) for _ in range(6))


def _books_url(args) -> str:
    stored = store.stored_url()
    raw = args.url or os.environ.get("SBPLIVE_URL") or stored or DEFAULT_URL
    url, warning = check_url(raw)
    if (args.url or os.environ.get("SBPLIVE_URL")) and stored:
        kept, _ = check_url(stored, must_resolve=False)
        if kept != url:
            given = "--url" if args.url else "SBPLIVE_URL"
            raise ConfigError(
                f"The books here are kept for {kept}, and one connection "
                f"reaches one SlowBooks Pro. Leave {given} out, or run {PROG} "
                "disconnect --forget first. Nothing was saved."
            )
    if warning:
        _say(warning)
    return url


def _book_token(args) -> str:
    if args.token_file == "-":
        return check_token_text(sys.stdin.readline(), "Standard input")
    if args.token_file:
        return read_token_file(args.token_file)
    if sys.stdin.isatty():
        typed = getpass.getpass(
            f"Paste the API token made in that company ({args.file}), from "
            "SlowBooks Pro, Settings, API Tokens (it starts with sbp_ and is "
            "not shown as you type): "
        )
        return check_token_text(typed, "The token you pasted")
    raise ConfigError(
        "No API token was given. Pass --token-file with a file that holds it "
        "(or - to read it from standard input), or run add-books in a "
        "terminal to paste it. Nothing was saved."
    )


def _role_refusal(who) -> str:
    if who.role in tools.ROLE_PHRASES:
        works = "That token " + tools.ROLE_PHRASES[who.role]
    else:
        works = "SlowBooks Pro did not say which role that token has"
    return (
        f"{works}; a company added here needs a bookkeeper token. Make one in "
        f"{who.name or who.file} under Settings, API Tokens. Nothing was saved."
    )


AI_NAME = "AI assistant"

OLDER_MAKER = (
    "This SlowBooks Pro cannot tell whether the token ends with the "
    "administrator who made it, and updating SlowBooks Pro will let setup "
    "check."
)


def _outlives_maker(who, what: str) -> tuple:
    """(what the token's name means, where to make another) for a token
    SlowBooks Pro does not end with its maker. A company with no name (one
    kept on PostgreSQL) and a token with no label still read as sentences."""
    company = who.name or who.file
    where = f"in {company}" if company else "there"
    if who.label:
        lead = f'{what} is named "{who.label}", so SlowBooks Pro keeps it working'
    else:
        lead = f"SlowBooks Pro keeps {what[0].lower()}{what[1:]} working"
    return (
        (
            f"{lead} even after the administrator who made it is deactivated "
            f"or is no longer an administrator {where}."
        ),
        f"Make one in {company or 'SlowBooks Pro'} under Settings, API Tokens.",
    )


def _maker_refusal(who, what: str) -> str:
    """add-books' sentence for a token SlowBooks Pro does not end with its
    maker."""
    means, make = _outlives_maker(who, what)
    return (
        f"{means} A company added here needs a bookkeeper token whose name "
        f'starts with "{AI_NAME}" (for example "{AI_NAME} (Switchboard)"), '
        f"which SlowBooks Pro turns off when that happens. {make}"
    )


def _setup_maker_refusal(who) -> str:
    """setup's: it adds no company, and it takes any role's token."""
    means, make = _outlives_maker(who, "That token")
    return (
        f"{means} Setup takes only a token whose name starts with "
        f'"{AI_NAME}" (for example "{AI_NAME} (Switchboard)"), which SlowBooks '
        f"Pro turns off when that happens. {make} Nothing was changed."
    )


def _setup_token(url: str):
    """setup's single token as the first row of the list, checked: (row,
    token), or ProbeError."""
    token = read_token_file(str(store.token_path()))
    config = Config(base_url=url, token=token, timeout=30)
    try:
        who = probe.whoami(config)
        if not who.file:
            raise probe.ProbeError(
                "The token setup stored works in a company SlowBooks Pro keeps "
                "on PostgreSQL, where a server serves one company, so no second "
                "company can be added. Nothing was saved."
            )
        probe.whoami(config, who.file)  # and it says so in its answer
    except probe.ProbeError as exc:
        raise probe.ProbeError(
            "The token setup stored is checked before a second company is "
            f"added, and it could not be: {exc}"
        ) from None
    if not who.ends_with_maker:
        raise probe.ProbeError(
            _maker_refusal(who, "The token setup stored")
            + f" Run {PROG} setup with it, then add the other company. "
            "Nothing was saved."
        )
    row = {
        "books": new_handle(who.file),
        "file": who.file,
        "name": who.name,
        "token_sha256": hash_token(token),
    }
    return row, token


def cmd_add_books(args) -> int:
    file = (args.file or "").strip()
    if not FILE_RE.fullmatch(file):
        _say(
            "--file must name the company's file as SlowBooks Pro names it, "
            "such as smith-plumbing.db: lowercase letters, digits, - and _, "
            "ending in .db. Nothing was saved."
        )
        return 2
    try:
        url = _books_url(args)
        token = _book_token(args)
    except ConfigError as exc:
        _say(str(exc))
        return 2
    except (EOFError, KeyboardInterrupt):
        _say("\nStopped; nothing was saved.")
        return 1
    with store.books_lock():
        try:
            rows = book_rows()
            moved = None
            if not rows and store.has_token():
                moved = _setup_token(url)
        except (ConfigError, probe.ProbeError) as exc:
            _say(str(exc))
            return 1
        kept = rows + ([moved[0]] if moved else [])
        sha = hash_token(token)
        for row in kept:
            if row["token_sha256"] == sha:
                _say(
                    f"That token is already saved for {row['name'] or row['file']}. "
                    "Nothing was saved."
                )
                return 1
        for row in kept:
            if row["file"] == file and moved and row is moved[0]:
                _say(
                    f"{row['name'] or file}'s books ({file}) are what the token "
                    "setup stored reaches already. Nothing was saved."
                )
                return 1
            if row["file"] == file:
                _say(
                    f"{row['name'] or file}'s books ({file}) are already kept as "
                    f"{row['books']}. To use another token for them, remove "
                    f"them first: {PROG} remove-books {row['books']}. Nothing "
                    "was saved."
                )
                return 1
        if len(kept) >= MAX_BOOKS:
            _say(f"{MAX_BOOKS} companies are kept already. Nothing was saved.")
            return 1
        try:
            who = probe.whoami(Config(base_url=url, token=token, timeout=30), file)
        except probe.ProbeError as exc:
            _say(str(exc))
            return 1
        if who.role != "bookkeeper":
            _say(_role_refusal(who))
            return 1
        if not who.ends_with_maker:
            _say(_maker_refusal(who, "That token") + " Nothing was saved.")
            return 1
        row = {
            "books": new_handle(file),
            "file": file,
            "name": who.name,
            "token_sha256": sha,
        }
        # The tokens first, the list last (replaced whole), and setup's file
        # only once the list holds it: a stop part way leaves either the old
        # state or the new one, never a company without its token.
        if moved:
            store.save_book_token(moved[0]["books"], moved[1])
        store.save_book_token(row["books"], token)
        settings = store.load_settings()
        settings["url"] = url
        settings["books"] = kept + [row]
        store.save_settings(settings)
        # setup's own file stays where it is: this version reads the list
        # and leaves it alone, and Switchboard 1.0.0, if it is put back,
        # still reaches that first company with it
    if moved:
        first = moved[0]
        _say(
            f"The token setup stored, for {first['name'] or first['file']}, is "
            f"now kept as {first['books']}."
        )
    _say(f"Added {who.name or file}'s books as {row['books']} (bookkeeper).")
    _say(
        "Claude names the books on every call, by the handle its context tool "
        f"lists. To see them all: {PROG} books."
    )
    return 0


def _remove_setup_token_of(row: dict) -> None:
    """setup's own token file goes with the company it reaches: add-books
    left it in place (for Switchboard 1.0.0), and once that company is
    removed it must not come back as the only company."""
    if not store.has_token():
        return
    try:
        token = read_token_file(str(store.token_path()))
    except ConfigError:
        return
    if hash_token(token) == row["token_sha256"]:
        store.remove_token()


def cmd_remove_books(args) -> int:
    handle = (args.handle or "").strip()
    with store.books_lock():
        try:
            rows = book_rows()
        except ConfigError as exc:
            _say(str(exc))
            return 1
        row = next((r for r in rows if r["books"] == handle), None)
        if row is None:
            listed = ", ".join(r["books"] for r in rows)
            _say(
                f"No company's books are kept here as {handle!r}. "
                + (f"These are: {listed}. " if rows else "None are kept. ")
                + "Nothing was changed."
            )
            return 1
        rest = [r for r in rows if r is not row]
        settings = store.load_settings()
        if rest:
            settings["books"] = rest
        else:
            settings.pop("books", None)
        store.save_settings(settings)
        store.remove_book_token(handle)
        _remove_setup_token_of(row)
    _say(
        f"Removed {row['name'] or row['file']}. Its token still works in "
        "SlowBooks Pro until you turn it off there (Settings, API Tokens)."
    )
    if not rest:
        _say(
            "No company's books are kept here now. To connect again, run: "
            f"{PROG} add-books, or {PROG} setup."
        )
    return 0


def cmd_books(args) -> int:
    from sbplive.config import load_config

    try:
        config = load_config()
    except ConfigError as exc:
        _say(str(exc))
        return 1
    if not config.books:
        _say(
            "This computer keeps one company's token, from setup, and no list "
            "of companies."
        )
        code = 0
        try:
            _say(probe.check(config).sentence())
        except probe.ProbeError as exc:
            _say(str(exc))
            code = 1
        _say(
            f"To add another company to the same connection: {PROG} add-books "
            "--file <its company file>."
        )
        return code
    _say(f"{len(config.books)} companies' books are kept for {config.base_url}:")
    return _say_books(config)


def cmd_serve(args) -> int:
    from sbplive.server import run

    return run()


def cmd_serve_http(args) -> int:
    from sbplive.serve_http import run_http

    return run_http(
        args.host,
        args.port,
        new_secret=args.new_secret,
        allowed_origins=args.allow_origin or (),
        public_url=args.public_url,
    )


def _port(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if not 0 <= value <= 65535:
        raise argparse.ArgumentTypeError("a port is a number from 0 to 65535")
    return value


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "SlowBooks Switchboard (sbplive) lets Claude work a SlowBooks Pro company's "
            "books through the app's own API. With no command, it runs the "
            "server that Claude starts."
        ),
    )
    p.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    sub = p.add_subparsers(dest="command", metavar="command")
    s = sub.add_parser(
        "setup",
        help="connect to the books, store the API token, connect Claude",
        description=(
            "Ask where the books are, take the API token, check it against "
            "SlowBooks Pro, store it for this user only, and connect Claude "
            "Desktop and Claude Code."
        ),
    )
    s.add_argument(
        "--url",
        help=f"the SlowBooks Pro address (default: this computer, {DEFAULT_URL})",
    )
    s.add_argument(
        "--token-file",
        metavar="PATH",
        help="a file that holds the API token, or - to read it from standard input",
    )
    s.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="ask nothing: take the defaults and connect every host found",
    )
    s.add_argument(
        "--no-claude-desktop", action="store_true", help="leave Claude Desktop alone"
    )
    s.add_argument(
        "--no-claude-code", action="store_true", help="leave Claude Code alone"
    )
    s.set_defaults(func=cmd_setup)
    st = sub.add_parser("status", help="say what is set up and whether it answers")
    st.set_defaults(func=cmd_status)
    d = sub.add_parser("disconnect", help="take SlowBooks out of Claude")
    d.add_argument(
        "--forget", action="store_true", help="also delete the stored API token"
    )
    d.set_defaults(func=cmd_disconnect)
    ab = sub.add_parser(
        "add-books",
        help="add another company's books to the same connection",
        description=(
            "Add one more company's books, under a bookkeeper token made in "
            "that company, so the same connection reaches it beside the "
            "others. The token is checked against SlowBooks Pro first: it "
            "must answer from that company file. The first time, the token "
            "setup stored is checked and kept as the first company."
        ),
    )
    ab.add_argument(
        "--file",
        required=True,
        metavar="FILE",
        help="the company's file in SlowBooks Pro, such as smith-plumbing.db",
    )
    ab.add_argument(
        "--token-file",
        metavar="PATH",
        help="a file that holds the token, or - to read it from standard input",
    )
    ab.add_argument(
        "--url",
        help=(
            "the SlowBooks Pro address, only when none is stored yet "
            f"(default: {DEFAULT_URL})"
        ),
    )
    ab.set_defaults(func=cmd_add_books)
    rb = sub.add_parser(
        "remove-books",
        help="take one company's books off the connection",
        description=(
            "Take one company's books off the connection and delete its "
            "stored token. The token still works in SlowBooks Pro until it is "
            "turned off there."
        ),
    )
    rb.add_argument("handle", help=f"the books' handle, as {PROG} books lists it")
    rb.set_defaults(func=cmd_remove_books)
    bk = sub.add_parser("books", help="list the companies kept, each checked")
    bk.set_defaults(func=cmd_books)
    sv = sub.add_parser("serve", help="run the server (the same as no command)")
    sv.set_defaults(func=cmd_serve)
    from sbplive.remote import DEFAULT_HOST, DEFAULT_PORT

    h = sub.add_parser(
        "serve-http",
        help="the remote door: serve Claude over HTTP, for a tunnel",
        description=(
            "Serve the same tools over MCP's Streamable HTTP transport at "
            "/mcp, for Claude on the web through your own tunnel "
            "(docs/REMOTE.md). With --public-url, Claude signs in with OAuth "
            "and the SlowBooks Pro administrator sign-in; the connector secret, "
            "made on first use and shown once, works beside it. The API token "
            "stays on this computer. Runs until Ctrl+C."
        ),
    )
    h.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"the address to listen on (default: {DEFAULT_HOST}, this computer only)",
    )
    h.add_argument(
        "--port",
        type=_port,
        default=DEFAULT_PORT,
        help=f"the port to listen on (default: {DEFAULT_PORT}; 0 picks a free one)",
    )
    h.add_argument(
        "--new-secret",
        action="store_true",
        help="make a new connector secret and show it; the old one stops working",
    )
    h.add_argument(
        "--allow-origin",
        action="append",
        metavar="ORIGIN",
        help="also accept requests a browser sends from this origin (repeatable)",
    )
    h.add_argument(
        "--public-url",
        metavar="URL",
        help=(
            "the tunnel's https:// address (such as https://books.example.com): "
            "turns on OAuth sign-in, so Claude signs in with the SlowBooks Pro "
            "administrator sign-in instead of needing a request header"
        ),
    )
    h.set_defaults(func=cmd_serve_http)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    func = getattr(args, "func", None) or cmd_serve
    if func is not cmd_serve:
        from sbplive import logs

        logs.setup()
    return func(args)
