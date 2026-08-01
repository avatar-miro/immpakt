"""Candidate photo pool and per-device rotation.

The pool is fetched from Immich once and refreshed on a timer, not per request:
a device wake must never block on a full library scan, and the pool changes far
more slowly than photos are displayed.

Each device walks its own deterministically-shuffled permutation of the pool,
so two frames in the same house show different photos, and each one sees every
eligible photo once before repeating.
"""

from __future__ import annotations

import logging
import random
import threading
import time

from .config import SourceConfig
from .immich import Asset, ImmichClient

log = logging.getLogger(__name__)


class AssetPool:
    def __init__(self, client: ImmichClient, source: SourceConfig):
        self._client = client
        self._source = source
        self._lock = threading.Lock()
        self._assets: list[Asset] = []
        self._fetched_at = 0.0
        self._error: str | None = None

    @property
    def assets(self) -> list[Asset]:
        return self._assets

    @property
    def status(self) -> dict:
        return {
            "count": len(self._assets),
            "fetched_at": self._fetched_at,
            "age_s": (time.time() - self._fetched_at) if self._fetched_at else None,
            "error": self._error,
        }

    def ensure_fresh(self, force: bool = False) -> list[Asset]:
        with self._lock:
            stale = time.time() - self._fetched_at > self._source.refresh_interval_s
            if force or stale or not self._assets:
                self._refresh_locked()
            return self._assets

    def _refresh_locked(self) -> None:
        s = self._source
        collected: dict[str, Asset] = {}
        try:
            if s.albums:
                for album_id in s.albums:
                    for a in self._client.album_assets(album_id):
                        collected[a.id] = a
            if s.memories:
                for a in self._client.memory_assets():
                    collected[a.id] = a
            # No album/memory restriction, or an explicit people/favourites
            # filter: fall back to a metadata search over the library.
            if not s.albums and not s.memories:
                for a in self._client.search_assets(
                    person_ids=s.people or None,
                    is_favorite=True if s.favorites_only else None,
                    taken_after=s.taken_after,
                    taken_before=s.taken_before,
                ):
                    collected[a.id] = a

            assets = list(collected.values())
            if s.min_aspect is not None:
                # aspect == 0 means Immich gave us no EXIF dimensions; keep
                # those rather than silently dropping half the library.
                assets = [a for a in assets if a.aspect == 0 or a.aspect >= s.min_aspect]

            if s.order == "newest":
                assets.sort(key=lambda a: a.taken_at or "", reverse=True)
            elif s.order == "oldest":
                assets.sort(key=lambda a: a.taken_at or "")
            else:
                assets.sort(key=lambda a: a.id)  # stable base for the shuffle

            self._assets = assets
            self._fetched_at = time.time()
            self._error = None
            log.info("asset pool refreshed: %d photos", len(assets))
        except Exception as e:  # noqa: BLE001 - a stale pool beats a blank frame
            self._error = f"{type(e).__name__}: {e}"
            log.error("asset pool refresh failed, keeping %d cached: %s",
                      len(self._assets), self._error)
            if not self._assets:
                self._fetched_at = 0.0


def pick(assets: list[Asset], cursor: int, seed: int, order: str) -> Asset | None:
    """Which photo this device should show at ``cursor``.

    For random order the permutation is regenerated per call from (seed, epoch)
    where epoch advances each time the cursor laps the pool -- so the sequence
    is stable within a pass, reshuffles between passes, and needs no stored
    state beyond a single integer.
    """
    if not assets:
        return None
    n = len(assets)
    if order != "random":
        return assets[cursor % n]

    epoch, offset = divmod(cursor, n)
    order_idx = list(range(n))
    random.Random((seed << 20) ^ epoch).shuffle(order_idx)
    return assets[order_idx[offset]]
