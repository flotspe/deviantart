import time
from dataclasses import dataclass
import requests
from token_store import get_refresh_token, save_refresh_token, refresh_lock

API_BASE = "https://www.deviantart.com/api/v1/oauth2"
OAUTH_TOKEN_URL = "https://www.deviantart.com/oauth2/token"

@dataclass
class OAuthConfig:
    client_id: str
    client_secret: str
    refresh_token: str

class DeviantArtClient:
    def __init__(
        self,
        oauth: OAuthConfig,
        *,
        user_agent: str = "featured-sync/1.0",
        request_timeout_s: int = 30,
        min_delay_s: float = 0.35,
        max_retries: int = 6,
    ) -> None:
        self.oauth = oauth
        self.user_agent = user_agent
        self.request_timeout_s = request_timeout_s
        self.min_delay_s = min_delay_s
        self.max_retries = max_retries

        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

        self._session = requests.Session()
        self._session.headers.update({"User-Agent": self.user_agent})

    def _refresh_access_token(self) -> str:
        with refresh_lock():
            self.oauth.refresh_token = get_refresh_token(self.oauth.refresh_token)

            data = {
                "grant_type": "refresh_token",
                "client_id": self.oauth.client_id,
                "client_secret": self.oauth.client_secret,
                "refresh_token": self.oauth.refresh_token.strip(),
            }

            r = self._session.post(
                OAUTH_TOKEN_URL,
                data=data,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                timeout=self.request_timeout_s,
            )

            if not r.ok:
                raise RuntimeError(f"Token refresh failed ({r.status_code}): {r.text}")

            payload = r.json()

            access_token = payload.get("access_token")
            expires_in = int(payload.get("expires_in", 3600))
            if not access_token:
                raise RuntimeError(f"Missing access_token in response: {payload}")

            new_refresh = payload.get("refresh_token")
            if new_refresh and new_refresh.strip():
                if new_refresh.strip() != self.oauth.refresh_token.strip():
                    save_refresh_token(new_refresh)
                    self.oauth.refresh_token = new_refresh.strip()
            else:
                save_refresh_token(self.oauth.refresh_token)

            self._access_token = access_token
            self._token_expires_at = time.time() + expires_in - 60
            return access_token

    def _get_access_token(self) -> str:
        if not self._access_token or time.time() >= self._token_expires_at:
            return self._refresh_access_token()
        return self._access_token

    def request(self, method: str, path: str, *, params: dict | None = None, data: dict | None = None) -> dict:
        token = self._get_access_token()

        # DeviantArt API examples commonly pass access_token as a parameter; both styles are typically accepted.
        # We use access_token param to match their console examples.
        params = dict(params or {})
        params["access_token"] = token

        url = f"{API_BASE}{path}"

        backoff = self.min_delay_s
        for attempt in range(1, self.max_retries + 1):
            try:
                r = self._session.request(
                    method=method.upper(),
                    url=url,
                    params=params,
                    data=data,
                    timeout=self.request_timeout_s,
                )

                # Token expired mid-run → refresh once and retry
                if r.status_code == 401:
                    time.sleep(self.min_delay_s)
                    self._refresh_access_token()
                    params["access_token"] = self._access_token  # type: ignore[assignment]
                    r = self._session.request(
                        method=method.upper(),
                        url=url,
                        params=params,
                        data=data,
                        timeout=self.request_timeout_s,
                    )

                # Handle rate limiting / transient failures with backoff
                if r.status_code in (429, 500, 502, 503, 504):
                    if attempt >= self.max_retries:
                        r.raise_for_status()
                    time.sleep(backoff)
                    backoff *= 2
                    continue

                r.raise_for_status()
                return r.json()

            except requests.RequestException:
                if attempt >= self.max_retries:
                    raise
                time.sleep(backoff)
                backoff *= 2

        raise RuntimeError("Unreachable")
