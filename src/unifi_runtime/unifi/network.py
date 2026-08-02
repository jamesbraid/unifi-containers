"""The UniFi Network Application dialect — the classic `/api/...` surface.

Served on 8443 (https) by the standalone controller and on 127.0.0.1:8081
(plain http) by the Network app bundled into UniFi OS; only the base URL
differs. Answers are wrapped: `{"meta": {"rc": "ok"}, "data": [...]}`.
"""

from ..http import DEFAULT_TIMEOUT, json_request

#: The v2 surface, which lags every v1 readiness signal: it 500s for a window
#: after login already works, while zone-based-firewall defaults materialize.
V2_FIREWALL_POLICIES = "/v2/api/site/{site}/firewall-policies"

STAT_DEVICE = "/api/s/{site}/stat/device"


def rc_of(body):
    """The `rc` field, from `meta` or the top level. None if absent."""
    if not isinstance(body, dict):
        return None
    meta = body.get("meta")
    if isinstance(meta, dict) and "rc" in meta:
        return meta.get("rc")
    return body.get("rc")


class NetworkApp:
    def __init__(self, base_url, timeout=DEFAULT_TIMEOUT, cookie_jar=None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        #: Path to persist the session to. Without it every call is anonymous,
        #: which is all the wizard and login probes need.
        self.cookie_jar = cookie_jar

    def _get(self, path):
        return json_request(self.base_url + path, timeout=self.timeout, cookie_jar=self.cookie_jar)

    def _post(self, path, payload):
        return json_request(
            self.base_url + path,
            method="POST",
            payload=payload,
            timeout=self.timeout,
            cookie_jar=self.cookie_jar,
        )

    def is_up(self):
        """The `/status` up flag — measured 5-20s EARLY, so not a readiness signal on its own."""
        body = self._get("/status").json()
        if not isinstance(body, dict):
            return False
        meta = body.get("meta")
        if isinstance(meta, dict) and "up" in meta:
            return bool(meta.get("up"))
        return bool(body.get("up"))

    def login(self, username, password):
        return self._post("/api/login", {"username": username, "password": password})

    def login_ok(self, username, password):
        """True only for a real JSON `rc: ok`. Never loop this: UniFi rate-limits login."""
        return rc_of(self.login(username, password).json()) == "ok"

    def v2_status(self, site="default"):
        """HTTP status of the v2 surface — a code, not a bool, because each means
        something different: 5xx is still materializing, 401 is a dead session,
        404 is a controller old enough to have no v2 at all."""
        return self._get(V2_FIREWALL_POLICIES.format(site=site)).status

    def devices(self, site="default"):
        """The `data` array from stat/device. [] when the answer is not a list —
        an error body must read as "no devices", never as an unknown shape."""
        data = self._get(STAT_DEVICE.format(site=site)).json().get("data")
        return data if isinstance(data, list) else []

    def add_default_admin(self, name, password, email="admin@example.invalid"):
        return self._post(
            "/api/cmd/sitemgr",
            {
                "cmd": "add-default-admin",
                "name": name,
                "email": email,
                "x_password": password,
            },
        )

    def set_super_identity(self, name):
        return self._post("/api/set/setting/super_identity", {"name": name})

    def set_installed(self):
        return self._post("/api/cmd/system", {"cmd": "set-installed"})
