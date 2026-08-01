"""Minimal Immich API client -- just what a photo frame needs.

Auth is a server API key sent as ``x-api-key`` (Immich Settings -> API Keys).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)


@dataclass
class Asset:
    id: str
    taken_at: str | None = None
    city: str | None = None
    country: str | None = None
    width: int = 0
    height: int = 0

    @property
    def aspect(self) -> float:
        return (self.width / self.height) if self.width and self.height else 0.0

    @classmethod
    def from_json(cls, a: dict) -> "Asset":
        info = a.get("exifInfo") or {}
        return cls(
            id=a["id"],
            taken_at=a.get("fileCreatedAt") or info.get("dateTimeOriginal"),
            city=info.get("city"),
            country=info.get("country"),
            width=int(info.get("exifImageWidth") or 0),
            height=int(info.get("exifImageHeight") or 0),
        )


class ImmichClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0, verify_tls: bool = True):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=f"{self.base_url}/api",
            headers={"x-api-key": api_key, "Accept": "application/json"},
            timeout=timeout,
            verify=verify_tls,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    # -- connectivity ------------------------------------------------------

    def ping(self) -> bool:
        try:
            r = self._client.get("/server/ping")
            return r.status_code == 200
        except httpx.HTTPError as e:
            log.warning("immich ping failed: %s", e)
            return False

    def whoami(self) -> dict:
        """Validates the API key -- /server/ping needs no auth, this does."""
        r = self._client.get("/users/me")
        r.raise_for_status()
        return r.json()

    def albums(self) -> list[dict]:
        """[{id, albumName, assetCount}] -- to find the UUIDs config wants."""
        r = self._client.get("/albums")
        r.raise_for_status()
        return r.json()

    # -- asset pools -------------------------------------------------------
    #
    # Deliberately NOT /search/random: it has a history of regressions (same
    # asset every call in 1.125.x, 404 in 2.1.0) and its results are biased
    # toward low UUIDs. Pulling a candidate pool and shuffling locally is both
    # more reliable and lets us hold a per-device cursor so two frames in the
    # same house do not show the same photo.

    def album_assets(self, album_id: str) -> list[Asset]:
        """Assets in an album, across Immich versions.

        Older builds inline an ``assets`` array in ``GET /albums/{id}``. Newer
        ones return album metadata only -- no ``assets`` key at all, just
        ``assetCount`` -- so an inline read silently yields zero photos and the
        frame goes blank. Fall back to a metadata search scoped to the album.
        """
        r = self._client.get(f"/albums/{album_id}")
        r.raise_for_status()
        body = r.json()

        inline = body.get("assets") or []
        if inline:
            return [Asset.from_json(a) for a in inline]

        count = body.get("assetCount") or 0
        if not count:
            return []  # genuinely empty album

        log.debug("album %s returned no inline assets (assetCount=%d); "
                  "falling back to search", album_id, count)
        return self.search_assets(album_ids=[album_id])

    def search_assets(
        self,
        *,
        album_ids: list[str] | None = None,
        person_ids: list[str] | None = None,
        is_favorite: bool | None = None,
        taken_after: str | None = None,
        taken_before: str | None = None,
        page_limit: int = 20,
    ) -> list[Asset]:
        """Paginated /search/metadata, restricted to still images."""
        assets: list[Asset] = []
        page = 1
        while page <= page_limit:
            body: dict = {"type": "IMAGE", "page": page, "size": 1000, "withExif": True}
            if album_ids:
                body["albumIds"] = album_ids
            if person_ids:
                body["personIds"] = person_ids
            if is_favorite is not None:
                body["isFavorite"] = is_favorite
            if taken_after:
                body["takenAfter"] = taken_after
            if taken_before:
                body["takenBefore"] = taken_before

            r = self._client.post("/search/metadata", json=body)
            r.raise_for_status()
            items = r.json().get("assets", {})
            batch = items.get("items", [])
            assets.extend(Asset.from_json(a) for a in batch)
            if not items.get("nextPage"):
                break
            page += 1
        return assets

    def memory_assets(self) -> list[Asset]:
        """'On this day' assets, the thing that makes a frame feel alive."""
        r = self._client.get("/memories")
        r.raise_for_status()
        out: list[Asset] = []
        for m in r.json():
            out.extend(Asset.from_json(a) for a in m.get("assets", []))
        return out

    # -- per-asset ---------------------------------------------------------

    def faces(self, asset_id: str) -> list[tuple[float, float, float, float]]:
        """Normalised (x1, y1, x2, y2) face boxes, for face-aware cropping.

        Immich reports boxes in the coordinate space of the image it ran
        detection on, which it also reports -- so we normalise rather than
        assuming it matches the asset's full resolution.
        """
        try:
            r = self._client.get(f"/assets/{asset_id}")
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.debug("face lookup failed for %s: %s", asset_id, e)
            return []

        boxes: list[tuple[float, float, float, float]] = []
        for person in r.json().get("people", []):
            for f in person.get("faces", []):
                iw, ih = f.get("imageWidth") or 0, f.get("imageHeight") or 0
                if not iw or not ih:
                    continue
                boxes.append(
                    (
                        f["boundingBoxX1"] / iw,
                        f["boundingBoxY1"] / ih,
                        f["boundingBoxX2"] / iw,
                        f["boundingBoxY2"] / ih,
                    )
                )
        return boxes

    def image_bytes(self, asset_id: str, size: str = "preview") -> bytes:
        """Fetch pixels. 'preview' is plenty for a 400x300 panel and much
        cheaper than 'original' -- we downscale to 400px wide regardless."""
        if size == "original":
            r = self._client.get(f"/assets/{asset_id}/original")
        else:
            r = self._client.get(f"/assets/{asset_id}/thumbnail", params={"size": size})
        r.raise_for_status()
        return r.content
