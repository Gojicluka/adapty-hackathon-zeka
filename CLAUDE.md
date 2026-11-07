@AGENTS.md

## Claude Code specifics

- Install Adapty's skills once: `adapty skills install --agent claude-code` (after `npm install -g adapty@latest`). They add `/adapty-skills:*` skills for A/B tests, audiences/segments, flows, attribution and docs lookup; prefer them over guessing Adapty behaviour.
- Optional: connect Adapty's MCP server for this project: `claude mcp add --transport http adapty https://mcp.adapty.io/mcp`, then `/mcp` → adapty → sign in with **your own** Adapty account.
- When a task touches Adapty objects, read `docs/ADAPTY_SETUP.md` first for the IDs, and confirm with the user before anything in rule 3 of AGENTS.md.
- Browser checks: the live dashboard needs the admin password (ask the user to log in; don't put it in commands or files).
