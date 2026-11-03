# Setting up a Factotum host

Written for Robert's mf-pc (Windows 11, WSL2 Ubuntu, always on, mirrored networking), but every
section says what is specific to that machine. Do the numbered sections in order; each ends with
a check you can run.

## 1. WSL2 with systemd

```bash
# inside WSL
cat /etc/wsl.conf          # should contain:
# [boot]
# systemd=true
systemctl --user status 2>/dev/null | head -1 || true
systemctl status | head -3    # "State: running" means systemd is the init
```
If `/etc/wsl.conf` lacks the `[boot]` block, add it and run `wsl --shutdown` from PowerShell,
then reopen the shell. Mirrored networking is set in `%UserProfile%\.wslconfig`:
```
[wsl2]
networkingMode=mirrored
```
Check: from WSL, `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:52000/` reaches the
Windows host (any HTTP code other than a connection error is a pass).

## 2. Python, Node and the Plaud CLI

```bash
sudo apt install -y python3.12-venv
cd ~ && git clone https://github.com/nthPerson/factotum.git && cd factotum
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest                       # must pass before anything else

# Node 20+ for the Plaud CLI
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt install -y nodejs
npm install -g @plaud-ai/cli
plaud login                            # opens the Windows browser; tokens land in ~/.plaud/tokens.json
plaud today                            # check: lists today's recordings
plaud transcript <id> -o /tmp/t.md     # check: a full transcript lands on disk with no size cap
```

Claude Code must be logged in for the user the service runs as (phase 3 runs `claude -p` headlessly;
there is no way to log in from a service):
```bash
claude                                 # once, interactively: /login with the Max account, then exit
.venv/bin/python -m factotum claude-check   # one tiny headless call (~$0.005): "claude is logged in"
```

## 3. Mailbird Next MCP (Windows side)

Mailbird Next → Settings → AI / MCP: enable the server, enable **write actions** (drafts need
it; sending still requires `confirm: true` from the client and Factotum never passes it), copy
the bearer token. Put it in `.env` as `MAILBIRD_TOKEN`.

Check from WSL (the certificate is self-signed, hence `-k`):
```bash
curl -sk -X POST https://127.0.0.1:52000/mcp \
  -H "Authorization: Bearer $MAILBIRD_TOKEN" -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}' | head -c 400
```
A JSON-RPC result with `serverInfo` is a pass. Claude Code already reaches it through
`mcp-remote`; Factotum's Python client (phase 4) uses the same URL and token.

## 4. ntfy

1. Install the ntfy app: iOS App Store "ntfy", Android Play Store or F-Droid "ntfy".
2. Pick a topic name that cannot be guessed (on the public server a topic name is the only
   secret): `echo "factotum-$(openssl rand -hex 5)"` → e.g. `factotum-7f3a9c2e1b`.
3. In the app: Subscribe to topic → enter the name, server `https://ntfy.sh`. On iOS allow
   notifications when asked.
4. Optional: create an account at https://ntfy.sh/signup, Account → Access tokens → create one,
   put it in `.env` as `NTFY_TOKEN`. Not required to publish to an unreserved topic; it raises
   the rate limits and lets you reserve the topic on a paid tier.
5. Put the topic in `config/factotum.yaml` under `notify.ntfy.topic` (or the Settings tab).

Check:
```bash
curl -d "Factotum says hi" -H "Title: Test" -H "Click: https://factotum.nthperson.dev" ntfy.sh/<topic>
```
The phone should buzz within seconds. Facts that shaped the design: max delay 3 days, 250
messages/day, up to 3 action buttons, 4 KB body, cancel by sequence id.

## 5. Notion internal integration

1. https://www.notion.so/profile/integrations → **New integration** → name `Factotum`, type
   Internal, workspace = yours → Capabilities: read content, update content, insert content
   (no user information needed) → Save → copy the **Internal Integration Secret** into `.env` as
   `NOTION_TOKEN`.
2. Connect it to each database Factotum touches. Open the database page in Notion → `•••`
   (top right) → **Connections** → **Connect to** → Factotum. Do this for:
   - 🎙️ Plaud Synced Notes (`61e12e128c7f473b94f22641d8a36260`)
   - Tasks (`b1c52e69d3924aea8962bee32440ffbe`)
   If the Tasks database is inline on the 📋 Planner page, connect the Planner page instead;
   connections inherit downward.
3. The ids are already in `config/factotum.example.yaml` (`sinks.*`). Another user puts their
   own database id and data source id there; the data source id is the `collection://…` value
   the Notion MCP shows, or the `data_sources[0].id` from `GET /v1/databases/{id}`.

Check:
```bash
set -a; source .env; set +a
curl -s https://api.notion.com/v1/databases/b1c52e69d3924aea8962bee32440ffbe \
  -H "Authorization: Bearer $NOTION_TOKEN" -H "Notion-Version: 2025-09-03" | head -c 300
.venv/bin/python -m factotum import-notion --dry-run      # prints the bucket report
```

## 6. Google Calendar (optional, phase 2)

Factotum reads your calendar from Python so the sweep can close Dated tasks whose event has
passed, show the next days in the brief, and (later) put deadlines on the calendar. It talks to
Google as *you*, through an OAuth "Desktop app" client that you create once in Google Cloud.
Nothing here costs money and no billing account is needed.

Two accounts are involved and they may be the same one: the Google account that **owns the Cloud
project** (any account) and the Google account **whose calendar** you want (for Robert:
robertashe2.0@gmail.com, where most events are). You consent with the second one in step 6.

The console has been reorganised several times; this is the 2025–2026 layout. If a page is named
differently for you, the old names are in brackets. Use the search box at the top of the console
("Search for resources, docs, products") to jump to any page by name.

1. **Project.** https://console.cloud.google.com → project picker (top left) → pick `Factotum`
   (you already created it). Everything below is inside that project.
2. **Enable the API** (done already): search "Google Calendar API" → Enable.
3. **The consent screen** [older UI: "OAuth consent screen"]. Search "Google Auth Platform" →
   **Overview** → if it says "Google Auth Platform not configured yet", click **Get started**:
   - App information: App name `Factotum`, User support email = your address → Next.
   - Audience: **External** → Next. (Internal is only offered to Google Workspace organisations.)
   - Contact information: your address → Next.
   - Agree to the policy → Continue → **Create**.
4. **Test user.** Google Auth Platform → **Audience** → under *Test users* click **Add users** →
   enter the calendar account (robertashe2.0@gmail.com) → Save. Only accounts on this list can
   consent while the app is in *Testing*.
5. **Desktop client** [older UI: "Credentials → Create credentials → OAuth client ID"].
   Google Auth Platform → **Clients** → **Create client** → Application type **Desktop app** →
   Name `Factotum on mf-pc` → Create. A dialog shows the client id and secret; click
   **Download JSON** (you can also download it later from the ⬇ icon in the Clients list). Save
   the file on the WSL side as `~/factotum/config/google-credentials.json` (git-ignored). From
   Windows the WSL path is `\\wsl$\Ubuntu\home\robert\factotum\config\`. Do not create a
   "Web application" client: its redirect rules break the local flow with `redirect_uri_mismatch`.
6. **Consent once, from WSL.** Use the paste-back flow; it works whether or not the Windows
   browser can reach a port inside WSL:
   ```bash
   cd ~/factotum && .venv/bin/python -m factotum google-auth --manual
   ```
   It prints a long `https://accounts.google.com/...` URL. Open it in any browser and sign in
   with the calendar account. Google shows **"Google hasn't verified this app"** because the app
   is yours and unreviewed: click **Advanced** → **Go to Factotum (unsafe)** → tick both calendar
   permissions → Continue. The browser then tries to open `http://localhost:8765/?state=...&code=...`
   and shows "This site can't be reached"; that is expected. Copy the whole address from the
   address bar and paste it at the `Pasted address:` prompt (pasting just the `code=` value also
   works). The command writes `config/google-token.json`. The code in that address is single-use
   and expires after a few minutes, so if the paste fails, run the command again for a fresh URL.

   Without `--manual` the command starts a tiny web server on a random port, opens the browser and
   catches the redirect itself. On WSL the browser often cannot reach that port, so prefer
   `--manual`.
7. **Avoid the 7-day expiry.** While the consent screen is in *Testing*, Google expires the
   token after 7 days and Factotum would then stop reading your calendar and ask you to run
   `google-auth` again. Switch it to production: Google Auth Platform → **Audience** →
   **Publish app** → Confirm. For the Calendar scopes this needs no review: the app stays
   "unverified" (the warning in step 6 keeps appearing, only to you) but tokens stop expiring.
   Publish first, then run step 6, because a token issued during Testing keeps its 7-day limit.
   If you published after consenting, delete `config/google-token.json` and run step 6 again.
8. Settings tab → **Google Calendar** on (or `sources.calendar.enabled: true` in
   `config/factotum.yaml`). `sources.calendar.calendar_id: primary` is the calendar of the
   account that consented; another calendar's id is under its Settings → "Integrate calendar".

Check: `.venv/bin/python -m factotum calendar-check` prints the next 7 days of events. A
`401`/`invalid_grant` there means the token expired or was revoked: run `google-auth` again.
`0 events` in a week that is not empty means you consented with the wrong Google account: the
token sees only that account's calendars. Sign in with the account whose calendar holds the
events (the one the old sync wrote to), which may differ from the account that owns the Cloud
project; delete `config/google-token.json` and run step 6 again. While the app is in Testing,
that account must also be listed under Audience → Test users.

**Service account instead of OAuth** (an alternative, not needed): a service account can read a
calendar you share with its email address, with no consent screen and no expiry, but Google
blocks personal Gmail calendars from being shared with service accounts in some regions, and it
cannot act as you. Supported by `sources.calendar.provider: google-service` with the key file in
`credentials_file`.

## 7. Cloudflare tunnel and Access

You already run `cloudflared` for `claude-context.nthperson.dev`. Two ways; A is simpler if the
existing tunnel runs as a service on this machine.

**A. Add a public hostname to the existing tunnel** (Zero Trust dashboard). On mf-pc this is the
only option that applies: `cloudflared.service` runs `tunnel run` with a token and has no local
`config.yml`, so ingress rules live in the dashboard, not on disk:
Networks → Tunnels → your tunnel → Public Hostname → **Add a public hostname** → subdomain
`factotum`, domain `nthperson.dev`, service type HTTP, URL `localhost:8750`. With mirrored
networking the Windows-side connector reaches the WSL port at localhost. Save.

**B. A separate tunnel** (what mf-pc uses; the host's existing `cloudflared.service` belongs to
another product and must not be touched):
```bash
cloudflared tunnel login                                  # browser; writes ~/.cloudflared/cert.pem
cloudflared tunnel create factotum                        # writes ~/.cloudflared/<tunnel-id>.json
cloudflared tunnel route dns factotum factotum.nthperson.dev
cat > ~/.cloudflared/config.yml <<EOF
tunnel: factotum
credentials-file: /home/robert/.cloudflared/<tunnel-id>.json   # paste the real id from the create step
ingress:
  - hostname: factotum.nthperson.dev
    service: http://localhost:8750
  - service: http_status:404
EOF
cloudflared tunnel --config ~/.cloudflared/config.yml run      # foreground check first (Ctrl-C after)
sudo cp deploy/cloudflared-factotum.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now cloudflared-factotum
```
Never run `cloudflared service install` on a host that already has a `cloudflared.service`: it
overwrites that unit (it also fails under `sudo` because it looks for the config in root's home).
The repo unit runs as the owner with an explicit `--config`.

If the hostname answers **530 / error 1033** while the foreground run logs "Registered tunnel
connection", `cloudflared tunnel info factotum` from a second shell shows the edge's view. "does not
have any active connection" there means the connector registers but the edge discards it. On mf-pc
(2026-11-01) the cause was an outdated client: 2026.5.0 did this, 2026.10.7 did not. Run
`sudo apt install --only-upgrade cloudflared` first; converting the tunnel to remote management in
the dashboard (Networks → Tunnels → factotum → Configure → Migrate) is the fallback.

**Access policy** (either way): Zero Trust → Access → Applications → **Add an application** →
Self-hosted → name Factotum, session duration 1 month, domain `factotum.nthperson.dev` → Identity
providers: One-time PIN is enough → Add policy: name Owner, action Allow, Include → Emails →
your three addresses → Save. Then App settings → enable "Instant Auth" is optional.

Check: open https://factotum.nthperson.dev on the phone; you get the Access login, then the UI.
The ntfy reminder actions are `view` links into this hostname, so the phone's browser session
with Access is what authorises them.

## 8. Secrets and the service

```bash
cp .env.example .env && nano .env         # NOTION_TOKEN, MAILBIRD_TOKEN, NTFY_TOKEN, CANVAS_TOKEN, IMAP_*, SMTP_* (or leave empty to reuse IMAP), FACTOTUM_SECRET
cp config/factotum.example.yaml config/factotum.yaml
sudo cp deploy/factotum.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now factotum
systemctl status factotum && curl -s http://127.0.0.1:8750/api/stats
journalctl -u factotum -f
```
The unit runs the venv interpreter, as user robert, with `HOME=/home/robert` so headless Claude
runs (phase 3+) inherit the interactive login and the user-scope MCP servers.

## 9. The Plaud trigger and the cutover from plaud-sync (phase 3)

The service watches the `plaud_trigger` mailbox (`IMAP_*` in `.env`, the account under
`sources.accounts` with that role) with IMAP IDLE. A Plaud "AutoFlow" mail starts one sync within
seconds: `plaud search` finds the recording by title, the summary and transcript land under
`data/runs/<id>/`, one headless Claude run reads them, and Python writes the tasks, reminders,
calendar events and the Notion note page. The Log tab shows each run with its cost.

Config: `sources.plaud.sender` (the exact From address, `no-reply@plaud.ai`), `from_filter`
(the IMAP search), `subject_prefix`, `auth_server` (`mx.google.com` for Gmail: the trigger
accepts a mail only when that server's `Authentication-Results` header shows DKIM or DMARC pass
for the sender's domain, so a spoofed From cannot start a run). Project descriptions, tie-breakers
and the note Type/Tag options live under `taxonomy:` and are what the run classifies with.

The service runs with systemd's minimal PATH, so `plaud` (under nvm) and `claude` (`~/.local/bin`)
are invisible to it unless `sources.plaud.cli` and `claude.binary` hold absolute paths (`which plaud`,
`which claude`). Both checks below say whether the service would find them.

Checks:
```bash
.venv/bin/python -m factotum plaud-check                 # the CLI, today's recordings, the mailbox and its unseen count
.venv/bin/python -m factotum sync-file <id> --dry-run    # a real run (~$0.15) on one recording; prints the JSON, writes nothing
.venv/bin/python -m factotum sync-file <id>              # the same, applied: tasks, note page, batch in the Log tab
```

Cutover: with plaud-sync still running on lark-server, both write the Notion note page (idempotent
on Source Link; last writer wins) and only Factotum writes tasks. Once a recording made after the
restart has landed correctly, on lark-server:
```bash
sudo systemctl disable --now plaud-sync
```
Leave no unseen Plaud mail behind: whatever is unseen when plaud-sync stops is processed at
Factotum's next service start, which is what you want. The old `planner@morning.timer`,
`planner@evening.timer` and `plaud-digest.timer` stay until phase 4.

## 10. Mail, Canvas and the planner (phase 4)

`.venv/bin/python -m factotum mail-check` lists Mailbird's accounts against the monitored ones in
Settings → Email accounts (an account Mailbird does not have, such as the Plaud trigger mailbox,
is simply skipped by the sweep). `canvas-check` shows the items in the window and the row each
one maps to. `sweep mail|canvas` runs one sweep by hand; the service runs them every
`sources.mail.sweep_minutes` and `sources.canvas.sweep_minutes`.

The planner runs inside the morning-brief job: `planner --dry-run` prints what it would propose
(a real Claude run, about $0.30); `planner --send` applies and mails the brief now. Its prompt and
result land under `data/runs/planner/<date>/`.

Cutover from plaud-sync (done 2026-11-02 for Robert): on lark-server
`sudo systemctl disable --now planner@morning.timer planner@evening.timer plaud-digest.timer`.
Then delete the Gmail filter on the SDSU account that forwarded mail to the trigger mailbox:
Gmail → Settings → Filters and Blocked Addresses → the forwarding rule → Delete. Nothing reads
that forwarded mail any more; the sweep reads the SDSU inbox through Mailbird directly.

## 11. Chat (phase 5)

Nothing new to install: a chat turn is a headless `claude -p` run whose only tool server is
`.venv/bin/python -m factotum mcp`, started per turn by the service. Check it with
`.venv/bin/python -m factotum chat-check`: it lists the server's tools over a pipe, reaches Mailbird,
then runs one tiny Claude call through the server (about $0.002). A turn from the terminal is
`.venv/bin/python -m factotum chat "what did Max propose?" --attach 388`.

Drafts need Mailbird's **Allow write actions** toggle (Settings → Wingman AI); without it the draft
tools report the refusal to the model and the reply says so. Factotum never sends: a draft the chat
wrote sits in Mailbird's Drafts (or the Quick Reply of its thread) until you send or delete it, and the
Chat tab flags a reply that reaches an address no task names. Prompts and results of every turn are
under `data/runs/chat/<thread>-<message>/`; the cost is on the Log tab.


## 12. Slack (optional, phase 6)

Read-only, several workspaces. For each workspace: https://api.slack.com/apps → **Create New App**
→ From scratch → name "Factotum", pick the workspace → **OAuth & Permissions** → User Token Scopes:
`search:read`, `channels:read`, `channels:history`, `groups:read`, `groups:history`, `im:history`,
`users:read` → **Install to Workspace** → copy the **User OAuth Token** (`xoxp-…`). A bot token
(`xoxb-…`) also works for everything except search. Then:

```yaml
# config/factotum.yaml
sources:
  slack:
    enabled: true
    workspaces:
      - { name: DiCE Lab,   token_env: SLACK_TOKEN_DICE }
      - { name: Car Sounds, token_env: SLACK_TOKEN_CARSOUNDS }
```
and one line per workspace in `.env` (`SLACK_TOKEN_DICE=xoxp-…`). Check with
`.venv/bin/python -m factotum slack-check`: each workspace's team, how many channels the token
sees, and whether search works. Factotum never posts; the client refuses write methods.

**The mention sweep.** With Slack on, every `sources.slack.sweep_minutes` (Settings → Slack) the
joined channels of each workspace are read since the last pass and @-mentions of you become Review
items: one that names `Task-N` flags that task ("Mentioned in Slack"); any other is a card with
Create task / Dismiss, and the task files under the workspace's `project`. Per workspace you can set
`project: DiCE Lab` and `channels: [general, papers]` (otherwise every channel the token's user has
joined, up to `max_channels`). The first pass looks back `lookback_days` (2). Run one now with
`.venv/bin/python -m factotum sweep slack`. The token's user must be a member of a channel for it
to be read; DMs are not read, and a mention made inside a thread is not seen (Slack keeps replies
out of a channel's history).

## 13. Discord (optional, phase 6)

Read-only, one bot for every server. https://discord.com/developers/applications → **New
Application** "Factotum" → **Bot** → Reset Token → copy it to `.env` as `DISCORD_BOT_TOKEN`; on the
same page turn on **Message Content Intent** (without it every message's text is empty). **OAuth2
→ URL Generator**: scope `bot`, permissions *View Channels* and *Read Message History*; open the
URL once per server to invite the bot. Server ids: Discord → User Settings → Advanced → Developer
Mode, then right-click the server → Copy Server ID.

```yaml
sources:
  discord:
    enabled: true
    guilds:
      - { name: LARK dev, id: "123456789012345678" }
```
`.venv/bin/python -m factotum discord-check` names the bot, lists the servers it is in against the
config, and reads one message to confirm the intent is on. Forums and threads need nothing more:
*Read Message History* covers the active threads of every channel the bot can see (forum posts
are threads); archived threads are listed per channel on request.

## 14. The skills and `factotum mcp` from elsewhere (phase 6)

On this machine both skills and the server are already registered:

```bash
ln -sfn /home/robert/factotum/skills/factotum-planner ~/.claude/skills/factotum-planner
ln -sfn /home/robert/factotum/skills/factotum-ops     ~/.claude/skills/factotum-ops
claude mcp add -s user factotum -e PYTHONPATH=/home/robert/factotum -- /home/robert/factotum/.venv/bin/python -m factotum mcp
claude mcp get factotum        # Status: ✔ Connected
```
A skill never writes task state: `stage_changes` records a batch that appears above every tab in
the UI with **Apply** / **Dismiss**.

From another machine, the same server answers `POST /mcp` on the service. Set
`FACTOTUM_MCP_TOKEN` in `.env` (`openssl rand -hex 32`), restart, let `/mcp` through Access (the
box below: a second application scoped to that path, not a policy on the existing one; the bearer
token is the gate), then on the other machine:

> **Letting a path through Access.** A policy has no path field: policies attach to an
> *application*, and an application is a hostname **plus an optional path**. The Factotum
> application you have is the bare hostname, so its policy editor cannot name `/mcp`. Create a
> second application for the path: Zero Trust → Access → Applications → **Add an application** →
> Self-hosted → name `Factotum MCP` → under the application's public hostname enter subdomain
> `factotum`, domain `nthperson.dev` and **Path** `mcp` (the Path box sits to the right of the
> domain; no leading slash) → policy: name `token-gated`, action **Bypass**, include **Everyone**
> → Save. Requests to `factotum.nthperson.dev/mcp` now match the more specific application and
> skip the login; everything else still matches the bare-hostname one. More paths go in the same
> application with **Add public hostname** (one entry per path, same policy), which is what §16
> needs. Check from any machine without an Access session:
>
> ```bash
> curl -si -X POST https://factotum.nthperson.dev/mcp -H 'Content-Type: application/json' -d '{}' | head -3
> ```
> `HTTP/2 401` with a `www-authenticate` line is Factotum answering (the path is through);
> a `302` to `cloudflareaccess.com` means Access still owns it (the path or hostname is wrong,
> or the application has not propagated yet, which takes a minute).

```bash
claude mcp add --transport http factotum https://factotum.nthperson.dev/mcp --header "Authorization: Bearer <token>"
```
By default the route serves reads and `stage_changes`; to allow drafts or Plaud reads from there,
set `server.mcp_http_tools` in the config to a list of tool names (or `all`).

**Another machine (the thinkpad).** Claude Code reads `~/.claude/skills` on *that* machine and its
own `claude mcp` list, so the two registrations above are per machine: clone the repo there (or
copy `skills/factotum-planner` and `skills/factotum-ops`), symlink them into `~/.claude/skills`,
and add the server over HTTP with the bearer token as shown. **Claude Desktop and claude.ai** do
not read `~/.claude/skills` at all: their skills are the ones uploaded under Settings →
Capabilities → Skills (zip each `skills/factotum-*` folder and upload it once; it then shows on
every device signed into the account), and their tools come from a connector — see §16.

## 15. Retiring plaud-sync (the user's clicks)

Everything below is reversible or archival; nothing in Factotum depends on it.

1. **Notion**: the ☀️ Today page and the Command Log database were written by plaud-sync's
   planner and command channel; nothing writes them now. Archive both (page menu → Delete / or
   move under an "Archive" page). The Tasks database stays: it is the mirror. The Plaud notes
   database stays: Factotum writes it.
2. **Skills**: `rm ~/.claude/skills/plaud-sync-planner` (or move it aside) so the two
   `factotum-*` skills are the only ones answering about tasks; in claude.ai, delete the uploaded
   `plaud-sync-planner` skill.
3. **lark-server**: the timers are already disabled (phase 4); `systemctl --user disable --now
   plaud-sync.service` if a service remains, then remove the checkout when nothing else there
   needs it.
4. **GitHub**: `gh repo archive nthPerson/plaud-sync` once Factotum has run a full week without
   anyone touching the old repo.
5. **Mirror**: `.venv/bin/python -m factotum mirror --all` once (about 400 requests, ~3 minutes);
   from then the scheduler keeps Notion current every `sinks.notion_tasks_mirror.sweep_minutes`.

## 16. The claude.ai / Claude Desktop connector (OAuth)

Factotum is its own OAuth server for `/mcp` (SPEC §15). Four steps, each once.

1. **Config.** Turn the connector on in **Settings → Connections**, not in the YAML: the Settings
   tab stores that toggle as an override in the database, and an override beats the file (an
   earlier auto-save of the unticked box leaves `false` there, and a later `enabled: true` in the
   YAML changes nothing). Then in `config/factotum.yaml` under `server.oauth`: `access_team: <team>` (the `<team>` in `https://<team>.cloudflareaccess.com`,
   shown under Zero Trust → Settings → **Team domain**; renaming it there only ends every Access
   session and changes an external IdP's callback URL, so do it before this step) and `access_aud: <tag>` (Zero Trust → Access →
   Applications → the Factotum application → Overview → **Application Audience (AUD) Tag**). The
   consent page refuses to render until both are set: it verifies the signed assertion Access adds
   to every request against the team's certificates, and a plain identity header is never trusted.
   Restart the service (the YAML is read at start; the Settings toggle is live at once).
2. **Add the connector's paths to the `Factotum MCP` application from §14.** No new application:
   open that one (Zero Trust → Access → Applications → Factotum MCP → edit), and in its domain
   section use **Add domain** (the current dashboard lets one self-hosted application list several
   hostname + path entries) to add five more entries, each `factotum.nthperson.dev` with one of
   these paths: `oauth/register`, `oauth/token`, `.well-known/oauth-protected-resource`,
   `.well-known/oauth-protected-resource/mcp`, `.well-known/oauth-authorization-server`. The
   application's existing Bypass / Everyone policy covers them all; the tokens are the gate. Two
   applications is the whole picture: the original Factotum one is the login for the UI and the
   consent page, `Factotum MCP` is everything a token protects, and they cannot be merged because
   a policy applies to the whole application. **Never add `oauth/authorize`**, and never a wildcard
   that covers it: that page is where you approve a connector, and Access is its login. Check:
   `curl -s https://factotum.nthperson.dev/.well-known/oauth-authorization-server` prints JSON with
   `"issuer"` once the connector is enabled; a login page means the path is still behind Access.
3. **Arm, then connect.** In Factotum → Settings → Connections tap **Allow a new connector**
   (10 minutes; both the registration claude.ai performs and your approval need the window, so
   a stranger cannot even register). Then in claude.ai: Settings → Connectors → **Add custom connector** → name
   Factotum, URL `https://factotum.nthperson.dev/mcp`, Authentication "Sign in now", OAuth client
   either **Use Claude's published identity** (recommended; Claude identifies itself by a document
   it hosts on claude.ai and nothing is registered) or **Register automatically** (Claude registers
   a client each time it connects) → Connect. Claude Desktop shares the same connectors. claude.ai
   sends you to the consent page (your Access login, then "Connect claude.ai to Factotum?" with
   how the client identified itself), and you tap Allow. With the published identity the client
   id is public, so every consent, not only the first, needs the arming window. A consent page
   that arrives when you did not just do this is someone else's: Deny.
4. **Check and revoke.** Settings → Connections (or `.venv/bin/python -m factotum connectors`)
   lists every connector, when it last spoke and its live tokens; Revoke kills them all at once.
   The connector gets the same tools as the bearer route (reads and `stage_changes`; drafts and
   Plaud only via `server.mcp_http_tools`), so a chat in claude.ai can read tasks and stage a batch
   for your tray, never write one. Upload the skills (§14) so those chats know the recipes.
