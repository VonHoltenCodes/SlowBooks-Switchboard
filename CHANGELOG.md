# Changelog

## 1.0.0

The first public release, alongside SlowBooks Pro 2.32.0.

- `sbplive`, the local MCP server: Claude (or any MCP host) works a
  SlowBooks Pro company's books through the app's own API, as the API token
  it was given, on stdio. Both protocol revisions: 2025-11-25 by handshake
  and 2026-07-28 per request.
- 21 tools: `context`, `search`, records and reports, the general
  `find_operations`, `describe_operation` and `call_operation` over the
  installed app's own API, and named tools for customers, vendors, items,
  invoices, bills, expenses, journal entries, payments (each with an
  idempotency key) and voids.
- `sbplive setup`, `status` and `disconnect`: the token stored for this user
  only, Claude Desktop and Claude Code connected and disconnected (the
  desktop file backed up first).
- `sbplive serve-http`: the self-hosted door, Streamable HTTP on this
  computer for your own Cloudflare Tunnel, with OAuth sign-in by a SlowBooks
  Pro administrator (`--public-url`) and a connector secret for Claude
  accounts with request headers.
- Python 3.10 to 3.13, standard library only. Signed builds for Windows and
  macOS, and a Linux build.
