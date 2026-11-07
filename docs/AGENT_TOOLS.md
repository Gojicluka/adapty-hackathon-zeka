# Tools and MCP servers for agents

Everything below was used to build this project. Each teammate sets these up on their own machine with **their own** logins; nothing here needs shared credentials except where noted.

## 1. Adapty skills (recommended first; also installs the Adapty MCP server)

```bash
npx adapty skills install          # or: npm install -g adapty@latest && adapty skills install
```
Restart the agent afterwards. In **Claude Code** and **Codex** this also registers Adapty's MCP server (Claude Code lists it as `plugin:adapty-skills:adapty`).

Skills that were useful here: `adapty-docs` (look up exact API fields and limits instead of guessing), `ab-tests`, `audiences` (segments, custom-attribute filter syntax `attributes__<key>@string|@float`), `adapty-integration`, `growth-advisor`, `flow-generator`, `paywall-teardown`.

## 2. Adapty MCP server (account access from the agent)

Sign in with **your own** Adapty account (invited to the team company), then pick the sandbox app **"Teodor sandbox (test)"**.

- **Claude Code:** if the skills plugin is installed, run `/mcp` → `plugin:adapty-skills:adapty` → Authenticate. Without the plugin:
  ```bash
  claude mcp add --transport http --scope user adapty https://mcp.adapty.io/mcp
  ```
  then `/mcp` → `adapty` → Authenticate, and start a new session.
- **Codex:**
  ```bash
  codex mcp add adapty --url https://mcp.adapty.io/mcp
  codex mcp login adapty
  ```
  (or add `[mcp_servers.adapty]` / `url = "https://mcp.adapty.io/mcp"` to `~/.codex/config.toml`), then start a new `codex` session.

The MCP server acts with your rights and its changes are real (AGENTS.md rule 3 applies).

## 3. Adapty CLI (what most Adapty setup here was done with)

```bash
npm install -g adapty@latest
adapty auth login            # browser sign-in; check with: adapty auth whoami
```
Commands used: `apps list|get|update`, `products list|create|get`, `paywalls list|create|update`, `placements get|update`, `segments list|get|create`, `ab-tests get|list`, `access-levels list`. The sandbox app ID is in `docs/ADAPTY_SETUP.md`. Add `--json` for machine-readable output.

Gotcha: `adapty apps get --json` prints the app's **secret key**. Don't paste its output into chats, issues or commits.

## 4. Playwright MCP (browser automation)

Used to drive the Adapty dashboard (registering custom attributes, which has no API; checking profiles and segments), Render, and PWABuilder. The browser profile keeps your logins, so sign in once by hand.

- **Claude Code:** this repo's `.mcp.json` declares it; approve it when Claude Code asks. Or:
  ```bash
  claude mcp add playwright -- npx -y @playwright/mcp@latest
  ```
- **Codex:**
  ```bash
  codex mcp add playwright -- npx -y @playwright/mcp@latest
  ```
- On Linux without Google Chrome, add `--executable-path /usr/bin/chromium` (or `--browser chromium`). Add `--user-data-dir <dir>` to keep logins between sessions.

Let the human type passwords and approve OAuth/GitHub permission screens; agents shouldn't read secrets off pages.

## 5. GitHub CLI

`gh` for repo, Actions runs and logs: `gh run list -R dinofied/adapty-hackathon-zeka`, `gh run view <id> --log`, `gh workflow run deploy.yml`. Pushing files under `.github/workflows/` needs SSH remotes or `gh auth refresh -s workflow` (the default `gh` token lacks the `workflow` scope).

## Not available to teammates (owner-only)

- **Render dashboard** (Teodor's account): environment variables, logs, deploy hook. Deploys go through GitHub Actions instead; ask Teodor for env changes or log lines.
- **Android signing key** (Teodor's PC, not in git): only needed to rebuild `static/lumen.apk` with PWABuilder (https://www.pwabuilder.com, package ID `com.lumen.referralsdemo`, reusing the existing key so `assetlinks.json` stays valid). Changes to `/app` reach the APK without a rebuild, since it loads the live site.
