"""Caption overlay (date / location), drawn *after* quantisation.

Text is composited in palette-index space rather than in RGB before the dither.
Dithered small text on a four-colour panel is mush; writing pure palette
indices keeps glyph edges crisp at 16px.
"""

from __future__ import annotations

import datetime as dt
import logging

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import panel

log = logging.getLogger(__name__)

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial.ttf",
)


def _font(size: int):
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    log.debug("no TrueType font found; falling back to PIL's bitmap font")
    return ImageFont.load_default()


def caption(asset, cfg) -> str:
    parts: list[str] = []
    if cfg.show_date and asset.taken_at:
        try:
            when = dt.datetime.fromisoformat(asset.taken_at.replace("Z", "+00:00"))
            try:
                parts.append(when.strftime(cfg.date_format))
            except ValueError:  # platform without %-d
                parts.append(when.strftime("%d %B %Y").lstrip("0"))
        except ValueError:
            pass
    if cfg.show_location:
        where = " ".join(p for p in (asset.city, asset.country) if p)
        if where:
            parts.append(where)
    # Stacked, not joined on one line: at 400px a combined
    # "1 June 2020 · Reykjavik Iceland" overflows the panel at the default
    # 16px, and the two facts read faster one above the other anyway.
    return "\n".join(parts)


def draw(indices: np.ndarray, text: str, cfg) -> np.ndarray:
    """Return a copy of ``indices`` with ``text`` composited in."""
    if not text:
        return indices

    align = "left" if cfg.position.endswith("left") else "right"
    mask = Image.new("L", (panel.WIDTH, panel.HEIGHT), 0)
    d = ImageDraw.Draw(mask)

    # Clamped so a silly margin can never push the caption off-panel.
    pad = max(0, min(int(getattr(cfg, "margin", 16)), panel.WIDTH // 4))
    avail = panel.WIDTH - 2 * pad

    # Shrink to fit rather than overrun the opposite margin: real place names
    # ("San Antonio United States of America") are far wider than the panel at
    # the default size, and a caption that respects the left inset while
    # bleeding off the right edge looks like a bug.
    size = max(9, int(cfg.font_size))
    while True:
        font = _font(size)
        spacing = max(2, size // 4)
        # multiline_* rather than the single-line variants: the caption is
        # newline-separated, and textbbox()/text() would measure and draw the
        # "\n" literally, putting the location off the side of the panel.
        x0, y0, x1, y1 = d.multiline_textbbox(
            (0, 0), text, font=font, spacing=spacing, align=align)
        if x1 - x0 <= avail or size <= 9:
            break
        size -= 1

    tw, th = x1 - x0, y1 - y0
    x = pad if align == "left" else panel.WIDTH - tw - pad
    y = pad if cfg.position.startswith("top") else panel.HEIGHT - th - pad

    def stamp(target, ox, oy, fill):
        ImageDraw.Draw(target).multiline_text(
            (x - x0 + ox, y - y0 + oy), text, fill=fill, font=font,
            spacing=spacing, align=align,
        )

    # A 1px outline in the shadow colour keeps the caption legible over both
    # bright and dark regions of the photo without a translucent bar (there is
    # no alpha on this panel).
    out = indices.copy()
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1)):
        stamp(mask, dx, dy, 255)
    out[np.asarray(mask) > 127] = cfg.shadow

    mask = Image.new("L", (panel.WIDTH, panel.HEIGHT), 0)
    stamp(mask, 0, 0, 255)
    out[np.asarray(mask) > 127] = cfg.color
    return out
