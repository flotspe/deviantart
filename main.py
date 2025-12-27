#!/usr/bin/env python3
"""
Sync DeviantArt Featured gallery to your top N most-favourited deviations.

Behavior:
- Identify "Featured" folderid
- Remove all existing deviations from Featured
- Compute top N deviations by stats.favourites across your gallery folders
- Copy those top N into Featured

Notes:
- DeviantArt gallery folder modification endpoints require scopes: browse + gallery. :contentReference[oaicite:6]{index=6}
- OAuth2 token refresh uses https://www.deviantart.com/oauth2/token :contentReference[oaicite:7]{index=7}
- /gallery/{folderid} supports pagination (offset/limit) and returns deviation objects with stats. :contentReference[oaicite:8]{index=8}
"""

from __future__ import annotations
from dotenv import load_dotenv
from token_store import get_refresh_token, save_refresh_token, refresh_lock

import os
import sys
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import requests

API_BASE = "https://www.deviantart.com/api/v1/oauth2"
OAUTH_TOKEN_URL = "https://www.deviantart.com/oauth2/token"

# API constraints in docs for deviationids arrays: max 24 per request for copy/remove endpoints. :contentReference[oaicite:9]{index=9}
MAX_DEVIATIONIDS_PER_MUTATION = 24


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

        self._access_token: Optional[str] = None
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

    def _request(self, method: str, path: str, *, params: dict | None = None, data: dict | None = None) -> dict:
        token = self._get_access_token()

        # DeviantArt API examples commonly pass access_token as a parameter; both styles are typically accepted.
        # We use access_token param to match their console examples. :contentReference[oaicite:11]{index=11}
        params = dict(params or {})
        params["access_token"] = token

        url = f"{API_BASE}{path}"

        backoff = 1.0
        for attempt in range(1, self.max_retries + 1):
            try:
                time.sleep(self.min_delay_s)

                r = self._session.request(
                    method=method.upper(),
                    url=url,
                    params=params,
                    data=data,
                    timeout=self.request_timeout_s,
                )

                # Token expired mid-run → refresh once and retry
                if r.status_code == 401:
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
                    if attempt == self.max_retries:
                        r.raise_for_status()
                    time.sleep(backoff)
                    backoff *= 2
                    continue

                r.raise_for_status()
                return r.json()

            except requests.RequestException as e:
                if attempt == self.max_retries:
                    raise
                time.sleep(backoff)
                backoff *= 2

        raise RuntimeError("Unreachable")

    # --- API wrappers ---

    def list_gallery_folders(self, *, calculate_size: bool = True, offset: int = 0, limit: int = 50) -> dict:
        # GET /gallery/folders :contentReference[oaicite:12]{index=12}
        return self._request(
            "GET",
            "/gallery/folders",
            params={
                "calculate_size": "1" if calculate_size else "0",
                "offset": offset,
                "limit": limit,
            },
        )

    def get_gallery_folder_contents(self, folderid: str, *, offset: int = 0, limit: int = 24) -> dict:
        # GET /gallery/{folderid} :contentReference[oaicite:13]{index=13}
        return self._request(
            "GET",
            f"/gallery/{folderid}",
            params={"offset": offset, "limit": limit},
        )

    def remove_deviations_from_folder(self, folderid: str, deviationids: List[str]) -> dict:
        # POST /gallery/folders/remove_deviations :contentReference[oaicite:14]{index=14}
        return self._request(
            "POST",
            "/gallery/folders/remove_deviations",
            params={"folderid": folderid},
            data={"deviationids[]": deviationids},
        )

    def copy_deviations_to_folder(self, target_folderid: str, deviationids: List[str]) -> dict:
        # POST /gallery/folders/copy_deviations :contentReference[oaicite:15]{index=15}
        return self._request(
            "POST",
            "/gallery/folders/copy_deviations",
            data={
                "target_folderid": target_folderid,
                "deviationids[]": deviationids},
        )


def chunked(items: List[str], n: int) -> Iterable[List[str]]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


def find_featured_folderid(folders: List[dict]) -> str:
    # The folder is typically named "Featured" and is parent=null. :contentReference[oaicite:16]{index=16}
    for f in folders:
        if (f.get("name") or "").strip().lower() == "featured" and f.get("parent") is None:
            fid = f.get("folderid")
            if fid:
                return fid
    # Fallback: any folder named Featured
    for f in folders:
        if (f.get("name") or "").strip().lower() == "featured":
            fid = f.get("folderid")
            if fid:
                return fid
    raise RuntimeError('Could not locate a "Featured" folder in /gallery/folders response.')

def find_folderid(folder_name: str, folders: List[dict]) -> str:
    for f in folders:
        print(f"is {f.get("name", "")} equal to {folder_name}?")
        if (f.get("name") or "").strip().lower() == folder_name.lower():
            fid = f.get("folderid")
            if fid:
                return fid
    raise RuntimeError(f'Could not locate a "{folder_name}" folder in /gallery/folders response.')


def fetch_all_folders(client: DeviantArtClient) -> List[dict]:
    all_folders: List[dict] = []
    offset = 0
    while True:
        page = client.list_gallery_folders(calculate_size=True, offset=offset, limit=50)
        results = page.get("results", [])
        all_folders.extend(results)
        if not page.get("has_more"):
            break
        next_offset = page.get("next_offset")
        if next_offset is None:
            break
        offset = int(next_offset)
    return all_folders


def fetch_all_deviations_across_folders(
    client: DeviantArtClient,
    folderids: List[str],
    *,
    per_folder_limit_cap: Optional[int] = None,
) -> Dict[str, int]:
    """
    Returns {deviationid: favourites_count} de-duplicated across folders.
    """
    favs_by_deviation: Dict[str, int] = {}

    for idx, folderid in enumerate(folderids, start=1):
        offset = 0
        fetched_in_folder = 0

        while True:
            page = client.get_gallery_folder_contents(folderid, offset=offset, limit=24)
            results = page.get("results", [])
            for dev in results:
                did = dev.get("deviationid")
                if not did:
                    continue
                # deviation.stats.favourites appears in deviation objects returned from gallery endpoints. :contentReference[oaicite:17]{index=17}
                favs = int(((dev.get("stats") or {}).get("favourites")) or 0)
                # Keep max in case the same deviation appears in multiple folders with any discrepancy
                favs_by_deviation[did] = max(favs_by_deviation.get(did, 0), favs)

            fetched_in_folder += len(results)

            if per_folder_limit_cap is not None and fetched_in_folder >= per_folder_limit_cap:
                break

            if not page.get("has_more"):
                break
            next_offset = page.get("next_offset")
            if next_offset is None:
                break
            offset = int(next_offset)

    return favs_by_deviation


def fetch_featured_deviationids(client: DeviantArtClient, featured_folderid: str) -> List[str]:
    ids: List[str] = []
    offset = 0
    while True:
        page = client.get_gallery_folder_contents(featured_folderid, offset=offset, limit=24)
        results = page.get("results", [])
        for dev in results:
            did = dev.get("deviationid")
            if did:
                ids.append(did)

        if not page.get("has_more"):
            break
        next_offset = page.get("next_offset")
        if next_offset is None:
            break
        offset = int(next_offset)
    return ids


def main() -> int:
    # --- Configuration via env vars ---
    load_dotenv()
    client_id = os.environ.get("DA_CLIENT_ID", "").strip()
    client_secret = os.environ.get("DA_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("DA_REFRESH_TOKEN", "").strip()

    top_n = int(os.environ.get("DA_TOP_N", "20"))
    # Optional: cap how many items to scan per folder (debug/speed). Leave unset for full scan.
    per_folder_cap_env = os.environ.get("DA_PER_FOLDER_CAP", "").strip()
    per_folder_cap = int(per_folder_cap_env) if per_folder_cap_env else None

    if not (client_id and client_secret and refresh_token):
        print(
            "Missing credentials. Set env vars:\n"
            "  DA_CLIENT_ID, DA_CLIENT_SECRET, DA_REFRESH_TOKEN\n"
            "Optional:\n"
            "  DA_TOP_N (default 20)\n"
            "  DA_PER_FOLDER_CAP (optional)\n",
            file=sys.stderr,
        )
        return 2

    client = DeviantArtClient(OAuthConfig(client_id, client_secret, refresh_token))

    print("Fetching gallery folders...")
    folders = fetch_all_folders(client)
    for folder in folders:
        print(folder["name"], folder["parent"])
    

    featured_folderid = find_folderid("Top 20 Favorites", folders)
    print(f'Featured folderid: {featured_folderid}')

    # Collect all folderids (including featured). We’ll include everything for scoring;
    # then we’ll reset featured and repopulate.
    folderids = [f["folderid"] for f in folders if f.get("folderid")]

    print("Listing current Featured contents...")
    featured_ids = fetch_featured_deviationids(client, featured_folderid)
    print(f"Featured currently contains {len(featured_ids)} deviations.")

    if featured_ids:
        print("Removing all deviations from Featured...")
        for batch in chunked(featured_ids, MAX_DEVIATIONIDS_PER_MUTATION):
            resp = client.remove_deviations_from_folder(featured_folderid, batch)
            if not resp.get("success"):
                raise RuntimeError(f"Remove failed for batch of {len(batch)}: {resp}")
        print("Featured cleared.")

    print("Fetching deviations across all folders to compute top favourites...")
    favs_by_dev = fetch_all_deviations_across_folders(
        client, folderids, per_folder_limit_cap=per_folder_cap
    )
    print(f"Scanned {len(favs_by_dev)} unique deviations.")

    top = sorted(favs_by_dev.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    top_ids = [did for did, _ in top]
    print(f"Top {top_n} selected. Highest favourites count: {top[0][1] if top else 'n/a'}")

    if not top_ids:
        print("No deviations found; nothing to copy.")
        return 0

    print("Copying top deviations into Featured...")
    for batch in chunked(top_ids, MAX_DEVIATIONIDS_PER_MUTATION):
        resp = client.copy_deviations_to_folder(featured_folderid, batch)
        if not resp.get("success"):
            raise RuntimeError(f"Copy failed for batch of {len(batch)}: {resp}")

    print("Done. Featured updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
