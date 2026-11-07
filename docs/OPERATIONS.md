# Operations: environment, secrets, deploys

## Environment variables

| Variable | Needed for | Where it comes from | Secret? |
|---|---|---|---|
| `ADAPTY_SECRET_KEY` | Live mode (server-side API) | Adapty → app "Teodor sandbox (test)" → App settings → General → Secret key. Every teammate invited to the team's Adapty company can read it there. | **Yes** |
| `ADAPTY_PUBLIC_KEY` | Paywall lookups (web API) | Same page; also in `render.yaml` | No |
| `ADMIN_PASSWORD` | `/dashboard` login (any user name) | Chosen by Teodor; set on Render | **Yes** |
| `ADAPTY_PLACEMENT_ID` | Paywall placement | `main_paywall` | No |
| `ADAPTY_ACCESS_LEVEL` | Reward access level | `premium` | No |
| `WEBHOOK_SECRET` | Optional override of the webhook Authorization value | Leave unset: it's derived from the secret key | Yes, if set |
| `PORT`, `DATA_DIR` | Server port, where the SQLite index goes | Render sets `PORT` | No |

Locally: `cp .env.example .env` and fill in. Without `ADAPTY_SECRET_KEY` the server runs in **mock mode**, which needs nothing.

## Sharing secrets with teammates

- **Don't** paste keys into the repo, issues, PRs, commit messages or group chats; this repo is public.
- **Adapty secret key:** no need to send it. Each teammate opens it in their own Adapty login (above). Keeping it that way means it's never in a chat history.
- **Dashboard password:** send it once, to each person, through a one-time link that expires after reading (Bitwarden Send, 1Password share link, or similar), or tell it in person at the table.
- **Render deploy hook URL:** only goes into GitHub as an Actions secret (below). Teammates never need it.
- Rotate anything that leaks: Adapty secret key in App settings (then update Render and teammates' `.env`), `ADMIN_PASSWORD` in Render → Environment, the deploy hook via "Regenerate" in Render.

## Deploys (no Render login needed)

`.github/workflows/deploy.yml`, on every push to `main`:
1. Compiles the Python files.
2. Starts the server in mock mode and runs `scripts/smoke_test.py` against it.
3. If both pass, calls the Render **deploy hook** stored in the repo secret `RENDER_DEPLOY_HOOK_URL`; Render then pulls `main` from this (public) repo and restarts in about a minute.

Pull requests run steps 1–2 only. If the secret isn't set, step 3 is skipped with a notice.

**One-time setup (repo admin + Render owner):**
1. Render (Teodor): service `lumen-referrals` → Settings → Build & Deploy → Repository → `https://github.com/dinofied/adapty-hackathon-zeka`, branch `main`, Auto-Deploy **off** (deploys come from the hook). Settings → Deploy Hook → copy the URL.
2. GitHub (repo admin): Settings → Secrets and variables → Actions → New repository secret `RENDER_DEPLOY_HOOK_URL` = that URL.

**Side effect of every deploy:** Render's free disk isn't kept, so the demo's index (dashboard users list, invite clicks, activity log) starts empty. Adapty data is untouched; users sign back in with their email and reappear. Avoid deploying in the minutes before the demo.

## Keeping it awake

Render's free plan sleeps after 15 minutes without traffic (first request then takes ~50 s, and the index is wiped). During the event something should hit `/healthz` every 10 minutes (Teodor's PC runs a timer; any uptime pinger works).

## Logs and checks

- `GET /healthz` → `{"ok": true, "mode": "live", "webhook": true}`.
- Render logs (Teodor's account): failed Adapty calls are printed as `Adapty <METHOD> <path> -> <status>: <body>`; webhook problems as `Webhook ... rejected` or `Webhook fallback ...`.
- Dashboard → Overview → "Adapty API calls" shows the last 60 calls with status codes.
- `python3 scripts/smoke_test.py https://lumen-referrals.onrender.com` runs the whole flow live (creates two test profiles and one demo purchase in Adapty, so use sparingly).
