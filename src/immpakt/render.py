"""JPEG -> 400x300 four-colour panel buffer.

This is the part of the system that decides how good the frame looks, and it is
deliberately server-side: the ESP32-C3 has ~400 KB of SRAM and no PSRAM, so a
single 400x300 RGB888 buffer (360 KB) would not coexist with WiFi and TLS.

Pipeline:  orient -> fit -> enhance -> dither -> pack
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageEnhance, ImageOps

from . import panel

PANEL_ASPECT = panel.WIDTH / panel.HEIGHT  # 1.3333


@dataclass
class FitOptions:
    """How to reconcile the source photo's aspect with the panel's 4:3.

    Default is ``cover``: every photo fills all 400x300, whatever its aspect.
    Nothing is ever letterboxed, and the crop tolerances below are unused.

    ``auto`` is the alternative -- crop up to a limit, mat beyond it. Its two
    tolerances are the largest aspect *mismatch factor* still solved by
    cropping, and are deliberately asymmetric, since losing the sides of a wide
    photo costs less than losing the top and bottom of a tall one:

        source        mismatch   knob under `auto`       `auto` result
        4:3            1.00x     --                      cover, nothing lost
        3:2            1.13x     crop_tolerance_wide     cover
        16:9           1.33x     crop_tolerance_wide     cover, ~25% off sides
        2:1            1.50x     crop_tolerance_wide     cover
        3:1 panorama   2.25x     crop_tolerance_wide     cover
        3:4 portrait   1.78x     crop_tolerance_tall     mat
        9:16 portrait  2.37x     crop_tolerance_tall     mat

    Under the default ``cover`` every row above fills the panel instead, which
    puts real weight on ``face_aware``: a 9:16 portrait keeps only ~42% of its
    height, so the crop window has to land on the subject.
    """

    mode: str = "cover"  # cover (fill always) | auto (crop, then mat) | contain
    # Only consulted when mode == "auto".
    crop_tolerance_wide: float = 8.0
    crop_tolerance_tall: float = 1.40
    mat_color: int = panel.WHITE  # palette index used for letterbox bars
    face_aware: bool = True


@dataclass
class EnhanceOptions:
    """Pre-dither tone/colour shaping.

    A four-colour panel only has red and yellow as chroma, so unmodified photos
    dither toward a washed-out grey. Pushing saturation and contrast before
    quantisation is what makes the panel actually use its colours.
    """

    autocontrast: bool = True
    autocontrast_cutoff: float = 1.0
    brightness: float = 1.0
    contrast: float = 1.10
    saturation: float = 1.45
    sharpness: float = 1.20


@dataclass
class RenderOptions:
    fit: FitOptions = field(default_factory=FitOptions)
    enhance: EnhanceOptions = field(default_factory=EnhanceOptions)
    dither: str = "floyd-steinberg"  # floyd-steinberg | none
    dither_strength: float = 0.9
    palette_rgb: tuple = panel.DEFAULT_PALETTE_RGB
    rotate: int = 0  # 0 | 90 | 180 | 270, applied last (portrait hanging)


# --------------------------------------------------------------------------
# fit
# --------------------------------------------------------------------------


def _crop_box_cover(
    src_w: int, src_h: int, faces: list[tuple[float, float, float, float]] | None
) -> tuple[int, int, int, int]:
    """Largest 4:3 window inside the source, centred on faces when available.

    ``faces`` are normalised (x1, y1, x2, y2) boxes in 0..1 source coordinates.
    """
    if src_w / src_h > PANEL_ASPECT:
        # Source is wider than the panel: full height, slide horizontally.
        box_w, box_h = int(round(src_h * PANEL_ASPECT)), src_h
    else:
        # Source is taller: full width, slide vertically.
        box_w, box_h = src_w, int(round(src_w / PANEL_ASPECT))

    # Default to the centre, then bias toward the centroid of detected faces.
    cx, cy = src_w / 2.0, src_h / 2.0
    if faces:
        cx = float(np.mean([(f[0] + f[2]) / 2.0 for f in faces])) * src_w
        cy = float(np.mean([(f[1] + f[3]) / 2.0 for f in faces])) * src_h
        # Portrait subjects read better with headroom: bias the window up a
        # little so a face centroid does not sit dead-centre.
        cy -= box_h * 0.05

    left = int(round(cx - box_w / 2.0))
    top = int(round(cy - box_h / 2.0))
    left = max(0, min(left, src_w - box_w))
    top = max(0, min(top, src_h - box_h))
    return left, top, left + box_w, top + box_h


def fit_to_panel(
    img: Image.Image,
    opts: FitOptions,
    faces: list[tuple[float, float, float, float]] | None = None,
    mat_rgb: tuple[int, int, int] = (255, 255, 255),
) -> Image.Image:
    """Return a WIDTH x HEIGHT RGB image, cropping or matting as configured."""
    src_w, src_h = img.size
    src_aspect = src_w / src_h

    mode = opts.mode
    if mode == "auto":
        if src_aspect >= PANEL_ASPECT:  # wider than the panel
            mismatch, tolerance = src_aspect / PANEL_ASPECT, opts.crop_tolerance_wide
        else:  # taller than the panel
            mismatch, tolerance = PANEL_ASPECT / src_aspect, opts.crop_tolerance_tall
        mode = "cover" if mismatch <= tolerance else "contain"

    if mode == "cover":
        box = _crop_box_cover(src_w, src_h, faces if opts.face_aware else None)
        return img.resize(
            (panel.WIDTH, panel.HEIGHT), Image.Resampling.LANCZOS, box=box
        )

    # contain: scale the whole photo in, pad the remainder with the mat colour.
    scaled = ImageOps.contain(
        img, (panel.WIDTH, panel.HEIGHT), Image.Resampling.LANCZOS
    )
    canvas = Image.new("RGB", (panel.WIDTH, panel.HEIGHT), mat_rgb)
    canvas.paste(
        scaled, ((panel.WIDTH - scaled.width) // 2, (panel.HEIGHT - scaled.height) // 2)
    )
    return canvas


# --------------------------------------------------------------------------
# enhance
# --------------------------------------------------------------------------


def enhance(img: Image.Image, opts: EnhanceOptions) -> Image.Image:
    if opts.autocontrast:
        img = ImageOps.autocontrast(img, cutoff=opts.autocontrast_cutoff)
    for factor, cls in (
        (opts.brightness, ImageEnhance.Brightness),
        (opts.contrast, ImageEnhance.Contrast),
        (opts.saturation, ImageEnhance.Color),
        (opts.sharpness, ImageEnhance.Sharpness),
    ):
        if abs(factor - 1.0) > 1e-3:
            img = cls(img).enhance(factor)
    return img


# --------------------------------------------------------------------------
# dither
# --------------------------------------------------------------------------


def _nearest(rgb: np.ndarray, pal: np.ndarray) -> np.ndarray:
    """Vectorised nearest-palette-colour lookup. rgb is (H, W, 3) float."""
    d = rgb[:, :, None, :] - pal[None, None, :, :]
    return np.argmin(np.einsum("hwpc,hwpc->hwp", d, d), axis=2).astype(np.uint8)


def dither_floyd_steinberg(
    rgb: np.ndarray, pal: np.ndarray, strength: float = 1.0
) -> np.ndarray:
    """Serpentine Floyd-Steinberg error diffusion onto a 4-entry palette.

    Inherently sequential, so this is a Python loop over ~120k pixels (a few
    hundred ms). Frames are cached and regenerate on a multi-hour cadence, so
    the cost never lands on a device request.
    """
    buf = rgb.astype(np.float32).copy()
    h, w, _ = buf.shape
    out = np.zeros((h, w), dtype=np.uint8)

    for y in range(h):
        rtl = y % 2 == 1  # serpentine: alternate direction to avoid worming
        xs = range(w - 1, -1, -1) if rtl else range(w)
        step = -1 if rtl else 1
        for x in xs:
            old = buf[y, x]
            diff = pal - old
            i = int(np.argmin(np.einsum("pc,pc->p", diff, diff)))
            out[y, x] = i
            err = (old - pal[i]) * strength

            nx = x + step
            if 0 <= nx < w:
                buf[y, nx] += err * (7 / 16)
            if y + 1 < h:
                if 0 <= x - step < w:
                    buf[y + 1, x - step] += err * (3 / 16)
                buf[y + 1, x] += err * (5 / 16)
                if 0 <= nx < w:
                    buf[y + 1, nx] += err * (1 / 16)

    return out


def quantize(img: Image.Image, opts: RenderOptions) -> np.ndarray:
    pal = panel.palette_array(opts.palette_rgb)
    rgb = np.asarray(img.convert("RGB"), dtype=np.float32)
    if opts.dither == "none":
        return _nearest(rgb, pal)
    return dither_floyd_steinberg(rgb, pal, opts.dither_strength)


# --------------------------------------------------------------------------
# top level
# --------------------------------------------------------------------------


def render(
    data: bytes,
    opts: RenderOptions | None = None,
    faces: list[tuple[float, float, float, float]] | None = None,
) -> tuple[bytes, np.ndarray]:
    """Render encoded image bytes to (30000-byte frame, index array).

    The index array is returned so callers can build a preview PNG without
    re-doing the expensive dither.
    """
    opts = opts or RenderOptions()
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img).convert("RGB")

    mat_rgb = tuple(int(c) for c in opts.palette_rgb[opts.fit.mat_color])
    img = fit_to_panel(img, opts.fit, faces=faces, mat_rgb=mat_rgb)
    img = enhance(img, opts.enhance)

    if opts.rotate:
        # Rotate after fitting so the composition is chosen in panel space.
        img = img.rotate(opts.rotate, expand=False)

    indices = quantize(img, opts)
    return panel.pack(indices), indices


def preview_png(indices: np.ndarray, opts: RenderOptions | None = None, scale: int = 2) -> bytes:
    """Exactly what the panel will show, as a PNG, for tuning without hardware."""
    opts = opts or RenderOptions()
    img = Image.fromarray(panel.to_rgb(indices, opts.palette_rgb), mode="RGB")
    if scale > 1:
        img = img.resize(
            (img.width * scale, img.height * scale), Image.Resampling.NEAREST
        )
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()
