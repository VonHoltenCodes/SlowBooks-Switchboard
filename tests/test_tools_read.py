"""Config, the loopback client and the read tools (Keith's tests, #655).

In the product these ran against the app in-process with a real bookkeeper
token; here they run against the fake API in tests/fakeapi.py, which serves
the product's own OpenAPI document and checks bodies against its schemas."""

import json
import os
import subprocess
import sys

import pytest
from bookskit import ManyBooks
from fakeapi import FakeInstall

from sbplive import operations, tools
from sbplive.client import ApiClient, ApiError, Refused
from sbplive.config import ConfigError, check_url, load_config
from sbplive.server import build_registry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

TOKEN_FAKE = "sbp_test_" + "a" * 40


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A valid environment; tests change one thing at a time."""
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN_FAKE + "\n")
    token_file.chmod(0o600)
    monkeypatch.delenv("SBPLIVE_URL", raising=False)
    monkeypatch.delenv("SBPLIVE_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("SBPLIVE_TOKEN_FILE", str(token_file))
    return token_file


def test_config_defaults(env):
    cfg = load_config()
    assert cfg.base_url == "http://127.0.0.1:3001"
    assert cfg.token == TOKEN_FAKE
    assert cfg.timeout == 120
    assert TOKEN_FAKE not in repr(cfg)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://127.0.0.1:3001",
        "http://127.0.0.1:3001/api",
        "http://user:pw@127.0.0.1:3001",
        "http://127.0.0.1:3001?x=1",
        "http://127.0.0.1:3001#top",
        "not a url",
        "",
    ],
)
def test_config_refuses_malformed_urls(env, monkeypatch, url):
    monkeypatch.setenv("SBPLIVE_URL", url or " ")
    with pytest.raises(ConfigError, match="must look like"):
        load_config()


@pytest.mark.parametrize(
    "url", ["http://8.8.8.8:3001", "http://[2001:4860::8888]:3001"]
)
def test_config_refuses_plain_http_to_a_public_host(env, monkeypatch, url):
    monkeypatch.setenv("SBPLIVE_URL", url)
    with pytest.raises(ConfigError, match="must start with https://"):
        load_config()


@pytest.mark.parametrize("url", ["http://0.0.0.0:3001", "http://224.0.0.1:3001"])
def test_config_refuses_addresses_that_are_not_a_computer(env, monkeypatch, url):
    monkeypatch.setenv("SBPLIVE_URL", url)
    with pytest.raises(ConfigError, match="not the address of a computer"):
        load_config()


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.20:3001",
        "http://10.0.0.5:3001",
        "http://172.16.4.4",
        "http://169.254.10.10:3001",
        "http://[fd00::20]:3001",
        "http://[fe80::1]:3001",
    ],
)
def test_check_url_takes_a_private_network_with_a_warning(url):
    clean, warning = check_url(url)
    assert clean == url
    assert "unencrypted" in warning


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:3001",
        "https://books.example.com",
        "https://203.0.113.9:8443",
        "http://localhost:3001",
        "http://[::1]:3001",
    ],
)
def test_check_url_takes_https_and_loopback_without_a_warning(url):
    assert check_url(url) == (url, None)


def test_check_url_resolves_a_name_for_plain_http(monkeypatch):
    from sbplive import config as cfg

    answers = {
        "office-server.lan": ["192.168.1.10"],
        "books.example.com": ["93.184.215.14"],
    }

    def fake(host, port):
        if host not in answers:
            raise OSError("not found")
        return [cfg.ipaddress.ip_address(a) for a in answers[host]]

    monkeypatch.setattr(cfg, "_resolve", fake)
    clean, warning = check_url("http://office-server.lan:3001")
    assert clean == "http://office-server.lan:3001" and "unencrypted" in warning
    with pytest.raises(ConfigError, match="must start with https://"):
        check_url("http://books.example.com:3001")
    with pytest.raises(ConfigError, match="could not be found"):
        check_url("http://nowhere.lan:3001")
    # the server lets an unknown name through: the tools then say the app
    # did not answer
    assert check_url("http://nowhere.lan:3001", must_resolve=False)[0]


# Plain http:// goes only to this computer and the networks an office can
# own (config._LOCAL_NETWORKS). No test here connects anywhere: each calls
# the check, with name lookups answered by a stub.

LOOPBACK = [
    "127.0.0.1",
    "127.255.255.254",
    "[::1]",
    "[::ffff:127.0.0.1]",  # IPv4-mapped: judged by the IPv4 address inside
    "localhost",
]
LOCAL = [
    "10.0.0.1",
    "10.255.255.254",
    "172.16.0.1",
    "172.31.255.254",
    "192.168.0.1",
    "192.168.255.254",
    "169.254.0.1",
    "169.254.255.254",
    "[fc00::1]",
    "[fdff:ffff:ffff:ffff:ffff:ffff:ffff:fffe]",
    "[fe80::1]",
    "[febf::1]",
    "[fe80::1%25eth0]",
    "[::ffff:10.0.0.5]",
    "[::ffff:172.16.4.4]",
    "[::ffff:192.168.1.20]",
    "[::ffff:169.254.10.10]",
]
REFUSED = [
    "192.0.2.10",  # documentation (TEST-NET-1)
    "198.51.100.10",  # documentation (TEST-NET-2)
    "203.0.113.10",  # documentation (TEST-NET-3)
    "[2001:db8::1]",  # documentation
    "198.18.0.1",  # benchmark
    "198.19.255.254",  # benchmark
    "240.0.0.1",  # reserved
    "255.255.255.255",  # broadcast
    "100.64.0.1",  # shared (carrier-grade NAT)
    "100.127.255.254",  # shared
    "192.0.0.1",  # protocol assignments
    "192.88.99.1",  # 6to4 anycast
    "0.0.0.1",  # "this network"
    "9.255.255.255",  # each edge of the lists, one address outside
    "11.0.0.0",
    "172.15.255.255",
    "172.32.0.0",
    "192.167.255.255",
    "192.169.0.0",
    "169.253.255.255",
    "169.255.0.0",
    "126.255.255.255",
    "128.0.0.0",
    "8.8.8.8",  # public
    "93.184.215.14",
    "[2606:4700:4700::1111]",  # public
    "[fec0::1]",  # site-local, withdrawn
    "[fbff::1]",
    "[64:ff9b::c0a8:114]",  # NAT64
    "[2002:c0a8:114::1]",  # 6to4
    "[100::1]",  # discard only
    "[::ffff:8.8.8.8]",  # IPv4-mapped, a public address inside
    "[::ffff:203.0.113.10]",
    "[::ffff:198.18.0.1]",
    "[::ffff:100.64.0.1]",
    "[::ffff:240.0.0.1]",
]
NOT_A_COMPUTER = [
    "0.0.0.0",
    "[::]",
    "[::ffff:0.0.0.0]",
    "224.0.0.1",
    "239.255.255.250",
    "[ff02::1]",
    "[::ffff:224.0.0.1]",
]


def _bare(host):
    return host.strip("[]").replace("%25", "%")


@pytest.mark.parametrize("host", LOOPBACK)
def test_plain_http_to_this_computer_needs_no_warning(host):
    url = f"http://{host}:3001"
    assert check_url(url) == (url, None)


@pytest.mark.parametrize("host", LOCAL)
def test_plain_http_is_taken_on_a_local_network_with_the_warning(host):
    from sbplive.config import UNENCRYPTED_WARNING

    url = f"http://{host}:3001"
    assert check_url(url) == (url, UNENCRYPTED_WARNING)
    assert check_url(url, must_resolve=False) == (url, UNENCRYPTED_WARNING)


@pytest.mark.parametrize("host", REFUSED)
@pytest.mark.parametrize("must_resolve", [True, False])
def test_plain_http_is_refused_anywhere_else(host, must_resolve):
    with pytest.raises(ConfigError) as refusal:
        check_url(f"http://{host}:3001", must_resolve=must_resolve)
    assert str(refusal.value) == (
        f"{_bare(host)} is not on a private network, so the address must start "
        "with https://: over plain http:// the API token would cross the "
        "internet unencrypted."
    )
    # the same host over https:// is taken: its certificate is verified
    assert check_url(f"https://{host}:3001") == (f"https://{host}:3001", None)


@pytest.mark.parametrize("host", NOT_A_COMPUTER)
@pytest.mark.parametrize("scheme", ["http", "https"])
def test_no_address_and_multicast_are_never_a_computer(host, scheme):
    with pytest.raises(ConfigError, match="is not the address of a computer"):
        check_url(f"{scheme}://{host}:3001")


# A zone (%eth0) belongs on an IPv6 address and nowhere else. The program
# connects to the host with its percent escapes undone, so that is the host
# that is judged: an address in front of a "%" says nothing about where a
# name goes.
PERCENT_HOSTS = [
    "10.0.0.1%2eevil.test",  # connects to the name 10.0.0.1.evil.test
    "10.0.0.1%.evil.test",
    "10.0.0.1%25eth0",
    "192.168.1.20%25",
    "127.0.0.1%2eevil.test",
    "[::1%2e2.3.4]",  # connects to ::1.2.3.4, which is not this computer
    "[fe80::1%25]",  # a zone with nothing in it
    "office%2dserver.lan",
]


@pytest.fixture
def no_lookup(monkeypatch):
    from sbplive import config as cfg

    def getaddrinfo(*args, **kwargs):
        raise AssertionError("a host with % in it must not be looked up")

    monkeypatch.setattr(cfg.socket, "getaddrinfo", getaddrinfo)


@pytest.mark.parametrize("host", PERCENT_HOSTS)
@pytest.mark.parametrize("must_resolve", [True, False])
def test_a_percent_does_not_make_a_host_a_local_address(host, must_resolve, no_lookup):
    with pytest.raises(ConfigError) as refusal:
        check_url(f"http://{host}:3001", must_resolve=must_resolve)
    assert str(refusal.value) == (
        f"{host.strip('[]')} is not on a private network, so the address must "
        "start with https://: over plain http:// the API token would cross "
        "the internet unencrypted."
    )


@pytest.mark.parametrize("host", ["[fe80::1%25eth0]", "[fe80::1%eth0]"])
def test_a_zone_on_a_link_local_ipv6_address_is_taken(host, no_lookup):
    from sbplive.config import UNENCRYPTED_WARNING

    url = f"http://{host}:3001"
    assert check_url(url) == (url, UNENCRYPTED_WARNING)


def test_an_escaped_local_address_is_judged_as_the_address_it_is(no_lookup):
    from sbplive.config import UNENCRYPTED_WARNING

    url = "http://%31%30.0.0.1:3001"  # the program connects to 10.0.0.1
    assert check_url(url) == (url, UNENCRYPTED_WARNING)
    with pytest.raises(ConfigError, match="is not on a private network"):
        check_url("http://%31%39%32.0.2.10:3001")  # 192.0.2.10


def test_the_local_networks_are_the_listed_ones_and_no_more():
    from sbplive import config as cfg

    assert sorted(str(n) for n in cfg._LOCAL_NETWORKS) == sorted(
        [
            "127.0.0.0/8",
            "::1/128",
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
            "169.254.0.0/16",
            "fc00::/7",
            "fe80::/10",
        ]
    )


# name -> the addresses the lookup gives, as getaddrinfo writes them
NAMES = {
    "one-good.lan": ["192.168.1.10"],
    "two-good.lan": ["10.1.2.3", "fd00::20"],
    "link-local.lan": ["fe80::1%eth0", "169.254.7.7"],
    "mapped-good.lan": ["::ffff:192.168.1.10"],
    "one-bad.example": ["93.184.215.14"],
    "documentation.example": ["203.0.113.9"],
    "benchmark.example": ["198.18.0.9"],
    "shared.example": ["100.64.0.9"],
    "mapped-bad.example": ["::ffff:203.0.113.9"],
    "mixed.example": ["192.168.1.10", "203.0.113.9"],
    "mixed-six.example": ["fd00::20", "2001:db8::9"],
    "mixed-last.example": ["10.0.0.1", "172.16.0.1", "198.51.100.9"],
    "nothing.example": [],
}
GOOD_NAMES = [n for n in NAMES if n.endswith(".lan")]
BAD_NAMES = [n for n in NAMES if n.endswith(".example")]


@pytest.fixture
def lookups(monkeypatch):
    """Name lookups answered from NAMES; a name not in it is not found. The
    real resolver is never asked."""
    from sbplive import config as cfg

    asked = []

    def getaddrinfo(host, port, *args, **kwargs):
        asked.append((host, port))
        if host not in NAMES:
            raise cfg.socket.gaierror("Name or service not known")
        out = []
        for text in NAMES[host]:
            six = ":" in text
            family = cfg.socket.AF_INET6 if six else cfg.socket.AF_INET
            where = (text, port, 0, 0) if six else (text, port)
            out.append((family, cfg.socket.SOCK_STREAM, 6, "", where))
        return out

    monkeypatch.setattr(cfg.socket, "getaddrinfo", getaddrinfo)
    return asked


@pytest.mark.parametrize("name", GOOD_NAMES)
def test_a_name_is_taken_when_every_address_is_local(lookups, name):
    from sbplive.config import UNENCRYPTED_WARNING

    url = f"http://{name}:3001"
    assert check_url(url) == (url, UNENCRYPTED_WARNING)
    assert lookups == [(name, 3001)]


@pytest.mark.parametrize("name", BAD_NAMES)
@pytest.mark.parametrize("must_resolve", [True, False])
def test_a_name_is_refused_when_any_address_is_not_local(lookups, name, must_resolve):
    with pytest.raises(ConfigError) as refusal:
        check_url(f"http://{name}:3001", must_resolve=must_resolve)
    assert str(refusal.value) == (
        f"{name} is not on a private network, so the address must start with "
        "https://: over plain http:// the API token would cross the internet "
        "unencrypted."
    )


def test_a_name_that_is_not_found_is_as_before(lookups):
    with pytest.raises(ConfigError, match="could not be found on the network"):
        check_url("http://nowhere.lan:3001")
    # the server, started by a host, lets it through to say the app did not answer
    url = "http://nowhere.lan:3001"
    assert check_url(url, must_resolve=False)[0] == url
    # https:// and this computer's own name are never looked up
    del lookups[:]
    assert check_url("https://one-bad.example") == ("https://one-bad.example", None)
    assert check_url("http://localhost:3001") == ("http://localhost:3001", None)
    assert lookups == []


def test_config_reads_the_store_when_nothing_is_set(monkeypatch):
    from sbplive import store

    monkeypatch.delenv("SBPLIVE_TOKEN_FILE", raising=False)
    with pytest.raises(ConfigError, match="not set up"):
        load_config()
    store.save("http://192.168.1.20:3001", TOKEN_FAKE)
    cfg = load_config()
    assert cfg.base_url == "http://192.168.1.20:3001"
    assert cfg.token == TOKEN_FAKE
    monkeypatch.setenv("SBPLIVE_URL", "http://127.0.0.1:4111")
    assert load_config().base_url == "http://127.0.0.1:4111"


def test_config_accepts_loopback_names(env, monkeypatch):
    for url in ("http://localhost:3001/", "http://[::1]:3001"):
        monkeypatch.setenv("SBPLIVE_URL", url)
        assert load_config().base_url == url.rstrip("/")


def test_config_token_file_required_and_present(env, monkeypatch, tmp_path):
    monkeypatch.delenv("SBPLIVE_TOKEN_FILE")
    with pytest.raises(ConfigError, match="SBPLIVE_TOKEN_FILE"):
        load_config()
    monkeypatch.setenv("SBPLIVE_TOKEN_FILE", str(tmp_path / "missing"))
    with pytest.raises(ConfigError, match="does not exist"):
        load_config()


def test_config_token_file_permissions(env):
    env.chmod(0o640)
    if os.name == "nt":
        # Every file reads as 0o666 on Windows, so the check is skipped there.
        assert load_config().token == TOKEN_FAKE
    else:
        with pytest.raises(ConfigError, match="chmod 600"):
            load_config()


@pytest.mark.parametrize(
    "content", ["", "\n", "hello\n", TOKEN_FAKE + "\nsecond line\n", "xsbp_abc\n"]
)
def test_config_token_content_refused_without_echo(env, content):
    env.write_text(content)
    with pytest.raises(ConfigError) as exc:
        load_config()
    assert "sbp_" in str(exc.value)
    for line in content.split():
        if len(line) > 5:  # the content, not a word of the sentence
            assert line not in str(exc.value)


def test_config_error_never_contains_token(env):
    env.write_text(TOKEN_FAKE + "\nmore\n")
    with pytest.raises(ConfigError) as exc:
        load_config()
    assert TOKEN_FAKE not in str(exc.value)


@pytest.mark.parametrize("value", ["0", "0.5", "3601", "abc", "nan", "inf", ""])
def test_config_timeout_bounds(env, monkeypatch, value):
    monkeypatch.setenv("SBPLIVE_TIMEOUT_SECONDS", value)
    if value == "":  # unset-like: the default applies
        assert load_config().timeout == 120
        return
    with pytest.raises(ConfigError, match="1 to 3600"):
        load_config()


def test_config_timeout_valid(env, monkeypatch):
    monkeypatch.setenv("SBPLIVE_TIMEOUT_SECONDS", "30")
    assert load_config().timeout == 30


def test_module_exits_2_when_not_set_up(home):
    from conftest import HOME_VARS, home_env

    clean = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("SBPLIVE_", "SLOWBOOKS_")) and k not in HOME_VARS
    }
    clean.update(home_env(home))
    r = subprocess.run(
        [sys.executable, "-m", "sbplive"],
        env=clean,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert r.returncode == 2
    assert "not set up" in r.stderr and "SBPLIVE_TOKEN_FILE" in r.stderr
    assert r.stdout == ""


# ---------------------------------------------------------------------------
# client
# ---------------------------------------------------------------------------


def test_client_errors_carry_the_apis_sentence(api):
    with pytest.raises(ApiError) as exc:
        api.get("/api/customers/999999")
    assert exc.value.status == 404
    assert str(exc.value).startswith("HTTP 404: ")
    assert exc.value.detail

    anon = ApiClient(lambda *a: (401, {"detail": "Not authenticated"}))
    with pytest.raises(ApiError, match="HTTP 401: Not authenticated"):
        anon.get("/api/customers")


def test_client_without_detail_falls_back_to_status(api):
    bare = ApiClient(lambda *a: (502, ""))
    with pytest.raises(ApiError, match="HTTP 502: .*502"):
        bare.get("/api/customers")


@pytest.mark.parametrize("path", ["/health", "api/customers", "/customers", "/"])
def test_client_reaches_the_api_only(api, path):
    with pytest.raises(ApiError) as exc:
        api.get(path)
    assert exc.value.status == 403
    assert "not part of the SlowBooks Pro API" in exc.value.detail


@pytest.mark.parametrize(
    "path", ["/api/tokens", "/api/users", "/api/system", "/api/company-file/encryption"]
)
def test_client_sends_admin_routes_to_the_api(api, books, path):
    """Match the software: the API decides, and its answer comes back."""
    try:
        api.get(path)
    except ApiError as exc:
        assert not isinstance(exc, Refused)  # the API's answer, not ours
    assert books.calls[-1][1] == path


def test_making_a_backup_is_the_apis_to_decide(api, books):
    # the app lets a token ticked "Can make backups" make one whatever its
    # role; this program sends the request and passes the answer on
    try:
        api.post("/api/backups", {})
    except ApiError as exc:
        assert not isinstance(exc, Refused)
    assert any(c[1] == "/api/backups" for c in books.calls)


def test_client_post_sends_a_body(api):
    made = api.post("/api/customers", {"name": "Posted Co"})
    assert made["name"] == "Posted Co"


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------


def test_context(api):
    ctx = tools.context(api)
    assert ctx["company"] == "Acme Fence Test Co"
    assert ctx["app_version"]
    assert "closing_date" in ctx and "company_type" in ctx
    # the whole company list is no longer read or given
    assert "companies" not in ctx
    assert "books" not in ctx and "company_file" not in ctx


def test_context_on_several_companies(spec):
    install = FakeInstall(
        spec, {"north.db": "North Test Co", "south.db": "South Test Co"}
    )
    many = ManyBooks.of(install)
    reg = build_registry(many)
    overview = json.loads(reg.call("context", {})["content"][0]["text"])
    assert [b["name"] for b in overview["books"]] == ["North Test Co", "South Test Co"]
    assert set(overview) == {"books", "next"}
    assert install.requests == []
    one = reg.call("context", {"books": many.handle("south.db")})
    ctx = json.loads(one["content"][1]["text"])
    assert ctx["company"] == "South Test Co" and ctx["company_file"] == "south.db"
    assert "companies" not in ctx
    assert not [r for r in install.requests if r[3] == "/api/companies"]


def test_search_list_and_get_a_customer(api):
    made = api.post("/api/customers", {"name": "Zephyr Roofing"})
    found = tools.search(api, "Zephyr")
    assert any(r["id"] == made["id"] for r in found["customers"])
    listed = tools.list_records(api, "customers", {"search": "Zephyr"})
    assert [c["id"] for c in listed] == [made["id"]]
    assert tools.get_record(api, "customers", made["id"])["name"] == "Zephyr Roofing"


def test_list_records_limit_and_kinds(api):
    for name in ("Alpha", "Zulu", "Quartz"):
        api.post("/api/customers", {"name": f"{name} Industries"})
    assert len(tools.list_records(api, "customers", limit=2)) == 2
    assert tools.list_records(api, "invoices", limit=5) == []
    with pytest.raises(ValueError, match="customers.*bank_transactions"):
        tools.list_records(api, "nope")
    with pytest.raises(ValueError, match="single-record"):
        tools.get_record(api, "bank_transactions", 1)


def _journal_entry(api, description):
    return api.post(
        "/api/journal",
        {
            "date": "2026-03-01",
            "description": description,
            "lines": [
                {"account_id": 1, "debit": 10, "credit": 0},
                {"account_id": 2, "debit": 0, "credit": 10},
            ],
        },
    )


def test_list_journal_entries_unwraps_the_page_envelope(api, books):
    """SlowBooks Pro 2.32.0 answers GET /api/journal as a page envelope
    ({entries, total, ...}, the fiscal year by default); a 2.31.x server
    answers a bare list. The tool hands back the rows either way, and the
    page's limit goes to the server."""
    for description in ("one", "two", "three"):
        _journal_entry(api, description)
    rows = tools.list_records(api, "journal_entries", limit=2)
    assert isinstance(rows, list) and len(rows) == 2
    assert [r["description"] for r in rows] == ["one", "two"]
    books.journal_envelope = False
    rows = tools.list_records(api, "journal_entries", limit=2)
    assert isinstance(rows, list) and len(rows) == 2


def test_reports(api):
    names = [r["name"] for r in tools.list_reports(api)]
    assert "profit-loss" in names and "balance-sheet" in names
    assert not any(n.endswith(("/pdf", "/csv", "/print")) for n in names)
    data = tools.report(
        api, "profit-loss", {"start_date": "2026-01-01", "end_date": "2026-12-31"}
    )
    assert isinstance(data, dict)
    with pytest.raises(ValueError, match="Closest: .*profit-loss"):
        tools.report(api, "profit-los")
    with pytest.raises(ValueError, match="No report named"):
        tools.report(api, "not-a-report")


def test_api_schema(api):
    exact = tools.api_schema(api, "/api/customers")
    assert "post" in exact and "get" in exact
    assert exact["post"]["request_body"]["properties"]
    rows = tools.api_schema(api, "/api/custo")
    assert any(r["path"] == "/api/customers" for r in rows)
    assert len(rows) <= 20
    assert "No route matches" in tools.api_schema(api, "/api/nothing")


def test_api_get(api):
    assert isinstance(operations.api_get(api, "/api/accounts"), list)
    with pytest.raises(ApiError, match="cannot manage users or tokens"):
        operations.api_get(api, "/api/tokens")


# ---------------------------------------------------------------------------
# registry (the SDK wrapper's tests, on the standard-library registry)
# ---------------------------------------------------------------------------

TOOL_NAMES = {
    "context",
    "api_schema",
    "search",
    "list_records",
    "get_record",
    "list_reports",
    "report",
    "api_get",
    "find_operations",
    "describe_operation",
}


def test_server_registers_the_read_only_tools(api):
    listed = {t["name"]: t for t in build_registry(api).definitions()}
    assert TOOL_NAMES <= set(listed)
    for name in TOOL_NAMES:
        t = listed[name]
        assert t["annotations"]["readOnlyHint"] is True
        assert t["annotations"]["destructiveHint"] is False
        assert "client" not in t["inputSchema"].get("properties", {})


def test_server_tool_errors_are_sentences(api):
    reg = build_registry(api)
    out = reg.call("list_records", {"kind": "nope"})
    assert out["isError"] and "Unknown kind" in out["content"][0]["text"]
    out = reg.call("api_get", {"path": "/api/tokens"})
    assert out["isError"]
    assert out["content"][0]["text"] == (
        "SlowBooks Pro refused this: API tokens cannot manage users or tokens."
    )
    out = reg.call("api_get", {"path": "/health"})
    assert out["isError"]
    assert out["content"][0]["text"].startswith("/health is not part of")


def test_server_refuses_a_stray_argument(api):
    """report(name, start_date=...) with the date beside name instead of inside
    params ran with default dates and said nothing (found on the Mac)."""
    reg = build_registry(api)
    out = reg.call("report", {"name": "profit-loss", "start_date": "2026-01-01"})
    assert out["isError"] and "start_date" in out["content"][0]["text"]
    # The right shape still works through the same schema.
    out = reg.call(
        "report", {"name": "profit-loss", "params": {"start_date": "2026-01-01"}}
    )
    assert not out["isError"]


def test_api_schema_reaches_admin_routes_but_not_pages(api):
    assert "not part of the SlowBooks Pro API" in tools.api_schema(api, "/health")
    for path in ("/api/tokens", "/api/users", "/api/backups/restore"):
        assert isinstance(tools.api_schema(api, path), dict), path


def test_https_to_the_app_speaks_tls_1_2_at_the_least():
    import ssl

    from sbplive.client import tls_context

    context = tls_context()
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
