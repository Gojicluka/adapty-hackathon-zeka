# Referral programs on Adapty

A user taps **Refer a friend**, the phone's share sheet sends a link, and the friend installs through it. Both users get referral data in Adapty, the **app owner decides the rewards**, and Adapty segments on that data so referred users, top referrers and tiers can each get their own paywall.

Adapty has no referral feature, but its docs describe the grant-access endpoint as handy "if you have bonuses for referrals". This project adds the missing layer: links, attribution of the install, an owner-defined rule engine, abuse limits and reporting.

## How it fits Adapty

| Step | Adapty feature |
|---|---|
| Friend installs through the link | **Adapty Attribution deferred deep link**: the link carries the code as `deferred_data_sub1`; on first launch the SDK returns it (`onInstallationDetailsSuccess`) and the app claims it here |
| Rewards | Server-side API **grant access level**; rewards stack on the current end date |
| Data on both users | **Custom attributes**: friend gets `referral_status = referred`, `referred_by`; inviter gets `referral_status = referrer`, `referrals_made`, `referrals_paid`, `referral_tier`, plus any attribute the owner's rules set |
| Different paywalls | **Segments** on those attributes, mapped to paywalls on a **placement** |
| Check what a user sees | **Web API get paywall** for that user |

Adapty attributes hold at most 50 characters and 30 per user, so the full list of who invited whom lives in this service; Adapty gets counts, tiers and flags.

## The app owner's program

Configured on the **App owner** tab, applied immediately:

- **Rules:** *when* (friend installs through the link · friend's first payment · inviter reaches N paid referrals) → *who* (friend or inviter) → *what* (premium for N days, or set an attribute).
- **Tiers** by paid referrals, sent as `referral_tier`.
- **Reward cap** per inviter. Always on: no self-referral, one account per email (case, `+tags` and Gmail dots count as the same).

Defaults: friend gets 7 days on install, inviter gets 14 days when the friend first pays, `referral_vip = yes` at 3 paid referrals, tiers bronze (1) / silver (3) / gold (5), cap 5.

## What's where

| URL | For | Login |
|---|---|---|
| `/app` | The phone app: installable to the home screen, real share sheet over HTTPS | none (each phone gets its own account) |
| `/r/<CODE>` | Invite link: install instructions per platform, passes the code into the app | none |
| `/dashboard` | App owner's view, styled as a proposed **Referrals** section of the Adapty dashboard | `ADMIN_PASSWORD` |
| `/simulator` | Two simulated phones on one screen, the fallback demo | `ADMIN_PASSWORD` |

How the invite code reaches the installed app, in order: `?ref=` on the app URL (the install manifest carries it, so a home-screen app opens with it), a cookie set by the invite page, then a match on the network address of a recent invite click (what attribution tools do when nothing else survives the install).

## Deploy on Render

1. Push this folder to a GitHub repo.
2. In Render: **New > Blueprint**, pick the repo. `render.yaml` sets everything up.
3. Fill in `ADAPTY_SECRET_KEY` (sandbox app only) and `ADMIN_PASSWORD` when asked.

The free plan sleeps after 15 minutes without visits and its disk is not kept, so the demo's data (users, referrals, rules) resets on sleep or redeploy. Something has to visit `/healthz` every 10 minutes during the event to keep it awake. Adapty profiles are not affected.

## Run it

Needs Python 3.9+ and nothing else.

```bash
cp .env.example .env     # fill in the keys
python3 server.py        # http://localhost:8080
```

- **Without `ADAPTY_SECRET_KEY`**: **mock mode**, no network calls, same screens. The fallback if venue Wi-Fi fails.
- **With the key**: **live** against that Adapty app. Use a sandbox app, never production.
- Listens on `0.0.0.0`; a laptop on the same Tailscale network opens `http://<pc-ip>:8080`. Invite links are `/r/<CODE>` and work in any browser.

### Adapty setup for live mode (once, in the sandbox app)

1. Segment **Referred users**: `attributes__referral_status@string` IN `referred`.
2. Paywall **Referral welcome**.
3. Placement `main_paywall`: *Referred users → Referral welcome* above *All users*.

More segments to try: `attributes__referral_tier@string` IN `gold`, `attributes__referrals_paid@float` ≥ 3.

## Demo script (about 3 minutes)

1. **App owner tab:** show the rules. Change the friend's reward to 10 days and save.
2. **Phones tab:** create **Ana** (organic). Tap **Refer a friend**, choose **WhatsApp**.
3. On the friend's phone: open the message, tap the link, **Get**, **Open**. The code is applied automatically. Sign up as **Mila**: she gets 10 days, and Adapty serves her **Referral welcome**.
4. Tap **Buy** on Mila: Ana earns 14 days. Repeat with a second friend: Ana's tier rises and her premium time stacks.
5. **In Adapty** on the dashboard shows each real profile: attributes and access levels.
6. **Dashboard:** who referred whom, referred vs organic conversion, premium days granted, and every Adapty API call.

## Honest limits

- **The phones are simulated** in one page. A real share sheet needs the native app (or an HTTPS page); the real link page `/r/<CODE>` works in any browser.
- **Purchases are simulated**, so revenue is illustrative.
- **Segments may update with a delay** after an attribute changes; press **In Adapty** again to recheck the paywall.
- **Reset demo** clears local data only; profiles created in live mode stay in Adapty.

## Built during the hackathon

All code in this folder was written on 7 November 2026, during the event. Nothing pre-existing is reused. Research notes made before the event are kept separately and are not part of this project.
