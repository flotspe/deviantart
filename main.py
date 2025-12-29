#!/usr/bin/env python3
"""
Sync chosen DeviantArt gallery to your top N most-favourited deviations.

Behavior:
- Identify chosen folderid
- Remove all existing deviations from chosen gallery
- Compute top N deviations by stats.favourites across your gallery folders
- Copy those top N into chosen gallery

Notes:
- DeviantArt gallery folder modification endpoints require scopes: browse + gallery.
- OAuth2 token refresh uses https://www.deviantart.com/oauth2/token
- /gallery/{folderid} supports pagination (offset/limit) and returns deviation objects with stats.
"""

import os
import sys

from dotenv import load_dotenv
from deviant_art_client import DeviantArtClient, OAuthConfig
from gallery import Gallery
from authorize import get_refresh_token
from token_store import save_refresh_token

# API constraints in docs for deviationids arrays: max 24 per request for copy/remove endpoints.
MAX_DEVIATIONIDS_PER_MUTATION = 24
REQUESTED_FOLDER = "Top 20 Favorites"

def main() -> int:
    # --- Configuration via env vars ---
    load_dotenv()
    client_id = os.environ.get("DA_CLIENT_ID", "").strip()
    client_secret = os.environ.get("DA_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("DA_REFRESH_TOKEN", "").strip()

    if refresh_token == "":
        refresh_token = get_refresh_token()
        if not refresh_token:
            raise RuntimeError("Couldn't acquire refresh token")
        save_refresh_token(refresh_token)

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
    gallery = Gallery(client)

    print("Fetching gallery folders...")
    folders = gallery.fetch_all_folders()
    for folder in folders:
        print(folder["name"])

    folderid = gallery.find_folderid(REQUESTED_FOLDER, folders)
    print(f'Folder id: {folderid}')

    # Collect all folderids. We’ll include everything for scoring;
    # then we’ll reset the requested folder and repopulate.
    folderids = [f["folderid"] for f in folders if f.get("folderid")]

    print("Listing current folder contents...")
    deviation_ids = gallery.fetch_folder_deviationids(folderid)
    print(f"'{REQUESTED_FOLDER}' currently contains {len(deviation_ids)} deviations.")

    if deviation_ids:
        print(f"Removing all deviations from {REQUESTED_FOLDER}...")
        for batch in gallery.chunked(deviation_ids, MAX_DEVIATIONIDS_PER_MUTATION):
            resp = gallery.remove_deviations_from_folder(folderid, batch)
            if not resp.get("success"):
                raise RuntimeError(f"Remove failed for batch of {len(batch)}: {resp}")
        print(f"{REQUESTED_FOLDER} cleared.")

    print("Fetching deviations across all folders to compute top favourites...")
    favs_by_dev = gallery.fetch_all_deviations_across_folders(
        folderids, per_folder_limit_cap=per_folder_cap
    )
    print(f"Scanned {len(favs_by_dev)} unique deviations.")

    top = sorted(favs_by_dev.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    top_ids = [did for did, _ in top]
    print(f"Top {top_n} selected. Highest favourites count: {top[0][1] if top else 'n/a'}")

    if not top_ids:
        print("No deviations found; nothing to copy.")
        return 0

    print(f"Copying top deviations into {REQUESTED_FOLDER}...")
    for batch in gallery.chunked(top_ids, MAX_DEVIATIONIDS_PER_MUTATION):
        resp = gallery.copy_deviations_to_folder(folderid, batch)
        if not resp.get("success"):
            raise RuntimeError(f"Copy failed for batch of {len(batch)}: {resp}")

    print(f"Done. {REQUESTED_FOLDER} updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
