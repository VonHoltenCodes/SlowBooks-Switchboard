# ============================================================================
# Connecting the hosts: Claude Desktop and Claude Code.
#
# Claude Desktop reads local servers from claude_desktop_config.json, under
# "mcpServers", each entry a command, its args and an optional env. Where
# the file is, as Anthropic's guide gives it (modelcontextprotocol.io,
# "Connect to local MCP servers", read 2026-11-06):
#
#   macOS    ~/Library/Application Support/Claude/claude_desktop_config.json
#   Windows  %APPDATA%\Claude\claude_desktop_config.json
#   Linux    ~/.config/Claude/claude_desktop_config.json
#
# The Windows app installed as an MSIX package (the Microsoft Store and,
# lately, the regular installer) reads the same file through Windows'
# package redirection, from
#   %LOCALAPPDATA%\Packages\Claude_<id>\LocalCache\Roaming\Claude\
# (reported in anthropics/claude-code issues #25579 and #26073; not on an
# Anthropic page). Every one of these folders that exists is written.
#
# Only the entry named "sbplive" is added, replaced or removed. The file is
# copied to a timestamped backup first, every other key is written back as
# the same JSON, and a file that is not valid JSON is left alone. The entry
# names the program only, never the token.
#
# Claude Code is connected with its own command, `claude mcp add --scope
# user`, so it works in every project; without the `claude` command the
# person is shown the one command to run.
# ============================================================================

import glob
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from sbplive import store

SERVER_NAME = "sbplive"
DESKTOP_FILE = "claude_desktop_config.json"
# Where an installed Claude Desktop app may be found when it has not made
# its configuration folder yet. Tests point these elsewhere.
MAC_APPS = ("/Applications/Claude.app", "~/Applications/Claude.app")


class HostError(Exception):
    """A host's configuration that cannot be changed safely: the sentence."""


# ---------------------------------------------------------------------------
# How the host starts this program
# ---------------------------------------------------------------------------


def server_command() -> tuple:
    """(command, args, env) that run this program the way it is installed.

    A frozen build is its own executable, which runs the server with no
    arguments. Otherwise this Python runs the package as a module; from a
    checkout (not an installed package), PYTHONPATH names the checkout."""
    if getattr(sys, "frozen", False):
        return sys.executable, [], {}
    parent = Path(__file__).resolve().parent.parent
    env = {}
    if parent.name not in ("site-packages", "dist-packages"):
        env["PYTHONPATH"] = str(parent)
    return sys.executable, ["-m", "sbplive"], env


def desktop_entry() -> dict:
    command, args, env = server_command()
    entry = {"command": command, "args": args}
    if env:
        entry["env"] = env
    return entry


# ---------------------------------------------------------------------------
# Claude Desktop
# ---------------------------------------------------------------------------


def _home() -> Path:
    return store._home()


def desktop_dirs() -> list:
    """The folders where an installed Claude Desktop reads its configuration.

    A folder counts when it exists, or when the app is installed and has
    not made it yet (setup then creates the file)."""
    found = []
    if store.platform() == "win32":
        appdata = os.environ.get("APPDATA")
        roaming = Path(appdata) if appdata else _home() / "AppData" / "Roaming"
        local = os.environ.get("LOCALAPPDATA")
        local = Path(local) if local else _home() / "AppData" / "Local"
        plain = roaming / "Claude"
        pattern = str(local / "Packages" / "Claude_*" / "LocalCache" / "Roaming")
        packaged = [Path(p) / "Claude" for p in sorted(glob.glob(pattern))]
        installed = (local / "AnthropicClaude").is_dir()
        if plain.is_dir() or (installed and not packaged):
            found.append(plain)
        found.extend(packaged)
    elif store.platform() == "darwin":
        folder = _home() / "Library" / "Application Support" / "Claude"
        apps = [Path(os.path.expanduser(a)) for a in MAC_APPS]
        if folder.is_dir() or any(a.is_dir() for a in apps):
            found.append(folder)
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME") or ""
        root = Path(xdg) if xdg and os.path.isabs(xdg) else _home() / ".config"
        if (root / "Claude").is_dir():
            found.append(root / "Claude")
    return found


def desktop_files() -> list:
    return [d / DESKTOP_FILE for d in desktop_dirs()]


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        raise HostError(f"{path} could not be read, so it was left as it is.") from None
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        raise HostError(
            f"{path} is not valid JSON, so it was left as it is. Fix it (or move "
            "it aside), then run this again."
        ) from None
    if not isinstance(data, dict):
        raise HostError(f"{path} does not hold a JSON object, so it was left as it is.")
    servers = data.get("mcpServers")
    if servers is not None and not isinstance(servers, dict):
        raise HostError(
            f"In {path}, mcpServers is not an object, so the file was left as it is."
        )
    return data


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.bak-sbplive-{stamp}")
    n = 2
    while target.exists():
        target = path.with_name(f"{path.name}.bak-sbplive-{stamp}-{n}")
        n += 1
    shutil.copy2(path, target)
    return target


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.sbplive.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    if path.exists():
        shutil.copymode(path, tmp)
    os.replace(tmp, path)


def desktop_connected(path: Path) -> bool:
    try:
        data = _read(path)
    except HostError:
        return False
    return SERVER_NAME in (data.get("mcpServers") or {})


def connect_desktop(path: Path, entry: dict | None = None) -> Path | None:
    """Add or replace the sbplive entry; returns the backup's path (None
    when there was no file before)."""
    data = _read(path)
    backup = _backup(path)
    servers = data.get("mcpServers")
    if servers is None:
        servers = data["mcpServers"] = {}
    servers[SERVER_NAME] = entry or desktop_entry()
    _write(path, data)
    return backup


def disconnect_desktop(path: Path) -> Path | None | bool:
    """Remove the sbplive entry; returns the backup's path, or False when
    there was no entry (the file is then not touched)."""
    data = _read(path)
    servers = data.get("mcpServers") or {}
    if SERVER_NAME not in servers:
        return False
    backup = _backup(path)
    del servers[SERVER_NAME]
    _write(path, data)
    return backup


# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------


def find_claude() -> str | None:
    return shutil.which("claude")


def code_config_path() -> Path:
    # Claude Code keeps user-scope servers in ~/.claude.json, or in
    # $CLAUDE_CONFIG_DIR/.claude.json when that is set.
    folder = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(folder) if folder else _home()) / ".claude.json"


def code_connected() -> bool:
    try:
        with open(code_config_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    return isinstance(servers, dict) and SERVER_NAME in servers


def code_add_args() -> list:
    command, args, env = server_command()
    out = ["mcp", "add", "--scope", "user", SERVER_NAME]
    for key, value in env.items():
        out += ["-e", f"{key}={value}"]
    return out + ["--", command, *args]


def code_remove_args() -> list:
    return ["mcp", "remove", "--scope", "user", SERVER_NAME]


def shown(args: list) -> str:
    """A command line the person can paste into their own shell."""
    words = ["claude", *args]
    if store.platform() == "win32":
        return subprocess.list2cmdline(words)
    return shlex.join(words)


def _run(claude: str, args: list) -> subprocess.CompletedProcess:
    return subprocess.run(
        [claude, *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def connect_code(claude: str) -> None:
    """Replace any earlier sbplive entry in the user scope, then add it."""
    _run(claude, code_remove_args())  # nothing to remove the first time
    done = _run(claude, code_add_args())
    if done.returncode != 0:
        said = (done.stderr or done.stdout or "").strip().splitlines()
        raise HostError(
            "Claude Code did not add the server"
            + (f": {said[-1]}" if said else ".")
            + f" To add it by hand, run: {shown(code_add_args())}"
        )


def disconnect_code(claude: str) -> bool:
    """True when Claude Code removed an entry."""
    return _run(claude, code_remove_args()).returncode == 0
