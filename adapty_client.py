"""Thin client for the Adapty server-side and web APIs.

Runs in mock mode when no secret key is configured, so the demo works offline.
Every call is recorded in CALL_LOG so the dashboard can show what reached Adapty.
"""

import json
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone

API = "https://api.adapty.io/api/v2"
CALL_LOG = deque(maxlen=50)


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class AdaptyClient:
    def __init__(self, secret_key, public_key, placement_id, access_level, referred_paywall_title):
        self.secret_key = secret_key or ""
        self.public_key = public_key or ""
        self.placement_id = placement_id
        self.access_level = access_level
        self.referred_paywall_title = referred_paywall_title
        self.mock = not self.secret_key
        # Mock state, used only when there is no key.
        self._mock_profiles = {}

    # ---------- internals ----------

    def _log(self, method, path, status, note=""):
        CALL_LOG.appendleft({
            "at": _now().strftime("%H:%M:%S"),
            "method": method,
            "path": path,
            "status": status,
            "note": note,
            "mock": self.mock,
        })

    def _request(self, method, path, body=None, user_id=None, key=None):
        url = f"{API}{path}"
        headers = {
            "Authorization": f"Api-Key {key or self.secret_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if user_id:
            headers["adapty-customer-user-id"] = user_id
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode() or "{}"
                self._log(method, path, resp.status, f"{int((time.time() - started) * 1000)} ms")
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

    # ---------- profiles ----------

    def create_profile(self, user_id, email, attrs):
        if self.mock:
            self._mock_profiles[user_id] = {"attrs": dict(attrs), "access": []}
            self._log("POST", "/server-side-api/profile/", 201, f"mock: {user_id}")
            return True, {"mock": True}
        body = {
            "email": email,
            "custom_attributes": self._attrs(attrs),
            "installation_meta": {
                "device_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"lumen-referrals/{user_id}")),
                "device": "Referral demo (web)",
                "locale": "en",
                "os": "Web",
                "platform": "iOS",
                "timezone": "Europe/Belgrade",
                "app_build": "1.0.0",
                "app_version": "1.0.0",
            },
        }
        status, data = self._request("POST", "/server-side-api/profile/", body, user_id=user_id)
        return 200 <= status < 300, data

    def update_attributes(self, user_id, attrs):
        if self.mock:
            self._mock_profiles.setdefault(user_id, {"attrs": {}, "access": []})["attrs"].update(attrs)
            self._log("PATCH", "/server-side-api/profile/", 200, f"mock: {user_id} {attrs}")
            return True, {"mock": True}
        body = {"custom_attributes": self._attrs(attrs)}
        status, data = self._request("PATCH", "/server-side-api/profile/", body, user_id=user_id)
        return 200 <= status < 300, data

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
        base = self._current_expiry(user_id) or starts
        expires = base + timedelta(days=days)
        if self.mock:
            self._mock_profiles.setdefault(user_id, {"attrs": {}, "access": []})["access"] = [
                {"access_level_id": self.access_level, "expires_at": _iso(expires)}]
            self._log("POST", "/server-side-api/purchase/profile/grant/access-level/", 200,
                      f"mock: {user_id} +{days}d → {_iso(expires)}")
            return True, {"expires_at": _iso(expires), "mock": True}
        body = {"access_level_id": self.access_level, "starts_at": _iso(starts), "expires_at": _iso(expires)}
        status, data = self._request(
            "POST", "/server-side-api/purchase/profile/grant/access-level/", body, user_id=user_id)
        return 200 <= status < 300, {"expires_at": _iso(expires), "response": data}

    def get_profile(self, user_id):
        if self.mock:
            self._log("GET", "/server-side-api/profile/", 200, f"mock: {user_id}")
            p = self._mock_profiles.get(user_id, {"attrs": {}, "access": []})
            return True, {"custom_attributes": p["attrs"], "access_levels": p["access"], "mock": True}
        status, data = self._request("GET", "/server-side-api/profile/", user_id=user_id)
        if 200 <= status < 300:
            d = data.get("data", data)
            attrs = {a.get("key"): a.get("value") for a in d.get("custom_attributes") or []}
            levels = [{"access_level_id": a.get("access_level_id"), "expires_at": a.get("expires_at")}
                      for a in d.get("access_levels") or []]
            return True, {"custom_attributes": attrs, "access_levels": levels}
        return False, data

    # ---------- paywall decision (Adapty's targeting) ----------

    def get_paywall(self, user_id):
        """Ask Adapty which paywall this user gets on the placement."""
        if self.mock or not self.public_key:
            p = self._mock_profiles.get(user_id, {"attrs": {}})
            referred = p["attrs"].get("referral_status") == "referred"
            name = self.referred_paywall_title if referred else "Paywall A - monthly first"
            self._log("POST", "/web-api/paywall/", 200, f"mock: {name}")
            return True, {"paywall_name": name, "products": [], "mock": True}
        body = {"store": "app_store", "locale": "en", "placement_id": self.placement_id,
                "customer_user_id": user_id}
        status, data = self._request("POST", "/web-api/paywall/", body, key=self.public_key)
        d = data.get("data", data) if isinstance(data, dict) else {}
        if 200 <= status < 300:
            return True, {"paywall_name": d.get("paywall_name"), "variation_id": d.get("variation_id"),
                          "products": [p.get("title") for p in d.get("products") or []]}
        return False, data
