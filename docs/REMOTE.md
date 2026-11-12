# SlowBooks Switchboard from anywhere: Claude on the web through your own tunnel

This guide is for a bookkeeper with an IT friend. At the end, Claude on the
web (and the Claude apps on a phone or another computer) can work the
office's books in SlowBooks Pro, from outside the office, with no port opened
on the office's router.

**Only in the office, with Claude Desktop?** You do not need any of this. See
[In the office with Claude Desktop](#in-the-office-with-claude-desktop) at the
end.

## How it fits together

```
Claude (Anthropic's servers)  --https-->  Cloudflare  --tunnel-->  cloudflared  -->  serve-http  -->  SlowBooks Pro
                                          your hostname            on the office     127.0.0.1:8743    its own API,
                                                                   computer                            the API token
```

- **SlowBooks Pro** runs as it does today: the office's Server Edition, or a
  desktop.
- **`sbplive serve-http`** runs on the same computer (or one
  that reaches SlowBooks Pro on the office network). It listens on this
  computer only, at `http://127.0.0.1:8743/mcp`, and answers only someone
  signed in: Claude, after an **administrator of the books signs in** on the
  door's own sign-in page (OAuth), or a caller that shows the **connector
  secret**.
- **`cloudflared`**, Cloudflare's small connector program, runs on the same
  computer. It makes an outgoing connection to Cloudflare, and Cloudflare
  sends the requests for your hostname down it. Nothing comes in through the
  router.
- **Claude** is given your hostname as a custom connector, and signs in once.

Three different things are involved, and they are not the same:

| | What it is | Where it goes |
|---|---|---|
| **The API token** (`sbp_...`) | Made in SlowBooks Pro, Settings, API Tokens. Its role decides what Claude may do | Stays on this computer. It is only ever sent to SlowBooks Pro |
| **The administrator sign-in** | The user name (if the books have several users) and password of a SlowBooks Pro **administrator** | Typed into the door's sign-in page when Claude connects. SlowBooks Pro checks it; the door keeps nothing of it and gives Claude a token of its own instead |
| **The connector secret** (`sbc_...`) | Made by `serve-http` the first time it runs | Only for a Claude account whose connector form has a **Request headers** section, as the alternative to signing in. It only opens the door; it is not a SlowBooks Pro password |

## What you need

- SlowBooks Pro, running, with an API token (step 1 below).
- SlowBooks Switchboard on the computer that will keep the door open (the
  program, or a checkout with Python 3.10 or newer: see
  [GUIDE.md](GUIDE.md#install-it)). That computer has to be on whenever Claude
  should reach the books.
- A **free Cloudflare account** and **a domain whose DNS is on Cloudflare**
  (an existing one, or a cheap new one; Cloudflare's pages require *"a domain
  on Cloudflare"* before an application can be published
  ([Create a tunnel (dashboard)](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/),
  read 2026-11-06, page dated Jun 23, 2026)). You will pick a hostname on it,
  such as `books.example.com`.
- A Claude account. Any plan can add a custom connector; on the Free plan,
  one. On a Team or Enterprise plan an Owner adds it
  ([Claude: add a connector](https://claude.com/docs/connectors/custom)).

## 1. Make the API token

In SlowBooks Pro, signed in as an administrator: Settings, API Tokens, make a
token. The **role** is what Claude will be able to do (see
[What Claude can then do](#what-claude-can-then-do)). Give it a label such as
"Claude (remote)"; every change Claude makes is in the audit log as
`token:<label>`. Copy it: it is shown once.

## 2. Store it on the computer that keeps the door open

```
sbplive setup --no-claude-desktop --no-claude-code
```

Answer where the books are (Enter for this computer) and paste the token. It
checks the token against SlowBooks Pro and stores it for this user only. (If
this computer also runs Claude Desktop, leave the two `--no-...` options out.)

## 3. Open the door: `serve-http`

Choose the hostname you will publish in step 4 (such as `books.example.com`),
and give it to the door as its public address:

```
sbplive serve-http --public-url https://books.example.com
```

`--public-url` turns on the sign-in: the door then serves the sign-in page
and the documents Claude reads to find it, under that address. It must be
exactly the `https://` address Claude will use, with nothing after the
hostname. (Without it, only the connector secret works.)

```
The remote door is open at http://127.0.0.1:8743/mcp
A new connector secret was made. It is shown this once and kept in /home/you/.config/sbplive/connector-secret, which only you can read:

    sbc_...

In Claude's Add custom connector form, under Request headers, add the header authorization with the value: Bearer sbc_... (only for a Claude account that has that section; OAuth sign-in, below, needs no secret)
SlowBooks Pro answered at http://127.0.0.1:3001: the company is Larkspur Test Fencing (SlowBooks Pro 2.31.8), and the token works as a bookkeeper (the daily books but not settings or payroll).
OAuth sign-in is on. In Claude, add the connector with the address https://books.example.com/mcp and Authentication left to OAuth: Claude opens a SlowBooks Pro sign-in page, where an administrator of these books signs in once.
Claude can do through it whatever the API token's role allows. Leave this running while Claude should reach the books; press Ctrl+C to close the door.
```

The connector secret is made the first time either way. You need it only for
the other way in (step 6, *Request headers instead*); if you might, copy the
`sbc_...` line somewhere safe (a password manager). It is not shown again:
the next time `serve-http` starts, it uses the stored one and says so. Lost
it? `sbplive serve-http --new-secret` makes a new one, shows it,
and the old one stops working.

Leave this window open. (Your IT friend may prefer to run it as a service
that starts with the computer; it needs nothing but the command above, run as
the user who did step 2.)

Options, should you need them: `--port` for another port (the default is
8743); `--host` to listen on another address (leave it alone: with
`cloudflared` on the same computer, `127.0.0.1` is right, and nothing on the
office network can reach the door directly).

## 4. Install `cloudflared` and create a named tunnel

Follow Cloudflare's own pages; the steps as they read on 2026-11-06:

1. In the Cloudflare dashboard, go to **Networking**, **Tunnels**, and click
   **Create a tunnel**. Give it a name (such as `office-books`) and click
   **Create Tunnel**
   ([Create a tunnel (dashboard)](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/)).
2. Select the computer's operating system. Cloudflare shows the command that
   installs `cloudflared` and connects it to this tunnel; copy it and run it
   in a terminal on the computer from step 3 (as administrator on Windows,
   with `sudo` on Linux). The installers are on Cloudflare's
   [downloads page](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/)
   (Windows from GitHub, `brew install cloudflared` on the Mac, the
   Cloudflare package repository on Debian and Ubuntu; note that on Windows
   `cloudflared` does not update itself). When the tunnel shows as connected,
   click **Continue**.
3. **Publish only Switchboard's port.** In the tunnel's **Routes** tab, click
   **Add route**, **Published application**. Enter a subdomain (`books`) and
   pick your domain, so the hostname is `books.example.com`. For **Service
   URL** enter exactly:

   ```
   http://localhost:8743
   ```

   and click **Add route**. Do not add a route for SlowBooks Pro itself
   (`:3001`) or anything else: Claude needs only this one, and the door
   answers nothing but `/mcp`.

   (A hostname with more than one level, such as `books.office.example.com`,
   needs an Advanced Certificate, Cloudflare notes; one level is simplest.)

**Your IT friend prefers the command line?** A locally-managed tunnel does the
same ([Cloudflare: create a locally-managed tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/create-local-tunnel/)):

```
cloudflared tunnel login
cloudflared tunnel create office-books
cloudflared tunnel route dns office-books books.example.com
```

with a `config.yml` in the `.cloudflared` directory that publishes this one
hostname and port and ends in the catch-all rule Cloudflare requires
([configuration file](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/configuration-file/)):

```yaml
tunnel: <Tunnel-UUID>
credentials-file: /home/you/.cloudflared/<Tunnel-UUID>.json
ingress:
  - hostname: books.example.com
    service: http://localhost:8743
  - service: http_status:404
```

then `cloudflared tunnel ingress validate` and
`cloudflared tunnel run office-books` (or `cloudflared service install` to
keep it running; on Linux the service reads `$HOME/.cloudflared/config.yml`,
and with `sudo` that is root's, so Cloudflare's
[service page](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/as-a-service/linux/)
passes `--config` explicitly).

**Just trying it?** `cloudflared tunnel --url http://localhost:8743` makes a
*quick tunnel* with a random `trycloudflare.com` address and no account. It
is for testing only: the address changes every time, it stops when the
command stops, and Cloudflare gives it no uptime guarantee
([Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/)).
Anyone who learns the address reaches the door, so the sign-in matters just
as much; and since the address changes, `--public-url` has to be given the
new one each time. Use a named tunnel for real work.

## 5. Do not put Cloudflare Access in front of it

Cloudflare offers **Access** (a login page in front of a hostname). **Do not
add an Access application or policy for this hostname.** Claude's requests
come from Anthropic's servers, which cannot sign in to a login page, so
Claude would be blocked and report that it could not reach the server.
Anthropic's troubleshooting page says the same of any firewall, bot rule or
rate limit that blocks its traffic
([Troubleshoot your connector](https://claude.com/docs/connectors/building/troubleshooting.md)).
What protects this address is the **sign-in**: without a token from it (or
the connector secret) the door answers 401 and nothing else, and an address
that keeps getting the password or the secret wrong is shut out for ten
minutes at a time.

If the Cloudflare zone has **Bot Fight Mode** or WAF rules, make sure they do
not block this hostname. Anthropic's traffic comes from `160.79.104.0/21`
([Authentication for connectors](https://claude.com/docs/connectors/building/authentication.md#network-reference)).

Check it from any computer (a phone's browser works too): opening
`https://books.example.com/mcp` should give a blank page with the status 401,
and `https://books.example.com/.well-known/oauth-protected-resource` a short
JSON document naming `https://books.example.com/mcp`. Any other path, such as
`https://books.example.com/`, gives 404.

## 6. Add it to Claude

In Claude (on the web, at claude.ai):

1. **Customize**, **Connectors**, **Add custom connector**
   ([Claude: add a connector](https://claude.com/docs/connectors/custom)).
   On a Team or Enterprise plan, an Owner does this under **Organization
   settings**, **Connectors**, **Add**, **Custom**, **Web**, and each member
   then clicks **Connect** and signs in.
2. **Name:** SlowBooks. **MCP server URL:** `https://books.example.com/mcp`
   (the `--public-url` address of step 3, then `/mcp`).
3. **Authentication:** **Sign in now** (OAuth). Leave the OAuth client ID
   and secret empty: if the form asks for the **OAuth client**, choose
   **Use Claude's published identity** (or **Register automatically**; the
   door takes both). If your form has a single screen, leave **Advanced
   settings** alone.
4. Click **Add** (or **Connect**). Claude opens the door's sign-in page,
   which says which company it is, what Claude will work as (the API token's
   role, such as "a bookkeeper") and that you go back to `claude.ai`
   afterwards. Sign in with the **SlowBooks Pro administrator sign-in**: the
   user name and password of an administrator of these books (the password
   alone when the books have only one user), and click **Sign in and
   allow**. A bookkeeper's or accountant's sign-in is refused: an
   administrator chose the API token Claude works with, so an administrator
   lets Claude in.
5. You are back in Claude, and the connector shows **Connected**; its page
   lists the 21 tools. Claude keeps itself signed in (a token for an hour,
   renewed by itself for thirty days of use); you sign in again only if it
   goes unused that long, or after the sign-ins are cleared (below).
6. In a chat, turn it on under **+**, **Connectors**, and ask "What company
   are you connected to?"

Claude Code connects the same way (`claude mcp add --transport http sbplive
https://books.example.com/mcp`): it opens the same page in your browser and
returns to a program on your own computer, which the page warns about in so
many words.

**Request headers instead.** A Claude account whose form has a **Request
headers** section (Anthropic offers it in beta to some organizations only)
can use the connector secret instead of signing in: **Authentication:** **No
sign-in**; **Request headers:** `authorization`, **Required**, the value
**`Bearer `** followed by the secret, with the space (`Bearer sbc_...`).
Claude sends exactly what you type and keeps the value without showing it
again ([Authenticate with request headers](https://claude.com/docs/connectors/custom#authenticate-with-request-headers)).
Everyone in the organization then shares that one secret.

## What Claude can then do

Exactly what the API token's role allows a person with that role, through
SlowBooks Pro's own rules (the closing date included), and nothing more; the
same as on the desktop ([GUIDE.md, What Claude can do](GUIDE.md#what-claude-can-do)):

- **Read-only:** read the books and run reports; change nothing. The safest
  token for a first try from outside the office.
- **Bookkeeper:** the daily books (customers, vendors, items, invoices,
  bills, expenses, journal entries, payments, voids); not settings, users,
  payroll or HR.
- **Accountant:** read everything except payroll, HR and employees, and post
  journal entries.

No token can manage users or tokens, restore a backup or change the company
file. Claude asks you to approve each tool call unless you choose **Always
allow** for it (keep that for the read-only tools). Every change is in SlowBooks
Pro's audit log as `token:<label>`. Anyone who has signed the connector in
(on a Team plan, every member an administrator signed in for) acts as that
one token: make one token per purpose, and revoke it when the purpose ends.
(A token for each person is still to be decided.)

## Turn it off

- **For now:** press Ctrl+C in the `serve-http` window (or stop the service).
  Claude then gets an error from Cloudflare instead of the books. Starting
  `serve-http` again reopens the door with the same secret.
- **The tunnel too:** in the Cloudflare dashboard, **Networking**,
  **Tunnels**, remove the route or delete the tunnel. That alone cuts the
  hostname off; `cloudflared` on the computer can then be stopped and
  uninstalled like any other program.
- **In Claude:** turn the connector off for a chat under **+**,
  **Connectors**, or remove it under **Customize**, **Connectors**.
- **Sign Claude out:** stop `serve-http`, delete `oauth.json` from the
  folder that holds the token (it holds the sign-ins, as hashes), and start
  it again. Every connection then has to sign in again.
- **Change the key:** `serve-http --new-secret`. The old secret stops
  working at once. Claude cannot edit a connector's headers, so remove the
  connector and add it again with the new one.
- **Lock it out for good:** revoke the API token in SlowBooks Pro, Settings,
  API Tokens. Nothing gets through the door after that, whatever secret it
  is shown and whoever signed in. `sbplive disconnect --forget`
  also deletes the stored token, connector secret and sign-ins from this
  computer.

## When something is wrong

- **Claude says "Couldn't reach the MCP server".** Check, in order: is
  `serve-http` running; is the tunnel shown as healthy in the dashboard; does
  `https://books.example.com/mcp` give 401 from a phone off the office
  Wi-Fi, and `/.well-known/oauth-protected-resource` the JSON document; is
  `serve-http` started with `--public-url` and exactly the address the
  tunnel publishes (the document names the address Claude must use, and
  Claude refuses a mismatch); is the URL in Claude exactly that plus `/mcp`
  (no `www.` or other host that redirects); is there no Access policy, Bot
  Fight Mode or WAF rule on the hostname. With request headers instead: is
  the secret preceded by `Bearer `.
- **"Authorization with the MCP server failed"**, or the sign-in page says
  something went wrong. The page's sentence says what: a wrong user name or
  password; a sign-in that is not an administrator's ("Only an administrator
  of these books can let Claude in"); SlowBooks Pro not answering; or the
  page left open more than ten minutes (go back to Claude and connect
  again).
- **"Too many requests" (429).** An address failed ten times in ten minutes
  (a wrong password on the sign-in page, or a wrong token or secret). Wait
  ten minutes, and fix what was wrong.
- **403.** The request carried a browser Origin the door does not allow. The
  log (`sbplive.log`, beside the token) names it; if it is Claude's own,
  start `serve-http` with `--allow-origin <that origin>` and tell us.
- **A tool fails after about two minutes.** SlowBooks Pro took too long to
  answer; Cloudflare also cuts a request off after about 125 seconds. Ask for
  less at once (a shorter date range).
- **"The remote door could not open on 127.0.0.1 port 8743".** Another
  `serve-http` is already running, or something else uses the port. Use
  `--port`, and give the same port in the tunnel's Service URL.

## In the office with Claude Desktop

On the office network you do not need a tunnel, `serve-http` or a connector
secret. Each person who uses Claude Desktop (or Claude Code) on their own
computer runs the ordinary setup from [GUIDE.md](GUIDE.md), giving the
Server Edition's office address at the first question (such as
`http://192.168.1.20:3001`), and Claude Desktop starts SlowBooks Switchboard
itself. That local connection and this remote door can be used side by
side, each with its own token.
