# AI log

Team Zeka, Adapty Hackathon Belgrade, 7–8 November 2026. The code in this repo was built with AI coding agents. People decided what to build, set up the guardrails, and checked the results.

## Tools and what we used them for

| Tool | Used for |
|---|---|
| **Claude Code** (CLI and VS Code) and **Codex** | Writing the server, Adapty client, phone app, dashboard, docs and tests; debugging against live Adapty |
| **GitHub Copilot** | Repo bootstrap (initial commit, `.gitignore`) |
| **Adapty skills + Adapty MCP server** | Looking up exact API fields and limits (`adapty-docs`), segment filter syntax (`audiences`), A/B tests, paywalls |
| **Adapty CLI** | Most of the Adapty setup: products, paywalls, placements, segments; reading app config without opening the dashboard |
| **Playwright MCP** | Dashboard-only tasks with no API (registering custom attributes), checking profiles and segments, PWABuilder, Render |
| **GitHub CLI** | Actions runs and logs, pushing branches |

## Setup

- **Rules files.** [`AGENTS.md`](AGENTS.md) is the single source for every agent; [`CLAUDE.md`](CLAUDE.md) imports it and adds Claude Code notes. It holds the one design rule (Adapty is the system of record), a file map, run and test commands, and eight hard rules: never commit secrets (public repo), sandbox app only, ask a human before irreversible Adapty actions, don't change the ID scheme.
- **Context docs.** `docs/ARCHITECTURE.md`, `ADAPTY_SETUP.md` (all IDs), `KNOWN_ISSUES.md` (agents must read it before starting a feature), `AGENT_TOOLS.md` (how each teammate installs the same tools).
- **Learned quirks go back into the rules.** Every Adapty surprise became a line in AGENTS.md rule 4, so the next agent session doesn't repeat it.
- **MCP:** Adapty (each person signs in with their own account) and Playwright, declared in `.mcp.json`.
- **Subagents:** not used. Tasks were sequential and small; one agent with good context was faster than splitting.
- **Checks:** CI runs a compile step, 38 unit tests and an end-to-end smoke test on every push; deploys only happen if all pass.

## Prompts that worked well

1. *"Is this statement true for this: 'Adapty records Referral as the friend's acquisition channel… the app never stores referral data itself.'"* Pointing the agent at a specific claim made it check the code instead of agreeing. It found three overstatements, including the SQLite index the pitch didn't mention, and proposed accurate wording.
2. *"Why don't users I create on the Lumen app appear?"* The agent didn't guess. It checked the local index, worked out the customer user ID the same way `server.py` does, and asked Adapty's API directly. The profile existed, with all its referral attributes; Adapty's Profiles list just lags behind.
3. *"Add missing tests"*, followed by *"break the code on purpose and show the tests catch it"*. The second prompt is the one that matters; see below.

## What AI got wrong, and how we caught it

- **Tests that couldn't fail.** The first test suite passed 38 of 38 on its first run. We didn't trust that, so we had the agent copy the code to a scratch folder, plant seven typical bugs one at a time (no once-only payout guard, no self-referral check, no reward cap, webhook auth off, …) and rerun the suite. Six of the seven were caught. The milestone test stopped at exactly three paid referrals, so a rule that fired again on every later payment still passed. We extended the test; now all seven are caught.
- **Code that only ran on Linux.** `server.py` read its HTML with `read_text()` and no encoding. CI (Ubuntu) and Render passed; on a teammate's Windows laptop `/app` returned an empty reply. We caught it by running the app ourselves rather than trusting green CI, and fixed it by adding `encoding="utf-8"`.
- **Guessed API shapes.** Early on, the first Adapty client sent whole numbers as ints and a non-UUID device ID; Adapty rejected the whole profile write each time. Logging every failed Adapty call with its response body made these visible within minutes. Both are now rules in AGENTS.md and covered by tests.

## What we would never delegate to AI

- **Secrets.** Agents never read, print or paste keys and passwords. The sandbox key goes into `.env` straight from the Adapty CLI without ever being shown, and people type passwords themselves.
- **Irreversible Adapty changes.** Stopping an A/B test, deleting segments, paywalls or profiles, changing the live placement or the webhook. An agent proposes the change; a person runs it.
- **What counts as a reward and who gets it.** Rewards are real access and real money for the app owner; the rules are a product decision.
- **Deciding the work is done.** Green tests written by the same agent aren't proof. A person runs the demo end to end on a real phone before calling it finished.
