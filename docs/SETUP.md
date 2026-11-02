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
6. **Consent once, from WSL:**
   ```bash
   cd ~/factotum && .venv/bin/python -m factotum google-auth
   ```
   It opens the Windows browser (or prints a URL to paste). Sign in with the calendar account.
   Google shows **"Google hasn't verified this app"** because the app is yours and unreviewed:
   click **Advanced** → **Go to Factotum (unsafe)** → tick the calendar permissions → Continue.
   The browser lands on a "The authentication flow has completed" page; the command writes
   `config/google-token.json` and prints the next few events as a check. If the browser cannot
   reach the localhost page (it happens on some VPNs), run
   `python -m factotum google-auth --manual`, which prints the URL and asks you to paste back the
   address bar of the page you land on, even if that page says the site cannot be reached.
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
