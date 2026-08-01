"""Panel geometry, palette, and the 2bpp wire format.

The PicPak panel is a 4.2" 400x300 four-colour e-ink (Black/White/Yellow/Red).
The firmware paints a raw, headerless buffer of exactly 30000 bytes:

    2 bits per pixel, 4 pixels per byte, MSB-first (leftmost pixel in bits 7:6)
    palette indices: 0=Black 1=White 2=Yellow 3=Red
    rows packed BOTTOM-TO-TOP (the panel scans that way)

Getting any of those three wrong produces a picture that is respectively
scrambled, mirrored in 4px blocks, or upside-down.
"""

from __future__ import annotations

import numpy as np

WIDTH = 400
HEIGHT = 300
BYTES_PER_ROW = WIDTH // 4  # 100
FRAME_BYTES = BYTES_PER_ROW * HEIGHT  # 30000

BLACK, WHITE, YELLOW, RED = 0, 1, 2, 3

# Nominal RGB for each panel colour. These are what the *dither* aims at, so
# they should match what the panel actually renders rather than pure sRGB
# primaries -- a real BWYR panel's "red" is a dark brick and its "yellow" is
# closer to mustard. Tune these against your own unit (config: palette.*);
# a palette that overstates saturation makes the dither under-use colour.
DEFAULT_PALETTE_RGB: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0),        # black
    (255, 255, 255),  # white
    (228, 190, 32),   # yellow
    (166, 42, 42),    # red
)


def palette_array(palette_rgb=DEFAULT_PALETTE_RGB) -> np.ndarray:
    """(4, 3) float array of the panel's colours, for error-diffusion maths."""
    return np.asarray(palette_rgb, dtype=np.float32)


def pack(indices: np.ndarray) -> bytes:
    """Pack a (HEIGHT, WIDTH) array of palette indices into the wire format.

    Applies the bottom-to-top row flip. Input must already be 0..3.
    """
    if indices.shape != (HEIGHT, WIDTH):
        raise ValueError(f"expected {(HEIGHT, WIDTH)} index array, got {indices.shape}")
    idx = np.ascontiguousarray(indices[::-1, :], dtype=np.uint8)  # bottom-to-top
    if idx.max(initial=0) > 3:
        raise ValueError("palette indices must be 0..3")
    packed = (
        (idx[:, 0::4] << 6) | (idx[:, 1::4] << 4) | (idx[:, 2::4] << 2) | idx[:, 3::4]
    )
    out = packed.astype(np.uint8).tobytes()
    if len(out) != FRAME_BYTES:
        raise AssertionError(f"packed {len(out)} bytes, expected {FRAME_BYTES}")
    return out


def unpack(data: bytes) -> np.ndarray:
    """Inverse of :func:`pack` -- used by the preview endpoint and tests."""
    if len(data) != FRAME_BYTES:
        raise ValueError(f"expected {FRAME_BYTES} bytes, got {len(data)}")
    packed = np.frombuffer(data, dtype=np.uint8).reshape(HEIGHT, BYTES_PER_ROW)
    idx = np.empty((HEIGHT, WIDTH), dtype=np.uint8)
    idx[:, 0::4] = (packed >> 6) & 3
    idx[:, 1::4] = (packed >> 4) & 3
    idx[:, 2::4] = (packed >> 2) & 3
    idx[:, 3::4] = packed & 3
    return idx[::-1, :]  # undo the bottom-to-top flip


def to_rgb(indices: np.ndarray, palette_rgb=DEFAULT_PALETTE_RGB) -> np.ndarray:
    """Render palette indices back to an (H, W, 3) uint8 image for previews."""
    return np.asarray(palette_rgb, dtype=np.uint8)[indices]
