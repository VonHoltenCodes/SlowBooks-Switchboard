# SlowBooks Switchboard (sbplive)

SlowBooks Switchboard lets Claude (Claude Desktop, Claude Code, or any other
program that speaks the [Model Context Protocol](https://modelcontextprotocol.io))
work the books of a [SlowBooks Pro](https://www.slowbookspro.com) company. It
is a small program, `sbplive`, separate from SlowBooks Pro: Claude starts it
on your computer and talks to it there, and it talks to SlowBooks Pro through
the app's own REST API with an API token you make. It does only what that
token's role allows, holds no books, never touches the company file or the
database, and sends nothing anywhere but your own SlowBooks Pro. Every change
Claude makes is in SlowBooks Pro's audit log as `token:<label>`.

- Free and open source under the [Apache License 2.0](LICENSE).
- Python 3.10 to 3.13, standard library only: nothing else to install.
- Both protocol revisions, 2025-11-25 (by handshake) and 2026-07-28 (per
  request), on stdio and over Streamable HTTP ([docs/PROTOCOL.md](docs/PROTOCOL.md)).

## Install

**The signed downloads.** Each [release](https://github.com/VonHoltenCodes/SlowBooks-Switchboard/releases)
carries one program per platform: Windows (signed with Azure Trusted Signing),
macOS on Apple silicon (signed with a Developer ID and notarized) and Linux
x86-64, each with a `SHA256SUMS` file to check the download against
(`sha256sum -c SHA256SUMS.linux`, or `shasum -a 256 -c SHA256SUMS.macos`).
Unpack it and put `sbplive` (`sbplive.exe` on Windows) where it will stay
before you run setup: Claude is told where it is, and if you move it, run
setup again.

**From source**, with Python 3.10 or newer:

```
pipx install git+https://github.com/VonHoltenCodes/SlowBooks-Switchboard
# or, in a virtual environment of your own:
pip install git+https://github.com/VonHoltenCodes/SlowBooks-Switchboard
```

Either puts `sbplive` on your path. From a checkout, `python -m sbplive` works
with nothing installed.

## Set it up

1. In SlowBooks Pro, signed in as an administrator, open **Settings, API
   Tokens** and make a token: a label you will recognise (it names every
   change Claude makes) and the role Claude should have, **Read-only**,
   **Bookkeeper** or **Accountant**. A bookkeeper token is the usual choice.
   Copy it: it is shown once.
2. Run `sbplive setup`. It asks where the books are (Enter for this
   computer, or the address of the office's SlowBooks Pro server), takes the
   token at a prompt that does not echo, checks it against SlowBooks Pro,
   stores it for your user only, and connects **Claude Desktop** (its
   `claude_desktop_config.json`, backed up first) and **Claude Code**
   (`claude mcp add --scope user`), both under the name `sbplive`.
3. Quit Claude Desktop completely and open it again, and ask "What company
   are you connected to?"

Without questions: `sbplive setup --yes --url http://192.168.1.20:3001 --token-file token.txt`
(`--token-file -` reads standard input; the token is never a command-line
argument).

- `sbplive status` says whether SlowBooks Pro answers, which company and
  version, the token's role, whether Claude Desktop and Claude Code are
  connected, and where the log is.
- `sbplive disconnect` takes it out of Claude Desktop and Claude Code;
  `--forget` also deletes the stored token. To lock it out for good, revoke
  the token in SlowBooks Pro under Settings, API Tokens.

The whole guide, with what Claude can do under each role, the tools and
what to do when something is wrong: [docs/GUIDE.md](docs/GUIDE.md).

## From outside the office: the self-hosted door

`sbplive serve-http` serves the same tools over MCP's Streamable HTTP
transport, on this computer only (`http://127.0.0.1:8743/mcp`), for Claude on
the web and the Claude apps through **your own Cloudflare Tunnel**: nothing
is opened on the office's router, and the API token never leaves the
computer. With `--public-url https://books.example.com` (the tunnel's
address) Claude signs in with OAuth, and an **administrator of the books**
signs in once on the door's own sign-in page; SlowBooks Pro checks that
sign-in and the door keeps nothing of it.

Add it in Claude as a custom connector at `https://books.example.com/mcp`.
The step-by-step guide, from making the token to the tunnel, the connector
and turning it off, is [docs/REMOTE.md](docs/REMOTE.md); the sign-in itself
is [docs/AUTH.md](docs/AUTH.md).

## Security

- **The token** is a SlowBooks Pro API token (`sbp_...`), made under
  Settings, API Tokens. It is stored in your own configuration folder,
  readable by you only (mode 600 on macOS and Linux), and sent only to
  SlowBooks Pro. No API token can manage users or tokens, restore a backup,
  or change settings or the company file.
- **Loopback by default.** The stdio server has no network port at all, and
  `serve-http` listens on `127.0.0.1` unless told otherwise.
- **Plain http only on your own network.** An `http://` address for
  SlowBooks Pro is accepted only when it is a private or link-local address
  (or a name that points to one), and setup warns that the token travels
  unencrypted there.
- **Public means https.** An address on the internet must be `https://`,
  with a certificate this computer trusts; there is no way to turn the check
  off. `--public-url` must be `https://` too.
- The log never holds the token, the connector secret, a sign-in, or what
  was sent.

Found a security problem? Write to support@slowbookspro.com rather than
opening a public issue.

## Developing

```
python -m pytest -q                         # needs only pytest; the contract test skips
pip install -r requirements-dev.txt         # the pinned official MCP client
python -m pytest -q tests/test_contract.py  # the contract test, over stdio and HTTP
```

Every test runs in a home directory of its own and never reads or writes
this computer's real configuration. `tests/test_end_to_end.py` starts a real
SlowBooks Pro from a checkout (`SBPLIVE_PRODUCT_DIR`) and skips, with the
reason, without one. Building the executables: [docs/BUILD.md](docs/BUILD.md).
The named tools, client and configuration began as Keith Bloemendaal's MCP
server for SlowBooks Pro.

## Support

support@slowbookspro.com. SlowBooks Pro: https://www.slowbookspro.com

Copyright 2026 Trent Von Holten. Licensed under the Apache License,
Version 2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE).
