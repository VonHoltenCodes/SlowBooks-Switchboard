# ============================================================================
# Checking an address and a token against the app, for setup and status:
# does SlowBooks Pro answer, does it take the token, which company, which
# version, which role. Every outcome is one sentence for the person.
# ============================================================================

from dataclasses import dataclass

from sbplive import tools
from sbplive.client import ApiClient, ApiError, urllib_transport
from sbplive.config import Config


class ProbeError(Exception):
    """The app could not be reached or did not take the token: the sentence."""


@dataclass
class Reached:
    url: str
    company: str
    version: str
    role: str | None

    def sentence(self) -> str:
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


def check(config: Config, client: ApiClient | None = None) -> Reached:
    client = client or ApiClient(urllib_transport(config))
    url = config.base_url
    try:
        settings = client.get("/api/settings")
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
        except ApiError:
            pass
    return Reached(
        url=url, company=company, version=version, role=tools.role_of(client)
    )
