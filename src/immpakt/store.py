"""SQLite state: known devices, their telemetry history, and photo cursors.

Devices self-register on first contact -- there is no pairing step. The only
thing persisted per device is what it needs to not repeat photos and what you
want to see on the dashboard.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id             TEXT PRIMARY KEY,
    first_seen     INTEGER NOT NULL,
    last_seen      INTEGER NOT NULL,
    battery_mv     INTEGER,
    battery_pct    INTEGER,
    rssi           INTEGER,
    wake_reason    TEXT,
    current_asset  TEXT,
    current_etag   TEXT,
    cursor         INTEGER NOT NULL DEFAULT 0,
    seed           INTEGER NOT NULL DEFAULT 0,
    wakes          INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS telemetry (
    device_id   TEXT NOT NULL,
    ts          INTEGER NOT NULL,
    battery_mv  INTEGER,
    battery_pct INTEGER,
    rssi        INTEGER
);
CREATE INDEX IF NOT EXISTS telemetry_dev_ts ON telemetry (device_id, ts);

-- Per-device overrides edited from the dashboard, deep-merged over the
-- `frame` block in config.yaml. Kept out of the YAML on purpose: a web UI
-- rewriting a hand-commented config file would trash the comments and race
-- with anyone editing it by hand.
CREATE TABLE IF NOT EXISTS settings (
    device_id TEXT PRIMARY KEY,
    json      TEXT NOT NULL,
    updated   INTEGER NOT NULL
);
"""


@dataclass
class Device:
    id: str
    first_seen: int
    last_seen: int
    battery_mv: int | None
    battery_pct: int | None
    rssi: int | None
    wake_reason: str | None
    current_asset: str | None
    current_etag: str | None
    cursor: int
    seed: int
    wakes: int


class Store:
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Pre-rename installs wrote picpak.db. Adopt it rather than starting
        # empty, which would silently lose every device's telemetry, cursor
        # position and settings and look like the frames had de-registered.
        legacy = path.with_name("picpak.db")
        if not path.exists() and legacy.exists():
            legacy.rename(path)
            log.info("migrated %s -> %s", legacy.name, path.name)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def touch(
        self,
        device_id: str,
        *,
        battery_mv: int | None = None,
        battery_pct: int | None = None,
        rssi: int | None = None,
        wake_reason: str | None = None,
    ) -> Device:
        """Record a wake, creating the device row if this is its first ever."""
        now = int(time.time())
        with self._db:
            self._db.execute(
                """INSERT INTO devices (id, first_seen, last_seen, seed)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(id) DO NOTHING""",
                (device_id, now, now, now),
            )
            self._db.execute(
                """UPDATE devices SET last_seen = ?, wakes = wakes + 1,
                          battery_mv  = COALESCE(?, battery_mv),
                          battery_pct = COALESCE(?, battery_pct),
                          rssi        = COALESCE(?, rssi),
                          wake_reason = COALESCE(?, wake_reason)
                    WHERE id = ?""",
                (now, battery_mv, battery_pct, rssi, wake_reason, device_id),
            )
            if battery_mv is not None or rssi is not None:
                self._db.execute(
                    "INSERT INTO telemetry VALUES (?, ?, ?, ?, ?)",
                    (device_id, now, battery_mv, battery_pct, rssi),
                )
        return self.get(device_id)  # type: ignore[return-value]

    def get(self, device_id: str) -> Device | None:
        row = self._db.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        return Device(**dict(row)) if row else None

    def all(self) -> list[Device]:
        rows = self._db.execute("SELECT * FROM devices ORDER BY last_seen DESC").fetchall()
        return [Device(**dict(r)) for r in rows]

    def set_current(self, device_id: str, asset_id: str, etag: str) -> None:
        with self._db:
            self._db.execute(
                "UPDATE devices SET current_asset = ?, current_etag = ? WHERE id = ?",
                (asset_id, etag, device_id),
            )

    def advance_cursor(self, device_id: str, by: int = 1) -> int:
        with self._db:
            cur = self._db.execute(
                "UPDATE devices SET cursor = cursor + ? WHERE id = ? RETURNING cursor",
                (by, device_id),
            ).fetchone()
        return cur["cursor"] if cur else 0

    def history(self, device_id: str, limit: int = 200) -> list[sqlite3.Row]:
        """Telemetry oldest-first, so it can be plotted left-to-right directly."""
        rows = self._db.execute(
            "SELECT ts, battery_mv, battery_pct, rssi FROM telemetry "
            "WHERE device_id = ? ORDER BY ts DESC LIMIT ?",
            (device_id, limit),
        ).fetchall()
        return list(reversed(rows))

    def delete(self, device_id: str) -> bool:
        """Forget a device entirely -- for clearing out test/phantom entries."""
        with self._db:
            cur = self._db.execute("DELETE FROM devices WHERE id = ?", (device_id,))
            self._db.execute("DELETE FROM telemetry WHERE device_id = ?", (device_id,))
            self._db.execute("DELETE FROM settings WHERE device_id = ?", (device_id,))
        return cur.rowcount > 0

    # -- dashboard-edited overrides ---------------------------------------

    def get_settings(self, device_id: str) -> dict:
        row = self._db.execute(
            "SELECT json FROM settings WHERE device_id = ?", (device_id,)
        ).fetchone()
        if not row:
            return {}
        try:
            return json.loads(row["json"])
        except json.JSONDecodeError:
            return {}

    def set_settings(self, device_id: str, settings: dict) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO settings (device_id, json, updated) VALUES (?, ?, ?) "
                "ON CONFLICT(device_id) DO UPDATE SET json = excluded.json, "
                "updated = excluded.updated",
                (device_id, json.dumps(settings), int(time.time())),
            )
