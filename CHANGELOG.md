# Changelog

## 1.1.0

For SlowBooks Pro 2.32.0-beta.2 and newer.

- One connection can reach several companies' books. `sbplive add-books
  --file smith-plumbing.db` adds another company under a bookkeeper token
  made in that company. SlowBooks Pro is asked first which company the
  token opens, and it is kept only when the answer comes from that same
  company. A token from another company, one kept already, a role other
  than bookkeeper or an older SlowBooks Pro is refused with a sentence, and
  nothing is saved.
- Each company gets a handle, such as `smith-plumbing~k3f9q2`, made on this
  computer when it is added. Every tool takes a `books` argument with that
  handle, which `context` lists. With one company it may be left out; with
  more it must be given, and a call that names none, an unknown handle, or
  a company's name or file is refused before anything is sent. On several
  companies every answer starts with the company it came from, such as
  "Books: Smith Plumbing (smith-plumbing~k3f9q2).", and an answer that does
  not say it came from that company's books stops the call.
- `sbplive books` lists and checks the companies, `sbplive remove-books
  <handle>` takes one off, and `sbplive status` names each one. Each
  company's token is kept in its own file, readable by you only.
- Set up with 1.0.0? Nothing changes until you add a second company, and
  nothing has to be set up again: the first `add-books` keeps the token
  setup stored as the first company. Its file is left in place, so going
  back to 1.0.0 still reaches that first company, and only that one.
- A company is added only under a token whose name starts with "AI
  assistant" (for example "AI assistant (Switchboard)"): SlowBooks Pro turns
  such a token off when the administrator who made it is deactivated or no
  longer an administrator there. Setup's token is held to the same rule
  when the first company is added.
- `sbplive setup` takes only such a token too when it is given a new one,
  including one given to replace a token it stored before. Given one with
  another name, it stores nothing, connects nothing, leaves a token it
  stored before as it was, and says what to make instead, ending "Nothing
  was changed." A SlowBooks Pro older than 2.32.0-beta.2 cannot say how a
  token is named: setup goes on there, and says that updating SlowBooks
  Pro will let it check. Running setup again with no new token, or with
  companies kept, works as it did, and a store set up with 1.0.0 has
  nothing to redo.
- The companies are kept for one SlowBooks Pro address. `SBPLIVE_URL`
  naming another one is refused, and no company's token is sent there.
  Companies added for another address while Claude is open are not
  reached until Claude starts the program again.
- With several companies kept, `sbplive setup --token-file` or `--url`
  changes nothing and says so: a new token for a company goes in with
  `remove-books`, then `add-books`.
- A company leaves the connection only when SlowBooks Pro turned its token
  away, never because one request was refused, and no tool signs in or
  changes a password through the sign-in routes.
- The self-hosted door (`serve-http`) serves only the first company in
  this version, and its sign-in is that company's.
- `context` no longer lists every company on the server.

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
