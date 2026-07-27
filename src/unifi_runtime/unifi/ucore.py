"""The UniFi OS `unifi-core` dialect on :443 — the UOS-native surface.

`/api/setup` creates a real Owner admin with no cloud and no SSO. This dialect
does not own X-API-KEY: unifi-core only reads the header and delegates
verification, so minting lives in `ulp`.
"""

from ..http import DEFAULT_TIMEOUT, json_request

#: The classic dialect proxied through UOS — what a production client calls.
CLASSIC_PROXY = "/proxy/network/api/s/{site}/stat/device"


class Ucore:
    def __init__(self, base_url="https://127.0.0.1", timeout=DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _url(self, path):
        return self.base_url + path

    def system(self):
        """GET /api/system — answers real JSON once the API is live."""
        return json_request(self._url("/api/system"), timeout=self.timeout)

    def is_api_answering(self):
        """True once /api/system returns its real payload; an empty 200 from nginx must not pass."""
        return "isSetup" in self.system().json()

    def setup(self, name, username, password, country=840, timezone="UTC", timeout=90):
        """POST /api/setup — creates the Owner. `country` is ISO-3166 numeric; takes ~25-31s."""
        return json_request(
            self._url("/api/setup"),
            method="POST",
            payload={
                "name": name,
                "username": username,
                "password": password,
                "country": country,
                "timezone": timezone,
                "updateFirmware": False,
                "sendDiagnostics": False,
            },
            timeout=timeout,
        )

    def login_status(self, username, password, timeout=8):
        """HTTP status of a session login; 0 when the request never landed.

        A code, not a bool: 429 is rate limiting, not evidence the owner is missing.
        """
        return json_request(
            self._url("/api/auth/login"),
            method="POST",
            payload={"username": username, "password": password},
            timeout=timeout,
        ).status

    def api_key_status(self, api_key, path=None, site="default", timeout=8):
        """HTTP status of an X-API-KEY request against the proxied dialect."""
        target = path or CLASSIC_PROXY.format(site=site)
        return json_request(
            self._url(target), headers={"X-API-KEY": api_key}, timeout=timeout
        ).status
