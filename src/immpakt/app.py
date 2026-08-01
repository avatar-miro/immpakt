"""HTTP server: one endpoint for the frame, plus a dashboard for humans.

The device protocol is deliberately a single request per wake:

    GET /api/frame.bin?id=<device>&mv=<batt>&pct=<n>&rssi=<n>&wake=<reason>
    If-None-Match: "<etag of what is currently painted>"

    200 + ETag + X-Next-Wake: <seconds> + 30000 bytes
    304 + X-Next-Wake: <seconds>

Telemetry rides up in the query string and the sleep interval rides down in a
response header, so the firmware needs no JSON parser on the wake path.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import threading
from collections import OrderedDict
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from . import config as config_mod
from . import dashboard, overlay, panel, render
from .immich import Asset, ImmichClient
from .selector import AssetPool, pick
from .store import Store

log = logging.getLogger(__name__)

# Rendering a frame costs ~1s of error diffusion, so keep recent results.
# Keyed by (asset id, render-options fingerprint) -- changing any tuning knob
# in config.yaml invalidates the entry for free.
_RENDER_CACHE: "OrderedDict[tuple, tuple[bytes, object, str]]" = OrderedDict()
_RENDER_CACHE_MAX = 32
_render_lock = threading.Lock()


class State:
    def __init__(self, cfg: config_mod.Config):
        self.cfg = cfg
        self.client = ImmichClient(
            cfg.immich.url, cfg.immich.api_key,
            timeout=cfg.immich.timeout_s, verify_tls=cfg.immich.verify_tls,
        )
        self.pool = AssetPool(self.client, cfg.source)
        self.store = Store(f"{cfg.server.data_dir}/immpakt.db")


state: State  # set in the lifespan handler


@asynccontextmanager
async def lifespan(app: FastAPI):
    global state
    cfg = config_mod.load()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    state = State(cfg)
    if not cfg.immich.api_key:
        log.warning("no Immich API key configured -- set IMMICH_API_KEY or immich.api_key")
    # Warm the pool off the request path so the first device wake is fast.
    threading.Thread(target=state.pool.ensure_fresh, daemon=True).start()
    yield
    state.client.close()
    state.store.close()


app = FastAPI(title="ImmPakt", lifespan=lifespan)


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def _fingerprint(frame_cfg) -> tuple:
    o = frame_cfg.render_options()
    ov = frame_cfg.overlay
    return (
        o.dither, o.dither_strength, o.rotate, o.palette_rgb,
        o.fit.mode, o.fit.crop_tolerance_wide, o.fit.crop_tolerance_tall,
        o.fit.mat_color, o.fit.face_aware,
        o.enhance.autocontrast, o.enhance.autocontrast_cutoff, o.enhance.brightness,
        o.enhance.contrast, o.enhance.saturation, o.enhance.sharpness,
        ov.enabled, ov.show_date, ov.show_location, ov.date_format,
        ov.position, ov.color, ov.shadow, ov.font_size, ov.margin,
    )


def render_asset(asset: Asset, frame_cfg) -> tuple[bytes, object, str]:
    """Return (frame bytes, index array, etag), memoised."""
    key = (asset.id, _fingerprint(frame_cfg))
    with _render_lock:
        if key in _RENDER_CACHE:
            _RENDER_CACHE.move_to_end(key)
            return _RENDER_CACHE[key]

    opts = frame_cfg.render_options()
    faces = state.client.faces(asset.id) if opts.fit.face_aware else []
    data = state.client.image_bytes(asset.id, size="preview")
    frame, indices = render.render(data, opts, faces=faces)

    if frame_cfg.overlay.enabled:
        text = overlay.caption(asset, frame_cfg.overlay)
        if text:
            indices = overlay.draw(indices, text, frame_cfg.overlay)
            frame = panel.pack(indices)

    etag = hashlib.sha256(frame).hexdigest()[:16]
    result = (frame, indices, etag)
    with _render_lock:
        _RENDER_CACHE[key] = result
        while len(_RENDER_CACHE) > _RENDER_CACHE_MAX:
            _RENDER_CACHE.popitem(last=False)
    return result


def frame_cfg_for(device_id: str):
    """Render config for a device, including dashboard-edited overrides."""
    return state.cfg.frame_for(device_id, state.store.get_settings(device_id))


# A device id is an identifier, not free text. Unvalidated it becomes the
# primary key of a row anyone on the network can create, and it is rendered
# into the dashboard -- escaping already covers the markup, but bounding the
# charset keeps it out of URLs, filenames and log lines as well.
_DEVICE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def check_device_id(device_id: str) -> str:
    if not _DEVICE_ID.match(device_id):
        raise HTTPException(
            400,
            "device id must be 1-64 chars of letters, digits, dot, dash or "
            "underscore, starting alphanumeric",
        )
    return device_id


# Only these may be set from the browser. Everything else stays file-owned, so
# a hostile or fat-fingered POST cannot repoint the server or corrupt rendering.
SETTABLE = {
    "interval_s": ("int", 30, 7 * 24 * 3600),
    "overlay.enabled": ("bool", None, None),
    "overlay.show_date": ("bool", None, None),
    "overlay.show_location": ("bool", None, None),
    "overlay.font_size": ("int", 9, 40),
    "overlay.margin": ("int", 0, 60),
    "overlay.position": (
        "enum", None, ("bottom-left", "bottom-right", "top-left", "top-right"),
    ),
}


def _validate_settings(payload: dict) -> dict:
    """Whitelist + coerce a settings POST into a nested override dict."""
    out: dict = {}
    for key, raw in payload.items():
        spec = SETTABLE.get(key)
        if spec is None:
            raise HTTPException(400, f"not a settable field: {key}")
        kind, lo, hi = spec
        if kind == "int":
            try:
                val: object = int(raw)
            except (TypeError, ValueError):
                raise HTTPException(400, f"{key} must be a whole number") from None
            if not (lo <= val <= hi):
                raise HTTPException(400, f"{key} must be between {lo} and {hi}")
        elif kind == "bool":
            val = raw if isinstance(raw, bool) else str(raw).lower() in ("1", "true", "on", "yes")
        else:
            val = str(raw)
            if val not in hi:
                raise HTTPException(400, f"{key} must be one of {', '.join(hi)}")

        node = out
        *parents, leaf = key.split(".")
        for p in parents:
            node = node.setdefault(p, {})
        node[leaf] = val
    return out


def _resolve(device_id: str, advance: bool, register: bool = True,
             peek: int = 0) -> tuple[Asset, object]:
    """Resolve which photo ``device_id`` should show.

    ``register`` is False for browser-side previews: looking at a frame in the
    dashboard must not invent a device row, or every preview URL you visit
    shows up forever as a phantom frame that never reports a battery.
    """
    frame_cfg = frame_cfg_for(device_id)
    assets = state.pool.ensure_fresh()
    if not assets:
        # Distinguish "couldn't talk to Immich" from "your filters match
        # nothing" -- they have completely different fixes.
        err = state.pool.status["error"]
        detail = (
            f"could not load photos from {state.cfg.immich.url}: {err}"
            if err
            else "Immich is reachable but no photos match source filters in config"
        )
        raise HTTPException(503, detail)

    dev = state.store.get(device_id)
    if dev is None:
        if not register:
            # Unknown device, preview only: show the pool from a stable
            # position without persisting anything.
            asset = pick(assets, peek, 0, state.cfg.source.order)
            if asset is None:
                raise HTTPException(503, "no photos available from Immich")
            return asset, frame_cfg
        dev = state.store.touch(device_id)

    cursor = state.store.advance_cursor(device_id) if advance else dev.cursor
    asset = pick(assets, cursor + peek, dev.seed, state.cfg.source.order)
    if asset is None:
        raise HTTPException(503, "no photos available from Immich")
    return asset, frame_cfg


# --------------------------------------------------------------------------
# device endpoint
# --------------------------------------------------------------------------


@app.get("/api/frame.bin")
def frame_bin(
    request: Request,
    id: str = Query(..., description="device id, e.g. picpak-a1b2c3"),
    mv: int | None = None,
    pct: int | None = None,
    rssi: int | None = None,
    wake: str | None = None,
    key: str = "",
):
    # compare_digest, not ==: a plain comparison leaks the shared secret one
    # byte at a time to anyone who can time the responses.
    if state.cfg.server.device_key and not hmac.compare_digest(
        key, state.cfg.server.device_key
    ):
        raise HTTPException(403, "bad device key")

    check_device_id(id)
    state.store.touch(id, battery_mv=mv, battery_pct=pct, rssi=rssi, wake_reason=wake)
    frame_cfg = frame_cfg_for(id)
    headers = {"X-Next-Wake": str(frame_cfg.interval_s), "Cache-Control": "no-store"}

    try:
        asset, frame_cfg = _resolve(id, advance=True)
        frame, _, etag = render_asset(asset, frame_cfg)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 - keep the last image, retry next wake
        log.exception("render failed for %s", id)
        raise HTTPException(503, f"render failed: {e}") from e

    if request.headers.get("if-none-match", "").strip('"') == etag:
        return Response(status_code=304, headers=headers)

    state.store.set_current(id, asset.id, etag)
    headers["ETag"] = f'"{etag}"'
    log.info("%s -> asset %s (%s bytes, batt %s mV)", id, asset.id, len(frame), mv)
    return Response(frame, media_type="application/octet-stream", headers=headers)


# --------------------------------------------------------------------------
# human endpoints
# --------------------------------------------------------------------------


@app.get("/preview.png")
def preview(id: str = "preview", next: bool = False, scale: int = 2, peek: int = 0):
    """Exactly what the panel will paint -- tune dithering with no hardware.

    register=False: browsing previews must never mint a device row.
    """
    asset, frame_cfg = _resolve(id, advance=next, register=False, peek=peek)
    _, indices, etag = render_asset(asset, frame_cfg)
    png = render.preview_png(indices, frame_cfg.render_options(), scale=scale)
    return Response(png, media_type="image/png", headers={
        "Cache-Control": "no-store", "X-Asset-Id": asset.id, "X-Etag": etag,
    })


@app.post("/api/devices/{device_id}/next")
def force_next(device_id: str):
    check_device_id(device_id)
    asset, _ = _resolve(device_id, advance=True)
    return {"device": device_id, "asset": asset.id}


@app.get("/api/status")
def status():
    return JSONResponse({
        "immich": {"url": state.cfg.immich.url, "reachable": state.client.ping()},
        "pool": state.pool.status,
        "interval_s": state.cfg.frame.interval_s,
        "devices": [
            {
                "id": d.id, "last_seen": d.last_seen, "wakes": d.wakes,
                "battery_mv": d.battery_mv, "battery_pct": d.battery_pct,
                "rssi": d.rssi, "wake_reason": d.wake_reason,
                "current_asset": d.current_asset,
            }
            for d in state.store.all()
        ],
    })


@app.get("/api/devices/{device_id}/settings")
def get_settings(device_id: str):
    check_device_id(device_id)
    cfg = frame_cfg_for(device_id)
    return {
        "effective": {
            "interval_s": cfg.interval_s,
            "overlay.enabled": cfg.overlay.enabled,
            "overlay.show_date": cfg.overlay.show_date,
            "overlay.show_location": cfg.overlay.show_location,
            "overlay.position": cfg.overlay.position,
            "overlay.font_size": cfg.overlay.font_size,
        "overlay.margin": cfg.overlay.margin,
        },
        "overrides": state.store.get_settings(device_id),
        "settable": sorted(SETTABLE),
    }


@app.post("/api/devices/{device_id}/settings")
async def put_settings(device_id: str, request: Request):
    """Save dashboard overrides. Takes effect on the device's next wake --
    there is no push channel; the radio is off until it wakes itself."""
    check_device_id(device_id)
    # Require a real JSON content type. request.json() will happily parse a
    # text/plain body, and text/plain is a CORS "simple request" -- so without
    # this a page you merely visit could silently reconfigure your frame with a
    # cross-origin form POST, no preflight and no consent.
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise HTTPException(415, "settings require Content-Type: application/json")

    if state.store.get(device_id) is None:
        raise HTTPException(404, f"no such device: {device_id}")

    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(400, "body is not valid JSON") from None
    if not isinstance(payload, dict):
        raise HTTPException(400, "expected a JSON object")

    merged = config_mod._deep_merge(
        state.store.get_settings(device_id), _validate_settings(payload)
    )
    state.store.set_settings(device_id, merged)

    dev = state.store.get(device_id)
    cfg = frame_cfg_for(device_id)
    due = (dev.last_seen + cfg.interval_s) if dev and dev.last_seen else None
    return {
        "device": device_id,
        "overrides": merged,
        "applies_at": due,
        "note": "takes effect on the next wake; tap the frame's button to apply now",
    }


@app.delete("/api/devices/{device_id}")
def delete_device(device_id: str):
    """Forget a device -- for clearing out test entries."""
    check_device_id(device_id)
    if not state.store.delete(device_id):
        raise HTTPException(404, f"no such device: {device_id}")
    return {"deleted": device_id}


@app.get("/", response_class=HTMLResponse)
def dashboard_page():
    devices = state.store.all()
    return dashboard.render(
        devices=devices,
        histories={d.id: state.store.history(d.id, limit=60) for d in devices},
        settings={d.id: frame_cfg_for(d.id) for d in devices},
        pool=state.pool.status,
        immich_url=state.cfg.immich.url,
        immich_ok=state.client.ping(),
        interval_s=state.cfg.frame.interval_s,
    )
