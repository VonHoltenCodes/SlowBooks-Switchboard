# ============================================================================
# The OAuth records of the self-hosted door, in one JSON file beside the
# token store (oauth.json, mode 600, the folder 700): the storage interface
# oauth.py asks for, put / get / delete / delete_where, for one process.
#
# What is in it: registered clients, fetched client documents (a day),
# pending sign-in pages and codes (ten minutes), access tokens (an hour) and
# refresh tokens (thirty days) as SHA-256 hashes only, and the sign-in
# failure times per address. Never a password and never a token itself.
# Expired records are swept on every write. Deleting the file signs every
# connection out; `disconnect --forget` deletes it with the rest.
# ============================================================================

import json
import os
import threading
import time
from pathlib import Path

from sbplive import store

OAUTH_NAME = "oauth.json"


def oauth_path() -> Path:
    return store.config_dir() / OAUTH_NAME


class FileStore:
    def __init__(self, path: Path | None = None, clock=time.time):
        self.path = Path(path) if path is not None else oauth_path()
        self.clock = clock
        self._lock = threading.Lock()
        self._data = self._load()  # kind -> key -> [expires, record]

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            # A damaged file: start empty (everyone signs in again) rather
            # than refuse to open the door.
            return {}
        if not isinstance(data, dict):
            return {}
        now = self.clock()
        out: dict = {}
        for kind, rows in data.items():
            if not isinstance(rows, dict):
                continue
            for key, entry in rows.items():
                if (
                    isinstance(entry, list)
                    and len(entry) == 2
                    and isinstance(entry[0], (int, float))
                    and isinstance(entry[1], dict)
                    and entry[0] > now
                ):
                    out.setdefault(kind, {})[key] = entry
        return out

    def _sweep(self, now: float) -> None:
        for kind in list(self._data):
            rows = self._data[kind]
            for key in [k for k, (exp, _) in rows.items() if exp <= now]:
                del rows[key]
            if not rows:
                del self._data[kind]

    def _write(self) -> None:
        folder = self.path.parent
        folder.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(folder, 0o700)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(self._data, fh, separators=(",", ":"))
                fh.flush()
                os.fsync(fh.fileno())
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        os.replace(tmp, self.path)
        if os.name != "nt":
            os.chmod(self.path, 0o600)

    # -- the interface -----------------------------------------------------------

    def put(self, kind: str, key: str, record: dict, ttl: float) -> None:
        with self._lock:
            now = self.clock()
            self._sweep(now)
            copy = json.loads(json.dumps(record))
            self._data.setdefault(kind, {})[key] = [now + ttl, copy]
            self._write()

    def get(self, kind: str, key: str):
        with self._lock:
            entry = self._data.get(kind, {}).get(key)
            if entry is None or entry[0] <= self.clock():
                return None
            return json.loads(json.dumps(entry[1]))

    def delete(self, kind: str, key: str) -> None:
        with self._lock:
            if self._data.get(kind, {}).pop(key, None) is not None:
                self._sweep(self.clock())
                self._write()

    def delete_where(self, kind: str, field_name: str, value) -> int:
        with self._lock:
            rows = self._data.get(kind, {})
            gone = [k for k, (_, rec) in rows.items() if rec.get(field_name) == value]
            for k in gone:
                del rows[k]
            if gone:
                self._sweep(self.clock())
                self._write()
            return len(gone)
