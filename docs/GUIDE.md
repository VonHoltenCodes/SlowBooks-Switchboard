# SlowBooks Switchboard: the guide

SlowBooks Switchboard lets Claude (Claude Desktop, Claude Code, or any other
program that speaks the Model Context Protocol) work your SlowBooks Pro books.
Claude starts it on your computer and talks to it there; it talks to
SlowBooks Pro through the app's own API with an API token you make, and does
only what that token's role allows. It adds nothing to the books and never
touches the company file directly. Nothing is sent anywhere but your own
SlowBooks Pro.

From outside the office, Claude on the web can reach the books through your
own Cloudflare Tunnel ([REMOTE.md](REMOTE.md)), where Claude signs in with
the SlowBooks Pro administrator sign-in. This guide is the connection on one
computer.

## Install it

- **The program:** `sbplive` (`sbplive.exe` on Windows), one file, from
  the signed downloads on the
  [releases page](https://github.com/VonHoltenCodes/SlowBooks-Switchboard/releases)
  (each platform's `SHA256SUMS` file lists the checksums). Put it where it
  will stay before you run setup: Claude is told where it is, and if you
  move it, run setup again.
- **Or with Python** 3.10 or newer: `pipx install` (or `pip install`) a
  checkout of this repository, which puts `sbplive` on your path; or run
  `python -m sbplive` from a checkout, with nothing installed, wherever this
  guide says `sbplive`.

## Set it up

1. In SlowBooks Pro, signed in as an administrator, open Settings, API
   Tokens, and make a token. Give it a label you will recognise (it names
   every change Claude makes in the audit log, as `token:<label>`) and the
   role Claude should have: **Read-only**, **Bookkeeper** or **Accountant**.
   Copy the token: it is shown once.
2. Run `sbplive setup`. It asks:

   ```
   SlowBooks Switchboard setup
   Where are the books? Press Enter for this computer (http://127.0.0.1:3001), or type the address of the office's SlowBooks Pro server:
   Paste the API token from SlowBooks Pro, Settings, API Tokens (it starts with sbp_ and is not shown as you type):
   SlowBooks Pro answered at http://127.0.0.1:3001: the company is Larkspur Test Fencing (SlowBooks Pro 2.31.8), and the token works as a bookkeeper (the daily books but not settings or payroll).
   The token is stored in /home/you/.config/sbplive/token, which only you can read.
   Connect Claude Desktop (/home/you/.config/Claude/claude_desktop_config.json)? [Y/n]
   Claude Desktop is connected. The file as it was is saved as ...claude_desktop_config.json.bak-sbplive-20261106-150210. Quit Claude Desktop completely and open it again to see SlowBooks among its tools.
   Connect Claude Code for your user? [Y/n]
   Claude Code is connected for your user, in every project.
   Done. To check it later, run: sbplive status. To take SlowBooks out of Claude: sbplive disconnect.
   ```

3. Quit Claude Desktop completely and open it again. SlowBooks is among its
   connectors; ask "What company are you connected to?" to try it.

**The office's Server Edition.** Type its address at the first question, such
as `http://192.168.1.20:3001` or `https://books.example.com`. A plain
`http://` address is accepted only on your own network (a private or
link-local address, or a name that points to one), and setup says that the
token travels unencrypted there. An address on the internet must start with
`https://`, and its certificate must be one this computer trusts; there is no
way to turn that check off.

**Without questions** (a script, or a computer set up for someone else):

```
sbplive setup --yes --url http://192.168.1.20:3001 --token-file token.txt
```

`--token-file -` reads the token from standard input. The token is never
given on the command line itself. `--no-claude-desktop` and
`--no-claude-code` leave either alone. Running setup again replaces the
stored token and address and the entries it made; nothing doubles up.

### Where things are kept

| What | Where |
|---|---|
| The token, the address and (for [REMOTE.md](REMOTE.md)) the connector secret and `oauth.json`, the remote door's sign-ins (hashes only, never a password) | Windows: `%APPDATA%\sbplive\`; Mac: `~/Library/Application Support/sbplive/`; Linux: `~/.config/sbplive/` |
| The log | `sbplive.log` in the same folder, up to 1 MB and three older copies. It holds the method, address and answer code of each request, never the token or what was sent |
| Claude Desktop's entry | `claude_desktop_config.json` (Mac: `~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\` and the Microsoft Store app's own copy, Linux: `~/.config/Claude/`), as `"sbplive"`; it names the program only, never the token |
| Claude Code's entry | Your user's Claude Code settings, through `claude mcp add --scope user` |

The token file can be read by you only (on the Mac and Linux, mode 600; on
Windows, your own profile's protection). Before setup or disconnect changes
Claude Desktop's file, it saves the file as it was beside it, with the date
and time in the name, and changes nothing in it but the `"sbplive"` entry.

## Check it

`sbplive status` says, in sentences, whether SlowBooks Pro
answers, which company and version, which role the token has, whether Claude
Desktop and Claude Code are connected, and where the log is.

## What Claude can do

Whatever the token's role allows a person with that role, and nothing more:

- **Read-only:** read the books and run reports; change nothing.
- **Bookkeeper:** the daily books: customers, vendors, items, invoices,
  bills, expenses, journal entries, payments received and bills paid, voids.
  Not settings, users, payroll or HR.
- **Accountant:** read everything except payroll, HR and employees, and post
  journal entries.

SlowBooks Pro decides every request, as it does for a person: the role, the
closing date and every rule of the app. When it refuses, Claude hears why in
a sentence and tells you. No API token can manage users or tokens, restore a
backup, or change settings or the company file. A token can make a backup
only when an administrator ticked Can make backups on it. A few things are
left to SlowBooks Pro itself: PDFs, CSV and IIF files, print pages and
uploads (open them in the app).

Payments carry a key, so a payment Claude retries after a lost answer is
booked once. Voids leave a reversing entry; nothing is deleted. Claude Desktop
and Claude Code ask you before each change.

### The tools

| Tool | Does |
|------|------|
| `context` | The company, its type and closing date, the app's version and the token's role. Claude reads it first |
| `search` | Find records by name, number or amount across the books |
| `list_records`, `get_record` | One kind of record, or one by id |
| `list_reports`, `report` | The reports and their options; run one by name |
| `find_operations`, `describe_operation`, `call_operation` | Reach every operation in the installed app's API, read from the app itself, so they are never out of date |
| `api_schema`, `api_get` | One route's schema; a read of any route |
| `create_customer`, `create_vendor`, `create_item` | New records |
| `create_invoice`, `create_bill`, `create_expense`, `create_journal_entry` | New documents and entries |
| `record_payment`, `record_bill_payment` | Money in and out, applied to invoices or bills, each with its key |
| `void_record` | Void an invoice, bill, expense, journal entry, payment or bill payment |

## Turn it off

- **For a while:** quit Claude, or turn SlowBooks off in Claude's connector
  list. Nothing runs when Claude is not using it.
- **Take it out of Claude:** `sbplive disconnect` removes the
  entries from Claude Desktop (saving the file first) and Claude Code. Add
  `--forget` to delete the stored token too (and the remote door's connector
  secret and sign-ins, if [REMOTE.md](REMOTE.md) made them).
- **Lock it out for good:** revoke the token in SlowBooks Pro under Settings,
  API Tokens. That works even if the token was copied somewhere: the app
  refuses it from then on.

## When something is wrong

- "SlowBooks Pro did not answer at ...": SlowBooks Pro is not open, or the
  address or port is different. Open it, then try again; run setup again if
  the address changed.
- "SlowBooks Pro did not accept the API token": it was revoked or not copied
  whole. Make a new one and run setup again.
- "... was made as an administrator, and API tokens can't be administrators
  any more": make a bookkeeper, accountant or read-only token instead.
- "is not set up on this computer for this user": run setup as the person who
  uses Claude on this computer.
- "must start with https://": the address is on the internet; use the
  server's `https://` address.
- "its certificate could not be verified": the server's certificate is not
  one this computer trusts. Fix the certificate; the check stays on.
- "... is not valid JSON, so it was left as it is": Claude Desktop's file was
  already broken. Fix it or move it aside, then run setup again.
- Claude does not show SlowBooks: quit Claude Desktop completely (not only
  its window) and open it again. Its own log of the server is
  `mcp-server-sbplive.log` in Claude's logs folder.

## Settings for developers

The environment overrides what setup stored, one value at a time:

| Variable | Meaning |
|----------|---------|
| `SBPLIVE_URL` | The app's address, by the same rules as setup |
| `SBPLIVE_TOKEN_FILE` | A file holding the token (one line; mode 600 on the Mac and Linux) |
| `SBPLIVE_TIMEOUT_SECONDS` | Per-request timeout, 1 to 3600 (default 120) |
| `SBPLIVE_LOG` | How much it logs: `DEBUG`, `INFO`, `WARNING` (default) or `ERROR` |
