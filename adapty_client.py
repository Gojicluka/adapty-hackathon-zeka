"""Client for Adapty's server-side and web APIs, as used by the referral service.

Runs in mock mode when no secret key is configured, so the demo still works offline.
Every call is recorded in CALL_LOG so the dashboard can show what reached Adapty.
"""

import ipaddress
import json
import secrets
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone

API = "https://api.adapty.io/api/v2"
CALL_LOG = deque(maxlen=60)
PLATFORM = "iOS"
STORE = "app_store"

# The sandbox app's products (store ids as created in Adapty) and their demo prices.
PRODUCTS = {
    "monthly": {"store_product_id": "sandbox.monthly", "price": 9.99, "days": 30, "label": "Monthly"},
    "annual": {"store_product_id": "sandbox.annual", "price": 59.99, "days": 365, "label": "Annual"},
}
BY_STORE_ID = {p["store_product_id"]: k for k, p in PRODUCTS.items()}

# Remote configs the service writes once to the paywalls, so their copy lives in Adapty.
DEFAULT_REMOTE_CONFIGS = {
    "Referral welcome": {"title": "A gift from your friend", "subtitle": "You were invited, so you get our best plan first.",
                         "highlight": "annual", "badge": "Friends price", "cta": "Claim my offer"},
    "Paywall A - monthly first": {"title": "Unlock Premium", "subtitle": "Unlimited AI edits, no watermark, every filter.",
                                  "highlight": "monthly", "badge": "Most popular", "cta": "Continue"},
    "Paywall B - annual first": {"title": "Go Premium for a year", "subtitle": "Our best value: every feature, all year.",
                                 "highlight": "annual", "badge": "Best value", "cta": "Continue"},
}


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def device_uuid(user_id):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"lumen-referrals/{user_id}")).upper()


class AdaptyClient:
    def __init__(self, secret_key, public_key, placement_id, access_level, referred_paywall_title):
        self.secret_key = secret_key or ""
        self.public_key = public_key or ""
        self.placement_id = placement_id
        self.access_level = access_level
        self.referred_paywall_title = referred_paywall_title
        self.mock = not self.secret_key
        self._mock_profiles = {}

    # ---------- internals ----------

    def _log(self, method, path, status, note=""):
        CALL_LOG.appendleft({"at": _now().strftime("%H:%M:%S"), "method": method, "path": path,
                             "status": status, "note": note, "mock": self.mock})

    def _request(self, method, path, body=None, user_id=None, key=None, note=""):
        headers = {"Authorization": f"Api-Key {key or self.secret_key}", "Content-Type": "application/json",
                   "Accept": "application/json"}
        if user_id:
            headers["adapty-customer-user-id"] = user_id
            headers["adapty-platform"] = PLATFORM
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{API}{path}", data=data, method=method, headers=headers)
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode() or "{}"
                self._log(method, path, resp.status, (note + " " if note else "") + f"{int((time.time() - started) * 1000)} ms")
                return resp.status, json.loads(raw)
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            self._log(method, path, e.code, raw[:160])
            print(f"Adapty {method} {path} -> {e.code}: {raw[:400]}", flush=True)
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"raw": raw[:500]}
        except (urllib.error.URLError, TimeoutError) as e:
            self._log(method, path, 0, f"network: {e}")
            print(f"Adapty {method} {path} -> network error: {e}", flush=True)
            return 0, {"error": str(e)}

    @staticmethod
    def _attrs(attrs):
        """Adapty takes string or float values; whole numbers must be sent as floats (3 -> 3.0)."""
        def norm(v):
            if isinstance(v, bool):
                return "yes" if v else "no"
            if isinstance(v, (int, float)):
                return float(v)
            return str(v)
        return [{"key": k, "value": norm(v)} for k, v in attrs.items()]

    @staticmethod
    def _data(d):
        return d.get("data", d) if isinstance(d, dict) else d

    # ---------- profiles ----------

    def create_profile(self, user_id, email, name, attrs, ip=None):
        """Create a full iOS profile. Returns (ok, {"profile_id": ...})."""
        if self.mock:
            pid = str(uuid.uuid4())
            self._mock_profiles[user_id] = {"attrs": dict(attrs), "access": [], "profile_id": pid}
            self._log("POST", "/server-side-api/profile/", 201, f"mock: {user_id}")
            return True, {"profile_id": pid}
        body = {
            "email": email, "first_name": name, "store": STORE,
            "custom_attributes": self._attrs(attrs),
            "installation_meta": {"device_id": device_uuid(user_id), "device": "iPhone", "locale": "en",
                                  "os": "iOS 18", "platform": PLATFORM, "timezone": "Europe/Belgrade",
                                  "app_build": "1", "app_version": "1.0.0"},
        }
        try:
            if ip and ipaddress.ip_address(ip).version == 4 and ipaddress.ip_address(ip).is_global:
                body["ip_v4_address"] = ip
        except ValueError:
            pass
        status, data = self._request("POST", "/server-side-api/profile/", body, user_id=user_id)
        d = self._data(data) if isinstance(data, dict) else {}
        return 200 <= status < 300, {"profile_id": d.get("profile_id") if isinstance(d, dict) else None, "response": data}

    def update_attributes(self, user_id, attrs):
        if self.mock:
            self._mock_profiles.setdefault(user_id, {"attrs": {}, "access": []})["attrs"].update(attrs)
            self._log("PATCH", "/server-side-api/profile/", 200, f"mock: {user_id} {attrs}")
            return True, {"mock": True}
        status, data = self._request("PATCH", "/server-side-api/profile/", {"custom_attributes": self._attrs(attrs)},
                                     user_id=user_id)
        return 200 <= status < 300, data

    def get_profile(self, user_id):
        if self.mock:
            self._log("GET", "/server-side-api/profile/", 200, f"mock: {user_id}")
            p = self._mock_profiles.get(user_id, {"attrs": {}, "access": []})
            return True, {"custom_attributes": p["attrs"], "access_levels": p["access"],
                          "profile_id": p.get("profile_id"), "subscriptions": [], "mock": True}
        status, data = self._request("GET", "/server-side-api/profile/", user_id=user_id)
        if 200 <= status < 300:
            d = self._data(data)
            attrs = {a.get("key"): a.get("value") for a in d.get("custom_attributes") or []}
            levels = [{"access_level_id": a.get("access_level_id"), "expires_at": a.get("expires_at"),
                       "store_product_id": a.get("store_product_id")} for a in d.get("access_levels") or []]
            subs = [{"store_product_id": s.get("store_product_id"), "expires_at": s.get("expires_at")}
                    for s in d.get("subscriptions") or []]
            return True, {"custom_attributes": attrs, "access_levels": levels, "subscriptions": subs,
                          "profile_id": d.get("profile_id"), "total_revenue_usd": d.get("total_revenue_usd")}
        return False, data

    # ---------- rewards ----------

    def _current_expiry(self, user_id):
        """End of the user's active reward access level, so new rewards stack on top of it."""
        ok, prof = self.get_profile(user_id)
        latest = None
        if ok:
            for a in prof.get("access_levels") or []:
                if a.get("access_level_id") != self.access_level or not a.get("expires_at"):
                    continue
                try:
                    exp = datetime.fromisoformat(str(a["expires_at"]).replace("Z", "+00:00"))
                except ValueError:
                    continue
                if exp > _now() and (latest is None or exp > latest):
                    latest = exp
        return latest

    def grant_access(self, user_id, days):
        starts = _now()
        expires = (self._current_expiry(user_id) or starts) + timedelta(days=days)
        if self.mock:
            self._mock_profiles.setdefault(user_id, {"attrs": {}, "access": []})["access"] = [
                {"access_level_id": self.access_level, "expires_at": _iso(expires)}]
            self._log("POST", "/server-side-api/purchase/profile/grant/access-level/", 200, f"mock: +{days}d")
            return True, {"expires_at": _iso(expires), "mock": True}
        body = {"access_level_id": self.access_level, "starts_at": _iso(starts), "expires_at": _iso(expires)}
        status, data = self._request("POST", "/server-side-api/purchase/profile/grant/access-level/", body,
                                     user_id=user_id, note=f"+{days}d")
        return 200 <= status < 300, {"expires_at": _iso(expires), "response": data}

    # ---------- purchases ----------

    def set_transaction(self, user_id, product_key, variation_id=None, country="RS"):
        """Record a subscription purchase in Adapty: it shows in analytics, the event feed and the profile,
        and unlocks the product's access level."""
        p = PRODUCTS[product_key]
        now = _now()
        tx_id = f"9{int(now.timestamp() * 1000)}{secrets.randbelow(1000):03d}"
        body = {
            "purchase_type": "subscription", "store": STORE, "environment": "Production",
            "store_product_id": p["store_product_id"], "store_transaction_id": tx_id,
            "store_original_transaction_id": tx_id, "is_family_shared": False,
            "price": {"country": country, "currency": "USD", "value": p["price"]},
            "purchased_at": _iso(now), "originally_purchased_at": _iso(now),
            "expires_at": _iso(now + timedelta(days=p["days"])), "renew_status": True,
            "variation_id": variation_id, "offer": None, "refunded_at": None, "cancellation_reason": None,
        }
        if self.mock:
            self._mock_profiles.setdefault(user_id, {"attrs": {}, "access": []})["access"].append(
                {"access_level_id": self.access_level, "expires_at": body["expires_at"]})
            self._log("POST", "/server-side-api/purchase/set/transaction/", 200, f"mock: {product_key}")
            return True, {"mock": True}
        status, data = self._request("POST", "/server-side-api/purchase/set/transaction/", body, user_id=user_id,
                                     note=f"{product_key} ${p['price']}")
        return 200 <= status < 300, data

    # ---------- paywalls (Adapty decides) ----------

    def get_paywall(self, user_id):
        """Ask Adapty which paywall this user gets on the placement, with its remote config."""
        if self.mock or not self.public_key:
            p = self._mock_profiles.get(user_id, {"attrs": {}})
            name = self.referred_paywall_title if p["attrs"].get("referral_status") == "referred" else "Paywall A - monthly first"
            self._log("POST", "/web-api/paywall/", 200, f"mock: {name}")
            return True, {"paywall_name": name, "variation_id": None, "remote_config": DEFAULT_REMOTE_CONFIGS.get(name, {}),
                          "products": [{"key": k, **v} for k, v in PRODUCTS.items()], "mock": True}
        body = {"store": STORE, "locale": "en", "placement_id": self.placement_id, "customer_user_id": user_id}
        status, data = self._request("POST", "/web-api/paywall/", body, key=self.public_key)
        if not 200 <= status < 300:
            return False, data
        d = self._data(data)
        rc = d.get("remote_config") or {}
        rc_data = rc.get("data") if isinstance(rc, dict) else None
        if isinstance(rc_data, str):
            try:
                rc_data = json.loads(rc_data)
            except ValueError:
                rc_data = {}
        products = []
        for prod in d.get("products") or []:
            key = BY_STORE_ID.get(prod.get("vendor_product_id"))
            if key:
                products.append({"key": key, **PRODUCTS[key], "title": prod.get("title")})
        return True, {"paywall_name": d.get("paywall_name"), "paywall_id": d.get("paywall_id"),
                      "variation_id": d.get("variation_id"), "ab_test_name": d.get("ab_test_name"),
                      "remote_config": rc_data or {}, "products": products}

    def record_paywall_view(self, user_id, variation_id):
        """Log a paywall view so Adapty's paywall and placement metrics count it."""
        if self.mock or not self.public_key or not variation_id:
            self._log("POST", "/web-api/paywall/visit/", 201, "mock")
            return True
        body = {"visited_at": _iso(_now()), "store": STORE, "variation_id": variation_id, "customer_user_id": user_id}
        status, _ = self._request("POST", "/web-api/paywall/visit/", body, key=self.public_key)
        return 200 <= status < 300

    # ---------- one-time setup: paywall copy lives in Adapty ----------

    def ensure_remote_configs(self):
        """Give each known paywall a remote config if it has none, so its copy is edited in Adapty."""
        if self.mock:
            return []
        status, data = self._request("GET", "/server-side-api/paywalls/?page%5Bsize%5D=100")
        if not 200 <= status < 300:
            return [f"could not list paywalls ({status})"]
        items = data.get("data", []) if isinstance(data, dict) else []
        done = []
        for pw in items:
            title, pid = pw.get("title"), pw.get("paywall_id")
            if title not in DEFAULT_REMOTE_CONFIGS or not pid:
                continue
            s, full = self._request("GET", f"/server-side-api/paywalls/{pid}/")
            full = self._data(full) if isinstance(full, dict) else {}
            if 200 <= s < 300 and full.get("remote_configs"):
                done.append(f"{title}: kept its remote config")
                continue
            body = {"remote_configs": [{"locale": "en", "data": json.dumps(DEFAULT_REMOTE_CONFIGS[title])}]}
            s, _ = self._request("PUT", f"/server-side-api/paywalls/{pid}/", body, note=title)
            done.append(f"{title}: remote config {'set' if 200 <= s < 300 else f'failed ({s})'}")
        return done
