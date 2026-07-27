"""The ULP identity service on 127.0.0.1:9080 — owner identity and API keys.

ULP trusts localhost, so everything here works with no session, cookie or CSRF
token. The key value cannot be chosen — ULP ignores a supplied `full_api_key`
and the store is opaque — so the published *path* is the contract.
"""

from ..http import DEFAULT_TIMEOUT, json_request


def _data(response):
    body = response.json()
    return body.get("data") if isinstance(body, dict) else None


class Ulp:
    def __init__(self, base_url="http://127.0.0.1:9080", timeout=DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def info(self):
        return json_request(self.base_url + "/api/v2/info", timeout=self.timeout)

    def owner_id(self):
        """The owner's UUID. ULP lags /api/setup, so None means "not yet", not "never"."""
        data = _data(self.info())
        if not isinstance(data, dict):
            return None
        owner = data.get("owner")
        if not isinstance(owner, dict):
            return None
        return owner.get("unique_id") or None

    def is_setup(self):
        """Whether first-run setup completed — a second opinion that needs no authentication."""
        data = _data(self.info())
        return bool(data.get("is_setuped")) if isinstance(data, dict) else False

    def mint_key(self, owner_id, name, timeout=15):
        """Mint an admin-scope API key; the plaintext value is returned once, or None."""
        response = json_request(
            f"{self.base_url}/api/v2/user/{owner_id}/keys",
            method="POST",
            payload={"name": name},
            timeout=timeout,
        )
        data = _data(response)
        if not isinstance(data, dict):
            return None
        return data.get("full_api_key") or None
