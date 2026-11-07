@AGENTS.md

## Claude Code specifics

- Install Adapty's skills once: `npx adapty skills install` (also adds the Adapty MCP server; sign in via `/mcp`). Full tool setup: `docs/AGENT_TOOLS.md`. They add `/adapty-skills:*` skills for A/B tests, audiences/segments, flows, attribution and docs lookup; prefer them over guessing Adapty behaviour.
- Without the skills plugin, add Adapty's MCP server by hand: `claude mcp add --transport http --scope user adapty https://mcp.adapty.io/mcp`, then `/mcp` → adapty → sign in with **your own** Adapty account.
- This repo's `.mcp.json` declares the Playwright MCP server (approve it when asked); use it for dashboard-only Adapty tasks such as registering custom attributes.
- When a task touches Adapty objects, read `docs/ADAPTY_SETUP.md` first for the IDs, and confirm with the user before anything in rule 3 of AGENTS.md.
- Browser checks: the live dashboard needs the admin password (ask the user to log in; don't put it in commands or files).
