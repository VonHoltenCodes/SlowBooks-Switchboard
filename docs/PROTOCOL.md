# The protocol revisions

SlowBooks Switchboard is a **dual-era** server: it speaks two revisions of
the Model Context Protocol, on stdio and over Streamable HTTP
with `serve-http`, so a client of either era connects through either door.
The HTTP rules are in [The remote door](#the-remote-door-streamable-http)
below.

| Era | Revision | How a client uses it |
|---|---|---|
| Modern | **2026-07-28** | No handshake. Every request carries the version and the client's capabilities in `_meta`; `server/discover` says what the server speaks |
| Handshake (the specification calls it *legacy*) | **2025-11-25** | `initialize`, then `notifications/initialized`, then requests |

The revisions are constants in `sbplive/protocol.py` and nowhere
else: `MODERN_VERSIONS` (served per request), `HANDSHAKE_VERSIONS` (served
through `initialize`) and `SUPPORTED_VERSIONS`, the two together, newest
first, which is what `server/discover` lists and what an unsupported-version
error names. `PROTOCOL_VERSION`, 2025-11-25, stays the one answer to
`initialize`.

The pinned official client for the contract test is **`mcp` 2.3.0** (the
Python SDK, `requirements-dev.txt`). It has three ways to connect, and the
contract test runs all three against this server:

| `mode=` | What the client does | What it lands on here |
|---|---|---|
| `"2026-07-28"` | Modern only: no probe and no handshake; the version in every request's `_meta` | 2026-07-28 |
| `"auto"` (its default) | Probes `server/discover`, stays modern on a `DiscoverResult`, falls back to `initialize` otherwise | 2026-07-28 (the probe is answered) |
| `"legacy"` | `initialize`, exactly as before 2026 | 2025-11-25 |

## Sources

Read on **2026-11-06**:

- [Versioning](https://modelcontextprotocol.io/specification/versioning):
  2026-07-28 is the current revision.
- 2026-07-28, [Versioning and Compatibility](https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning),
  including *Backward Compatibility with Initialization-Based Versions* and
  its compatibility matrix. (The 2026-07-28 lifecycle page now serves the
  same text: there is no separate lifecycle any more.)
- 2026-07-28, [Base protocol overview](https://modelcontextprotocol.io/specification/2026-07-28/basic/index):
  messages, `resultType`, error codes, statelessness, `_meta` and its
  per-request and per-response fields.
- 2026-07-28, [Discovery](https://modelcontextprotocol.io/specification/2026-07-28/server/discover).
- 2026-07-28, [stdio](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio),
  including its *Backward Compatibility* section.
- 2026-07-28, [Tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)
  and [Caching](https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/caching).
- 2026-07-28, [Key Changes](https://modelcontextprotocol.io/specification/2026-07-28/changelog)
  since 2025-11-25.
- The [2026-07-28 schema](https://github.com/modelcontextprotocol/specification/blob/main/schema/2026-07-28/schema.ts):
  `RequestMetaObject`, `Result`, `UnsupportedProtocolVersionError`,
  `DiscoverRequest`, `DiscoverResult`, `CacheableResult`, `ListToolsResult`,
  `CallToolResult`, `ToolAnnotations` (unchanged from 2025-11-25: the same
  four hints and defaults), `ClientRequest` (no `ping`, no `initialize`).
- The official client's own code, `mcp` 2.3.0: `mcp/client/_probe.py` (the
  `auto` probe) and `mcp/client/client.py` (the modes).
- The 2025-11-25 pages listed under "The handshake era" below, read the same
  day for the first version of this server.

## How a message picks its era

The 2026-07-28 versioning page: *"A dual-era server selects its behavior from
how the client opens: A request carrying modern per-request `_meta` is served
statelessly according to this revision. An `initialize` request selects
legacy semantics, scoped to the stdio process (stdio) … A dual-era server MAY
serve both eras concurrently on the same endpoint or process."*

So, for each request:

1. **`initialize`** is the handshake, whatever its `_meta`: 2026-07-28 has no
   such method, so it can only be the handshake era.
2. **A request whose `_meta` has `io.modelcontextprotocol/protocolVersion`**
   is modern, and is served by its own `_meta` alone, before or after an
   `initialize`.
3. **Anything else** is a handshake-era request: served as 2025-11-25 if the
   process has been initialized; otherwise refused (see the table).

### A mixed session

This server takes the **MAY** above: it serves both eras side by side in one
process.

- Modern requests first, then `initialize`: the handshake opens as usual.
  This is exactly what a dual-era client does when its probe fails, and what
  the official client's `_probe.py` allows for when a probe times out.
- `initialize` first, then a modern request: the modern request is answered
  as 2026-07-28 and uses nothing from the handshake, because *"Servers MUST
  NOT rely on prior requests over the same connection to establish context
  (e.g., capabilities, protocol version, client identity)"* (Base protocol,
  Statelessness). The handshake stays open beside it; handshake-era requests
  keep their 2025-11-25 answers, and a second `initialize` is still refused.
- The process never locks to one era. Locking was the other reading of
  "scoped to the stdio process"; serving both is what the MAY sentence
  permits and loses nothing, since a modern request needs no state.

## The modern era: the rules applied (2026-07-28)

| Rule | Where it says so | What this server does |
|---|---|---|
| Every request declares its version in `_meta` `io.modelcontextprotocol/protocolVersion`; `io.modelcontextprotocol/clientCapabilities` is required too | Base protocol, per-request protocol fields | Both are read from each request |
| *"A request missing any required field is malformed; the server MUST reject it with JSON-RPC error code `-32602`"* | Base protocol, `_meta` | `-32602` for: a version that is not a non-empty string, capabilities missing or not an object, `clientInfo` not an object, `_meta` not an object |
| An unsupported version **MUST** get `UnsupportedProtocolVersionError`, `-32022`, `data: {supported, requested}` | Versioning, Protocol Version Negotiation | `-32022` with `supported` = `SUPPORTED_VERSIONS` and the requested version, checked before the method |
| Servers **MUST** implement `server/discover` | Versioning; Discovery | Answered with `supportedVersions`, `capabilities` (`tools`, `listChanged: false`), `instructions`, and `serverInfo` in `_meta` |
| Every result **MUST** carry `resultType` | Base protocol, ResultType; schema `Result` | `"complete"` on every modern result (this server never needs more input) |
| Servers **SHOULD** put `io.modelcontextprotocol/serverInfo` in every result's `_meta` | Base protocol, per-response fields | On every modern result |
| `server/discover` and `tools/list` **MUST** carry `ttlMs` (>= 0) and `cacheScope` | Caching; schema `CacheableResult` | `ttlMs` one hour (`CACHE_TTL_MS`) and `"public"`: the tool list is fixed for the process and the same for every caller, whatever the token's role, which the caching page names as the case for `"public"` |
| Tools in a deterministic order | Tools, Capabilities | The registry's order, always the same |
| An unknown tool or a malformed call is a protocol error (`-32602`); a failed call, input validation included, is a result with `isError` | Tools, Error Handling | Unchanged from 2025-11-25 |
| `ping`, `logging/setLevel` and the handshake are removed | Key Changes 2 and 5; schema `ClientRequest` | A modern `ping` (or any other method not offered) is `-32601` |
| Notifications from the client: only `notifications/cancelled` | Schema `ClientNotification` | Ignored, as before: requests are answered in order and at once |
| stdio framing: one message per line, UTF-8, nothing but MCP on stdout | stdio | Unchanged |
| The server **MUST NOT** write requests to stdout | stdio, Receiving Messages | It sends none in either era |
| The server **MUST NOT** emit codes `-32020` to `-32099` other than those defined, nor `-32002` or `-32042` | Base protocol, Error Codes | Only `-32022` from that range |

## The stdio probe, and requests with no version before `initialize`

The stdio page: a dual-era client **SHOULD** probe with `server/discover`
carrying its modern version. A `DiscoverResult` makes it modern; a recognized
modern error such as `-32022` makes it retry with a listed version and **not**
fall back; any other error, or silence, makes it fall back to `initialize`.

- A probe with a version: answered, so dual-era clients (the official one in
  `auto`; Claude Code too, which by its release notes negotiates 2026-07-28
  with stdio servers since 2.1.292) now connect modern.
- A probe at a version this server does not speak gets `-32022` listing
  2026-07-28 and 2025-11-25. A dual-era client then retries at 2026-07-28;
  the official client also falls back to `initialize` when the list names a
  handshake-era version and no mutual modern one.
- A request with **no version and no handshake**, `server/discover` or
  `tools/list` or `tools/call`: the only era that could serve it is the
  modern one, which calls it malformed, so it is **`-32602`** with a sentence
  naming both ways in (send `initialize`, or put the version in `_meta`).
  This replaced the `-32600` the handshake-only server gave a tool request
  before `initialize`; the 2025-11-25 text leaves that answer to the server,
  and a dual-era client falls back on either.
- `ping` with no version is the 2025-11-25 `ping` and is answered before and
  after `initialize`, as that revision allows.

## The handshake era (2025-11-25), unchanged

Read on 2026-11-06 from:

- [Base protocol](https://modelcontextprotocol.io/specification/2025-11-25/basic):
  JSON-RPC 2.0; a request id is a string or an integer and never null; a
  notification is never answered.
- [Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle):
  `initialize` first; the server answers with the client's version if it
  supports it, else another it supports (here, always 2025-11-25), and the
  client disconnects if it cannot speak that; then
  `notifications/initialized`. Only `ping` is expected before.
- [Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports):
  stdio, UTF-8, one message per line with no embedded newline, nothing but
  protocol messages on stdout, logs on stderr.
- [Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools):
  `tools/list` and `tools/call`; an unknown tool or a malformed call is a
  protocol error; a failed call, input validation included, is a result with
  `isError: true`. Input schemas default to JSON Schema 2020-12.
- [Schema](https://github.com/modelcontextprotocol/specification/blob/main/schema/2025-11-25/schema.ts),
  `ToolAnnotations`: `readOnlyHint` (default false), `destructiveHint`
  (default true, meaningful only for a tool that writes), `idempotentHint`
  (default false), `openWorldHint` (default true). Every tool here sets all
  four.

Handshake-era results carry no `resultType`, `ttlMs` or `_meta` serverInfo:
the answers are byte for byte what they were.

## Every message and its answer

| Message | Answer |
|---|---|
| A line that is not UTF-8 or not JSON | `-32700 Parse error`, id null |
| A JSON array (a batch) | `-32600 Invalid Request`, id null: batching was removed from MCP in 2025-06-18 |
| Not an object, no `jsonrpc: "2.0"`, no method, a null or non-integer id | `-32600 Invalid Request` |
| `params` that is not an object, or `_meta` that is not an object | `-32602 Invalid params` |
| **Modern** (a version in `_meta`): a version this server does not serve per request, 2025-11-25 included | `-32022`, `data: {supported: ["2026-07-28", "2025-11-25"], requested}` |
| **Modern**: a version that is not a string, capabilities missing or not an object, `clientInfo` not an object | `-32602` |
| **Modern**: `server/discover` | `DiscoverResult` |
| **Modern**: `tools/list`, `tools/call` | As in 2025-11-25, plus `resultType: "complete"`, serverInfo in `_meta`, and on `tools/list` `ttlMs` and `cacheScope` |
| **Modern**: `ping`, or any method not offered | `-32601 Method not found` |
| No version, no handshake yet: `tools/list`, `tools/call`, `server/discover` | `-32602` with a sentence |
| No version: `server/discover` after `initialize` | `-32602` |
| An unknown method, in either era | `-32601 Method not found` |
| `initialize` (with or without `_meta`) | The 2025-11-25 handshake; a second one is `-32600` |
| `initialize` without a string `protocolVersion` | `-32602 Invalid params` |
| Any notification | No answer; `notifications/initialized` marks the handshake ready |
| A response from the client | Ignored: this server sends no requests |
| `tools/call` for an unknown tool, or without a name | `-32602` |
| Arguments that do not fit the tool's schema | A tool result with `isError` and a sentence |
| A refusal from the API | A tool result with `isError` and the API's sentence |

Capabilities declare `tools` only (`listChanged: false`), in both eras. There
are no resources, prompts, logging, subscriptions or tasks.

## What the specification left unclear

1. **Whether a dual-era server lists its handshake-era revision.** The
   versioning page's own `-32022` example lists `["2026-07-28",
   "2025-11-25"]`, so this server does, in the error and in
   `supportedVersions`. But `DiscoverResult.supportedVersions` is described
   as versions "for use in subsequent requests", and 2025-11-25 cannot be
   used per request. A modern client picks a modern version from the list
   (the official one does); the official client reads a list with only
   handshake-era versions as "legacy", which this list is not.
2. **A modern request at 2025-11-25.** The server speaks that revision, but
   not per request. The spec's MUST ("does not implement the requested
   version") points to `-32022`; the message says it is served through
   `initialize`.
3. **A request with no version before `initialize`.** The 2025-11-25 text
   leaves pre-`initialize` answers to the server; the 2026-07-28 text makes
   a request missing its required `_meta` fields `-32602`. This server uses
   `-32602`.
4. **"Scoped to the stdio process".** Read together with the MAY on serving
   both eras concurrently: the handshake's state belongs to the process, and
   modern requests beside it are still served. Locking the process to the
   handshake would also have been a reading.
5. **The cache lifetime.** The caching page asks for a hint, not a value;
   one hour is this server's choice. A new version of the program is a new
   process, and the tool list changes only with one.

## The remote door (Streamable HTTP)

`sbplive serve-http` (`sbplive/remote.py`) puts
the same `Session` and tool registry behind one HTTP endpoint. No tool logic
is in it: it decides the era and the HTTP status, checks the headers the
transport adds, and hands the JSON-RPC message to the core exactly as the
stdio loop does.

### Sources

Read on **2026-11-06**:

- 2026-07-28, [Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http):
  Security & Endpoint, Sending Messages, Receiving Messages, Request
  Metadata (the protocol version header, the standard request headers, value
  encoding, server validation), Backward Compatibility and *Earlier
  Streamable HTTP Revisions*.
- 2026-07-28, [Versioning and Compatibility](https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning),
  the compatibility matrix again for its HTTP column, and *"An `initialize`
  request selects legacy semantics, scoped to the stdio process (stdio) or
  the session (HTTP)"*.
- 2025-11-25, [Transports, Streamable HTTP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports#streamable-http):
  Security Warning, Sending Messages to the Server, Listening for Messages
  from the Server, Session Management, Protocol Version Header.
- The official SDK's code, `mcp` 2.3.0: `mcp/server/_streamable_http_modern.py`
  and `mcp/shared/inbound.py` (its header ladder and its table from JSON-RPC
  error code to HTTP status), `mcp/server/streamable_http_manager.py`
  (sessions), `mcp/client/streamable_http.py` (what the client does with a
  405, a 404 and a JSON error body). Read to match the reference client, not
  as the rule: where it and the text differ, the text wins.

### What it answers

| Request | Answer |
|---|---|
| Any path but `/mcp` (or `/mcp/`; a query string is ignored) | `404`, empty body, before anything else is looked at. No pages, no listing, nothing of SlowBooks Pro's; `/.well-known/...` is 404 too, unless `--public-url` turned OAuth on: then the two protected resource documents, the authorization server document and `/oauth/authorize`, `/oauth/token`, `/oauth/register` are served ([Authorization](#authorization-oauth-serve-http---public-url)) |
| An `Origin` that is not allowed (below) | `403`, a JSON-RPC error with no id |
| An address with too many failed sign-ins | `429` with `Retry-After`, empty body |
| No `Authorization: Bearer <connector secret>`, or the wrong one | `401` with `WWW-Authenticate: Bearer`, empty body; counted against the address. With OAuth on, a valid access token is taken too, the 401 carries `resource_metadata` and `scope`, and only a wrong token is counted |
| `GET`, `PUT`, `PATCH`, `OPTIONS`, `HEAD`, anything else | `405` with `Allow: POST, DELETE` |
| `DELETE` with a known `Mcp-Session-Id` | `200`: the session ends |
| `DELETE` with an unknown one / without one | `404` / `405` |
| `POST` whose `Content-Type` is not `application/json` | `415` |
| `POST` whose `Accept` (when given) does not take `application/json` | `406` |
| `POST` without `Content-Length` or chunked encoding / over 4 MiB | `411` / `413` (connection closed) |
| `POST` body that is not JSON | `400`, `-32700`, id null |
| A batch (a JSON array), or not an object | `400`, `-32600` |
| A notification | `202`, empty body (with an `Mcp-Session-Id` it goes to that session, so `notifications/initialized` marks it ready; an unknown session is `404`) |
| A response from the client | `202`: this server sends no requests |
| **2026-07-28** request (a version in `_meta`, method not `initialize`) | The header checks below, then the core; `200`, or the error's status from the table below; never an `Mcp-Session-Id` |
| **2025-11-25** `initialize` | `200` and a new `Mcp-Session-Id`; an `initialize` the core refuses is `400` and opens nothing; more than 256 open sessions is `503` |
| **2025-11-25** request with a known `Mcp-Session-Id` | `200` with the core's answer, an error included (as on stdio) |
| ... with an unknown or expired `Mcp-Session-Id` | `404` and a JSON-RPC error saying to initialize again |
| ... with an `MCP-Protocol-Version` that is not 2025-11-25 | `400`, `-32600` |
| ... with no `Mcp-Session-Id` | `ping` is answered (`200`); anything else is the core's `-32602` sentence (send `initialize`, or the version in `_meta`) at `400` |
| A request without a version in `_meta` but with `MCP-Protocol-Version: 2026-07-28` | `400`, `-32602`: a modern request carries its version in the body |

Every answer to a request is one `application/json` body with its
`Content-Length`. There is no SSE stream anywhere.

### The rules applied

| Rule | Where it says so | What this server does |
|---|---|---|
| One MCP endpoint path, POST (2026-07-28); POST and GET (2025-11-25) | Both revisions | `/mcp`; GET is offered as the 405 2025-11-25 allows |
| Every other path | Not in the specification | `404`: the step's own rule. Nothing is proxied to SlowBooks Pro; the app is reached only through the tools |
| A request is answered with `application/json` or `text/event-stream`; *"The client MUST support both"* | Both, Sending Messages | Always `application/json`: the server sends no notifications or requests, so a stream would carry only the response |
| The server MAY send notifications on an SSE response; the final response SHOULD end it; `X-Accel-Buffering: no` when it opens one | 2026-07-28, Receiving Messages | Not used (no stream) |
| GET: *"The server MUST either return `Content-Type: text/event-stream` ... or else return HTTP 405 Method Not Allowed"* | 2025-11-25, Listening for Messages | `405`, `Allow: POST, DELETE` |
| A modern-only server SHOULD answer GET and DELETE with 405, ignore `Mcp-Session-Id`, and mint none | 2026-07-28, Earlier Streamable HTTP Revisions | GET 405; a modern request's `Mcp-Session-Id` is ignored and none is minted for it. DELETE ends a 2025-11-25 session (that revision's rule), else 405 |
| A notification (or, in 2025-11-25, a response) the server accepts: `202 Accepted`, no body | Both, Sending Messages | `202` |
| Notifications are never answered with a JSON-RPC message | Base protocol | Unchanged: the core returns nothing for them |
| **Origin**: *"Servers MUST validate the `Origin` header on all incoming connections"*; present and invalid: `403`, the body MAY be a JSON-RPC error with no id | Both, Security | Allowed: no `Origin` at all; `http(s)://localhost`, `127.0.0.1` or `[::1]` on any port; `https://claude.ai` and `https://claude.com`; whatever `--allow-origin` adds. Anything else, `null` included, is 403 with an id-less `-32600` |
| Bind only to localhost when running locally (SHOULD) | Both, Security | `--host` defaults to `127.0.0.1`; another address prints a warning |
| *"Servers SHOULD implement proper authentication for all connections"* | Both, Security | OAuth with `--public-url` ([Authorization](#authorization-oauth-serve-http---public-url)); the connector secret by bearer token beside it, and alone without `--public-url` |
| `MCP-Protocol-Version` on every POST, equal to `_meta`'s version, else `400` and `HeaderMismatch` `-32020` | 2026-07-28, Protocol Version Header | Checked on every modern request: missing or different is `400 -32020` |
| An unsupported version: `400` and `UnsupportedProtocolVersionError` | 2026-07-28, Protocol Version Header | `400 -32022` with `supported` and `requested` (from the core) |
| An unimplemented method: `404` and `-32601`, the JSON-RPC body telling it from a plain 404 | 2026-07-28, Protocol Version Header | `404 -32601` with the body |
| `Mcp-Method` on all requests; `Mcp-Name` for `tools/call`, `resources/read`, `prompts/get`; REQUIRED | 2026-07-28, Standard Request Headers | Both required on modern requests and compared with the body; missing or different is `400 -32020`. `Mcp-Name` is compared only when the body has the name (the official SDK does the same) |
| `=?base64?...?=` values MUST be decoded before comparing | 2026-07-28, Value Encoding | Decoded; malformed or non-canonical base64 never matches |
| Header names are case-insensitive, values case-sensitive | 2026-07-28, Case Sensitivity | `http.server` folds names; values are compared exactly |
| A routing header given twice | Not in the text; the SDK rejects it (`find_duplicated_routing_header`) | `400 -32020`: a first-copy and a last-copy reader would disagree |
| `Mcp-Param-{Name}` from `x-mcp-header` | 2026-07-28, Custom Headers | No tool declares `x-mcp-header`, so no `Mcp-Param-*` header is recognized; they are ignored, as an unrecognized header must be |
| Status of other modern JSON-RPC errors | 2026-07-28 names 400 for header and version failures and `MissingRequiredClientCapabilityError`, 404 for `-32601` | As the SDK's table: `-32700`, `-32600`, `-32602`, `-32020`, `-32022` are `400`; `-32601` is `404`; anything else (`-32603`) stays `200`. A tool's own failure is a result with `isError`, `200` |
| Dual-era: modern `_meta` is served statelessly; `initialize` selects legacy semantics *"scoped ... to the session (HTTP)"*; both MAY be served on the same endpoint | 2026-07-28, Versioning | Era picked per request by the body, as on stdio (the SDK picks by the `MCP-Protocol-Version` header and notes body-first as its follow-up); every modern request goes to one `Session` that never sees `initialize`; each `initialize` gets a `Session` of its own |
| Session id: MAY be assigned in the `initialize` response's `MCP-Session-Id`; globally unique, cryptographically secure, visible ASCII | 2025-11-25, Session Management | Always assigned: 32 characters of `secrets.token_urlsafe` |
| Requests without the session id SHOULD get `400`; a terminated session MUST get `404`; DELETE MAY be refused with 405 | 2025-11-25, Session Management | `400` (but `ping`, which needs no session, is answered), `404`, DELETE honoured |
| `MCP-Protocol-Version` on later requests; absent: assume the negotiated version (or 2025-03-26 if none); invalid or unsupported: `400` | 2025-11-25, Protocol Version Header | In a session, absent is 2025-11-25 (the session's own), anything other than 2025-11-25 is `400`. `initialize` itself is not checked: its version is in its body |
| Resumability, `Last-Event-ID`, server-initiated requests on SSE | 2025-11-25 (MAY) | None: there are no streams to resume |
| Clients MUST send `Accept` listing `application/json` and `text/event-stream` | Both | A server-side check is not required by the text: a given `Accept` that does not take JSON is `406` (the SDK's answer); none at all is taken to mean anything |
| Closing the response stream is cancellation (2026-07-28) | 2026-07-28, Cancellation | A request is answered as soon as the app answers; a client that disconnects loses the answer, and a write already sent to the app stands (payments carry their idempotency key, so a retry books once) |

### Sign-in and limits

- **The connector secret**: `sbc_` and 43 characters of
  `secrets.token_urlsafe(32)` (256 random bits), made by `serve-http` on its
  first run, written with mode 600 to `connector-secret` beside the API token,
  shown once on the console, replaced by `serve-http --new-secret`. It is
  compared with `hmac.compare_digest`. It is **not** the API token: the API
  token is only ever sent to SlowBooks Pro, from this computer.
- **The answer to a failed sign-in** is `401` with `WWW-Authenticate: Bearer`
  and no body: nothing says whether the header was missing, malformed or
  wrong. (For Anthropic's client a `401` is what starts an OAuth sign-in
  ([authentication](https://claude.com/docs/connectors/building/authentication.md));
  with nothing at `/.well-known/` (404 here) the
  [troubleshooting page](https://claude.com/docs/connectors/building/troubleshooting.md)
  says the connection fails with "Couldn't reach the MCP server". Whether a
  connector set up with request headers goes looking at all, the pages do
  not say. Either way a wrong secret does not get in.)
- **The failure count**: 10 failed sign-ins from one address within 10
  minutes and that address gets `429` until its oldest failure is 10
  minutes old, the right secret included. Up to 4,096 addresses are
  remembered. Through `cloudflared` every connection comes from this
  computer, so the address is the `CF-Connecting-IP` header, believed only on
  a connection from a loopback address (anywhere else it could be forged).
  With 256 random bits the count is not what stops guessing; it keeps a
  stranger's noise out of the log and off the app.
- **Limits**: a request body of 4 MiB; 256 open 2025-11-25 sessions, each
  forgotten after an hour unused (its id is then `404`, and the client
  initializes again); a connection silent for 60 seconds is closed.
- **The log** (the same file and standard error as the stdio door): the
  HTTP method, "the endpoint", "the sign-in (<one of the fixed OAuth
  paths>)" or "another path", the status, the client address, and a warning
  line for each refused sign-in or Origin. Never the path (it can be the
  secret), a query (it carries codes), a header value or a body; the log's
  mask also covers anything shaped like `sbc_...` or `sbo_...` (OAuth codes
  and tokens). `http.server`'s own request lines are
  switched off, and its error pages carry no text.

### What the specifications left unclear

1. **Which `Origin` Claude's servers send.** Anthropic's
   [testing page](https://claude.com/docs/connectors/building/testing.md)
   lists *"Strict `Origin`-header validation: an overly strict check rejects
   Anthropic's requests"* as a cause of failure without naming a value. This
   server lets a request with no `Origin` through and allows
   `https://claude.ai` and `https://claude.com` by guess; if the real walk
   through is refused with 403, the log names it and `--allow-origin` adds it.
2. **The HTTP status of a JSON-RPC error inside a 2025-11-25 session.** The
   2025-11-25 text gives none; the SDK's handshake-era server answers them at
   `200`, and so does this one. The 2026-07-28 table applies to modern
   requests only.
3. **A request with no version, no `initialize` and no session.** On stdio
   this is the core's `-32602` sentence; over HTTP it is the same at `400`,
   which both the 2025-11-25 "missing session id" SHOULD and the 2026-07-28
   status table agree on.
4. **How a dual-era HTTP server picks the era.** The text says *how the
   client opens*; the SDK reads the `MCP-Protocol-Version` header first.
   This server reads the body, as on stdio, and then requires the header to
   agree, so a client that disagrees with itself is told so (`-32020`).
5. **Long calls behind a proxy.** The SDK switches a slow answer to an SSE
   stream with keep-alive comments after 15 seconds so a proxy's idle timeout
   does not cut it. This server does not: its API calls time out at 120
   seconds (`SBPLIVE_TIMEOUT_SECONDS`), under Cloudflare's default proxy
   read timeout of 125 seconds
   ([error 524](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-524/),
   read 2026-11-06), and under Claude's 240-second limit per tool call
   ([building](https://claude.com/docs/connectors/building/index.md)).
   A Cloudflare quick tunnel does not carry SSE at all, which is one more
   reason to answer in JSON.

## Authorization (OAuth, `serve-http --public-url`)

`sbplive/oauth.py` is the authorization server and the bearer
check, with no transport or file in it ([AUTH.md](AUTH.md)); `remote.py`
routes to it, `oauth_store_file.py` keeps its records, `app_signin.py` is the
self-hosted door's sign-in. Authorization belongs to the HTTP transport only:
both revisions say an stdio server *"SHOULD NOT follow this specification,
and instead retrieve credentials from the environment"*, so the stdio door
is unchanged.

### Sources

Read on **2026-11-06**:

- Anthropic, [Authentication for connectors](https://claude.com/docs/connectors/building/authentication.md)
  and [Lazy authentication](https://claude.com/docs/connectors/building/lazy-authentication.md)
  (the 401 shape, the CIMD example, loopback matching); Claude Code's own
  [client document](https://claude.ai/oauth/claude-code-client-metadata)
  (`redirect_uris` `http://localhost/callback` and `http://127.0.0.1/callback`,
  `token_endpoint_auth_method` `none`).
- 2025-11-25, [Authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
  (one page: discovery, client registration, scopes, the flow, the resource
  parameter, tokens, errors, security considerations).
- 2026-07-28, [Authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)
  and its three sub-pages, [Authorization Server Discovery](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/authorization-server-discovery),
  [Client Registration](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration)
  and [Security Considerations](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations).
- RFC 9728 (protected resource metadata), RFC 8414 (authorization server
  metadata), RFC 7591 (dynamic registration), RFC 7636 (PKCE; its Appendix B
  example is a test), RFC 6749 sections 3.1, 4.1 and 5.2 (parameters, the
  code grant, error codes), RFC 6750 section 3 (`WWW-Authenticate`), RFC 8707
  (resource indicators), RFC 8252 section 7.3 (loopback redirects), RFC 9207
  (the `iss` response parameter), draft-ietf-oauth-client-id-metadata-document-00
  as the MCP pages quote it.
- The official client's code, `mcp` 2.3.0: `mcp/client/auth/oauth2.py` and
  `utils.py` (what it checks: the metadata `issuer` as a plain string, `iss`
  on the redirect when advertised, the PRM `resource` against the server
  URL; it appends `offline_access` when the server's `scopes_supported`
  lists it) and `mcp/shared/auth.py` (URLs kept without an added trailing
  slash).

### What each revision requires, and the differences

Both revisions share the core: OAuth 2.1 for the authorization server;
protected resource metadata (RFC 9728) as a MUST for the MCP server, found
through `resource_metadata` on a `401` or the two well-known URLs;
authorization server metadata (RFC 8414 or OpenID Connect Discovery);
PKCE with S256, which a client must see in `code_challenge_methods_supported`
or refuse to go on; the `resource` parameter in authorization and token
requests; the bearer token in the `Authorization` header on every request
and never in the query; the server validates the token's audience, answers
an invalid or expired one `401` and an insufficient scope `403`
`insufficient_scope`; refresh tokens rotated for public clients; exact
redirect URI matching; all authorization endpoints over HTTPS and redirect
URIs on HTTPS or localhost; for CIMD, the document's `client_id` equal to
its URL, its redirect URIs checked, SSRF considered, the redirect host shown
and an extra warning for loopback-only clients.

| Point | 2025-11-25 | 2026-07-28 | What this door does |
|---|---|---|---|
| Dynamic Client Registration | MAY, "for backwards compatibility" | MAY, and **deprecated** in favour of CIMD; a DCR client MUST send `application_type` | Both offered: `registration_endpoint` and `client_id_metadata_document_supported: true` (Claude falls back to DCR without the CIMD pair; Anthropic's page). `application_type` is accepted and echoed, not required, since a 2025-11-25 client need not send it |
| `iss` on the authorization response (RFC 9207) | Not mentioned | Servers SHOULD send it and, when they do, MUST advertise `authorization_response_iss_parameter_supported: true`; clients MUST compare it | Sent on every redirect, the error ones included, and advertised. A 2025-11-25 client ignores a parameter it does not know |
| Metadata `issuer` validation | Not stated | Clients MUST reject metadata whose `issuer` differs from the issuer the well-known URL was built from | The issuer is the public URL exactly (no path, no trailing slash), the same string in `authorization_servers`, `issuer` and `iss` |
| `offline_access` | Not mentioned | Clients MAY ask for it when the AS lists it; resources SHOULD NOT list it in `WWW-Authenticate` scope or the PRM's `scopes_supported` | Listed only in the authorization server's `scopes_supported`; the PRM and the 401 say `books`. A refresh token is issued only when `offline_access` was asked |
| Scope challenge on `403` | Include existing and new scopes | Only the scopes the operation needs, all at once; scope union is the client's job | Not used: one scope, `books`; a token without it is a 401 |
| Standards listed | OAuth 2.1, RFC 8414, 7591, 9728, CIMD | Adds RFC 6750, 8707, 9207, OpenID Connect Discovery and Registration | The rules above already follow the added RFCs |
| Multiple authorization servers | Client chooses (RFC 9728 7.6) | Each is independent; client credentials bound to the issuer | One server, listed first and alone (Anthropic uses only the first) |

Since one door serves both revisions with one authorization server, it takes
the union: everything 2026-07-28 asks of a server, none of which a 2025-11-25
client trips on. The token is HTTP-level, so the session door (2025-11-25)
and the stateless door (2026-07-28) check it the same way, on every request.

### The rules applied

| Rule | Where it says so | What this door does |
|---|---|---|
| A 401 with `WWW-Authenticate: Bearer resource_metadata=...`; Claude ignores it on a 200 | Both revisions; RFC 9728 5.1; Anthropic | `/mcp` without a valid bearer: `401`, `Bearer resource_metadata="<issuer>/.well-known/oauth-protected-resource/mcp", scope="books"`, empty body. With a token that is not valid, `error="invalid_token"` first (RFC 6750 3.1: an error only when a token was presented). GET without a token is the 401 too, not 405 |
| PRM at the MCP path's well-known URL and at the root | Both revisions; Anthropic probes both | `/.well-known/oauth-protected-resource/mcp` and `/.well-known/oauth-protected-resource`, the same document |
| PRM `resource` equals the URL the person enters; `authorization_servers` first entry used | Anthropic; RFC 9728 | `resource` = `<public URL>/mcp`; `authorization_servers` = `[<public URL>]`; `bearer_methods_supported: ["header"]`; `scopes_supported: ["books"]` |
| AS metadata at `/.well-known/oauth-authorization-server` (no path insertion for an issuer without a path) | RFC 8414 3.1; both revisions | Served, with `issuer`, the three endpoints, `response_types_supported: ["code"]`, `grant_types_supported`, `code_challenge_methods_supported: ["S256"]`, `token_endpoint_auth_methods_supported: ["none"]`, `client_id_metadata_document_supported`, `authorization_response_iss_parameter_supported`, `scopes_supported: ["books", "offline_access"]`. OpenID discovery is not served (404): MCP clients try RFC 8414 first |
| CIMD is used by Claude only with `client_id_metadata_document_supported: true` **and** `none` in `token_endpoint_auth_methods_supported` | Anthropic | Both advertised |
| DCR: JSON, `redirect_uris` required, errors `invalid_redirect_uri` / `invalid_client_metadata`, the server may replace values | RFC 7591 2, 3.2 | `201` with `client_id`, `client_id_issued_at`, the URIs, grants, `token_endpoint_auth_method: none` (always: every client is public; `private_key_jwt` is refused as metadata this door cannot honour). No secret. 30 registrations an hour per address; a registration is forgotten after 90 days |
| CIMD: fetch the `https` URL, `client_id` must equal it, `redirect_uris` required, cache, SSRF | Both revisions; the draft, section 6 | A `client_id` starting `https://` (with a path) is fetched: 10 s, 64 KiB, no redirects followed, certificates verified, and only when every address the host resolves to is public; cached a day. A document with another `client_id`, no `redirect_uris`, or an auth method other than `none` is refused on the page |
| Exact redirect URIs; loopback port ignored, `localhost` too | Both revisions; RFC 8252 7.3; Anthropic | Accepted at all: `https://claude.ai/api/mcp/auth_callback`, and `http://localhost/callback`, `http://127.0.0.1/callback`, `http://[::1]/callback` on any port. A request's URI must match one the client registered (or its document lists) and that list |
| Bad client or redirect URI: never redirect | RFC 6749 4.1.2.1 | A page with the sentence, status `400`, no `Location` |
| Other authorization errors go back on the redirect with `state` | RFC 6749 4.1.2.1 | `303` to the redirect URI with `error`, `error_description`, `state` and `iss`: `unsupported_response_type`, `invalid_request` (no PKCE, a method other than S256), `invalid_target` (RFC 8707: a `resource` that is not this door), `invalid_scope`, `access_denied` (the person declined) |
| PKCE S256 required | Both revisions; OAuth 2.1; Anthropic | A challenge of 43 base64url characters and `S256`, or the request is refused; the verifier (43 to 128 unreserved characters) is checked with `hmac.compare_digest` |
| `resource` in authorization and token requests | Both revisions (clients MUST send it) | Compared with the scheme and host in any case and without a trailing slash, as the text asks servers to accept; absent is taken as this door (the token is still bound to it) |
| Show the redirect host; warn for loopback-only clients; name the CIMD host, not its self-asserted name | Both revisions, Localhost Redirect URI Risks; Anthropic | The page says where the person goes back to, names the document's host (and "calls itself ..." for the name), and for a loopback-only client says it is a program on this computer and to continue only if they just asked one to connect |
| Codes single use, short-lived, bound | OAuth 2.1; RFC 6749 4.1.2 | 256 random bits, ten minutes, bound to the client, redirect URI, challenge, resource, scope and principal; spent on the first try, right or wrong; a second use revokes the tokens the first bought |
| Token endpoint takes `application/x-www-form-urlencoded`; a parameter twice is an error | RFC 6749 3.1, 4.1.3; Anthropic | Anything else is `400 invalid_request`; answers carry `Cache-Control: no-store` and `Pragma: no-cache` |
| Public clients: `client_id` in the body, no secret | OAuth 2.1; Anthropic | Missing `client_id` is `401 invalid_client`; the code's or refresh token's client must match |
| Short-lived access tokens | Both revisions (SHOULD) | Opaque, 256 random bits, one hour |
| Rotate refresh tokens for public clients; return the new one in the same answer | Both revisions (MUST); Anthropic | Every refresh answers a new access and refresh token; the old refresh token is marked spent; using a spent one revokes its whole family (every token since that sign-in). Thirty days from each issue |
| A dead refresh token is `invalid_grant` | Anthropic; RFC 6749 5.2 | Always `invalid_grant`, never a custom code |
| Validate the token's audience; accept only own tokens; never pass one through | Both revisions | A token is looked up by its SHA-256, compared in constant time, and must be unexpired, for this resource and carry `books`. It is never sent to SlowBooks Pro: the tools use the API token, as before |
| Tokens never in the query string | Both revisions | Only the header is read |
| Endpoints within 10 s (30 s for refresh) | Anthropic | Nothing but the sign-in itself calls out: the app's own login (argon2, about 100 ms) with an 8-second timeout, and a client document fetch with 10 |
| Origin checks unchanged | Streamable HTTP, both revisions | `/mcp` as before. The sign-in POST must carry no `Origin` or this door's own (a page of another site cannot post it) |
| The page: no scripts, a CSP that allows nothing but itself | AUTH.md | `default-src 'none'`, the one style block by hash, `form-action 'self'` plus the redirect URI's origin (browsers check a form's redirect against `form-action`), `frame-ancestors 'none'`, `X-Frame-Options: DENY`, no referrer, `no-store`. The form is bound to a pending record (random id, ten minutes) that a success consumes, so a page cannot be replayed |
| Sign-in failures | AUTH.md | One sentence that never says which part was wrong; ten failures from one address in ten minutes lock it out for ten minutes (`429`, `Retry-After`; the app is not asked meanwhile). A missing token on `/mcp` is how every sign-in starts and is not counted against the address; a wrong one is |
| A 2025-11-25 session and its sign-in | Not in the text (security best practices: sessions are not authentication) | A session belongs to the credential that opened it (the connector secret, or one OAuth sign-in through all its rotations); another credential gets `404` for it |

### What the specifications left unclear

1. **The PRM at the root.** RFC 9728 has the root document describe the
   origin as the resource; Anthropic requires `resource` to equal the MCP
   URL. Both paths serve the MCP URL, which is what both clients check.
2. **`resource` absent.** Clients MUST send it, but nothing says what a
   server does when one does not. Taken as this door, since there is only
   one resource.
3. **`offline_access` and refresh tokens.** 2026-07-28 leaves issuing them to
   the server's discretion; issued only when asked, so a client that did not
   ask is not handed a thirty-day credential.
4. **What the page asks.** The text leaves authentication to the
   authorization server. Here it is the SlowBooks Pro administrator sign-in,
   checked by the app itself; see [AUTH.md](AUTH.md) for why administrators
   only, and why the door does not sign out of the app afterwards.

## What a future bump has to re-check

1. The versioning page: which revision is current, and whether the ones
   listed here are still served by the hosts that matter (Claude Desktop,
   Claude Code, the official SDKs).
2. The changelog of every revision since 2026-07-28, for changes to: the
   per-request `_meta` fields, `server/discover`, the backward-compatibility
   rules and the matrix, the stdio framing, the error codes (`-32022` and any
   new ones), `resultType`, the caching fields, `tools/list` pagination, the
   `Tool` fields, `ToolAnnotations` and their defaults, `CallToolResult`
   (`isError`, `structuredContent`, `outputSchema`) and the JSON Schema
   dialect.
3. Whether the handshake era is still worth serving (the specification
   deprecates features for at least twelve months before removal; check the
   deprecated features registry).
4. The Streamable HTTP page of the new revision: the endpoint, the headers
   and their validation, the status table, sessions, and what a dual-era
   server answers on GET and DELETE; update `remote.py` and the section
   above.
5. The new revision's Authorization pages: what changed for servers
   (registration, metadata fields, `iss`, scopes, refresh tokens), against
   the tables in [Authorization](#authorization-oauth-serve-http---public-url);
   update `oauth.py` and those tables.
6. Add the new revision to `MODERN_VERSIONS`, bump `mcp` in
   `requirements-dev.txt` to a release that speaks it, run
   `tests/test_contract.py` in every mode the client offers (over stdio and
   over HTTP), and update
   `test_the_revisions_are_listed_once` in `tests/test_protocol.py` and this
   page.
