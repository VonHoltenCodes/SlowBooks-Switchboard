# ============================================================================
# The companies this user added with `sbplive add-books`, as a Books
# provider for the registry (registry.Books): one ApiClient per company, each
# under that company's own token, naming its company file in
# X-SlowBooks-Company on every request and taking an answer only when
# SlowBooks Pro says it came from that same file. All of them share one copy
# of the OpenAPI document.
#
# The list is read again when settings.json changes, so a company added or
# removed while Claude runs is seen on its next call. A token SlowBooks Pro
# refused (401) is not sent again by this process: the company is listed as
# not answering, and every call to it is refused before anything is sent.
# ============================================================================

import logging
import os
import threading
from dataclasses import replace
from pathlib import Path

from sbplive import registry, store
from sbplive.client import ApiClient, Refused, SpecHolder, urllib_transport
from sbplive.config import Config, ConfigError, check_url, hash_token, load_books

log = logging.getLogger("sbplive")

TURNED_OFF = "turned off in that company"


def _signature(path: Path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_ino, st.st_mtime_ns, st.st_size)


class TokenBooks:
    """Config.books as a registry.Books provider.

    folder: the store's folder, watched for a changed list (None keeps the
    list it was given). transport: makes a transport from a Config (the
    tests pass one that answers in-process)."""

    def __init__(
        self,
        config: Config,
        *,
        folder: Path | None = None,
        transport=urllib_transport,
    ):
        self.config = config
        self._folder = folder
        self._make_transport = transport
        self._holder = SpecHolder()
        self._lock = threading.Lock()
        self._refused: set = set()  # token hashes SlowBooks Pro refused
        self._settings = (folder / store.SETTINGS_NAME) if folder else None
        self._seen = _signature(self._settings) if self._settings else None
        self._rows: list = []
        self._build(config.books)

    def _build(self, books: dict) -> None:
        rows = []
        for handle, book in books.items():
            client = ApiClient(
                self._make_transport(replace(self.config, token=book.token)),
                company=book.file,
                expect_served=book.file,
                spec_holder=self._holder,
            )
            rows.append(
                {
                    "books": handle,
                    "file": book.file,
                    "name": book.name or book.file,
                    "sha": hash_token(book.token),
                    "client": client,
                }
            )
        self._rows = rows

    def _load(self) -> dict:
        """The list as settings.json holds it now, used only while it was
        added for the address this process sends to: after disconnect
        --forget and add-books for another SlowBooks Pro, the new tokens
        would go to the old address (load_config checks the address only
        when the process starts)."""
        books = load_books(self._folder)
        if not books:
            return books
        stored = store.stored_url(self._folder)
        kept = check_url(stored, must_resolve=False)[0] if stored else None
        if kept != self.config.base_url:
            raise ConfigError(
                f"the companies' books there were added for {kept or 'no address'}"
                f", and this connection sends to {self.config.base_url}, so "
                "none of them is reached until Claude is started again"
            )
        return books

    def _fresh(self) -> list:
        """The rows, read again first when settings.json changed."""
        with self._lock:
            if self._settings is not None:
                now = _signature(self._settings)
                if now != self._seen:
                    self._seen = now
                    try:
                        self._build(self._load())
                    except ConfigError as exc:
                        # A list that cannot be trusted reaches nothing.
                        log.warning("the list of companies was not used: %s", exc)
                        self._rows = []
            return list(self._rows)

    # -- registry.Books -----------------------------------------------------------

    def entries(self) -> list:
        out = []
        for r in self._fresh():
            off = r["sha"] in self._refused
            out.append(
                {
                    "books": r["books"],
                    "name": r["name"],
                    "file": r["file"],
                    "answers": not off,
                    "why": TURNED_OFF if off else "",
                }
            )
        return out

    def count(self) -> int:
        return len(self._fresh())

    def pick(self, books, tool):
        for r in self._fresh():
            if r["books"] != books:
                continue
            if r["sha"] in self._refused:
                raise Refused(401, self.lost_sentence(r))
            entry = {k: r[k] for k in ("books", "name", "file")}
            return r["client"], entry
        raise Refused(
            404,
            f"This connection no longer reaches the books {books}. Call context "
            f"to see the books it reaches. {registry.NOTHING_DONE}",
        )

    def lost_sentence(self, entry: dict) -> str:
        name = entry.get("name") or entry.get("file") or ""
        handle = entry.get("books") or ""
        return (
            f"{name} no longer accepts this connection: its token was turned off "
            "there, or the person who made it is no longer an administrator "
            f"there. To use it again, the person makes a new token in {name} and "
            f"runs sbplive remove-books {handle}, then sbplive add-books with "
            f"it. {registry.NOTHING_DONE}"
        )

    def lost(self, books) -> None:
        for r in self._fresh():
            if r["books"] == books:
                self._refused.add(r["sha"])
                log.warning("SlowBooks Pro refused the token for %s", books)


def provider(config: Config, *, watch: bool = True):
    """What the doors serve for config: a TokenBooks for a store of several
    companies, else one ApiClient (setup's single token)."""
    if config.books:
        return TokenBooks(config, folder=store.config_dir() if watch else None)
    return ApiClient(urllib_transport(config))
