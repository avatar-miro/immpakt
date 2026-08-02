"""Configuration: YAML file with environment-variable overrides for secrets."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

from . import panel
from .render import EnhanceOptions, FitOptions, RenderOptions

log = logging.getLogger(__name__)


@dataclass
class ImmichConfig:
    url: str = "http://localhost:2283"
    api_key: str = ""
    verify_tls: bool = True
    timeout_s: float = 30.0


@dataclass
class SourceConfig:
    """Which photos are eligible. Empty everything = the whole library."""

    albums: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    favorites_only: bool = False
    memories: bool = False
    taken_after: str | None = None
    taken_before: str | None = None
    # Skip photos narrower than this aspect (1.0 drops all portraits). Null
    # keeps everything and lets the fit policy mat them instead.
    min_aspect: float | None = None
    order: str = "random"  # random | newest | oldest
    refresh_interval_s: int = 3600


@dataclass
class OverlayConfig:
    enabled: bool = False
    show_date: bool = True
    show_location: bool = True
    date_format: str = "%-d %B %Y"
    position: str = "bottom-left"  # bottom-left | bottom-right | top-left | top-right
    color: int = panel.WHITE
    shadow: int = panel.BLACK
    font_size: int = 16
    # Inset from the panel edges, in pixels, applied on all four sides. On a
    # 400x300 panel 8px reads as flush against the edge; 16 gives the caption
    # room to sit in without eating the photo.
    margin: int = 16


@dataclass
class FrameConfig:
    # How long the device deep-sleeps between wakes. 6h is a sane default:
    # the panel refresh is the dominant power cost, so photo cadence and
    # battery life are the same dial.
    interval_s: int = 21600
    rotate: int = 0
    dither: str = "floyd-steinberg"
    dither_strength: float = 0.9
    palette: list[list[int]] = field(
        default_factory=lambda: [list(c) for c in panel.DEFAULT_PALETTE_RGB]
    )
    fit: FitOptions = field(default_factory=FitOptions)
    enhance: EnhanceOptions = field(default_factory=EnhanceOptions)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)

    def render_options(self) -> RenderOptions:
        return RenderOptions(
            fit=self.fit,
            enhance=self.enhance,
            dither=self.dither,
            dither_strength=self.dither_strength,
            palette_rgb=tuple(tuple(c) for c in self.palette),
            rotate=self.rotate,
        )


@dataclass
class AuthConfig:
    """Login for the dashboard and management API.

    Not applied to /api/frame.bin: a frame that is asleep with its radio off
    cannot log in. That endpoint stays gated by server.device_key only.
    """

    enabled: bool = True
    username: str = "immpakt"
    password: str = "immpakt"
    session_hours: int = 720          # 30 days
    # Set true when a TLS-terminating proxy sits in front, so the cookie is
    # never sent over plain http. Left false by default because the common
    # deployment is plain http on a LAN, where a Secure cookie is never sent
    # at all and login would appear to silently fail.
    cookie_secure: bool = False


@dataclass
class ServerConfig:
    bind: str = "0.0.0.0"
    port: int = 8080
    data_dir: str = "./data"
    # Optional shared secret; when set, devices must send ?key=<secret>.
    # Unnecessary on a LAN, worth setting if you expose this to the internet.
    device_key: str = ""
    auth: AuthConfig = field(default_factory=AuthConfig)


@dataclass
class Config:
    immich: ImmichConfig = field(default_factory=ImmichConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    source: SourceConfig = field(default_factory=SourceConfig)
    frame: FrameConfig = field(default_factory=FrameConfig)
    # Per-device overrides keyed by device id, e.g. a portrait-hung second
    # frame: {"picpak-a1b2c3": {"rotate": 90, "interval_s": 43200}}
    devices: dict[str, dict[str, Any]] = field(default_factory=dict)

    def frame_for(self, device_id: str, extra: dict | None = None) -> FrameConfig:
        """Frame config for a device.

        Layered lowest-to-highest: the global ``frame`` block, then
        ``devices.<id>`` from the YAML, then ``extra`` (dashboard-edited
        overrides out of the database), so an edit in the UI wins over the file.
        """
        merged = _to_dict(self.frame)
        if override := self.devices.get(device_id):
            merged = _deep_merge(merged, override)
        if extra:
            merged = _deep_merge(merged, extra)
        if merged == _to_dict(self.frame):
            return self.frame
        return _build(FrameConfig, merged)


def _to_dict(obj: Any) -> dict:
    out = {}
    for f in fields(obj):
        v = getattr(obj, f.name)
        out[f.name] = _to_dict(v) if is_dataclass(v) else v
    return out


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _build(cls, data: dict):
    """Overlay a dict onto a dataclass's defaults, recursing into nested
    dataclasses and ignoring unknown keys so a stale config never hard-fails.

    Works off instantiated defaults rather than ``f.type`` because
    ``from __future__ import annotations`` makes every annotation a string.
    """
    obj = cls()
    for f in fields(cls):
        if f.name not in data:
            continue
        v = data[f.name]
        cur = getattr(obj, f.name)
        if is_dataclass(cur) and isinstance(v, dict):
            setattr(obj, f.name, _build(type(cur), v))
        else:
            setattr(obj, f.name, v)
    return obj


def _env(name: str) -> str:
    """Read IMMPAKT_<name>, falling back to the pre-rename PICPAK_<name>.

    Kept so an existing deployment (an old Dockerfile, a shell alias, someone's
    .env) keeps working across the rename instead of silently reverting to
    defaults -- an ignored IMMPAKT_DATA_DIR would point the database somewhere
    new and look like every device had vanished.
    """
    return os.environ.get(f"IMMPAKT_{name}") or os.environ.get(f"PICPAK_{name}") or ""


def load(path: str | Path | None = None) -> Config:
    path = Path(path or _env("CONFIG") or "config.yaml")
    raw: dict = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text()) or {}

    # `crop_tolerance` was one symmetric knob before wide and tall were split.
    # Map it onto the tall side, which is the case it was really protecting.
    legacy = ((raw.get("frame") or {}).get("fit") or {}).pop("crop_tolerance", None)

    cfg = _build(Config, raw)

    if legacy is not None:
        cfg.frame.fit.crop_tolerance_tall = float(legacy)
        log.warning(
            "config frame.fit.crop_tolerance is deprecated; applied it to "
            "crop_tolerance_tall=%s. Set crop_tolerance_wide to control how "
            "far panoramas are cropped to fill the panel.", legacy,
        )

    # Env overrides -- so an API key never has to live in the config file.
    env = os.environ
    if env.get("IMMICH_URL"):
        cfg.immich.url = env["IMMICH_URL"]
    if env.get("IMMICH_API_KEY"):
        cfg.immich.api_key = env["IMMICH_API_KEY"]
    if _env("PORT"):
        cfg.server.port = int(_env("PORT"))
    if _env("DATA_DIR"):
        cfg.server.data_dir = _env("DATA_DIR")
    if _env("DEVICE_KEY"):
        cfg.server.device_key = _env("DEVICE_KEY")
    if _env("USERNAME"):
        cfg.server.auth.username = _env("USERNAME")
    if _env("PASSWORD"):
        cfg.server.auth.password = _env("PASSWORD")

    return cfg
