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
   Tokens, and make a token. Give it a name that starts with "AI
   assistant", such as `AI assistant (Switchboard)` (it names every change
   Claude makes in the audit log, as `token:<name>`), and the role Claude
   should have: **Read-only**, **Bookkeeper** or **Accountant**. Copy the
   token: it is shown once. SlowBooks Pro turns a token with such a name
   off by itself when the administrator who made it is deactivated or is
   no longer an administrator there, and setup refuses a new token with
   any other name, even one given to replace a token it stored before: it
   stores nothing, connects nothing, leaves a token it stored before as it
   was, and says "Nothing was changed." A SlowBooks Pro older than
   2.32.0-beta.2 cannot say how a token is named; setup then goes on and
   says that updating SlowBooks Pro will let it check. Setup run again with
   no new token keeps the token it stored before and does not check it
   again.
2. Run `sbplive setup`. It asks:

   ```
   SlowBooks Switchboard setup
   Where are the books? Press Enter for this computer (http://127.0.0.1:3001), or type the address of the office's SlowBooks Pro server:
   Paste the API token from SlowBooks Pro, Settings, API Tokens (it starts with sbp_ and is not shown as you type):
   SlowBooks Pro answered at http://127.0.0.1:3001: the company is Larkspur Test Fencing (SlowBooks Pro 2.32.0), and the token works as a bookkeeper (the daily books but not settings or payroll).
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

### Several companies on one connection

An accountant who keeps the books of several clients can reach each of them
through the same connection, so Claude needs no second entry. This needs
SlowBooks Pro 2.32.0-beta.2 or newer, which can say which company a token
opens, running as **Server Edition**: started with `--serve-lan` (and not
with `--bind 127.0.0.1`, which turns Server Edition off), it serves every
company in its list of companies. The SlowBooks Pro window serves only the
company open in it, and `--no-window` only the company it starts with, so
there `add-books` cannot add a second company (it says SlowBooks Pro does
not serve it, or does not accept the token setup stored), and with another
company opened, the first company's token is refused. On one computer,
close the SlowBooks Pro window, start it with `--serve-lan`, and give setup
one of the addresses it shows (they are also in `connect-urls.txt` in its
data folder). Server Edition serves the office network, so the other
computers there can reach its sign-in screen. A company file encrypted at
the SlowBooks Pro window, under a recovery key, opens only there: Server
Edition does not serve it. For each company:

1. In SlowBooks Pro, open that company, sign in as its administrator, and
   make a **Bookkeeper** token under Settings, API Tokens, with a name that
   starts with "AI assistant", such as `AI assistant (Switchboard)`.
   SlowBooks Pro turns such a token off by itself when the administrator
   who made it is deactivated or is no longer an administrator there, so a
   client who ends your access ends this connection's too. `add-books`
   refuses a token with any other name, and the token setup stored is held
   to the same rule when the first company is added (a token stored by an
   earlier setup, or by one run against an older SlowBooks Pro, may have
   another name: make a new one and run setup with it first).
2. Run `sbplive add-books --file <the company's file>`, such as
   `sbplive add-books --file smith-plumbing.db`, and paste the token (or
   pass `--token-file`, or `--token-file -` for standard input).

`add-books` asks SlowBooks Pro which company the token opens, and keeps it
only when the answer comes from that same file and the token is a
bookkeeper's. It then says:

```
Added Smith Plumbing's books as smith-plumbing~k3f9q2 (bookkeeper).
```

`smith-plumbing~k3f9q2` is the company's **handle**. The last six
characters, lowercase letters and the digits 2 to 7, are random and made on
this computer when the company is added, so no other computer's handle, and
no company's name or file, ever reaches these books. Claude reads the
handles with its `context` tool and names one on every call (the `books`
argument). With one company on the connection it may leave it out; with
more it must give it, and a call that names none is refused before anything
is sent. With more than one company, the answer to a call that names one by
its handle starts with that company, refusals included, such as "Books:
Smith Plumbing (smith-plumbing~k3f9q2)." `context` called without `books`
answers with the list of handles instead, and the refusal of a call that
names no company, or names one by anything but its handle, does not start
with a company.

The first `add-books` keeps the token setup stored as the first company,
with a handle of its own: nothing has to be set up again. A connection
set up with one token and never given another works exactly as before.
Setup's own token file stays where it is, so if you go back to
Switchboard 1.0.0 it still reaches that first company (and only that one);
`remove-books` for that company deletes it too.

- `sbplive books` lists the companies, checks each one, and says whether it
  answers and under which role.
- `sbplive remove-books <handle>` takes one company off and deletes its
  stored token. The token itself still works in SlowBooks Pro until you
  turn it off there (Settings, API Tokens, in that company).
- A company added or removed while Claude is open is seen on Claude's next
  call. A company whose token SlowBooks Pro refuses is not tried again until
  Claude starts the program again; `sbplive books` shows which one it is.
- All the companies are on one SlowBooks Pro (one address). Running setup
  again keeps them: it connects Claude and stores no token, and given
  `--token-file` or `--url` it changes nothing and says so (a new token for
  a company goes in with `remove-books`, then `add-books`). `SBPLIVE_URL`
  naming another address is refused while companies are kept, so their
  tokens never go to a copy of the books on another computer.
- The self-hosted door ([REMOTE.md](REMOTE.md)) serves only the first
  company in this version, and its sign-in is that company's.

### Where things are kept

| What | Where |
|---|---|
| The token, the address and (for [REMOTE.md](REMOTE.md)) the connector secret and `oauth.json`, the remote door's sign-ins (hashes only, never a password) | Windows: `%APPDATA%\sbplive\`; Mac: `~/Library/Application Support/sbplive/`; Linux: `~/.config/sbplive/` |
| Several companies (`add-books`) | Each company's token in `books/<handle>.token` in the same folder (the folder and the files readable by you only); `settings.json` lists each company's handle, file and name, and a hash of its token, never the token |
| The log | `sbplive.log` in the same folder, up to 1 MB and three older copies. At the default level it holds only warnings and errors, such as a tool that hit an internal error or, with several companies, a company's token SlowBooks Pro refused, so it is often empty; with `SBPLIVE_LOG=INFO` it also holds the method, path and answer code of each request. Never the token or what was sent |
| Claude Desktop's entry | `claude_desktop_config.json` (Mac: `~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\` and the Microsoft Store app's own copy, Linux: `~/.config/Claude/`), as `"sbplive"`; it names the program only, never the token |
| Claude Code's entry | Your user's Claude Code settings, through `claude mcp add --scope user` |

The token file can be read by you only (on the Mac and Linux, mode 600; on
Windows, your own profile's protection). Before setup or disconnect changes
Claude Desktop's file, it saves the file as it was beside it, with the date
and time in the name, and changes nothing in it but the `"sbplive"` entry.

## Check it

`sbplive status` says, in sentences, whether SlowBooks Pro
answers, which company and version, which role the token has, whether Claude
Desktop and Claude Code are connected, and where the log is. With several
companies it names each one, with its handle, and says whether it answers.

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
booked once. Voids leave a reversing entry; nothing is deleted. Every change
is marked so that Claude Desktop and Claude Code can ask you before it runs.

### Asking before a bare create

An assistant does what it is asked, and a customer entered from a name
alone may be one that should have been checked first. So Switchboard holds
back a new customer, vendor, item, employee, account, invoice, bill,
expense, estimate or purchase order that carries only what SlowBooks Pro requires:
nothing is written, and the assistant is told which fields are empty (as
the app's forms name them: for a customer, Company, Email, Phone, Billing
address, Terms and Sales Tax) and to ask you whether you have any of them,
inventing none. It then creates the record with what you gave, or as it is
if you say so. A record that already fills at least half of those fields
(one of three for an account and for a document; for a document's lines,
an item or a description on each) is created at once. After a create of
one of these records the assistant hears two lists, so it can tell you: what was left blank
(`left_blank`), and what it did not give that SlowBooks Pro filled with its
own default, with the value used (`defaulted`: a customer's terms "Net 30"
and "taxable", a vendor's terms and 1099, an invoice's terms and due date,
a bill's number; for an item with no income or expense account, the
account the app will post its sales to instead, read from your chart).

### Sales tax on a new sales document

SlowBooks Pro's own forms start a new invoice, estimate, sales receipt or
credit memo at the company's Default Tax Rate (Settings), and a credit memo
for an invoice at that invoice's rate. From SlowBooks Pro 2.32.0 the app's
API does the same when no rate is sent; before 2.32.0 it took a missing
rate as 0%. Switchboard does what the form does on either: when the
assistant gives no `tax_rate`, the document gets the company's default, and
the answer says so (`tax_rate_source`, for example "company default
(6.25%)", and `tax_note`, a sentence to pass on). A rate the assistant
gives, 0 included, is always used as given. A non-taxable customer, a
document marked tax exempt or (from SlowBooks Pro 2.32.0) a document for a
tax-exempt job is charged no tax whatever the rate, and the answer says
that instead: no sales tax was charged, and why. A credit memo for an
invoice that charged no tax gives none back, and the answer says that too.
If the company's default is 0% and the customer is taxable, "Sales tax
rate" is one of the fields the assistant asks you about, and it is listed
in `left_blank` if the document is created without one.

Asking first and the two lists are Switchboard's own, not SlowBooks Pro's:
the app's screens, imports and API tokens used directly create records as
they always have.
Updates, payments, voids and reads are not affected.

### The tools

| Tool | Does |
|------|------|
| `context` | The company, its type and closing date, the app's version and the token's role. Claude reads it first. On a connection that reaches several companies, it first lists their handles, and every other tool then names one with `books` |
| `search` | Find records by name, number or amount across the books |
| `list_records`, `get_record` | One kind of record, or one by id |
| `list_reports`, `report` | The reports and their options; run one by name |
| `find_operations`, `describe_operation` | Find any operation in the installed app's API and what it takes, read from the app itself, so they are never out of date |
| `api_schema`, `api_get` | One route's schema; a read (`GET`) of any route, which changes nothing |
| `post_operation` | A `POST` to any operation: create a record, or run an action such as a void or an apply |
| `update_operation` | A `PUT` or `PATCH`: change a record |
| `delete_operation` | A `DELETE`: remove a record, or, where SlowBooks Pro keeps its history (a customer, vendor or item), make it inactive; the answer's `result` says which (posted documents are voided instead) |
| `create_customer`, `create_vendor`, `create_item` | New records (a bare one is held back until you were asked, above) |
| `create_invoice`, `create_bill`, `create_expense`, `create_journal_entry` | New documents and entries (an expense without the account it was paid from is not made: the assistant is given the account the app's form would start at, to check with you) |
| `record_payment`, `record_bill_payment` | Money in and out, applied to invoices or bills, each with its key; the answer says where the money went (`money_to`, `money_note`: a payment with no deposit account waits in Undeposited Funds until a bank deposit) |
| `void_record` | Void an invoice, bill, expense, journal entry, payment or bill payment |

## Turn it off

- **For a while:** quit Claude, or turn SlowBooks off in Claude's connector
  list. Nothing runs when Claude is not using it.
- **Take it out of Claude:** `sbplive disconnect` removes the
  entries from Claude Desktop (saving the file first) and Claude Code. Add
  `--forget` to delete the stored token too, every company's when there are
  several (and the remote door's connector secret and sign-ins, if
  [REMOTE.md](REMOTE.md) made them).
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
- "That token answers from harbor-books.db, not smith-plumbing.db": the token
  was made in another company. Open the company you are adding, make the
  token there, and run add-books again.
- "This SlowBooks Pro is older than 2.32.0-beta.2": update SlowBooks Pro to
  add a second company; the first keeps working.
- "... no longer accepts this connection": that company's token was turned
  off there, or the administrator who made it is no longer one there. Make
  a new bookkeeper token in that company, then
  `sbplive remove-books <handle>` and `sbplive add-books` again.
- "That token is named ..., so SlowBooks Pro keeps it working" (from setup
  or add-books): make the token again with a name that starts with "AI
  assistant".
- "This SlowBooks Pro cannot tell whether the token ends with the
  administrator who made it": setup went on. Update SlowBooks Pro to
  2.32.0-beta.2 or newer to have it checked.
- "The companies' books here were added for ...": `SBPLIVE_URL` names
  another address. Take it out of the environment.
- "The list of companies in ... cannot be read": `settings.json` was changed
  by hand. Put back a copy, or run `sbplive disconnect --forget` and add the
  companies again.
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
