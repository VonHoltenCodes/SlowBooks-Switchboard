# ============================================================================
# The log: standard error (which a host such as Claude Desktop keeps in its
# own log of the server) and a file beside the token store, rotated by size
# with the standard library: sbplive.log, up to 1 MB, and three older
# copies. SBPLIVE_LOG sets the level (DEBUG, INFO, WARNING or
# ERROR; WARNING when unset).
#
# What is logged is the method, path and status of each request and the
# program's own errors. Never the token, never the remote door's connector
# secret or its OAuth codes and tokens, and never a request or response
# body: the client and the door log no more than that, and a filter masks
# anything shaped like one of this program's secrets (sbp_..., sbc_...,
# sbo_... and the rest of the sb?_ family) or an Authorization header in
# case a message ever carries one.
#
# On POSIX the file and its rotated copies are readable by you only (600),
# as the token store's files are, and a log left wider by an older version
# is tightened when it is opened. Windows keeps the folder's own access.
# ============================================================================

import logging
import logging.handlers
import os
import re
import sys

from sbplive import store

LOGGER = "sbplive"
MAX_BYTES = 1_000_000
BACKUPS = 3
_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
_SECRET = re.compile(r"(\bsb[a-z]_[A-Za-z0-9_\-]+|Bearer\s+\S+)")
_FORMAT = "%(asctime)s sbplive %(levelname)s %(message)s"


class _PrivateFileHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler whose file is created 600 on POSIX. A rotated
    copy is the same file renamed, so it keeps that mode; copies left wider
    are tightened too."""

    def _open(self):
        if os.name != "nt":
            fd = os.open(
                self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
            )
            os.close(fd)
            os.chmod(self.baseFilename, 0o600)
            # BACKUPS, not self.backupCount: that is set after the first open.
            for n in range(1, BACKUPS + 1):
                try:
                    os.chmod(f"{self.baseFilename}.{n}", 0o600)
                except OSError:
                    pass
        return super()._open()


class _MaskSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        text = record.getMessage()
        masked = _SECRET.sub("[hidden]", text)
        if masked != text:
            record.msg, record.args = masked, None
        return True


def level() -> str:
    raw = (os.environ.get("SBPLIVE_LOG") or "WARNING").upper()
    return raw if raw in _LEVELS else "WARNING"


def setup(to_file: bool = True):
    """Log to standard error and, when to_file, to the rotating file.

    Returns the file's path, or None when it could not be opened (the
    program carries on with standard error alone)."""
    logger = logging.getLogger(LOGGER)
    logger.setLevel(level())
    logger.propagate = False
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    mask = _MaskSecrets()
    err = logging.StreamHandler(sys.stderr)
    err.setFormatter(logging.Formatter("sbplive %(levelname)s %(message)s"))
    err.addFilter(mask)
    logger.addHandler(err)
    if not to_file:
        return None
    path = store.log_path()
    try:
        store.ensure_dir()
        fh = _PrivateFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
        )
    except OSError:
        return None
    fh.setFormatter(logging.Formatter(_FORMAT))
    fh.addFilter(mask)
    logger.addHandler(fh)
    return path
