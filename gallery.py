from typing import Dict, Iterable, List, Optional
import json
from datetime import datetime
from deviant_art_client import DeviantArtClient

class Gallery:
    def __init__ (self, client: DeviantArtClient):
        self.client = client
    def list_gallery_folders(self, *, calculate_size: bool = True, offset: int = 0, limit: int = 50) -> dict:
        # GET /gallery/folders
        return self.client.request(
            "GET",
            "/gallery/folders",
            params={
                "calculate_size": "1" if calculate_size else "0",
                "offset": offset,
                "limit": limit,
            },
        )

    def get_gallery_folder_contents(self, folderid: str, *, offset: int = 0, limit: int = 24) -> dict:
        # GET /gallery/{folderid}
        return self.client.request(
            "GET",
            f"/gallery/{folderid}",
            params={"offset": offset, "limit": limit, "mature_content":1},
        )

    def remove_deviations_from_folder(self, folderid: str, deviationids: List[str]) -> dict:
        # POST /gallery/folders/remove_deviations
        return self.client.request(
            "POST",
            "/gallery/folders/remove_deviations",
            data={
                "folderid" : folderid,
                "deviationids[]": deviationids
            },
        )

    def copy_deviations_to_folder(self, target_folderid: str, deviationids: List[str]) -> dict:
        # POST /gallery/folders/copy_deviations
        return self.client.request(
            "POST",
            "/gallery/folders/copy_deviations",
            data={
                "target_folderid": target_folderid,
                "deviationids[]": deviationids
            },
        )


    def chunked(self, items: List[str], n: int) -> Iterable[List[str]]:
        for i in range(0, len(items), n):
            yield items[i : i + n]


    def find_folderid(self, folder_name: str, folders: List[dict]) -> str:
        for f in folders:
            if (f.get("name") or "").strip().lower() == folder_name.lower():
                fid = f.get("folderid")
                if fid:
                    return fid
        raise RuntimeError(f'Could not locate a "{folder_name}" folder in /gallery/folders response.')


    def fetch_all_folders(self) -> List[dict]:
        all_folders: List[dict] = []
        offset = 0
        while True:
            page = self.list_gallery_folders(calculate_size=True, offset=offset, limit=50)
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
        self,
        folderids: List[str],
        *,
        per_folder_limit_cap: Optional[int] = None,
    ) -> Dict[str, int]:
        """
        Returns {deviationid: favourites_count} de-duplicated across folders.
        """
        favs_by_deviation: Dict[str, int] = {}

        for _, folderid in enumerate(folderids, start=1):
            offset = 0
            fetched_in_folder = 0

            while True:
                page = self.get_gallery_folder_contents(folderid, offset=offset, limit=24)
                results = page.get("results", [])
                for dev in results:
                    date_published_timestamp = dev.get("published_time")
                    if not date_published_timestamp:
                        continue
                    did = dev.get("deviationid")
                    if not did:
                        continue
                    date_published = datetime.fromtimestamp(int(date_published_timestamp))
                    one_year_ago = datetime(datetime.today().year-1, datetime.today().month, datetime.today().day)
                    if date_published<one_year_ago:
                        continue
                    # deviation.stats.favourites appears in deviation objects returned from gallery endpoints.
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


    def fetch_folder_deviationids(self, featured_folderid: str) -> List[str]:
        ids: List[str] = []
        offset = 0
        while True:
            page = self.get_gallery_folder_contents(featured_folderid, offset=offset, limit=24)
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
