# ============================================================================
# serve-http: the self-hosted remote door's runner. It opens remote.Door
# (Streamable HTTP on http.server) over the same registry as the stdio door
# (server.py), with the connector secret, and with OAuth sign-in when given
# a public address (oauth_door.py). Kept out of server.py so that a program
# embedding the tools (server.Mcp) carries nothing that prints the secret.
# Standard library only.
# ============================================================================

import logging

from sbplive import logs, store
from sbplive.client import ApiClient, urllib_transport
from sbplive.config import ConfigError, load_config
from sbplive.server import INSTRUCTIONS, build_registry

log = logging.getLogger("sbplive")


def _say(text: str = "") -> None:
    # The remote door's own words go to standard output (the console the
    # person started it in); its log goes to standard error and the file.
    print(text, flush=True)


def run_http(
    host: str,
    port: int,
    *,
    new_secret: bool = False,
    allowed_origins=(),
    public_url: str | None = None,
) -> int:
    """serve-http: the remote door, until Ctrl+C. With public_url, the door
    also signs Claude in with OAuth (oauth_door.py)."""
    from sbplive import oauth_door, probe, remote

    logs.setup()
    try:
        config = load_config()
        origins = tuple(remote.check_origin_arg(o) for o in allowed_origins)
        signin = oauth_door.make(config, public_url)
        secret, made = remote.ensure_secret(replace=new_secret)
    except ConfigError as exc:
        _say(str(exc))
        return 2
    client = ApiClient(urllib_transport(config))
    door = remote.Door(
        build_registry(client),
        INSTRUCTIONS,
        secret,
        allowed_origins=origins,
        oauth=signin.server if signin else None,
    )
    try:
        httpd = remote.make_server(door, host, port)
    except OSError as exc:
        _say(
            f"The remote door could not open on {host} port {port}: "
            f"{exc.strerror or exc}. Is another serve-http already running? "
            "Choose another port with --port."
        )
        return 1
    bound = httpd.server_address[1]
    url = remote.endpoint_url(host, bound)
    _say(f"The remote door is open at {url}")
    if not remote.is_loopback_host(host):
        _say(
            f"It listens on {host}, so other computers that can reach this one "
            "can knock too: the connector secret is what keeps them out. To "
            "keep it to this computer (and cloudflared on it), leave --host out."
        )
    if made:
        _say(
            "A new connector secret was made. It is shown this once and kept in "
            f"{store.secret_path()}, which only you can read:"
        )
        _say()
        _say(f"    {secret}")
        _say()
        hint = (
            "In Claude's Add custom connector form, under Request headers, "
            f"add the header authorization with the value: Bearer {secret}"
            + (
                " (only for a Claude account that has that section; OAuth "
                "sign-in, below, needs no secret)"
                if signin
                else ""
            )
        )
        _say(hint)
        if new_secret:
            _say("The connector secret used before this one no longer works.")
    else:
        _say(
            f"It uses the connector secret kept in {store.secret_path()} (not "
            "shown again; serve-http --new-secret makes a new one, and the old "
            "one then stops working)."
        )
    try:
        reached = probe.check(config, client)
        _say(reached.sentence())
        if signin:
            signin.token_role(reached.role)
    except probe.ProbeError as exc:
        _say(f"{exc} The door stays open; the tools answer once the app does.")
    if signin:
        _say(signin.sentence())
    _say(
        "Claude can do through it whatever the API token's role allows. Leave "
        "this running while Claude should reach the books; press Ctrl+C to "
        "close the door."
    )
    log.info("remote door open on %s port %s", host, bound)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    _say("The remote door is closed.")
    return 0
