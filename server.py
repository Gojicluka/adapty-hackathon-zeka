"""Referral program on Adapty: demo server. Python standard library only.

Run:  python3 server.py      then open http://<host>:8080
Without ADAPTY_SECRET_KEY in .env it runs in mock mode (no network calls).

Flow: an inviter shares a link -> a friend installs through it (in production: an Adapty
Attribution link carrying the code as deferred_data_sub1) -> on first launch the app claims
the code here -> the app owner's rules run (premium days, attributes) -> Adapty segments on
those attributes pick the paywall each user sees.
"""

import json
import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from adapty_client import CALL_LOG, AdaptyClient

ROOT = Path(__file__).resolve().parent
PRODUCTS = {"monthly": 9.99, "annual": 59.99}  # simulated prices, USD
REFERRED_PAYWALL = "Referral welcome"

TRIGGERS = {
    "install": "Friend installs through the link",
    "first_payment": "Friend makes a first payment",
    "paid_referrals_reached": "Inviter reaches N paid referrals",
}
RECIPIENTS = {"friend": "Friend (new user)", "inviter": "Inviter"}
ACTIONS = {"grant": "Grant premium for N days", "set_attribute": "Set an Adapty attribute"}

DEFAULT_PROGRAM = {
    "rules": [
        {"trigger": "install", "recipient": "friend", "action": "grant", "days": 7},
        {"trigger": "first_payment", "recipient": "inviter", "action": "grant", "days": 14},
        {"trigger": "paid_referrals_reached", "n": 3, "recipient": "inviter", "action": "set_attribute",
         "key": "referral_vip", "value": "yes"},
    ],
    "tiers": [{"name": "bronze", "min_paid": 1}, {"name": "silver", "min_paid": 3}, {"name": "gold", "min_paid": 5}],
    "max_rewarded_referrals": 5,
}


def load_env():
    env = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("ADAPTY_SECRET_KEY", "ADAPTY_PUBLIC_KEY", "ADAPTY_PLACEMENT_ID", "ADAPTY_ACCESS_LEVEL", "PORT",
              "DATA_DIR", "ADMIN_PASSWORD", "APK_URL", "ASSETLINKS_JSON"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


ENV = load_env()
ADAPTY = AdaptyClient(
    secret_key=ENV.get("ADAPTY_SECRET_KEY"),
    public_key=ENV.get("ADAPTY_PUBLIC_KEY"),
    placement_id=ENV.get("ADAPTY_PLACEMENT_ID", "main_paywall"),
    access_level=ENV.get("ADAPTY_ACCESS_LEVEL", "premium"),
    referred_paywall_title=REFERRED_PAYWALL,
)
DATA_DIR = Path(ENV.get("DATA_DIR") or ROOT)
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / ("referrals-mock.db" if ADAPTY.mock else "referrals.db")
ADMIN_PASSWORD = ENV.get("ADMIN_PASSWORD", "")  # empty = no login (local use)
LOCK = threading.RLock()


class Problem(Exception):
    pass


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            email_key TEXT NOT NULL,
            customer_user_id TEXT NOT NULL UNIQUE,
            code TEXT NOT NULL UNIQUE,
            referred_by INTEGER REFERENCES users(id),
            via TEXT,
            created_at TEXT NOT NULL,
            first_paid_at TEXT,
            revenue REAL NOT NULL DEFAULT 0,
            tier TEXT NOT NULL DEFAULT 'none',
            adapty_synced INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS rewards (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            reason TEXT NOT NULL,
            kind TEXT NOT NULL,
            detail TEXT NOT NULL,
            days INTEGER NOT NULL DEFAULT 0,
            granted_at TEXT NOT NULL,
            ok INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS clicks (id INTEGER PRIMARY KEY, code TEXT NOT NULL, channel TEXT, at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS program (id INTEGER PRIMARY KEY CHECK (id = 1), json TEXT NOT NULL);
        """)
        if not c.execute("SELECT 1 FROM program").fetchone():
            c.execute("INSERT INTO program (id, json) VALUES (1, ?)", (json.dumps(DEFAULT_PROGRAM),))
        # Columns added for the phone app; ignore if they already exist.
        for sql in ("ALTER TABLE users ADD COLUMN token TEXT",
                    "ALTER TABLE clicks ADD COLUMN ip TEXT",
                    "ALTER TABLE clicks ADD COLUMN ua TEXT",
                    "ALTER TABLE clicks ADD COLUMN claimed INTEGER NOT NULL DEFAULT 0"):
            try:
                c.execute(sql)
            except sqlite3.OperationalError:
                pass


def event(c, kind, text):
    c.execute("INSERT INTO events (at, kind, text) VALUES (?, ?, ?)", (now(), kind, text))


def get_program():
    with db() as c:
        return json.loads(c.execute("SELECT json FROM program WHERE id = 1").fetchone()[0])


def validate_program(p):
    rules = []
    for r in p.get("rules", []):
        t, who, act = r.get("trigger"), r.get("recipient"), r.get("action")
        if t not in TRIGGERS or who not in RECIPIENTS or act not in ACTIONS:
            raise Problem("Each rule needs a valid trigger, recipient and action.")
        if t == "paid_referrals_reached" and who != "inviter":
            raise Problem("“Inviter reaches N paid referrals” can only reward the inviter.")
        rule = {"trigger": t, "recipient": who, "action": act}
        if t == "paid_referrals_reached":
            rule["n"] = int(r.get("n") or 0)
            if not 1 <= rule["n"] <= 1000:
                raise Problem("N must be between 1 and 1000.")
        if act == "grant":
            rule["days"] = int(r.get("days") or 0)
            if not 1 <= rule["days"] <= 365:
                raise Problem("Premium days must be between 1 and 365.")
        else:
            key, value = str(r.get("key", "")).strip(), str(r.get("value", "")).strip()
            # Adapty limits: key up to 30 chars of [A-Za-z0-9_.-], value up to 50 chars.
            if not re.fullmatch(r"[A-Za-z0-9_.\-]{1,30}", key):
                raise Problem("Attribute keys: 1–30 letters, digits, _ . or -  (Adapty’s limit).")
            if not 1 <= len(value) <= 50:
                raise Problem("Attribute values: 1–50 characters (Adapty’s limit).")
            if key in ("referral_status", "referred_by", "referrals_made", "referrals_paid", "referral_tier"):
                raise Problem(f"“{key}” is set by the referral system itself; choose another key.")
            rule["key"], rule["value"] = key, value
        rules.append(rule)
    tiers = []
    for t in p.get("tiers", []):
        name = str(t.get("name", "")).strip().lower()
        if not re.fullmatch(r"[a-z0-9_-]{1,20}", name):
            raise Problem("Tier names: 1–20 lowercase letters, digits, _ or -.")
        tiers.append({"name": name, "min_paid": max(1, int(t.get("min_paid") or 1))})
    tiers.sort(key=lambda t: t["min_paid"])
    cap = int(p.get("max_rewarded_referrals") or 0)
    if not 1 <= cap <= 1000:
        raise Problem("The reward cap must be between 1 and 1000.")
    return {"rules": rules, "tiers": tiers, "max_rewarded_referrals": cap}


def save_program(p):
    p = validate_program(p)
    with LOCK, db() as c:
        c.execute("UPDATE program SET json = ? WHERE id = 1", (json.dumps(p),))
        event(c, "program", f"App owner updated the program: {len(p['rules'])} rules, {len(p['tiers'])} tiers")
    return p


def email_key(email):
    """Normalise an email so obvious duplicates (case, +tags, Gmail dots) match."""
    email = email.strip().lower()
    local, _, domain = email.partition("@")
    local = local.split("+", 1)[0]
    if domain in ("gmail.com", "googlemail.com"):
        local, domain = local.replace(".", ""), "gmail.com"
    return f"{local}@{domain}"


def new_code(name):
    base = re.sub(r"[^A-Z]", "", name.upper())[:5] or "USER"
    return f"{base}-{secrets.token_hex(2).upper()}"


def user_by(c, **kw):
    (k, v), = kw.items()
    return c.execute(f"SELECT * FROM users WHERE {k} = ?", (v,)).fetchone()


def tier_for(paid, tiers):
    name = "none"
    for t in tiers:
        if paid >= t["min_paid"]:
            name = t["name"]
    return name


# ---------- rules engine ----------

def run_rules(trigger, friend, inviter, program, context=""):
    """Apply the owner's rules for one trigger. Returns a list of what happened."""
    done = []
    for rule in program["rules"]:
        if rule["trigger"] != trigger:
            continue
        target = friend if rule["recipient"] == "friend" else inviter
        if target is None:
            continue
        reason = f"{TRIGGERS[trigger]}{context}"
        if rule["action"] == "grant":
            if rule["recipient"] == "inviter":
                with db() as c:
                    granted = c.execute("SELECT COUNT(*) FROM rewards WHERE user_id = ? AND kind = 'grant'",
                                        (inviter["id"],)).fetchone()[0]
                if granted >= program["max_rewarded_referrals"]:
                    with LOCK, db() as c:
                        event(c, "blocked", f"{inviter['name']} hit the cap of {program['max_rewarded_referrals']} rewards")
                    continue
            ok, g = ADAPTY.grant_access(target["customer_user_id"], rule["days"])
            detail = f"+{rule['days']} days premium"
        else:
            ok, _ = ADAPTY.update_attributes(target["customer_user_id"], {rule["key"]: rule["value"]})
            detail = f"{rule['key']} = {rule['value']}"
        with LOCK, db() as c:
            c.execute("INSERT INTO rewards (user_id, reason, kind, detail, days, granted_at, ok) VALUES (?,?,?,?,?,?,?)",
                      (target["id"], reason, rule["action"], detail, rule.get("days", 0), now(), 1 if ok else 0))
            event(c, "reward", f"{target['name']}: {detail} ({reason})" + ("" if ok else " — Adapty call failed"))
        done.append({"to": target["name"], "detail": detail, "ok": ok})
    return done


def sync_inviter(inviter_id, program):
    """Recompute the inviter's counts and tier and push them to Adapty."""
    with LOCK, db() as c:
        inv = user_by(c, id=inviter_id)
        made = c.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (inviter_id,)).fetchone()[0]
        paid = c.execute("SELECT COUNT(*) FROM users WHERE referred_by = ? AND first_paid_at IS NOT NULL",
                         (inviter_id,)).fetchone()[0]
        tier = tier_for(paid, program["tiers"])
        changed = tier != inv["tier"]
        c.execute("UPDATE users SET tier = ? WHERE id = ?", (tier, inviter_id))
        if changed:
            event(c, "tier", f"{inv['name']} moved to tier “{tier}”")
    attrs = {"referrals_made": made, "referrals_paid": paid, "referral_tier": tier}
    if not inv["referred_by"]:
        attrs["referral_status"] = "referrer"
    ADAPTY.update_attributes(inv["customer_user_id"], attrs)
    return paid


# ---------- actions ----------

def create_user(name, email, code=None, via=None):
    """Sign up. With a code, this is a friend opening the app after installing through a link."""
    name, email, code = name.strip(), email.strip(), (code or "").strip().upper()
    if not name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise Problem("Enter a name and a valid email.")
    key = email_key(email)
    program = get_program()
    with LOCK, db() as c:
        if c.execute("SELECT 1 FROM users WHERE email_key = ?", (key,)).fetchone():
            raise Problem("This email (or a variant of it) already has an account.")
        inviter = None
        if code:
            inviter = user_by(c, code=code)
            if not inviter:
                raise Problem(f"Referral code {code} doesn't exist.")
            if inviter["email_key"] == key:
                raise Problem("You can't refer yourself.")
        cuid = f"refdemo-{secrets.token_hex(4)}"
        user_code = new_code(name)
        token = secrets.token_urlsafe(24)
        cur = c.execute(
            "INSERT INTO users (name, email, email_key, customer_user_id, code, referred_by, via, created_at, token)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (name, email, key, cuid, user_code, inviter["id"] if inviter else None, via, now(), token))
        uid = cur.lastrowid
        event(c, "install", f"{name} installed the app"
              + (f" through {inviter['name']}’s link{f' ({via})' if via else ''}" if inviter else " (organic)"))
        friend = user_by(c, id=uid)

    attrs = {"referral_status": "referred", "referred_by": inviter["code"]} if inviter else {"referral_status": "organic"}
    attrs["referrals_made"] = 0
    ok, _ = ADAPTY.create_profile(cuid, email, attrs)
    with LOCK, db() as c:
        c.execute("UPDATE users SET adapty_synced = ? WHERE id = ?", (1 if ok else 0, uid))
    actions = []
    if inviter:
        actions = run_rules("install", friend, inviter, program, context=f" ({name} via {inviter['name']})")
        sync_inviter(inviter["id"], program)
    p_ok, paywall = ADAPTY.get_paywall(cuid)
    return {"user_id": uid, "code": user_code, "customer_user_id": cuid, "token": token,
            "referred_by": inviter["name"] if inviter else None,
            "adapty_profile_ok": ok, "actions": actions, "paywall": paywall if p_ok else {"error": paywall}}


def purchase(user_id, product):
    if product not in PRODUCTS:
        raise Problem("Unknown product.")
    price, program = PRODUCTS[product], get_program()
    with LOCK, db() as c:
        u = user_by(c, id=user_id)
        if not u:
            raise Problem("No such user.")
        first = u["first_paid_at"] is None
        c.execute("UPDATE users SET revenue = revenue + ?, first_paid_at = COALESCE(first_paid_at, ?) WHERE id = ?",
                  (price, now(), user_id))
        event(c, "purchase", f"{u['name']} bought {product} (${price:.2f}, simulated)")
        friend = user_by(c, id=user_id)
        inviter = user_by(c, id=u["referred_by"]) if u["referred_by"] else None
    actions = []
    if first and inviter:
        actions += run_rules("first_payment", friend, inviter, program, context=f" ({u['name']} paid)")
        paid = sync_inviter(inviter["id"], program)
        for rule in program["rules"]:
            if rule["trigger"] == "paid_referrals_reached" and paid == rule["n"]:
                actions += run_rules("paid_referrals_reached", friend, inviter,
                                     {**program, "rules": [rule]}, context=f" ({paid} paid)")
    return {"first_payment": first, "actions": actions}


def record_click(code, channel):
    with LOCK, db() as c:
        inv = user_by(c, code=code.upper())
        if not inv:
            raise Problem("Unknown code.")
        c.execute("INSERT INTO clicks (code, channel, at) VALUES (?,?,?)", (inv["code"], channel, now()))
        event(c, "share", f"{inv['name']} shared their link via {channel}")
    return {"ok": True}


def adapty_view(user_id):
    with db() as c:
        u = user_by(c, id=user_id)
    if not u:
        raise Problem("No such user.")
    p_ok, profile = ADAPTY.get_profile(u["customer_user_id"])
    w_ok, paywall = ADAPTY.get_paywall(u["customer_user_id"])
    return {"profile": profile if p_ok else {"error": profile}, "paywall": paywall if w_ok else {"error": paywall}}


def state():
    with db() as c:
        users = [dict(r) for r in c.execute(
            "SELECT u.id, u.name, u.email, u.code, u.customer_user_id, u.created_at, u.first_paid_at, u.revenue,"
            " u.tier, u.via, u.adapty_synced, r.name AS referred_by_name,"
            " (SELECT COUNT(*) FROM users x WHERE x.referred_by = u.id) AS invited,"
            " (SELECT COUNT(*) FROM users x WHERE x.referred_by = u.id AND x.first_paid_at IS NOT NULL) AS invited_paid,"
            " (SELECT GROUP_CONCAT(x.name, ', ') FROM users x WHERE x.referred_by = u.id) AS invited_names,"
            " (SELECT COALESCE(SUM(days),0) FROM rewards w WHERE w.user_id = u.id) AS reward_days,"
            " (SELECT COUNT(*) FROM clicks k WHERE k.code = u.code AND k.channel != 'link-open') AS shares,"
            " (SELECT COUNT(*) FROM clicks k WHERE k.code = u.code AND k.channel = 'link-open') AS opens"
            " FROM users u LEFT JOIN users r ON r.id = u.referred_by ORDER BY u.id")]
        rewards = [dict(r) for r in c.execute(
            "SELECT w.*, u.name FROM rewards w JOIN users u ON u.id = w.user_id ORDER BY w.id DESC LIMIT 40")]
        events = [dict(r) for r in c.execute("SELECT * FROM events ORDER BY id DESC LIMIT 40")]
        shares = c.execute("SELECT COUNT(*) FROM clicks WHERE channel != 'link-open'").fetchone()[0]
        opens = c.execute("SELECT COUNT(*) FROM clicks WHERE channel = 'link-open'").fetchone()[0]

    def group(rows):
        n, paid = len(rows), sum(1 for r in rows if r["first_paid_at"])
        rev = sum(r["revenue"] for r in rows)
        return {"users": n, "paid": paid, "conversion": round(100 * paid / n, 1) if n else 0,
                "revenue": round(rev, 2), "revenue_per_user": round(rev / n, 2) if n else 0}

    referred = [u for u in users if u["referred_by_name"]]
    organic = [u for u in users if not u["referred_by_name"]]
    return {
        "mode": "mock" if ADAPTY.mock else "live",
        "placement": ADAPTY.placement_id,
        "program": get_program(),
        "catalog": {"triggers": TRIGGERS, "recipients": RECIPIENTS, "actions": ACTIONS},
        "products": PRODUCTS,
        "users": users,
        "rewards": rewards,
        "events": events,
        "stats": {"referred": group(referred), "organic": group(organic), "shares": shares, "opens": opens,
                  "reward_days": sum(r["days"] for r in rewards)},
        "calls": list(CALL_LOG),
    }


def reset():
    with LOCK, db() as c:
        c.executescript("DELETE FROM rewards; DELETE FROM events; DELETE FROM clicks; DELETE FROM users;")
    CALL_LOG.clear()


# ---------- phone app (one user per device token) ----------

def user_by_token(token):
    if not token:
        raise Problem("Not signed in.")
    with db() as c:
        u = user_by(c, token=token)
    if not u:
        raise Problem("Not signed in.")
    return u


def app_me(token):
    u = user_by_token(token)
    program = get_program()
    with db() as c:
        invited = [dict(r) for r in c.execute(
            "SELECT name, created_at, first_paid_at, via FROM users WHERE referred_by = ? ORDER BY id", (u["id"],))]
        inviter = user_by(c, id=u["referred_by"]) if u["referred_by"] else None
        rewards = [dict(r) for r in c.execute(
            "SELECT detail, reason, granted_at FROM rewards WHERE user_id = ? ORDER BY id DESC", (u["id"],))]
    p_ok, profile = ADAPTY.get_profile(u["customer_user_id"])
    premium_until = None
    if p_ok:
        for a in profile.get("access_levels") or []:
            if a.get("access_level_id") == ADAPTY.access_level and a.get("expires_at"):
                if premium_until is None or a["expires_at"] > premium_until:
                    premium_until = a["expires_at"]
    if premium_until and premium_until.replace("Z", "") < datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"):
        premium_until = None
    friend_reward = next((r["days"] for r in program["rules"]
                          if r["trigger"] == "install" and r["recipient"] == "friend" and r["action"] == "grant"), None)
    inviter_reward = next((r["days"] for r in program["rules"]
                           if r["trigger"] == "first_payment" and r["recipient"] == "inviter" and r["action"] == "grant"), None)
    return {
        "name": u["name"], "email": u["email"], "code": u["code"], "tier": u["tier"],
        "paid": bool(u["first_paid_at"]), "invited_by": inviter["name"] if inviter else None,
        "invited": invited, "rewards": rewards, "premium_until": premium_until,
        "attributes": profile.get("custom_attributes") if p_ok else None,
        "offer": {"friend_days": friend_reward, "inviter_days": inviter_reward},
        "mode": "mock" if ADAPTY.mock else "live",
    }


def app_paywall(token):
    u = user_by_token(token)
    ok, pw = ADAPTY.get_paywall(u["customer_user_id"])
    return pw if ok else {"error": pw}


def record_invite_click(code, ip, ua):
    with LOCK, db() as c:
        inv = user_by(c, code=code.upper())
        if not inv:
            return None
        c.execute("INSERT INTO clicks (code, channel, at, ip, ua) VALUES (?,?,?,?,?)",
                  (inv["code"], "link-open", now(), ip, ua[:200]))
        event(c, "click", f"Someone opened {inv['name']}’s invite link")
        return inv


def claim_ref(ip):
    """Deferred-link fallback: the newest unclaimed invite click from the same IP in the last 2 hours."""
    with db() as c:
        row = c.execute(
            "SELECT k.code, u.name FROM clicks k JOIN users u ON u.code = k.code"
            " WHERE k.ip = ? AND k.channel = 'link-open' AND k.claimed = 0"
            " AND k.at >= datetime('now', '-2 hours') ORDER BY k.id DESC LIMIT 1", (ip,)).fetchone()
    return {"code": row["code"], "inviter": row["name"]} if row else {"code": None}


def ref_info(code):
    with db() as c:
        inv = user_by(c, code=(code or "").upper())
    if not inv:
        return {"code": None}
    program = get_program()
    days = next((r["days"] for r in program["rules"]
                 if r["trigger"] == "install" and r["recipient"] == "friend" and r["action"] == "grant"), None)
    return {"code": inv["code"], "inviter": inv["name"], "friend_days": days}


def mark_claimed(code, ip):
    with LOCK, db() as c:
        c.execute("UPDATE clicks SET claimed = 1 WHERE code = ? AND ip = ? AND channel = 'link-open'", (code, ip))


# ---------- HTTP ----------

STATIC = ROOT / "static"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css",
         ".png": "image/png", ".svg": "image/svg+xml", ".json": "application/json", ".webmanifest": "application/manifest+json",
         ".apk": "application/vnd.android.package-archive"}


def manifest(ref):
    start = "/app" + (f"?ref={ref}" if ref else "")
    return {
        "name": "Lumen: AI Photo Editor", "short_name": "Lumen", "id": "/app",
        "start_url": start, "scope": "/", "display": "standalone",
        "background_color": "#14213d", "theme_color": "#14213d",
        "description": "Demo app for a referral program built on Adapty.",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/static/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "LumenReferrals/1.0"

    def log_message(self, fmt, *args):
        pass

    # --- helpers ---
    def _send(self, code, payload, ctype="application/json", headers=None):
        body = payload if isinstance(payload, bytes) else (
            payload.encode() if isinstance(payload, str) else json.dumps(payload).encode())
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, cache=False):
        if not path.is_file():
            return self._send(404, {"error": "Not found"})
        headers = {"Cache-Control": "public, max-age=3600"} if cache else None
        self._send(200, path.read_bytes(), TYPES.get(path.suffix, "application/octet-stream"), headers)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        if n > 100_000:
            raise Problem("Request too large.")
        try:
            return json.loads(self.rfile.read(n))
        except ValueError:
            raise Problem("Bad JSON.")

    def _ip(self):
        fwd = self.headers.get("X-Forwarded-For", "")
        return (fwd.split(",")[0].strip() if fwd else self.client_address[0])[:64]

    def _cookie(self, name):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return None

    def _token(self):
        return self.headers.get("X-Token", "")

    def _admin_ok(self):
        if not ADMIN_PASSWORD:
            return True
        import base64
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            try:
                _, _, pw = base64.b64decode(auth[6:]).decode().partition(":")
                return secrets.compare_digest(pw, ADMIN_PASSWORD)
            except Exception:
                return False
        return False

    def _need_admin(self):
        if self._admin_ok():
            return False
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Lumen Referrals admin", charset="UTF-8"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def _handle(self, fn):
        try:
            self._send(200, fn())
        except Problem as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # keep the demo alive, show the error
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    # --- routes ---
    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = dict(p.partition("=")[::2] for p in query.split("&") if p)
        if path in ("/", "/index.html"):
            return self._send(302, b"", headers={"Location": "/app"})
        if path == "/healthz":
            return self._send(200, {"ok": True, "mode": "mock" if ADAPTY.mock else "live"})
        if path == "/app":
            ref = re.sub(r"[^A-Z0-9-]", "", unquote(params.get("ref", "")).upper())[:20]
            page = (STATIC / "app.html").read_text()
            if ref:
                page = page.replace('href="/manifest.webmanifest"', f'href="/manifest.webmanifest?ref={ref}"')
            return self._send(200, page, "text/html; charset=utf-8")
        if path == "/sw.js":
            return self._file(STATIC / "sw.js")
        if path == "/manifest.webmanifest":
            ref = re.sub(r"[^A-Z0-9-]", "", unquote(params.get("ref", "")).upper())[:20]
            return self._send(200, manifest(ref), "application/manifest+json")
        if path == "/.well-known/assetlinks.json":
            raw = ENV.get("ASSETLINKS_JSON") or ((STATIC / "assetlinks.json").read_text()
                                                 if (STATIC / "assetlinks.json").exists() else "[]")
            return self._send(200, raw, "application/json")
        if path.startswith("/static/"):
            name = path[len("/static/"):]
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name.endswith(".html"):
                return self._send(404, {"error": "Not found"})
            return self._file(STATIC / name, cache=True)
        if path == "/download/lumen.apk":
            if ENV.get("APK_URL"):
                return self._send(302, b"", headers={"Location": ENV["APK_URL"]})
            return self._file(STATIC / "lumen.apk")
        m = re.fullmatch(r"/r/([A-Za-z0-9-]{1,20})", path)
        if m:
            code = unquote(m.group(1)).upper()
            inv = record_invite_click(code, self._ip(), self.headers.get("User-Agent", ""))
            page = (STATIC / "invite.html").read_text()
            page = (page.replace("{{CODE}}", re.sub(r"[^A-Z0-9-]", "", code))
                        .replace("{{NAME}}", re.sub(r"[<>&\"']", "", inv["name"]) if inv else "A friend")
                        .replace("{{VALID}}", "1" if inv else "0")
                        .replace("{{APK}}", "1" if (ENV.get("APK_URL") or (STATIC / "lumen.apk").exists()) else "0"))
            cookie = f"lumen_ref={code}; Max-Age=86400; Path=/; SameSite=Lax; Secure" if inv else ""
            return self._send(200, page, "text/html; charset=utf-8", {"Set-Cookie": cookie} if cookie else None)
        # phone app API
        if path == "/api/app/me":
            return self._handle(lambda: app_me(self._token()))
        if path == "/api/app/paywall":
            return self._handle(lambda: app_paywall(self._token()))
        if path == "/api/app/ref":
            def find():
                code = re.sub(r"[^A-Z0-9-]", "", unquote(params.get("code", "")).upper()) or self._cookie("lumen_ref")
                if code:
                    info = ref_info(code)
                    if info["code"]:
                        return {**info, "how": "link"}
                found = claim_ref(self._ip())
                return {**ref_info(found["code"]), "how": "matched"} if found["code"] else {"code": None}
            return self._handle(find)
        # admin
        if path in ("/dashboard", "/simulator") or path.startswith("/api/"):
            if self._need_admin():
                return
        if path == "/dashboard":
            return self._file(STATIC / "dashboard.html")
        if path == "/simulator":
            return self._file(STATIC / "index.html")
        if path == "/api/state":
            return self._send(200, state())
        m = re.fullmatch(r"/api/users/(\d+)/adapty", path)
        if m:
            return self._handle(lambda: adapty_view(int(m.group(1))))
        self._send(404, {"error": "Not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        # phone app API (user token, no admin)
        if path == "/api/app/signup":
            def signup():
                b = self._body()
                code = (b.get("ref") or "").strip().upper() or None
                r = create_user(b.get("name", ""), b.get("email", ""), code, b.get("via") or ("Link" if code else None))
                if code:
                    mark_claimed(code, self._ip())
                return {"token": r["token"], "actions": r["actions"], "referred_by": r["referred_by"]}
            return self._handle(signup)
        if path == "/api/app/share":
            return self._handle(lambda: record_click(user_by_token(self._token())["code"],
                                                     str(self._body().get("channel", "share"))[:20]))
        if path == "/api/app/purchase":
            return self._handle(lambda: purchase(user_by_token(self._token())["id"],
                                                 self._body().get("product", "monthly")))
        # admin
        if self._need_admin():
            return
        routes = {
            "/api/users": lambda b: create_user(b.get("name", ""), b.get("email", ""), b.get("code"), b.get("via")),
            "/api/program": lambda b: save_program(b),
            "/api/program/default": lambda b: save_program(DEFAULT_PROGRAM),
            "/api/share": lambda b: record_click(str(b.get("code", "")), str(b.get("channel", "link"))[:20]),
            "/api/reset": lambda b: reset() or {"ok": True},
        }
        if path in routes:
            return self._handle(lambda: routes[path](self._body()))
        m = re.fullmatch(r"/api/users/(\d+)/purchase", path)
        if m:
            return self._handle(lambda: purchase(int(m.group(1)), self._body().get("product", "monthly")))
        self._send(404, {"error": "Not found"})


def main():
    init_db()
    port = int(ENV.get("PORT", 8080))
    print(f"Lumen Referrals on http://0.0.0.0:{port}  mode={'MOCK (no secret key)' if ADAPTY.mock else 'LIVE'}"
          f"  placement={ADAPTY.placement_id}  db={DB_PATH}  admin={'password' if ADMIN_PASSWORD else 'open'}",
          flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
