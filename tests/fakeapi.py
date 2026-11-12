"""A fake SlowBooks Pro API: enough of the app, in memory, for the tools.

It serves the real OpenAPI document the product generated (the fixture),
checks request bodies against that document's schemas the way the app's
strict models do (an unknown field is a 422 naming it, a missing required
field too), and keeps an invented company's records. It runs in-process (as
an ApiClient transport) or behind http.server on 127.0.0.1 and an ephemeral
port, for the subprocess and contract tests.

No real company's data: every name here is invented.
"""

import hashlib
import json
import re
import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

FIXTURE = Path(__file__).parent / "fixtures" / "openapi-2.31.8.json"
TOKEN_FAKE = "sbp_test_" + "0" * 32
COMPANY = "Acme Fence Test Co"

# The fake app's people, for the OAuth door's sign-in. Invented; the
# passwords are test constants.
OWNER_PW = "fake-owner-pw-0001"
CLERK_PW = "fake-clerk-pw-0002"

# collection route -> (audit table, label in sentences)
COLLECTIONS = {
    "/api/customers": ("customers", "Customer"),
    "/api/vendors": ("vendors", "Vendor"),
    "/api/items": ("items", "Item"),
    "/api/accounts": ("accounts", "Account"),
    "/api/invoices": ("invoices", "Invoice"),
    "/api/bills": ("bills", "Bill"),
    "/api/expenses": ("expenses", "Expense"),
    "/api/journal": ("journal_entries", "Journal entry"),
    "/api/payments": ("payments", "Payment"),
    "/api/bill-payments": ("bill_payments", "Bill payment"),
}
KEYED = {"/api/payments", "/api/bill-payments"}
SERVER_LIMIT = {"/api/invoices", "/api/bills", "/api/banking/transactions"}


def load_spec() -> dict:
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


# What the app's door says to any token on these routes (app/main.py).
TOKEN_DOOR = "API tokens cannot manage users or tokens"
ROLE_REFUSAL = "Your role doesn't allow this action"
ACCOUNTANT_READ_REFUSAL = (
    "An accountant's sign-in reads the books, but payroll, HR and employee "
    "records stay with an administrator."
)


class FakeBooks:
    def __init__(self, spec=None, token: str = TOKEN_FAKE, role: str = "bookkeeper"):
        self.spec = spec if spec is not None else load_spec()
        self.token = token
        self.role = role
        self.records = {route: {} for route in COLLECTIONS}
        self.audit = []
        self.keys = {}
        self.calls = []
        self._next = 1
        self.settings = {
            "company_name": COMPANY,
            "company_type": "llc",
            "closing_date": None,
        }
        # name -> {"pw": ..., "role": ...}; one user is the single-user
        # install, whose sign-in is the password alone.
        self.users = {}
        self.sessions = {}  # cookie value -> user name
        self.sign_ins = []  # every /api/auth/login attempt's outcome
        self.logouts = 0
        self.accounts = {}
        for number, name, kind in (
            ("1000", "Checking", "asset"),
            ("1100", "Accounts Receivable", "asset"),
            ("2000", "Accounts Payable", "liability"),
            ("4000", "Sales", "income"),
            ("6000", "Supplies", "expense"),
        ):
            row = self._store(
                "/api/accounts",
                {"account_number": number, "name": name, "account_type": kind},
                audited=False,
            )
            self.accounts[number] = row

    # -- helpers ---------------------------------------------------------

    def _store(self, route, data, audited=True):
        rid = self._next
        self._next += 1
        row = {"id": rid, **data}
        self.records[route][rid] = row
        if audited:
            self.audit.append(
                {
                    "table_name": COLLECTIONS[route][0],
                    "record_id": rid,
                    "action": "INSERT",
                    "username": "token:mcp-test",
                }
            )
        return row

    def _schema(self, route, method):
        op = self.spec["paths"].get(route, {}).get(method, {})
        ref = (
            op.get("requestBody", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema", {})
            .get("$ref", "")
        )
        return self.spec["components"]["schemas"].get(ref.rsplit("/", 1)[-1])

    def _validate(self, route, method, body):
        schema = self._schema(route, method)
        if schema is None:
            return None
        if not isinstance(body, dict):
            return [{"loc": ["body"], "msg": "Input should be an object"}]
        errors = []
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in body:
                if key not in props:
                    errors.append(
                        {
                            "type": "extra_forbidden",
                            "loc": ["body", key],
                            "msg": "Extra inputs are not permitted",
                        }
                    )
        for key in schema.get("required", []):
            if key not in body:
                errors.append(
                    {"type": "missing", "loc": ["body", key], "msg": "Field required"}
                )
        return errors or None

    @staticmethod
    def _limit(params, default=None):
        raw = (params or {}).get("limit", default)
        return int(raw) if raw is not None else None

    # -- the API ---------------------------------------------------------

    def handle(self, method, path, params=None, body=None, headers=None):
        """(status, payload) for one request, as the app would answer."""
        self.calls.append((method, path, dict(params or {}), body, dict(headers or {})))
        params = params or {}
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        if path == "/openapi.json" and method == "GET":
            return 200, self.spec
        # The app's door, cut to what the tests use: no token manages users
        # or tokens, a read-only token reads everything but the audit log,
        # an accountant's does not read the employee directory.
        if path.startswith(("/api/users", "/api/tokens")):
            return 403, {"detail": TOKEN_DOOR}
        if self.role == "readonly" and (
            method != "GET" or path.startswith("/api/audit")
        ):
            return 403, {"detail": ROLE_REFUSAL}
        if self.role == "accountant" and path.startswith("/api/employees"):
            return 403, {"detail": ACCOUNTANT_READ_REFUSAL}
        if method == "GET":
            return self._get(path, params)
        if method == "POST":
            return self._post(path, params, body, headers)
        return 405, {"detail": "Method Not Allowed"}

    def _get(self, path, params):
        if path == "/api/auth/status":
            return 200, {"authenticated": True, "company_name": COMPANY}
        if path == "/api/settings":
            return 200, dict(self.settings)
        if path == "/api/system":
            return 200, {"version": self.spec["info"]["version"], "desktop": True}
        if path == "/api/employees":
            return 200, []
        if path == "/api/companies":
            return 200, [{"name": COMPANY, "database": "acme-fence-test"}]
        if path == "/api/search":
            q = str(params.get("q", "")).lower()
            out = {}
            for route in ("/api/customers", "/api/vendors", "/api/items"):
                out[COLLECTIONS[route][0]] = [
                    r
                    for r in self.records[route].values()
                    if q and q in str(r.get("name", "")).lower()
                ]
            return 200, out
        if path == "/api/audit":
            table = params.get("table_name")
            return 200, [r for r in self.audit if not table or r["table_name"] == table]
        if path == "/api/banking/transactions":
            return 200, []
        if path.startswith("/api/reports/"):
            name = path[len("/api/reports/") :]
            if path in self.spec["paths"]:
                return 200, {"report": name, "params": dict(params), "rows": []}
            return 404, {"detail": "Not Found"}
        if path in self.records:
            rows = list(self.records[path].values())
            search = params.get("search")
            if search:
                rows = [r for r in rows if search.lower() in r.get("name", "").lower()]
            if path in SERVER_LIMIT and params.get("limit") is not None:
                rows = rows[: self._limit(params)]
            return 200, rows
        m = re.fullmatch(r"(/api/[a-z-]+)/(\d+)", path)
        if m and m.group(1) in self.records:
            row = self.records[m.group(1)].get(int(m.group(2)))
            if row is None:
                label = COLLECTIONS[m.group(1)][1]
                return 404, {"detail": f"{label} {m.group(2)} not found"}
            return 200, row
        return 404, {"detail": "Not Found"}

    def _post(self, path, params, body, headers):
        m = re.fullmatch(r"(/api/[a-z-]+)/(\d+)/void", path)
        if m and m.group(1) in self.records:
            row = self.records[m.group(1)].get(int(m.group(2)))
            if row is None:
                return 404, {"detail": f"{COLLECTIONS[m.group(1)][1]} not found"}
            if m.group(1) == "/api/invoices":
                row["status"] = "void"
            row["is_voided"] = True
            if params.get("fee_refunded") is not None:
                row["fee_refunded"] = params["fee_refunded"] in (True, "true")
            return 200, row
        if path not in self.records or path == "/api/accounts":
            return 404, {"detail": "Not Found"}
        errors = self._validate(path, "post", body)
        if errors:
            return 422, {"detail": errors}
        key = headers.get("idempotency-key") if path in KEYED else None
        digest = hashlib.sha256(
            json.dumps(body, sort_keys=True, default=str).encode()
        ).hexdigest()
        if key:
            seen = self.keys.get((path, key))
            if seen:
                if seen[0] != digest:
                    return 409, {
                        "detail": "This Idempotency-Key was already used for a "
                        "different request. Use a new key for a new payment."
                    }
                return seen[1], seen[2]
        if path == "/api/journal":
            debit = sum(Decimal(str(x.get("debit", 0))) for x in body["lines"])
            credit = sum(Decimal(str(x.get("credit", 0))) for x in body["lines"])
            if debit != credit:
                return 400, {
                    "detail": f"Debits ({debit:.2f}) must equal credits "
                    f"({credit:.2f})."
                }
        row = self._store(path, dict(body))
        if path == "/api/invoices":
            row["status"] = "open"
        if key:
            self.keys[(path, key)] = (digest, 201, row)
        return 201, row

    # -- the app's own sign-in (cookies), cut to what the door uses -----------

    def add_user(self, name, password, role):
        self.users[name] = {"pw": password, "role": role}

    def auth(self, method, path, body, cookie):
        """(status, payload, set-cookie or None) for /api/auth/*."""
        user = self.sessions.get(cookie) if cookie else None
        multi = len(self.users) > 1
        if path == "/api/auth/status" and method == "GET":
            out = {
                "setup_needed": not self.users,
                "authenticated": user is not None,
                "multi_user": multi,
                "company_name": COMPANY,
            }
            if user is not None:
                out["user"] = {"username": user, "role": self.users[user]["role"]}
            return 200, out, None
        if path == "/api/auth/login" and method == "POST":
            body = body if isinstance(body, dict) else {}
            name = (body.get("username") or "").strip().lower()
            if not multi and self.users:
                name = name or next(iter(self.users))
            if multi and not name:
                self.sign_ins.append("refused")
                return 400, {"detail": "Username required"}, None
            row = self.users.get(name)
            if row is None or row["pw"] != body.get("password"):
                self.sign_ins.append("refused")
                return 401, {"detail": "Incorrect password"}, None
            self.sign_ins.append("ok")
            value = "s" + str(len(self.sessions) + 1) + "-" + name
            self.sessions[value] = name
            return 200, {"status": "ok", "authenticated": True}, value
        if path == "/api/auth/logout" and method == "POST":
            self.logouts += 1
            self.sessions.pop(cookie, None)
            return 200, {"status": "ok"}, ""
        return 404, {"detail": "Not Found"}, None

    # -- adapters ----------------------------------------------------------

    def transport(self, method, path, params, body, headers):
        """An ApiClient transport that answers in-process."""
        return self.handle(method, path, params, body, headers)


class _Handler(BaseHTTPRequestHandler):
    books: FakeBooks

    def log_message(self, *a):  # keep test output quiet
        pass

    def _answer(self):
        books = self.server.books
        parts = urlsplit(self.path)
        params = {k: v[-1] for k, v in parse_qs(parts.query).items()}
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw) if raw else None
        if parts.path.startswith("/api/auth/"):
            cookie = None
            for part in (self.headers.get("Cookie") or "").split(";"):
                name, _, value = part.strip().partition("=")
                if name == "slowbooks_session":
                    cookie = value
            status, payload, set_cookie = books.auth(
                self.command, parts.path, body, cookie
            )
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            if set_cookie is not None:
                self.send_header(
                    "Set-Cookie",
                    f"slowbooks_session={set_cookie}; Path=/; HttpOnly; SameSite=strict",
                )
            self.end_headers()
            self.wfile.write(data)
            return
        if self.headers.get("Authorization") != f"Bearer {books.token}":
            status, payload = 401, {"detail": "Not authenticated"}
        else:
            status, payload = books.handle(
                self.command, parts.path, params, body, dict(self.headers)
            )
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _answer


class FakeServer:
    """FakeBooks behind http.server on 127.0.0.1 and an ephemeral port."""

    def __init__(self, books: FakeBooks):
        self.books = books
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.books = books
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
