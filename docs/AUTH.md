# Sign-in with OAuth: one module, two doors

Claude's remote client signs a person in with OAuth 2.0 (Anthropic's
[Authentication for connectors](https://claude.com/docs/connectors/building/authentication.md),
read 2026-11-06). The static header is a beta for some organizations, so OAuth
is the sign-in every account can use, and the only one a directory listing
accepts. This program carries the OAuth logic once, in a pure module, and the
self-hosted door (`serve-http`, behind the person's own tunnel) uses it; a
door supplies only **who signs in** and **where the records are kept**.

## The module: `sbplive/oauth.py`

Standard library only, no transport in it: it takes parsed requests
(method, path, query, form or JSON body, headers) and returns status, headers
and body, plus a small storage interface the door implements. Everything a
door needs:

- **Metadata.** The authorization server document (RFC 8414) and the
  protected resource document (RFC 9728), built from the door's public URL:
  `issuer`, `authorization_endpoint`, `token_endpoint`,
  `registration_endpoint`, `response_types_supported: ["code"]`,
  `grant_types_supported: ["authorization_code", "refresh_token"]`,
  `code_challenge_methods_supported: ["S256"]`,
  `token_endpoint_auth_methods_supported: ["none"]`,
  `client_id_metadata_document_supported: true`,
  `scopes_supported: ["books", "offline_access"]`; the resource document's
  `resource` is the MCP URL exactly as the person enters it in Claude,
  `authorization_servers` lists the issuer first and only,
  `bearer_methods_supported: ["header"]`.
- **The 401.** An MCP request without a valid bearer token answers
  `401` with `WWW-Authenticate: Bearer resource_metadata="<issuer>/.well-known/oauth-protected-resource/mcp", scope="books"`.
  Claude needs the 401 itself (it ignores the header on a 200), and it also
  probes `/.well-known/oauth-protected-resource/mcp` then
  `/.well-known/oauth-protected-resource`; both are served.
- **Client registration, two ways.** Dynamic Client Registration (RFC 7591,
  `POST /oauth/register`, JSON): a public client (`token_endpoint_auth_method`
  "none"), its `redirect_uris` kept, a random `client_id`, no secret. And a
  Client ID Metadata Document: a `client_id` that is an `https://` URL is
  fetched (10-second timeout, 64 KiB cap, cached for a day), its
  `redirect_uris` taken from the document. Redirect URIs accepted:
  `https://claude.ai/api/mcp/auth_callback`, and the loopback forms Claude
  Code uses, `http://localhost/callback` and `http://127.0.0.1/callback`
  matched with the port ignored (RFC 8252 section 7.3). Any other URI is
  refused at registration and at authorization.
- **Authorization.** `GET /oauth/authorize` checks `response_type=code`,
  the client, the redirect URI (exact, loopback port aside), `state`,
  `code_challenge` with `code_challenge_method=S256` (a request without PKCE
  is refused), `scope`, and `resource` (must be this door's MCP URL). It
  shows one page: the sign-in fields the door asks for, what is being asked
  ("Let Claude work the books of <company> as <role>"), the redirect host in
  plain words, and the extra warning the MCP specification asks for when the
  redirect is a loopback address. `POST` of that page verifies the sign-in
  through a function the door supplies (`verify(fields) -> principal or
  None`), which never stores a password; on success a single-use code
  (random, ten minutes, bound to the client, redirect URI, challenge,
  resource, scope and principal) goes back on the redirect with `state`.
  Failures are one sentence and never say whether the account exists. Ten
  failed sign-ins from one address in ten minutes lock that address out for
  ten minutes.
- **Tokens.** `POST /oauth/token`, `application/x-www-form-urlencoded`
  (Claude sends nothing else), JSON back with `Cache-Control: no-store`.
  `authorization_code`: the code (once), the `code_verifier` checked against
  the challenge, `redirect_uri` and `client_id` matched, `resource` matched;
  answers an opaque access token (256 random bits, one hour) and a refresh
  token (thirty days) when `offline_access` was asked. `refresh_token`:
  rotates; the old token is invalidated in the same answer, and a reuse of
  an old one revokes the whole family. Errors are RFC 6749's: `invalid_grant`
  for a dead refresh token, never a custom code. Tokens are stored as
  SHA-256 hashes with their principal, scope, resource and expiry; the bearer
  check looks the hash up, compares in constant time, and checks expiry and
  resource.
- **Both revisions.** The 2025-11-25 and 2026-07-28 authorization
  specifications are read before building and any difference between them
  recorded in PROTOCOL.md.
- **The storage interface.** `put(kind, key, record, ttl)`, `get`,
  `delete`, `delete_where(kind, field, value)`: a JSON file beside the token
  store, mode 600, for the self-hosted door. Expired records are swept on
  write.
- **The pages.** Plain HTML, no scripts, a form, the program's name, a
  `Content-Security-Policy` that allows nothing but itself, and the sign-in
  form bound to a pending-authorization record by a random id so a page
  cannot be replayed.

Claude's latency rule applies to every endpoint: under ten seconds, and the
sign-in verification must not wait on anything slow.

## The self-hosted door

`serve-http --public-url https://books.example.com` turns the OAuth door on
(the public URL is the issuer and the base of every redirect; without it
only the connector secret works). Who signs in: **an administrator of the
books**. The page asks for the SlowBooks Pro sign-in (user name and password
when the books have several users, the password alone otherwise); the door
verifies it by signing in to the local app the way the app's own page does
(`POST /api/auth/login`, a cookie jar of its own, `GET /api/auth/status` for
the role, then the jar is dropped: no sign-out, see "As built" below), keeps
nothing, and requires the role to be
administrator, because the administrator chose the API token that tool calls
use and its role. A token for each person is on the "still to decide" list.
The connector secret stays accepted beside it for accounts with Claude's
Request headers section.

## As built (2026-11-06)

Built as above, with these differences, each for a reason:

- **No sign-out after the check.** SlowBooks Pro's `POST /api/auth/logout`
  ends *every* session of that user (it bumps the user's session version),
  so signing out would sign the administrator out of SlowBooks Pro in their
  own browser each time Claude connects (the end-to-end test hit exactly
  that). The app's session is a signed cookie held by the client; the door
  drops its cookie jar, and nothing of the session is kept anywhere.
- **The 2026-07-28 additions.** The authorization response carries `iss`
  (RFC 9207) and the metadata says `authorization_response_iss_parameter_supported`;
  the protected resource document lists `books` only, since that revision
  says a resource SHOULD NOT list `offline_access` (the authorization
  server's list keeps both, so Claude still asks for a refresh token). DCR
  is kept although 2026-07-28 deprecates it: Claude falls back to it.
  [PROTOCOL.md](PROTOCOL.md#authorization-oauth-serve-http---public-url) has
  both revisions side by side.
- **The failure count on `/mcp`.** A request with no token at all is how
  every sign-in starts, so with OAuth on it is not counted against the
  address; a wrong token or secret is, as before. The sign-in page has its
  own lockout (in the storage, so it holds across requests).
- **Redirects.** `http://[::1]/callback` is accepted beside the two loopback
  forms (RFC 8252 names the IP literals); after the sign-in POST the answer
  is `303 See Other`.
- **Codes.** Spent on the first exchange, right or wrong; a second use of a
  code revokes the tokens the first bought (RFC 6749 section 4.1.2).
- **Sessions.** A 2025-11-25 session belongs to the sign-in (the token
  family, through every rotation) or the secret that opened it.
- **Client documents** are believed for a day whatever their HTTP cache
  headers say (the specification's SHOULD); they are fetched only from hosts
  whose every address is public, against SSRF from the office network.
- **Limits not in the spec:** 30 registrations an hour per address; a
  registered client is forgotten after 90 days (a connection already signed
  in keeps working: tokens carry their client).
- **The public URL** is `https://host[:port]` with no path (the issuer and the
  base of the documents); `http://` only for this computer, for tests.
- **The storage interface** is the four calls as written; the module holds
  a lock around each read-then-write (single-use codes, rotation), so a
  storage need not be atomic itself. Records are JSON-able dicts. A door
  passes the client's address in each request for the lockout.
- **Known limits of the self-hosted sign-in:** on a Server Edition serving
  several companies, the sign-in goes to the company the app opens by
  default (the page has no company choice yet); the app's own limit of five
  sign-ins a minute per address applies to the door as one address, and its
  refusal is shown as one sentence. Demoting an administrator does not end a
  connection already signed in until its refresh token runs out; deleting
  `oauth.json` signs every connection out.
