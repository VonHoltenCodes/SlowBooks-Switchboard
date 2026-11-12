# ============================================================================
# The self-hosted door's sign-in (docs/AUTH.md): the SlowBooks Pro
# administrator sign-in, checked against the local app the way the app's own
# page checks it, and nothing kept.
#
#   POST /api/auth/login   with a cookie jar of its own, made for this one try
#   GET  /api/auth/status  the role of the session just made
#
# and then the cookie jar is dropped. No /api/auth/logout: SlowBooks Pro's
# sign-out ends every session of that user (it bumps the user's session
# version, app/routes/auth.py _end_sessions_of), so signing out here would
# sign the administrator out of SlowBooks Pro in their own browser each
# time Claude connects. The app's session is a signed cookie held by the
# client; with the jar gone, nothing of it is kept anywhere.
#
# Only an administrator may let Claude in: the administrator chose the API
# token that every tool call uses, and its role. The password goes to the
# app and nowhere else; it is not logged, stored or returned. The API token
# is never sent on these requests.
# ============================================================================

import json
import logging
import ssl
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

from sbplive.oauth import DEFAULT_FIELDS, Field, SignInRefused

log = logging.getLogger("sbplive")

TIMEOUT = 8.0  # under Claude's ten seconds for the whole page
ADMIN_ROLE = "admin"

NOT_ADMIN = (
    "Only an administrator of these books can let Claude in; sign in as an "
    "administrator."
)
PASSWORD_ONLY = (
    Field("password", "Password", secret=True, autocomplete="current-password"),
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AppSignIn:
    """verify(), fields() and about() for oauth.OAuthServer, against the app
    at base_url. role_phrase is what the API token works as, for the page."""

    def __init__(
        self, base_url: str, *, role_phrase: str | None = None, timeout=TIMEOUT
    ):
        self.base_url = base_url.rstrip("/")
        self.role_phrase = role_phrase
        self.timeout = timeout

    def _opener(self, jar=None):
        handlers = [_NoRedirect()]
        if jar is not None:
            handlers.append(urllib.request.HTTPCookieProcessor(jar))
        if self.base_url.startswith("https://"):
            handlers.append(
                urllib.request.HTTPSHandler(context=ssl.create_default_context())
            )
        return urllib.request.build_opener(*handlers)

    def _call(self, opener, method: str, path: str, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with opener.open(req, timeout=self.timeout) as resp:
                status, raw = resp.status, resp.read(65536)
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read(65536)
        try:
            payload = json.loads(raw.decode("utf-8") or "null")
        except (UnicodeDecodeError, ValueError):
            payload = None
        return status, payload

    def _status(self) -> dict:
        try:
            status, payload = self._call(self._opener(), "GET", "/api/auth/status")
        except (OSError, ValueError):
            return {}
        return payload if status == 200 and isinstance(payload, dict) else {}

    # -- what the OAuth module asks -----------------------------------------------

    def fields(self):
        """The user name and password when the books have several users, the
        password alone otherwise (as the app's own sign-in page asks)."""
        status = self._status()
        if status and not status.get("multi_user"):
            return list(PASSWORD_ONLY)
        return list(DEFAULT_FIELDS)

    def about(self) -> dict:
        return {
            "company": self._status().get("company_name") or None,
            "role": self.role_phrase,
        }

    def verify(self, fields: dict):
        password = fields.get("password") or ""
        username = (fields.get("username") or "").strip()
        if not password:
            return None
        body = {"password": password}
        if username:
            body["username"] = username
        opener = self._opener(CookieJar())
        try:
            status, payload = self._call(opener, "POST", "/api/auth/login", body)
        except (OSError, ValueError):
            raise SignInRefused(
                f"SlowBooks Pro did not answer at {self.base_url}, so the sign-in "
                "could not be checked. Open SlowBooks Pro and try again."
            ) from None
        if status in (400, 401, 422):
            log.warning("a sign-in to the remote door was refused by SlowBooks Pro")
            return None
        if status == 409:
            raise SignInRefused(
                "These books have no password yet. Set one in SlowBooks Pro first."
            )
        if status == 429:
            raise SignInRefused(
                "SlowBooks Pro is taking no more sign-ins for a minute. Wait and "
                "try again."
            )
        if status == 403:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            raise SignInRefused(detail if isinstance(detail, str) else NOT_ADMIN)
        if status != 200:
            raise SignInRefused(
                f"SlowBooks Pro answered the sign-in with status {status}. Try again."
            )
        try:
            _, state = self._call(opener, "GET", "/api/auth/status")
        except (OSError, ValueError):
            state = None
        opener = None  # the session cookie goes with it (see the top)
        if not isinstance(state, dict) or state.get("authenticated") is not True:
            raise SignInRefused(
                "SlowBooks Pro took the sign-in but did not say who signed in. "
                "Try again."
            )
        user = state.get("user") if isinstance(state.get("user"), dict) else {}
        # The app's own rule: a session with no user row is the operator,
        # an administrator (app/routes/_roles.py).
        role = user.get("role") or ADMIN_ROLE
        if role != ADMIN_ROLE:
            log.warning("a non-administrator sign-in to the remote door was refused")
            raise SignInRefused(NOT_ADMIN)
        return {"username": user.get("username") or None, "role": ADMIN_ROLE}
