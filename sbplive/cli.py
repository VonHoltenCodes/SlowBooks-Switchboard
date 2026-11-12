# ============================================================================
# The commands. With no command the program is the stdio server, so a host's
# configuration is the program alone; `serve` says the same thing aloud.
#
#   sbplive                 the server (what a host runs)
#   sbplive setup           connect to the books, store the token,
#                           connect Claude
#   sbplive status          what is set up and what answers
#   sbplive disconnect      take SlowBooks out of Claude
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
import sys

from sbplive import __version__, hosts, probe, store
from sbplive.config import (
    DEFAULT_URL,
    Config,
    ConfigError,
    check_token_text,
    check_url,
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
    _say(reached.sentence())
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


def cmd_status(args) -> int:
    from sbplive.config import load_config

    code = 0
    try:
        config = load_config()
    except ConfigError as exc:
        _say(str(exc))
        config = None
        code = 1
    if config is not None:
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
        removed = store.forget()
        if removed:
            _say(
                "The stored token was deleted. It still works in SlowBooks Pro "
                "until it is revoked there, under Settings, API Tokens."
            )
        else:
            _say("No token was stored.")
    return 0


def cmd_serve(args) -> int:
    from sbplive.server import run

    return run()


def cmd_serve_http(args) -> int:
    from sbplive.server import run_http

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
