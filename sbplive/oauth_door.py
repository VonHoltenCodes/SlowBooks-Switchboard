# ============================================================================
# The self-hosted door's OAuth, put together for serve-http --public-url:
# the pure module (oauth.py) with its records in oauth.json beside the token
# (oauth_store_file.py) and the SlowBooks Pro administrator sign-in checked
# against the local app (app_signin.py). Kept out of server.py and cli.py so
# those carry one option and one call each.
# ============================================================================

from dataclasses import dataclass

from sbplive.app_signin import AppSignIn
from sbplive.config import ConfigError
from sbplive.oauth import OAuthServer, check_public_url
from sbplive.oauth_store_file import FileStore

# What the API token works as, for the page's "as ..." (tools.ROLE_PHRASES
# says the same at more length).
ROLE_WORDS = {
    "bookkeeper": "a bookkeeper",
    "accountant": "an accountant",
    "readonly": "a read-only user",
}


@dataclass
class SignInDoor:
    server: OAuthServer
    app: AppSignIn

    def token_role(self, role: str | None) -> None:
        self.app.role_phrase = ROLE_WORDS.get(role or "")

    def sentence(self) -> str:
        return (
            f"OAuth sign-in is on. In Claude, add the connector with the address "
            f"{self.server.resource} and Authentication left to OAuth: Claude "
            "opens a SlowBooks Pro sign-in page, where an administrator of these "
            "books signs in once."
        )


def make(config, public_url: str | None):
    """The OAuth door for public_url, or None without one; ConfigError when
    the address is not one."""
    if not public_url:
        return None
    try:
        issuer = check_public_url(public_url)
    except ValueError as exc:
        raise ConfigError(str(exc)) from None
    app = AppSignIn(config.base_url)
    server = OAuthServer(
        issuer,
        FileStore(),
        app.verify,
        fields=app.fields,
        about=app.about,
    )
    return SignInDoor(server, app)
