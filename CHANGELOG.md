# Changelog

## 1.0.0

The first public release, alongside SlowBooks Pro 2.32.0.

- `sbplive`, the local MCP server: Claude (or any MCP host) works a
  SlowBooks Pro company's books through the app's own API, as the API token
  it was given, on stdio. Both protocol revisions: 2025-11-25 by handshake
  and 2026-07-28 per request.
- 23 tools: `context`, `search`, records and reports, the general
  `find_operations`, `describe_operation`, `api_schema` and `api_get` over
  the installed app's own API with its writes as separate tools
  (`post_operation`, `update_operation`, `delete_operation`), and named tools for customers, vendors, items,
  invoices, bills, expenses, journal entries, payments (each with an
  idempotency key) and voids.
- Careful creates: a customer, vendor, item, employee, account, invoice,
  bill, expense, estimate or purchase order sent with little on it is held
  back. The answer is `ask_first` with the empty fields worth having, for
  the assistant to ask the person about; `asked_user: true` sends it. An
  expense with no paid-from account is never sent: the answer suggests the
  account the app's form starts at, to confirm with the person. The answer
  to a create of one of these records lists `left_blank` (still empty) and
  `defaulted` (not given, so SlowBooks Pro used its default, with the
  value).
- Sales tax as the app's own form does it: a new invoice, estimate, sales
  receipt or credit memo sent without `tax_rate` takes the company's default
  tax rate (a credit memo for an invoice takes that invoice's rate), and the
  answer says so in `tax_rate_source` and `tax_note`. Where the company's
  default rate is 0%, the answer says to ask the person what rate applies;
  where it cannot be read, no rate is sent and the answer says what rate
  the saved document carries. A rate given, 0 included, is used as given. A
  document that is charged no tax because its customer is non-taxable, it is
  marked tax exempt or (SlowBooks Pro 2.32.0) its job is tax exempt is
  answered as that: no sales tax was charged, and why. So is a credit memo
  for an invoice that charged no tax: none is given back.
- Every tool that changes the books is marked destructive, so a host can ask
  before it runs; the keyed payments, the void and the delete are marked
  idempotent. A delete's answer says in `result` whether the record was
  deleted or deactivated, as SlowBooks Pro reports it. A payment's answer
  names the account the money went into, or for a bill payment the account
  it was paid from.
- The server introduces itself to the host as SlowBooks Pro, with its
  website and a small icon, for hosts that show them.
- `sbplive setup`, `status` and `disconnect`: the token stored for this user
  only, Claude Desktop and Claude Code connected and disconnected (the
  desktop file backed up first).
- `sbplive serve-http`: the self-hosted door, Streamable HTTP on this
  computer for your own Cloudflare Tunnel, with OAuth sign-in by a SlowBooks
  Pro administrator (`--public-url`) and a connector secret for Claude
  accounts with request headers. The sign-in page names the assistant that
  is asking.
- Python 3.10 to 3.13, standard library only. Signed builds for Windows and
  macOS, and a Linux build.
