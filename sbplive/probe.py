# ============================================================================
# Checking an address and a token against the app, for setup and status:
# does SlowBooks Pro answer, does it take the token, which company, which
# version, which role. Every outcome is one sentence for the person.
#
# For several companies (add-books, books, status): GET /api/whoami under a
# company's token and its company header says which company file answered,
# under which role, and the answer must say in X-SlowBooks-Company-Served
# that it came from that same file.
# ============================================================================

from dataclasses import dataclass, replace

from sbplive import tools
from sbplive.client import ApiClient, ApiError, Unconfirmed, urllib_transport
from sbplive.config import Config


class ProbeError(Exception):
    """The app could not be reached or did not take the token: the sentence."""


class OlderApp(ProbeError):
    """The app has no /api/whoami: SlowBooks Pro before 2.32.0-beta.2."""


@dataclass
class Reached:
    url: str
    company: str
    version: str
    role: str | None
    # (name, handle) of every company on a connection of several
    companies: tuple = ()

    def sentence(self) -> str:
        said = self._first()
        if len(self.companies) > 1:
            listed = ", ".join(f"{n} ({h})" for n, h in self.companies)
            said += (
                f" This connection reaches {len(self.companies)} companies' "
                f"books: {listed}."
            )
        return said

    def _first(self) -> str:
        company = (
            f"the company is {self.company}"
            if self.company
            else ("the company has no name yet")
        )
        version = f" (SlowBooks Pro {self.version})" if self.version else ""
        if self.role:
            role = "the token " + tools.ROLE_PHRASES[self.role]
        else:
            role = "SlowBooks Pro did not show which role the token has"
        return f"SlowBooks Pro answered at {self.url}: {company}{version}, and {role}."


def _client(config: Config) -> ApiClient:
    company = config.company or None
    return ApiClient(urllib_transport(config), company=company, expect_served=company)


def check(config: Config, client: ApiClient | None = None) -> Reached:
    client = client or _client(config)
    url = config.base_url
    try:
        settings = client.get("/api/settings")
    except Unconfirmed:
        raise ProbeError(
            f"SlowBooks Pro at {url} did not confirm it answered from "
            f"{config.company}."
        ) from None
    except ApiError as exc:
        detail = str(exc.detail or "").strip().rstrip(".")
        if exc.status == 0:
            raise ProbeError(
                f"{detail}. Open SlowBooks Pro, or check the address, then try again."
            ) from None
        if exc.status == 401:
            raise ProbeError(
                f"SlowBooks Pro at {url} did not accept the API token ({detail}). "
                "Check that it was copied whole, or make a new one in SlowBooks "
                "Pro under Settings, API Tokens."
            ) from None
        if exc.status == 404:
            raise ProbeError(
                f"Something answered at {url}, but not SlowBooks Pro's API. "
                "Check the address and the port."
            ) from None
        raise ProbeError(
            f"SlowBooks Pro at {url} refused the API token: {detail}."
        ) from None
    if not isinstance(settings, dict):
        raise ProbeError(
            f"Something answered at {url}, but not SlowBooks Pro's API. "
            "Check the address and the port."
        )
    company = str(settings.get("company_name") or "")
    version = ""
    try:
        info = client.get("/api/system")
        if isinstance(info, dict):
            version = str(info.get("version") or "")
    except ApiError:
        pass
    if not version:
        try:
            version = str((tools._spec(client).get("info") or {}).get("version", ""))
        except (ApiError, Unconfirmed):
            pass
    try:
        role = tools.role_of(client)
    except Unconfirmed:
        role = None
    return Reached(
        url=url,
        company=company,
        version=version,
        role=role,
        companies=tuple((b.name or b.file, h) for h, b in config.books.items()),
    )


# -- one company's token, by /api/whoami --------------------------------------------

OLDER_APP = (
    "This SlowBooks Pro is older than 2.32.0-beta.2 and cannot keep companies "
    "apart for a token. Update it to add a second company."
)


class NotAnAnswer(ApiError):
    """A 2xx /api/whoami whose body is not an object."""


@dataclass
class Who:
    file: str
    name: str
    role: str | None
    # the token's name, and whether SlowBooks Pro ends it when the
    # administrator who made it is no longer one there (a token named for
    # an AI assistant)
    label: str = ""
    ends_with_maker: bool = False


def _whoami(client: ApiClient):
    who = client.get("/api/whoami")
    if not isinstance(who, dict):
        # an app without /api/whoami answers 404; a 200 that is not an
        # object is no answer SlowBooks Pro gives, and no sign of its age
        raise NotAnAnswer(200, "not an answer SlowBooks Pro gives")
    role = who.get("role")
    label = who.get("label")
    return Who(
        file=str(who.get("company_file") or ""),
        name=str(who.get("company_name") or ""),
        role=role if isinstance(role, str) else None,
        label=label if isinstance(label, str) else "",
        ends_with_maker=who.get("ends_with_maker") is True,
    )


def whoami(config: Config, file: str | None = None) -> Who:
    """Which company config.token opens, asked for file (None: with no
    company header, the company SlowBooks Pro serves by default). The answer
    must come from file. ProbeError's sentence says what is wrong; it ends
    "Nothing was saved."."""
    url = config.base_url
    client = ApiClient(urllib_transport(config), company=file, expect_served=file)
    try:
        who = _whoami(client)
    except Unconfirmed as exc:
        if exc.served:
            raise ProbeError(_mismatch(exc.served, file)) from None
        raise ProbeError(
            f"SlowBooks Pro at {url} did not confirm it answered from {file}. "
            f"{OLDER_APP} Nothing was saved."
        ) from None
    except NotAnAnswer:
        raise ProbeError(
            f"What {url} answered about that token is not an answer SlowBooks "
            "Pro gives. Check that the address is SlowBooks Pro's, then try "
            "again. Nothing was saved."
        ) from None
    except ApiError as exc:
        detail = str(exc.detail or "").strip().rstrip(".")
        if exc.status == 0:
            raise ProbeError(
                f"{detail}. Open SlowBooks Pro, or check the address, then try "
                "again. Nothing was saved."
            ) from None
        if exc.status == 404:
            raise OlderApp(f"{OLDER_APP} Nothing was saved.") from None
        if exc.status == 401 and file:
            # Where does the token answer, then? Asked with no header, the
            # company SlowBooks Pro serves by default says if it is that one.
            try:
                plain = _whoami(ApiClient(urllib_transport(config)))
            except (ApiError, Unconfirmed):
                plain = None
            if plain is not None and plain.file and plain.file != file:
                raise ProbeError(_mismatch(plain.file, file)) from None
        if exc.status == 401:
            raise ProbeError(
                f"SlowBooks Pro at {url} did not accept that token"
                + (f" for {file}" if file else "")
                + f" ({detail}). Check that it was made in that company and "
                "copied whole. Nothing was saved."
            ) from None
        if exc.status == 421 and not file:
            raise ProbeError(
                f"SlowBooks Pro at {url} answered that it serves no company "
                "right now. Check that the company is open there, then try "
                "again. Nothing was saved."
            ) from None
        if exc.status == 421:
            raise ProbeError(
                f"SlowBooks Pro at {url} does not serve {file} right now. Check "
                "the company file's name, and that the company is open there. "
                "Nothing was saved."
            ) from None
        raise ProbeError(
            f"SlowBooks Pro at {url} refused that token: {detail}. Nothing was "
            "saved."
        ) from None
    if file and who.file != file:
        raise ProbeError(_mismatch(who.file or "a database that is not a file", file))
    return who


def _mismatch(served: str, file: str | None) -> str:
    return f"That token answers from {served}, not {file}. Nothing was saved."


def book_sentence(config: Config, handle: str, book) -> tuple:
    """(answers, the sentence) for one company of a connection of several."""
    name = book.name or book.file
    client = ApiClient(
        urllib_transport(replace(config, token=book.token)),
        company=book.file,
        expect_served=book.file,
    )
    try:
        who = _whoami(client)
    except Unconfirmed:
        return False, (
            f"{name} ({handle}): SlowBooks Pro did not confirm it answered from "
            f"{book.file}."
        )
    except NotAnAnswer:
        return False, (
            f"{name} ({handle}): what SlowBooks Pro answered about its token "
            "is not an answer SlowBooks Pro gives."
        )
    except ApiError as exc:
        detail = str(exc.detail or "").strip().rstrip(".")
        if exc.status == 0:
            sentence = f"{detail}."
        elif exc.status == 401:
            sentence = (
                "SlowBooks Pro no longer accepts its token: it was turned off "
                "there, or it expired. Make a new one there, then remove these "
                "books and add them again."
            )
        elif exc.status == 421:
            sentence = (
                f"SlowBooks Pro at {config.base_url} does not serve {book.file} "
                "right now."
            )
        elif exc.status == 404:
            sentence = OLDER_APP
        else:
            sentence = f"SlowBooks Pro refused its token: {detail}."
        return False, f"{name} ({handle}): {sentence}"
    if who.file != book.file:
        return False, (
            f"{name} ({handle}): its token answers from {who.file}, not "
            f"{book.file}."
        )
    role = (
        "the token " + tools.ROLE_PHRASES[who.role]
        if who.role in tools.ROLE_PHRASES
        else "SlowBooks Pro did not show which role the token has"
    )
    return True, f"{name} ({handle}, {book.file}) answers, and {role}."
