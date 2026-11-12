# ============================================================================
# OAuth 2.1 sign-in for an MCP door, as one pure module (docs/AUTH.md): the
# authorization server and the resource server's half of it, for the
# self-hosted door (serve-http --public-url) and any other door built on it.
#
# Pure means: no socket, no file, no http.server. A door turns each HTTP
# request into a Request (method, path, raw query string, headers, raw body,
# the client's address) and sends back the Response this module returns. The
# door also supplies:
#
#   storage   put(kind, key, record, ttl) / get(kind, key) / delete(kind, key)
#             / delete_where(kind, field, value): JSON-able dicts that expire
#             after ttl seconds. MemoryStore below is the reference; the
#             self-hosted door keeps a JSON file (oauth_store_file.py).
#   verify    verify(fields) -> principal (a JSON-able dict) or None, and may
#             raise SignInRefused(sentence) for a right sign-in that may not
#             let Claude in. It never hands back, and this module never
#             stores, a password.
#   fields    fields() -> the sign-in fields the page asks for (Field).
#   about     about() -> {"company": ..., "role": ...} for the consent line.
#   fetch     fetch(url) -> bytes, for Client ID Metadata Documents;
#             fetch_document() below is the default (https only, 10 s, 64 KiB,
#             no redirects, public addresses only).
#
# Endpoints, every rule and its source: docs/PROTOCOL.md, "Authorization".
#
#   GET  /.well-known/oauth-protected-resource[/mcp]  RFC 9728
#   GET  /.well-known/oauth-authorization-server      RFC 8414
#   POST /oauth/register                               RFC 7591, public clients
#   GET  /oauth/authorize                              the sign-in page
#   POST /oauth/authorize                              sign in, code on redirect
#   POST /oauth/token                                  codes and refresh, PKCE S256
#
# Standard library only.
# ============================================================================

import base64
import hashlib
import hmac
import html
import ipaddress
import json
import re
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

PRODUCT = "SlowBooks Switchboard"

# Paths, under the issuer (which has no path of its own).
PRM_PATH = "/.well-known/oauth-protected-resource"
ASM_PATH = "/.well-known/oauth-authorization-server"
AUTHORIZE_PATH = "/oauth/authorize"
TOKEN_PATH = "/oauth/token"
REGISTER_PATH = "/oauth/register"

SCOPE = "books"  # what an access token is for: the books, through the tools
OFFLINE = "offline_access"  # asks for a refresh token
SCOPES = (SCOPE, OFFLINE)

# Lifetimes, in seconds.
PENDING_TTL = 600  # a sign-in page
CODE_TTL = 600  # an authorization code
ACCESS_TTL = 3600  # an access token
REFRESH_TTL = 30 * 86400  # a refresh token, from its own issue
CLIENT_TTL = 90 * 86400  # a registered (DCR) client
CIMD_TTL = 86400  # a fetched client document is believed for a day

# The sign-in lockout: this many failures from one address within the window
# lock that address out until its oldest failure leaves the window.
MAX_SIGNIN_FAILURES = 10
SIGNIN_WINDOW = 600
# Dynamic registrations from one address within an hour.
MAX_REGISTRATIONS = 30
REGISTRATION_WINDOW = 3600

MAX_FORM = 64 * 1024  # a token request, a sign-in form, a registration
CIMD_TIMEOUT = 10.0
CIMD_MAX = 64 * 1024

# The redirect URIs any client may use (docs/AUTH.md): Claude's hosted apps,
# and the loopback callback of Claude Code and other native clients, matched
# with the port ignored (RFC 8252 section 7.3; Anthropic asks for localhost
# too).
CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"
LOOPBACK_CALLBACKS = (
    "http://localhost/callback",
    "http://127.0.0.1/callback",
    "http://[::1]/callback",
)
ALLOWED_REDIRECTS = (CLAUDE_CALLBACK,) + LOOPBACK_CALLBACKS
_LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}

ACCESS_PREFIX = "sbo_at_"
REFRESH_PREFIX = "sbo_rt_"
CODE_PREFIX = "sbo_code_"
CLIENT_PREFIX = "sbo_client_"
TOKEN_SHAPE = re.compile(r"sbo_(?:at|rt|code)_[A-Za-z0-9_\-]{20,200}")

_VERIFIER = re.compile(r"[A-Za-z0-9\-._~]{43,128}")  # RFC 7636 section 4.1
_CHALLENGE = re.compile(r"[A-Za-z0-9\-_]{43}")  # BASE64URL(SHA-256), no padding

# Kinds of record in the storage.
K_CLIENT, K_CIMD, K_PENDING, K_CODE = "client", "cimd", "pending", "code"
K_ACCESS, K_REFRESH = "access", "refresh"
K_FAILURES, K_REGISTRATIONS = "signin_failures", "registrations"

SENTENCE_BAD_SIGNIN = "That sign-in did not work. Check what you typed and try again."
SENTENCE_LOCKED = (
    "Too many sign-ins failed from this address. Wait ten minutes and try again."
)
SENTENCE_EXPIRED_PAGE = (
    "This sign-in page has expired or was already used. Go back to Claude and "
    "connect again."
)


class SignInRefused(Exception):
    """A sign-in that was right but may not let Claude in, or could not be
    checked; str() is the one sentence the page shows."""


class _OAuthError(Exception):
    def __init__(self, error: str, description: str, status: int = 400):
        super().__init__(description)
        self.error, self.description, self.status = error, description, status


# -- requests and answers -----------------------------------------------------


@dataclass
class Request:
    method: str
    path: str  # without the query
    query: str = ""  # the raw query string
    headers: dict = field(default_factory=dict)  # names in any case
    body: bytes = b""
    addr: str = ""  # who is asking, for the lockout

    def header(self, name: str):
        want = name.lower()
        for key, value in self.headers.items():
            if key.lower() == want:
                return value
        return None


@dataclass
class Response:
    status: int
    headers: dict = field(default_factory=dict)
    body: bytes = b""


@dataclass(frozen=True)
class Field:
    name: str  # the form field, handed to verify under this name
    label: str
    secret: bool = False  # a password field
    autocomplete: str = ""


DEFAULT_FIELDS = (
    Field("username", "User name", autocomplete="username"),
    Field("password", "Password", secret=True, autocomplete="current-password"),
)


def _json(status: int, doc, headers=None) -> Response:
    out = {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
    }
    out.update(headers or {})
    body = json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode()
    return Response(status, out, body)


def _oauth_error(exc: _OAuthError, headers=None) -> Response:
    return _json(
        exc.status,
        {"error": exc.error, "error_description": exc.description},
        headers,
    )


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def s256(verifier: str) -> str:
    """The S256 code challenge for a verifier (RFC 7636 section 4.2)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _parse_form(raw: str) -> dict:
    """application/x-www-form-urlencoded or a query string, as a dict; a
    parameter given twice is a malformed request (RFC 6749 section 3.1)."""
    out: dict = {}
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=False)
    except ValueError:
        raise _OAuthError("invalid_request", "The request is malformed.") from None
    for key, value in pairs:
        if key in out:
            raise _OAuthError(
                "invalid_request", f"The parameter {key} is given more than once."
            )
        out[key] = value
    return out


# -- URLs ---------------------------------------------------------------------


def check_public_url(raw: str) -> str:
    """The door's public address as an issuer: https://host[:port], nothing
    after it; http only for this computer (testing). ValueError otherwise."""
    text = (raw or "").strip().rstrip("/")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise ValueError(f"{raw!r} is not an address.") from None
    host = (parts.hostname or "").lower()
    if (
        not host
        or parts.path
        or parts.query
        or parts.fragment
        or parts.username is not None
    ):
        raise ValueError(
            f"{raw!r} is not a public address. Give the tunnel's address alone, "
            "such as https://books.example.com, with nothing after the host."
        )
    if parts.scheme == "https" or (parts.scheme == "http" and host in _LOOPBACK_NAMES):
        shown = f"[{host}]" if ":" in host else host
        return f"{parts.scheme}://{shown}" + (f":{port}" if port else "")
    raise ValueError(
        f"{raw!r} does not start with https://. Claude signs in only over HTTPS; "
        "give the tunnel's https:// address."
    )


def _same_resource(given: str, ours: str) -> bool:
    """RFC 8707 resources compared with the scheme and host in any case and
    without a trailing slash, as the MCP specification asks servers to
    accept."""

    def norm(url: str):
        try:
            p = urlsplit(url.strip())
            port = p.port
        except ValueError:
            return None
        if p.fragment or not p.hostname:
            return None
        return (p.scheme.lower(), p.hostname.lower(), port, p.path.rstrip("/"), p.query)

    a = norm(given)
    return a is not None and a == norm(ours)


def _loopback(parts) -> bool:
    return parts.scheme == "http" and (parts.hostname or "").lower() in _LOOPBACK_NAMES


def redirect_matches(requested: str, registered: str) -> bool:
    """Exact, except that a loopback http redirect ignores the port (RFC 8252
    section 7.3)."""
    if requested == registered:
        return True
    try:
        a, b = urlsplit(requested), urlsplit(registered)
        _ = a.port, b.port
    except ValueError:
        return False
    if not (_loopback(a) and _loopback(b)):
        return False
    return (
        a.hostname.lower() == b.hostname.lower()
        and a.path == b.path
        and a.query == b.query
        and not a.fragment
        and not b.fragment
        and a.username is None
    )


def _with_params(uri: str, params: dict) -> str:
    parts = urlsplit(uri)
    query = parts.query
    extra = urlencode({k: v for k, v in params.items() if v is not None})
    query = f"{query}&{extra}" if query else extra
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _host_of(uri: str) -> str:
    try:
        parts = urlsplit(uri)
        return (parts.hostname or "") + (f":{parts.port}" if parts.port else "")
    except ValueError:
        return ""


def _origin_of(uri: str) -> str:
    parts = urlsplit(uri)
    return f"{parts.scheme}://{parts.netloc}"


# -- the Client ID Metadata Document fetcher ------------------------------------


class DocumentError(Exception):
    """A client document that could not be fetched or used: the sentence."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _public_host(host: str) -> bool:
    """Whether every address the host resolves to is a public one, so the
    door cannot be made to fetch something on its own network (SSRF)."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    addrs = {info[4][0].split("%", 1)[0] for info in infos}
    try:
        return bool(addrs) and all(ipaddress.ip_address(a).is_global for a in addrs)
    except ValueError:
        return False


def fetch_document(
    url: str,
    *,
    timeout: float = CIMD_TIMEOUT,
    limit: int = CIMD_MAX,
    public_only: bool = True,
) -> bytes:
    """GET a client document: https only (unless public_only is off, for
    tests), certificates verified, redirects refused, at most limit bytes,
    within timeout seconds, and only from public addresses."""
    parts = urlsplit(url)
    if public_only:
        if parts.scheme != "https":
            raise DocumentError("A client document must be fetched over https.")
        if not _public_host(parts.hostname or ""):
            raise DocumentError("The client document's host is not a public address.")
    opener = urllib.request.build_opener(_NoRedirect())
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": PRODUCT}
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise DocumentError("The client document did not answer 200.")
            data = resp.read(limit + 1)
    except urllib.error.HTTPError:
        raise DocumentError("The client document could not be fetched.") from None
    except (OSError, ValueError):
        raise DocumentError("The client document could not be fetched.") from None
    if len(data) > limit:
        raise DocumentError("The client document is too large.")
    return data


# -- the storage interface's reference ------------------------------------------


class MemoryStore:
    """The storage interface in memory: what a door implements. Records are
    JSON-able dicts; an expired record is gone (swept on every write)."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self._data: dict = {}  # (kind, key) -> (expires, record)
        self._lock = threading.Lock()

    def _sweep(self, now: float) -> None:
        for k in [k for k, (exp, _) in self._data.items() if exp <= now]:
            del self._data[k]

    def put(self, kind: str, key: str, record: dict, ttl: float) -> None:
        with self._lock:
            now = self.clock()
            self._sweep(now)
            self._data[(kind, key)] = (now + ttl, json.loads(json.dumps(record)))

    def get(self, kind: str, key: str):
        with self._lock:
            entry = self._data.get((kind, key))
            if entry is None or entry[0] <= self.clock():
                return None
            return json.loads(json.dumps(entry[1]))

    def delete(self, kind: str, key: str) -> None:
        with self._lock:
            self._data.pop((kind, key), None)

    def delete_where(self, kind: str, field_name: str, value) -> int:
        with self._lock:
            gone = [
                k
                for k, (_, rec) in self._data.items()
                if k[0] == kind and rec.get(field_name) == value
            ]
            for k in gone:
                del self._data[k]
            return len(gone)


# -- the server -----------------------------------------------------------------


class OAuthServer:
    """Everything OAuth for one issuer and one MCP resource."""

    def __init__(
        self,
        issuer: str,
        storage,
        verify,
        *,
        fields=None,
        about=None,
        fetch=fetch_document,
        clock=time.time,
        resource_path: str = "/mcp",
        sign_in_name: str = "the SlowBooks Pro administrator sign-in",
        allowed_redirects=ALLOWED_REDIRECTS,
    ):
        self.issuer = check_public_url(issuer)
        self.resource = self.issuer + resource_path
        self.resource_path = resource_path
        self.storage = storage
        self.verify = verify
        self.fields = fields or (lambda: list(DEFAULT_FIELDS))
        self.about = about or dict
        self.fetch = fetch
        self.clock = clock
        self.sign_in_name = sign_in_name
        self.allowed_redirects = tuple(allowed_redirects)
        # Single-use codes and refresh rotation must not race: one lock
        # around every read-then-write on the storage.
        self._lock = threading.Lock()

    # -- what a door asks ------------------------------------------------------

    @property
    def metadata_url(self) -> str:
        return self.issuer + PRM_PATH + self.resource_path

    def paths(self) -> tuple:
        return (
            PRM_PATH,
            PRM_PATH + self.resource_path,
            ASM_PATH,
            AUTHORIZE_PATH,
            TOKEN_PATH,
            REGISTER_PATH,
        )

    def owns(self, path: str) -> bool:
        return path in self.paths()

    def challenge(self, presented: bool = False) -> Response:
        """The 401 an MCP request without a valid bearer token gets."""
        parts = [f'resource_metadata="{self.metadata_url}"', f'scope="{SCOPE}"']
        if presented:
            # RFC 6750 section 3.1: an error only when a token was presented.
            parts.insert(0, 'error="invalid_token"')
        return Response(401, {"WWW-Authenticate": "Bearer " + ", ".join(parts)})

    def authenticate(self, token: str):
        """The access token's grant ({"principal", "client_id", "scope",
        "family"}) when it is valid for this resource now, else None."""
        if not token or not token.startswith(ACCESS_PREFIX):
            return None
        digest = _hash(token)
        rec = self.storage.get(K_ACCESS, digest)
        if rec is None or not hmac.compare_digest(rec.get("hash", ""), digest):
            return None
        if rec.get("expires", 0) <= self.clock():
            return None
        if rec.get("resource") != self.resource or SCOPE not in rec.get(
            "scope", ""
        ).split(" "):
            return None
        return {
            "principal": rec.get("principal"),
            "client_id": rec.get("client_id"),
            "scope": rec.get("scope"),
            "family": rec.get("family"),
        }

    def handle(self, req: Request):
        """The Response for an OAuth path, or None for any other path."""
        path = req.path
        if path in (PRM_PATH, PRM_PATH + self.resource_path):
            return self._get_only(req, self.protected_resource_metadata)
        if path == ASM_PATH:
            return self._get_only(req, self.authorization_server_metadata)
        if path == REGISTER_PATH:
            if req.method != "POST":
                return Response(405, {"Allow": "POST"})
            return self._register(req)
        if path == TOKEN_PATH:
            if req.method != "POST":
                return Response(405, {"Allow": "POST"})
            return self._token(req)
        if path == AUTHORIZE_PATH:
            if req.method == "GET":
                return self._authorize_get(req)
            if req.method == "POST":
                return self._authorize_post(req)
            return Response(405, {"Allow": "GET, POST"})
        return None

    # -- metadata (RFC 9728, RFC 8414) -------------------------------------------

    def _get_only(self, req: Request, make) -> Response:
        if req.method not in ("GET", "HEAD"):
            return Response(405, {"Allow": "GET"})
        return _json(200, make(), {"Cache-Control": "max-age=3600"})

    def protected_resource_metadata(self) -> dict:
        return {
            "resource": self.resource,
            "authorization_servers": [self.issuer],
            "bearer_methods_supported": ["header"],
            # Not offline_access: 2026-07-28 says a protected resource SHOULD
            # NOT list it, since refresh tokens are not a resource need.
            "scopes_supported": [SCOPE],
            "resource_name": PRODUCT,
        }

    def authorization_server_metadata(self) -> dict:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": self.issuer + AUTHORIZE_PATH,
            "token_endpoint": self.issuer + TOKEN_PATH,
            "registration_endpoint": self.issuer + REGISTER_PATH,
            "response_types_supported": ["code"],
            "response_modes_supported": ["query"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "client_id_metadata_document_supported": True,
            "authorization_response_iss_parameter_supported": True,
            "scopes_supported": list(SCOPES),
        }

    # -- clients ---------------------------------------------------------------

    def _redirect_allowed(self, uri: str) -> bool:
        return any(redirect_matches(uri, ok) for ok in self.allowed_redirects)

    def _register(self, req: Request) -> Response:
        ctype = (req.header("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            return _oauth_error(
                _OAuthError(
                    "invalid_client_metadata", "Registration takes a JSON body."
                )
            )
        now = self.clock()
        with self._lock:
            times = [
                t
                for t in (self.storage.get(K_REGISTRATIONS, req.addr) or {}).get(
                    "times", []
                )
                if now - t < REGISTRATION_WINDOW
            ]
            if len(times) >= MAX_REGISTRATIONS:
                return _json(
                    429,
                    {
                        "error": "invalid_request",
                        "error_description": "Too many registrations; try later.",
                    },
                    {
                        "Retry-After": str(
                            int(REGISTRATION_WINDOW - (now - times[0])) + 1
                        )
                    },
                )
            times.append(now)
            self.storage.put(
                K_REGISTRATIONS, req.addr, {"times": times}, REGISTRATION_WINDOW
            )
        try:
            meta = json.loads(req.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            meta = None
        try:
            if not isinstance(meta, dict):
                raise _OAuthError(
                    "invalid_client_metadata", "Registration takes a JSON object."
                )
            uris = meta.get("redirect_uris")
            if (
                not isinstance(uris, list)
                or not uris
                or len(uris) > 10
                or not all(isinstance(u, str) and len(u) <= 2000 for u in uris)
            ):
                raise _OAuthError(
                    "invalid_redirect_uri", "redirect_uris must list the callbacks."
                )
            for uri in uris:
                if not self._redirect_allowed(uri):
                    raise _OAuthError(
                        "invalid_redirect_uri",
                        f"{uri} is not a redirect URI this server accepts.",
                    )
            grants = meta.get("grant_types", ["authorization_code", "refresh_token"])
            if not isinstance(grants, list) or not set(grants) <= {
                "authorization_code",
                "refresh_token",
            }:
                raise _OAuthError(
                    "invalid_client_metadata",
                    "Only authorization_code and refresh_token grants are offered.",
                )
            types = meta.get("response_types", ["code"])
            if types != ["code"]:
                raise _OAuthError(
                    "invalid_client_metadata", "Only the code response type is offered."
                )
            method = meta.get("token_endpoint_auth_method", "none")
            if method == "private_key_jwt":
                raise _OAuthError(
                    "invalid_client_metadata",
                    "Only public clients (token_endpoint_auth_method none) register.",
                )
            name = meta.get("client_name")
            name = name.strip()[:200] if isinstance(name, str) else ""
        except _OAuthError as exc:
            return _oauth_error(exc)
        client_id = CLIENT_PREFIX + secrets.token_urlsafe(18)
        record = {
            "client_id": client_id,
            "client_name": name,
            "redirect_uris": uris,
            "grant_types": grants,
            "issued": int(now),
        }
        self.storage.put(K_CLIENT, client_id, record, CLIENT_TTL)
        answer = {
            "client_id": client_id,
            "client_id_issued_at": int(now),
            "redirect_uris": uris,
            "grant_types": grants,
            "response_types": ["code"],
            # RFC 7591 lets the server replace what was asked: every client
            # here is public, whatever it asked for.
            "token_endpoint_auth_method": "none",
        }
        if name:
            answer["client_name"] = name
        if isinstance(meta.get("application_type"), str):
            answer["application_type"] = meta["application_type"]
        if isinstance(meta.get("scope"), str):
            answer["scope"] = meta["scope"]
        return _json(201, answer)

    def _client(self, client_id: str) -> dict:
        """The client's record: registered, or its metadata document. Raises
        _OAuthError (invalid_client) when there is none."""
        if not isinstance(client_id, str) or not client_id or len(client_id) > 2000:
            raise _OAuthError("invalid_client", "The client is not known here.")
        if client_id.startswith("https://"):
            return self._document_client(client_id)
        rec = self.storage.get(K_CLIENT, client_id)
        if rec is None:
            raise _OAuthError("invalid_client", "The client is not known here.")
        return rec

    def _document_client(self, url: str) -> dict:
        parts = urlsplit(url)
        if not parts.hostname or parts.path in ("", "/") or parts.fragment:
            raise _OAuthError(
                "invalid_client", "A client ID URL needs a host and a path."
            )
        cached = self.storage.get(K_CIMD, url)
        if cached is not None:
            return cached
        try:
            doc = json.loads(self.fetch(url).decode("utf-8"))
        except DocumentError as exc:
            raise _OAuthError("invalid_client", str(exc)) from None
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise _OAuthError(
                "invalid_client", "The client document is not JSON."
            ) from None
        if not isinstance(doc, dict) or doc.get("client_id") != url:
            raise _OAuthError(
                "invalid_client",
                "The client document's client_id is not its own address.",
            )
        uris = doc.get("redirect_uris")
        if (
            not isinstance(uris, list)
            or not uris
            or not all(isinstance(u, str) for u in uris)
        ):
            raise _OAuthError(
                "invalid_client", "The client document lists no redirect_uris."
            )
        method = doc.get("token_endpoint_auth_method", "none")
        if method != "none":
            raise _OAuthError(
                "invalid_client",
                "Only public clients (token_endpoint_auth_method none) are served.",
            )
        name = doc.get("client_name")
        record = {
            "client_id": url,
            # Self-asserted: the page names the document's host, not this.
            "client_name": name.strip()[:200] if isinstance(name, str) else "",
            "redirect_uris": uris[:20],
            "document": True,
        }
        self.storage.put(K_CIMD, url, record, CIMD_TTL)
        return record

    # -- authorization -----------------------------------------------------------

    def _check_authorization(self, params: dict):
        """(client, error-or-None, values). Errors in the client or the
        redirect URI are shown on the page (never redirected to); the others
        go back on the redirect, as RFC 6749 section 4.1.2.1 says."""
        client = self._client(params.get("client_id"))  # raises: shown
        redirect = params.get("redirect_uri")
        if redirect is None and len(client["redirect_uris"]) == 1:
            redirect = client["redirect_uris"][0]
        if not isinstance(redirect, str) or not (
            any(redirect_matches(redirect, r) for r in client["redirect_uris"])
            and self._redirect_allowed(redirect)
        ):
            raise _OAuthError(
                "invalid_request",
                "The address Claude asked to return to is not one this door "
                "accepts, so the sign-in stops here.",
            )
        values = {"redirect_uri": redirect, "state": params.get("state")}
        if params.get("response_type") != "code":
            return (
                client,
                ("unsupported_response_type", "Only code is offered."),
                values,
            )
        challenge = params.get("code_challenge")
        if params.get("code_challenge_method") != "S256" or not (
            isinstance(challenge, str) and _CHALLENGE.fullmatch(challenge)
        ):
            return (
                client,
                (
                    "invalid_request",
                    "PKCE is required: code_challenge with code_challenge_method S256.",
                ),
                values,
            )
        resource = params.get("resource")
        if resource is not None and not _same_resource(resource, self.resource):
            return client, ("invalid_target", "The resource is not this door."), values
        asked = (params.get("scope") or "").split()
        unknown = [s for s in asked if s not in SCOPES]
        if unknown:
            return client, ("invalid_scope", f"Unknown scope {unknown[0]}."), values
        # The books are the only thing a token is for; offline_access adds a
        # refresh token.
        granted = [SCOPE] + ([OFFLINE] if OFFLINE in asked else [])
        values.update(challenge=challenge, scope=" ".join(granted))
        return client, None, values

    def _error_redirect(self, values: dict, error: str, description: str) -> Response:
        location = _with_params(
            values["redirect_uri"],
            {
                "error": error,
                "error_description": description,
                "state": values.get("state"),
                "iss": self.issuer,
            },
        )
        return Response(303, {"Location": location, "Cache-Control": "no-store"})

    def _authorize_get(self, req: Request) -> Response:
        try:
            params = _parse_form(req.query)
            client, error, values = self._check_authorization(params)
        except _OAuthError as exc:
            return self._page(400, error=exc.description)
        if error:
            return self._error_redirect(values, *error)
        pending_id = secrets.token_urlsafe(24)
        pending = {
            "client_id": client["client_id"],
            "client_name": client.get("client_name", ""),
            "document": bool(client.get("document")),
            "redirect_uri": values["redirect_uri"],
            "state": values["state"],
            "challenge": values["challenge"],
            "scope": values["scope"],
            "resource": self.resource,
            "loopback_only": all(
                _loopback(urlsplit(u)) for u in client["redirect_uris"]
            ),
        }
        self.storage.put(K_PENDING, pending_id, pending, PENDING_TTL)
        return self._sign_in_page(200, pending_id, pending)

    def _locked(self, addr: str, now: float):
        times = [
            t
            for t in (self.storage.get(K_FAILURES, addr) or {}).get("times", [])
            if now - t < SIGNIN_WINDOW
        ]
        return times, len(times) >= MAX_SIGNIN_FAILURES

    def _failed(self, addr: str, times: list, now: float) -> None:
        times = (times + [now])[-MAX_SIGNIN_FAILURES:]
        self.storage.put(K_FAILURES, addr, {"times": times}, SIGNIN_WINDOW)

    def _authorize_post(self, req: Request) -> Response:
        origin = req.header("Origin")
        if origin is not None and origin.rstrip("/").lower() != self.issuer.lower():
            return self._page(403, error="This sign-in came from another site.")
        ctype = (req.header("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/x-www-form-urlencoded" or len(req.body) > MAX_FORM:
            return self._page(400, error=SENTENCE_EXPIRED_PAGE)
        try:
            form = _parse_form(req.body.decode("utf-8"))
        except (_OAuthError, UnicodeDecodeError):
            return self._page(400, error=SENTENCE_EXPIRED_PAGE)
        pending_id = form.get("pending") or ""
        pending = self.storage.get(K_PENDING, pending_id) if pending_id else None
        if pending is None:
            return self._page(400, error=SENTENCE_EXPIRED_PAGE)
        values = {"redirect_uri": pending["redirect_uri"], "state": pending["state"]}
        if form.get("action") == "deny":
            self.storage.delete(K_PENDING, pending_id)
            return self._error_redirect(values, "access_denied", "The person declined.")
        now = self.clock()
        with self._lock:
            times, locked = self._locked(req.addr, now)
        if locked:
            return self._sign_in_page(
                429,
                pending_id,
                pending,
                error=SENTENCE_LOCKED,
                headers={"Retry-After": str(int(SIGNIN_WINDOW - (now - times[0])) + 1)},
            )
        given = {
            f.name: form.get(f.name, "")
            for f in self.fields()
            if isinstance(form.get(f.name, ""), str)
        }
        try:
            principal = self.verify(given)
        except SignInRefused as exc:
            return self._sign_in_page(403, pending_id, pending, error=str(exc))
        del given  # the password goes no further than verify
        if not isinstance(principal, dict):
            with self._lock:
                times, _ = self._locked(req.addr, now)
                self._failed(req.addr, times, now)
            return self._sign_in_page(
                401, pending_id, pending, error=SENTENCE_BAD_SIGNIN
            )
        with self._lock:
            if self.storage.get(K_PENDING, pending_id) is None:
                return self._page(400, error=SENTENCE_EXPIRED_PAGE)
            self.storage.delete(K_PENDING, pending_id)
            self.storage.delete(K_FAILURES, req.addr)
            code = CODE_PREFIX + secrets.token_urlsafe(32)
            self.storage.put(
                K_CODE,
                _hash(code),
                {
                    "client_id": pending["client_id"],
                    "redirect_uri": pending["redirect_uri"],
                    "challenge": pending["challenge"],
                    "scope": pending["scope"],
                    "resource": pending["resource"],
                    "principal": principal,
                    "expires": now + CODE_TTL,
                    "used": False,
                },
                CODE_TTL,
            )
        location = _with_params(
            pending["redirect_uri"],
            {"code": code, "state": pending["state"], "iss": self.issuer},
        )
        return Response(303, {"Location": location, "Cache-Control": "no-store"})

    # -- tokens ----------------------------------------------------------------

    def _token(self, req: Request) -> Response:
        try:
            ctype = (req.header("Content-Type") or "").split(";", 1)[0]
            if ctype.strip().lower() != "application/x-www-form-urlencoded":
                raise _OAuthError(
                    "invalid_request",
                    "The token endpoint takes application/x-www-form-urlencoded.",
                )
            if len(req.body) > MAX_FORM:
                raise _OAuthError("invalid_request", "The request is too large.")
            try:
                form = _parse_form(req.body.decode("utf-8"))
            except UnicodeDecodeError:
                raise _OAuthError(
                    "invalid_request", "The request is malformed."
                ) from None
            client_id = form.get("client_id")
            if not client_id:
                raise _OAuthError(
                    "invalid_client", "client_id is required.", status=401
                )
            grant = form.get("grant_type")
            if grant == "authorization_code":
                return self._grant_code(form, client_id)
            if grant == "refresh_token":
                return self._grant_refresh(form, client_id)
            raise _OAuthError(
                "unsupported_grant_type",
                "Only authorization_code and refresh_token are offered.",
            )
        except _OAuthError as exc:
            headers = {"WWW-Authenticate": "Basic"} if exc.status == 401 else None
            return _oauth_error(exc, headers)

    def _revoke_family(self, family: str) -> None:
        self.storage.delete_where(K_ACCESS, "family", family)
        self.storage.delete_where(K_REFRESH, "family", family)

    def _grant_code(self, form: dict, client_id: str) -> Response:
        code = form.get("code") or ""
        verifier = form.get("code_verifier") or ""
        now = self.clock()
        with self._lock:
            key = _hash(code)
            rec = self.storage.get(K_CODE, key) if code else None
            if rec is None or rec.get("expires", 0) <= now:
                raise _OAuthError("invalid_grant", "The code is not valid.")
            if rec.get("used"):
                # A code used twice: whatever it bought is revoked too
                # (RFC 6749 section 4.1.2).
                if rec.get("family"):
                    self._revoke_family(rec["family"])
                self.storage.delete(K_CODE, key)
                raise _OAuthError("invalid_grant", "The code was already used.")
            family = secrets.token_urlsafe(16)
            rec["used"], rec["family"] = True, family
            # Burned now, right or wrong: a code is good for one try.
            self.storage.put(K_CODE, key, rec, max(1.0, rec["expires"] - now))
            if not hmac.compare_digest(rec["client_id"], client_id):
                raise _OAuthError(
                    "invalid_grant", "The code was issued to another client."
                )
            if form.get("redirect_uri") != rec["redirect_uri"]:
                raise _OAuthError(
                    "invalid_grant", "redirect_uri does not match the authorization."
                )
            if not _VERIFIER.fullmatch(verifier) or not hmac.compare_digest(
                s256(verifier), rec["challenge"]
            ):
                raise _OAuthError("invalid_grant", "The code_verifier does not match.")
            resource = form.get("resource")
            if resource is not None and not _same_resource(resource, rec["resource"]):
                raise _OAuthError("invalid_target", "The resource is not this door.")
            return self._issue(rec, family, now)

    def _grant_refresh(self, form: dict, client_id: str) -> Response:
        token = form.get("refresh_token") or ""
        now = self.clock()
        with self._lock:
            key = _hash(token)
            rec = self.storage.get(K_REFRESH, key) if token else None
            if rec is None or not hmac.compare_digest(rec.get("hash", ""), key):
                raise _OAuthError("invalid_grant", "The refresh token is not valid.")
            if rec.get("expires", 0) <= now:
                raise _OAuthError("invalid_grant", "The refresh token has expired.")
            if rec.get("used"):
                # An old refresh token again: someone has a copy. Everything
                # in its family ends (OAuth 2.1 section 4.3.1).
                self._revoke_family(rec["family"])
                raise _OAuthError("invalid_grant", "The refresh token is not valid.")
            if not hmac.compare_digest(rec["client_id"], client_id):
                raise _OAuthError(
                    "invalid_grant", "The refresh token was issued to another client."
                )
            resource = form.get("resource")
            if resource is not None and not _same_resource(resource, rec["resource"]):
                raise _OAuthError("invalid_target", "The resource is not this door.")
            asked = form.get("scope")
            scope = rec["scope"]
            if asked is not None:
                if not set(asked.split()) <= set(scope.split()):
                    raise _OAuthError(
                        "invalid_scope", "A refresh cannot widen the scope."
                    )
                keep = OFFLINE in asked.split() and OFFLINE in scope.split()
                scope = SCOPE + (f" {OFFLINE}" if keep else "")
            rec["used"] = True
            self.storage.put(K_REFRESH, key, rec, max(1.0, rec["expires"] - now))
            grant = dict(rec, scope=scope)
            return self._issue(grant, rec["family"], now, refresh=True)

    def _issue(self, grant: dict, family: str, now: float, refresh=False) -> Response:
        access = ACCESS_PREFIX + secrets.token_urlsafe(32)
        base = {
            "family": family,
            "client_id": grant["client_id"],
            "principal": grant["principal"],
            "scope": grant["scope"],
            "resource": grant["resource"],
        }
        akey = _hash(access)
        self.storage.put(
            K_ACCESS, akey, dict(base, hash=akey, expires=now + ACCESS_TTL), ACCESS_TTL
        )
        answer = {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": ACCESS_TTL,
            "scope": grant["scope"],
        }
        if refresh or OFFLINE in grant["scope"].split():
            token = REFRESH_PREFIX + secrets.token_urlsafe(32)
            rkey = _hash(token)
            self.storage.put(
                K_REFRESH,
                rkey,
                dict(base, hash=rkey, expires=now + REFRESH_TTL, used=False),
                REFRESH_TTL,
            )
            answer["refresh_token"] = token
        return _json(200, answer)

    # -- the pages ---------------------------------------------------------------

    def _sign_in_page(
        self, status, pending_id, pending, error=None, headers=None
    ) -> Response:
        about = {}
        try:
            about = self.about() or {}
        except Exception:  # noqa: BLE001 - the page still works without it
            about = {}
        company = about.get("company") or "this company"
        role = about.get("role")
        asking = (
            f"Let Claude work the books of {company} as {role}?"
            if role
            else f"Let Claude work the books of {company}?"
        )
        redirect_host = _host_of(pending["redirect_uri"])
        if pending.get("document"):
            who = (
                f"The app asking is published at {_host_of(pending['client_id'])}"
                + (
                    f" (it calls itself {pending['client_name']})"
                    if pending.get("client_name")
                    else ""
                )
                + "."
            )
        elif pending.get("client_name"):
            who = f"The app asking calls itself {pending['client_name']}."
        else:
            who = "The app asking gave no name."
        notes = [who, f"After you sign in, you go back to {redirect_host}."]
        warning = None
        if pending.get("loopback_only"):
            warning = (
                f"{redirect_host} is a program on your own computer, not a "
                "website. Any program on this computer could be waiting at "
                "that address: continue only if you just asked Claude Code (or "
                "another program you trust) to connect."
            )
        try:
            fields = list(self.fields())
        except Exception:  # noqa: BLE001
            fields = list(DEFAULT_FIELDS)
        inputs = []
        for f in fields:
            kind = "password" if f.secret else "text"
            auto = (
                f' autocomplete="{html.escape(f.autocomplete)}"'
                if f.autocomplete
                else ""
            )
            inputs.append(
                f'<label>{html.escape(f.label)}<input type="{kind}" '
                f'name="{html.escape(f.name)}"{auto} required></label>'
            )
        body = (
            f"<h1>{html.escape(PRODUCT)}</h1>"
            f"<p class=ask>{html.escape(asking)}</p>"
            + "".join(f"<p>{html.escape(n)}</p>" for n in notes)
            + (
                f"<p class=warn role=alert>{html.escape(warning)}</p>"
                if warning
                else ""
            )
            + (f"<p class=err role=alert>{html.escape(error)}</p>" if error else "")
            + f'<form method=post action="{AUTHORIZE_PATH}">'
            f'<input type=hidden name=pending value="{html.escape(pending_id)}">'
            f"<p>Sign in with {html.escape(self.sign_in_name)}.</p>"
            + "".join(inputs)
            + "<button type=submit name=action value=approve>Sign in and allow</button> "
            "<button type=submit name=action value=deny formnovalidate>Do not allow</button>"
            "</form>"
        )
        return self._html(
            status,
            "Sign in",
            body,
            form_targets=[_origin_of(pending["redirect_uri"])],
            headers=headers,
        )

    def _page(self, status: int, error: str) -> Response:
        body = (
            f"<h1>{html.escape(PRODUCT)}</h1>"
            f"<p class=err role=alert>{html.escape(error)}</p>"
        )
        return self._html(status, "Sign-in stopped", body)

    def _html(self, status, title, body, form_targets=(), headers=None) -> Response:
        doc = (
            "<!doctype html><html lang=en><head><meta charset=utf-8>"
            '<meta name=viewport content="width=device-width, initial-scale=1">'
            f"<title>{html.escape(title)} - {html.escape(PRODUCT)}</title>"
            f"<style>{_STYLE}</style></head><body><main>{body}</main></body></html>"
        )
        # Nothing but this page itself: its one style block by hash, a form
        # posted to itself (and the redirect it answers with, which browsers
        # check against form-action too), no scripts, no frames.
        targets = " ".join(["'self'", *form_targets])
        csp = (
            f"default-src 'none'; style-src '{_STYLE_HASH}'; "
            f"form-action {targets}; frame-ancestors 'none'; base-uri 'none'"
        )
        out = {
            "Content-Type": "text/html; charset=utf-8",
            "Content-Security-Policy": csp,
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
        }
        out.update(headers or {})
        return Response(status, out, doc.encode("utf-8"))


_STYLE = (
    "body{font:16px/1.5 system-ui,sans-serif;margin:0;background:#f4f4f2;color:#1d1d1b}"
    "main{max-width:28rem;margin:3rem auto;padding:1.5rem;background:#fff;"
    "border:1px solid #d8d8d4;border-radius:8px}"
    "h1{font-size:1.25rem;margin:0 0 1rem}.ask{font-weight:600}"
    "label{display:block;margin:.75rem 0}"
    "input{display:block;width:100%;box-sizing:border-box;padding:.5rem;"
    "font:inherit;margin-top:.25rem}"
    "button{font:inherit;padding:.5rem 1rem;margin:.75rem .5rem 0 0}"
    ".warn{background:#fff4d6;padding:.5rem}.err{color:#a40000;font-weight:600}"
)
_STYLE_HASH = "sha256-" + base64.b64encode(
    hashlib.sha256(_STYLE.encode("utf-8")).digest()
).decode("ascii")
