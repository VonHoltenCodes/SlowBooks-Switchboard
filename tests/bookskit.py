"""A Books provider for the tests: several companies of one FakeInstall,
each through its own token, its own company header and the served-header
check, sharing one OpenAPI document. It is the shape sbplive's store gives
the registry, cut to what the tests need.

Run as a script, it serves the tools over stdio for the given companies
(the contract test starts it under the official MCP client):

    python tests/bookskit.py <app url> <books.json>

where books.json is [{"books", "name", "file", "token"}], kept 0600.
"""

import json
import secrets
import sys
import threading

from sbplive.client import ApiClient, Refused, SpecHolder

_BASE32 = "abcdefghijklmnopqrstuvwxyz234567"


def new_handle(file: str) -> str:
    stem = file.removesuffix(".db")
    return stem + "~" + "".join(secrets.choice(_BASE32) for _ in range(6))


class ManyBooks:
    def __init__(self, rows: list):
        """rows: [{"books", "name", "file", "client"}]."""
        self.rows = [dict(r, off="") for r in rows]
        self.lost_calls: list = []
        self.picks: list = []
        self.lock = threading.Lock()

    @classmethod
    def of(cls, install, files=None):
        """Every company of a FakeInstall (or only files), in order."""
        holder = SpecHolder()
        rows = []
        for file in files or list(install.companies):
            books = install.companies[file]
            client = ApiClient(
                install.transport(books.token),
                company=file,
                expect_served=file,
                spec_holder=holder,
            )
            rows.append(
                {
                    "books": new_handle(file),
                    "name": books.company,
                    "file": file,
                    "client": client,
                }
            )
        return cls(rows)

    def row(self, file: str) -> dict:
        return next(r for r in self.rows if r["file"] == file)

    def handle(self, file: str) -> str:
        return self.row(file)["books"]

    def entries(self) -> list:
        return [
            {
                "books": r["books"],
                "name": r["name"],
                "file": r["file"],
                "answers": not r["off"],
                "why": "turned off in that company" if r["off"] else "",
            }
            for r in self.rows
        ]

    def pick(self, books, tool):
        self.picks.append((books, tool))
        for r in self.rows:
            if r["books"] == books:
                if r["off"]:
                    raise Refused(
                        401,
                        f"{r['name']} no longer accepts this connection. "
                        "Nothing was done.",
                    )
                entry = {k: r[k] for k in ("books", "name", "file")}
                return r["client"], entry
        raise Refused(403, "No such books. Nothing was done.")

    def count(self) -> int:
        return len(self.rows)

    def lost(self, books) -> None:
        with self.lock:
            self.lost_calls.append(books)
            for r in self.rows:
                if r["books"] == books:
                    r["off"] = "refused"


def main(argv) -> int:
    from sbplive.client import Config, urllib_transport
    from sbplive.protocol import Session, serve_stdio
    from sbplive.server import INSTRUCTIONS, build_registry

    url, listed = argv[1], argv[2]
    with open(listed, encoding="utf-8") as fh:
        wanted = json.load(fh)
    holder = SpecHolder()
    rows = []
    for w in wanted:
        client = ApiClient(
            urllib_transport(Config(url, w["token"], 30.0)),
            company=w["file"],
            expect_served=w["file"],
            spec_holder=holder,
        )
        rows.append({k: w[k] for k in ("books", "name", "file")} | {"client": client})
    out = sys.stdout.buffer
    sys.stdout = sys.stderr
    session = Session(build_registry(ManyBooks(rows)), INSTRUCTIONS)
    return serve_stdio(session, sys.stdin.buffer, out)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
