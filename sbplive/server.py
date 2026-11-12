# ============================================================================
# The server: every tool registered with its description, input schema and
# annotations, over one ApiClient, and the two entry points: run() for the
# stdio door and run_http() for the remote door (remote.py). Both serve the
# same registry through the same protocol.Session, and serve_mcp() is the one
# answer every HTTP-shaped door gives an MCP message.
# Standard library only. A refusal from the API reaches the model as the
# API's own sentence, in a tool result marked as an error.
# ============================================================================

import logging
import sys

from sbplive import logs, operations, store, tools
from sbplive.client import ApiClient, urllib_transport
from sbplive.config import ConfigError, load_config
from sbplive.protocol import (
    HANDSHAKE_VERSIONS,
    META_VERSION,
    MODERN_VERSIONS,
    Session,
    serve_stdio,
)
from sbplive.registry import (
    DESTRUCTIVE,
    KEYED_WRITE,
    READ_ONLY,
    WRITE,
    Registry,
    Tool,
)

log = logging.getLogger("sbplive")

INSTRUCTIONS = (
    "Tools for working the books of a running SlowBooks Pro company through "
    "its own API. Call `context` first. The named tools cover everyday work; "
    "for anything else, `find_operations` finds the API operation, "
    "`describe_operation` says what it takes and `call_operation` makes the "
    "call. Amounts are decimal dollars as the API returns them; dates are ISO "
    "(YYYY-MM-DD). The API enforces the token's role, the closing date and "
    "validation: a refusal is a sentence to pass on to the person, not a "
    "reason to retry. Every write is audited as token:<label>. Use one stable key "
    "per payment so a retry can never book it twice; void instead of delete."
)

_DATA = (
    "The record's fields as the API takes them; describe_operation with "
    "POST and the route lists every field. Unknown fields are refused."
)
_KEY = (
    "A stable key for this one intended payment: 1 to 128 characters of "
    "A-Z a-z 0-9 _ . : -. The same key with the same data books once; the "
    "same key with other data is refused."
)
_KIND = "One of: " + ", ".join(tools.KINDS) + "."


def _tool(tool_name, title, fn, annotations, description, /, **params) -> Tool:
    return Tool(
        name=tool_name,
        title=title,
        description=description,
        fn=fn,
        annotations=annotations,
        params=params,
    )


def build_registry(client: ApiClient) -> Registry:
    reg = Registry(client)
    add = reg.add

    # -- reading --------------------------------------------------------------
    add(
        _tool(
            "context",
            "Company context",
            tools.context,
            READ_ONLY,
            "The company's name, type, closing date and the app's version. Read "
            "it before working: a change dated on or before the closing date is "
            "refused by the app.",
        )
    )
    add(
        _tool(
            "search",
            "Search the books",
            tools.search,
            READ_ONLY,
            "Find customers, vendors, items, invoices, bills and other records "
            "by name, number or amount, grouped by kind.",
            q="The words, number or amount to look for.",
            limit="The most records to return across all groups.",
        )
    )
    add(
        _tool(
            "list_records",
            "List records",
            tools.list_records,
            READ_ONLY,
            "List one kind of record. Filters pass through to the API as query "
            "parameters (for customers, {'search': 'Acme'}); describe_operation "
            "with GET and the kind's route lists them.",
            kind=_KIND,
            filters="Query parameters for the list route, by name.",
            limit="The most records to return.",
        )
    )
    add(
        _tool(
            "get_record",
            "Get one record",
            tools.get_record,
            READ_ONLY,
            "One record by kind and id, with its lines and balances.",
            kind=_KIND,
            record_id="The record's id.",
        )
    )
    add(
        _tool(
            "list_reports",
            "List reports",
            tools.list_reports,
            READ_ONLY,
            "The reports that can be run, each with its parameters.",
        )
    )
    add(
        _tool(
            "report",
            "Run a report",
            tools.report,
            READ_ONLY,
            "Run a report by name (see list_reports), such as profit-loss or "
            "balance-sheet, and return its figures. Dates and other options go "
            "in params, never beside name.",
            name="The report's name from list_reports.",
            params="The report's parameters, such as "
            "{'start_date': '2026-01-01', 'end_date': '2026-12-31'}.",
        )
    )
    add(
        _tool(
            "api_schema",
            "Route schema",
            tools.api_schema,
            READ_ONLY,
            "How to call one API route: its methods, parameters and request "
            "body. A prefix lists the routes under it.",
            path="A route such as /api/invoices, or a prefix such as /api/inv.",
        )
    )
    add(
        _tool(
            "api_get",
            "Read any route",
            tools.api_get,
            READ_ONLY,
            "A read-only GET of any API route not covered by another tool "
            "(see api_schema or find_operations).",
            path="The concrete route, such as /api/accounts or /api/invoices/12.",
            params="Query parameters by name.",
        )
    )

    # -- reaching every operation ----------------------------------------------
    add(
        _tool(
            "find_operations",
            "Find API operations",
            operations.find_operations,
            READ_ONLY,
            "Search the installed app's API (its OpenAPI document) for "
            "operations whose path, summary or tags match the words, with a "
            "line on what each does. Use it for anything the named tools do not "
            "cover, then describe_operation and call_operation.",
            query="Words such as 'credit memo', 'deposit', 'class' or 'budget'.",
            limit="The most operations to return, up to 50.",
        )
    )
    add(
        _tool(
            "describe_operation",
            "Describe an API operation",
            operations.describe_operation,
            READ_ONLY,
            "One operation's parameters and request body, with the schemas it "
            "refers to resolved, and whether it needs an idempotency key. Read "
            "it before call_operation.",
            method="GET, POST, PUT, PATCH or DELETE.",
            path="The path from find_operations, such as "
            "/api/invoices/{invoice_id}; a concrete path also works.",
        )
    )
    add(
        _tool(
            "call_operation",
            "Call an API operation",
            operations.call_operation,
            # It can make any call the token's role allows, deletes included,
            # so it carries the strictest hints; reads have their own tools.
            DESTRUCTIVE,
            "Make any API call the token's role allows, and return the API's "
            "answer. Path and query parameters go in params by name, the record "
            "in body. A payment-recording operation needs idempotency_key. The "
            "API decides what is allowed: pass a refusal on to the person "
            "rather than retrying.",
            method="GET, POST, PUT, PATCH or DELETE.",
            path="The path from find_operations, such as "
            "/api/invoices/{invoice_id}; a concrete path also works.",
            params="Path and query parameters by name, such as " "{'invoice_id': 12}.",
            body="The request body, when the operation takes one.",
            idempotency_key="Required where describe_operation says so: " + _KEY,
        )
    )

    # -- everyday writes -------------------------------------------------------
    for name, title, fn, what in (
        ("create_customer", "Create a customer", tools.create_customer,
         "Create a customer. Required: name."),
        ("create_vendor", "Create a vendor", tools.create_vendor,
         "Create a vendor. Required: name."),
        ("create_item", "Create an item", tools.create_item,
         "Create an item. Required: name and item_type."),
        ("create_invoice", "Create an invoice", tools.create_invoice,
         "Create an invoice. Required: customer_id, date and lines."),
        ("create_bill", "Enter a bill", tools.create_bill,
         "Enter a vendor's bill. Required: vendor_id, date and lines."),
        ("create_expense", "Record an expense", tools.create_expense,
         ("Record a paid expense. Required: date, paid_from_account_id, and "
          "either amount with expense_account_id or lines.")),
        ("create_journal_entry", "Post a journal entry",
         tools.create_journal_entry,
         ("Post a manual journal entry. Required: date, description and "
          "lines; debits must equal credits.")),
    ):  # fmt: skip
        add(
            _tool(
                name,
                title,
                fn,
                WRITE,
                what + " If the answer is lost, check with search before "
                "trying again.",
                data=_DATA,
            )
        )
    add(
        _tool(
            "record_payment",
            "Receive a payment",
            tools.record_payment,
            KEYED_WRITE,
            "Record a customer's payment. Required data: customer_id, date and "
            "amount; allocations apply it to invoices. A retry with the same "
            "key and data books it once.",
            data=_DATA,
            key=_KEY,
        )
    )
    add(
        _tool(
            "record_bill_payment",
            "Pay a bill",
            tools.record_bill_payment,
            KEYED_WRITE,
            "Pay a vendor. Required data: vendor_id, date and amount; "
            "allocations apply it to bills. A retry with the same key and data "
            "books it once.",
            data=_DATA,
            key=_KEY,
        )
    )
    add(
        _tool(
            "void_record",
            "Void a record",
            tools.void_record,
            DESTRUCTIVE,
            "Void an invoice, bill, expense, journal entry, payment or bill "
            "payment with a reversing entry. Nothing is deleted, but the record "
            "stops counting: confirm with the person first.",
            kind="One of: " + ", ".join(tools._VOID_PATHS) + ".",
            record_id="The record's id.",
            fee_refunded="Payments only: the provider gave the processing fee " "back.",
        )
    )
    return reg


# -- the MCP core of the HTTP-shaped doors ---------------------------------------
#
# An HTTP-shaped door (the remote door, remote.py) receives one JSON-RPC
# message with HTTP-style headers and answers a status, headers and a
# JSON-RPC answer. That answer is decided here, once, for every such door: the era
# is picked from the message itself (a version in _meta is 2026-07-28, served
# statelessly with its routing headers checked; initialize opens a
# 2025-11-25 session named by Mcp-Session-Id), exactly as docs/PROTOCOL.md
# says for the remote door. The transport around it (HTTP bodies and media
# types) stays with each door.


class Mcp:
    """The core every HTTP-shaped door serves: the registry, the open 2025-11-25 sessions
    and one Session for every stateless request."""

    def __init__(self, registry: Registry, instructions: str, sessions=None):
        from sbplive.remote import Sessions

        self.registry = registry
        self.instructions = instructions
        self.sessions = Sessions() if sessions is None else sessions
        # The modern era needs no state: one Session that never sees
        # initialize answers every stateless request (and, for a request
        # with neither a version nor a session, says how to begin).
        self.stateless = self.session()

    def session(self) -> Session:
        return Session(self.registry, self.instructions)


def serve_mcp(mcp: Mcp, message, headers, owner: str = "secret") -> tuple:
    """Serve one decoded JSON-RPC message for whichever era it belongs to.

    owner names the sign-in making the request (the connector secret, or an
    OAuth principal): a 2025-11-25 session belongs to the sign-in that opened
    it, and a request from another sign-in does not find it.

    headers answers get(name) and get_all(name), case-insensitively (an
    email.message.Message, as http.server gives them). Returns (status,
    headers to add, the JSON-RPC answer or None when there is none to send,
    as for a notification)."""
    if not isinstance(message, dict):
        # A batch, or not an object: the core's own sentence.
        return 400, {}, mcp.stateless.handle_message(message)
    if "id" not in message:
        return _notification(mcp, message, headers, owner)
    if "method" not in message:
        if "result" in message or "error" in message:
            return 202, {}, None  # a response: this server asks nothing
        return 400, {}, mcp.stateless.handle_message(message)
    method = message.get("method")
    params = message.get("params")
    meta = params.get("_meta") if isinstance(params, dict) else None
    if method != "initialize" and isinstance(meta, dict) and META_VERSION in meta:
        return _modern(mcp, message, headers)
    if method == "initialize":
        return _initialize(mcp, message, owner)
    return _handshake_era(mcp, message, headers, owner)


def _session_for(mcp: Mcp, sid: str, owner: str):
    """The open session sid, when it belongs to this sign-in."""
    session = mcp.sessions.get(sid)
    if session is None or getattr(session, "owner", owner) != owner:
        return None
    return session


def _notification(mcp: Mcp, message: dict, headers, owner: str = "secret") -> tuple:
    from sbplive.remote import SESSION_HEADER

    sid = headers.get(SESSION_HEADER)
    target = mcp.stateless
    if sid is not None:
        target = _session_for(mcp, sid, owner)
        if target is None:
            return 404, {}, None
    reply = target.handle_message(message)
    return (202, {}, None) if reply is None else (400, {}, reply)


def _modern(mcp: Mcp, message: dict, headers) -> tuple:
    from sbplive import remote

    problem = _header_problem(message, headers)
    if problem:
        return (
            400,
            {},
            remote._rpc_error(
                message.get("id"), remote.HEADER_MISMATCH, f"Header mismatch: {problem}"
            ),
        )
    reply = mcp.stateless.handle_message(message)
    status = 200
    if "error" in reply:
        status = remote._MODERN_STATUS.get(reply["error"].get("code"), 200)
    return status, {}, reply


def _initialize(mcp: Mcp, message: dict, owner: str = "secret") -> tuple:
    from sbplive import remote

    session = mcp.session()
    session.owner = owner  # later requests must come from the same sign-in
    reply = session.handle_message(message)
    if "error" in reply:
        return 400, {}, reply
    sid = mcp.sessions.add(session)
    if sid is None:
        return (
            503,
            {},
            remote._rpc_error(
                message.get("id"),
                remote.INVALID_REQUEST,
                "Too many open sessions; try again later.",
            ),
        )
    return 200, {remote.SESSION_HEADER: sid}, reply


def _handshake_era(mcp: Mcp, message: dict, headers, owner: str = "secret") -> tuple:
    from sbplive import remote

    id_ = message.get("id")
    version = headers.get(remote.VERSION_HEADER)
    if version in MODERN_VERSIONS:
        return (
            400,
            {},
            remote._rpc_error(
                id_,
                remote.INVALID_PARAMS,
                f"Invalid params: a {version} request carries {META_VERSION} "
                "and the client's capabilities in params._meta.",
            ),
        )
    sid = headers.get(remote.SESSION_HEADER)
    if sid is None:
        # No session: ping is answered; anything else is told how to
        # begin (400, as 2025-11-25 says for a missing session id).
        reply = mcp.stateless.handle_message(message)
        return (400 if "error" in reply else 200), {}, reply
    session = _session_for(mcp, sid, owner)
    if session is None:
        return (
            404,
            {},
            remote._rpc_error(
                id_,
                remote.INVALID_REQUEST,
                "Session not found: it ended or expired. Send initialize "
                "again without Mcp-Session-Id.",
            ),
        )
    if version is not None and version not in HANDSHAKE_VERSIONS:
        return (
            400,
            {},
            remote._rpc_error(
                id_,
                remote.INVALID_REQUEST,
                f"Unsupported {remote.VERSION_HEADER}: {version}; this session "
                f"speaks {', '.join(HANDSHAKE_VERSIONS)}.",
            ),
        )
    return 200, {}, session.handle_message(message)


def _header_problem(message: dict, headers) -> str | None:
    """Why a 2026-07-28 request's headers do not match its body, or None."""
    from sbplive import remote

    for name in remote._ROUTING_HEADERS:
        if len(headers.get_all(name) or []) > 1:
            return f"{name} appears more than once."
    params = message.get("params") or {}
    version = params["_meta"].get(META_VERSION)
    sent = headers.get(remote.VERSION_HEADER)
    if sent is None:
        return f"the {remote.VERSION_HEADER} header is missing."
    if sent != version:
        return f"the {remote.VERSION_HEADER} header does not match the body's version."
    method = message.get("method")
    sent = headers.get(remote.METHOD_HEADER)
    if sent is None:
        return f"the {remote.METHOD_HEADER} header is missing."
    if sent != method:
        return f"the {remote.METHOD_HEADER} header does not match the body's method."
    key = remote._NAME_BEARING.get(method)
    if key is not None and params.get(key) is not None:
        sent = headers.get(remote.NAME_HEADER)
        if sent is None:
            return f"the {remote.NAME_HEADER} header is missing."
        if remote.decode_header_value(sent) != params.get(key):
            return f"the {remote.NAME_HEADER} header does not match the body's {key}."
    return None


def run() -> int:
    logs.setup()
    try:
        config = load_config()
    except ConfigError as exc:
        log.error("%s", exc)
        return 2
    log.info("started for %s", config.base_url)
    # Only protocol messages go to stdout: anything printed by mistake goes
    # to stderr instead of corrupting the stream.
    out = sys.stdout.buffer
    sys.stdout = sys.stderr
    session = Session(build_registry(ApiClient(urllib_transport(config))), INSTRUCTIONS)
    try:
        return serve_stdio(session, sys.stdin.buffer, out)
    except KeyboardInterrupt:
        return 0


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
