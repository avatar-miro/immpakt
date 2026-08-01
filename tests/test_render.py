import io

import numpy as np
import pytest
from PIL import Image

from immpakt import panel, render
from immpakt.render import FitOptions


def jpeg(w, h, color=(120, 80, 200)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="JPEG", quality=92)
    return buf.getvalue()


# -- wire format -----------------------------------------------------------


def test_frame_is_exactly_30000_bytes():
    frame, _ = render.render(jpeg(1600, 1200))
    assert len(frame) == panel.FRAME_BYTES == 30000


def test_pack_unpack_roundtrip():
    rng = np.random.default_rng(0)
    idx = rng.integers(0, 4, (panel.HEIGHT, panel.WIDTH), dtype=np.uint8)
    assert np.array_equal(panel.unpack(panel.pack(idx)), idx)


def test_rows_are_packed_bottom_to_top():
    """The panel scans bottom-to-top; a top-row marker must land in the LAST
    row of the buffer. Getting this wrong paints every photo upside-down."""
    idx = np.full((panel.HEIGHT, panel.WIDTH), panel.WHITE, dtype=np.uint8)
    idx[0, :] = panel.RED
    data = panel.pack(idx)
    last_row = data[-panel.BYTES_PER_ROW:]
    assert set(last_row) == {panel.RED * 0x55}
    assert set(data[: panel.BYTES_PER_ROW]) == {panel.WHITE * 0x55}


def test_pixel_order_is_msb_first():
    idx = np.zeros((panel.HEIGHT, panel.WIDTH), dtype=np.uint8)
    idx[panel.HEIGHT - 1, 0] = panel.RED  # bottom-left -> first byte after flip
    assert panel.pack(idx)[0] == panel.RED << 6


def test_pack_rejects_out_of_range_indices():
    idx = np.full((panel.HEIGHT, panel.WIDTH), 4, dtype=np.uint8)
    with pytest.raises(ValueError):
        panel.pack(idx)


# -- fit policy ------------------------------------------------------------


def _has_mat(indices, color=panel.WHITE):
    """True when the image was matted rather than cropped.

    Which edges carry the bars depends on the source: a portrait in a landscape
    panel is pillarboxed (left/right), a too-wide landscape is letterboxed
    (top/bottom). Accept either.
    """
    letterbox = (indices[0] == color).all() and (indices[-1] == color).all()
    pillarbox = (indices[:, 0] == color).all() and (indices[:, -1] == color).all()
    return bool(letterbox or pillarbox)


def test_native_4_3_is_not_cropped():
    src = Image.new("RGB", (2000, 1500))
    out = render.fit_to_panel(src, FitOptions())
    assert out.size == (panel.WIDTH, panel.HEIGHT)


@pytest.mark.parametrize("size", [(1920, 1080), (3000, 2000), (1600, 1200)])
def test_landscape_shapes_crop_rather_than_mat(size):
    frame, idx = render.render(jpeg(*size, color=(30, 30, 30)))
    assert not _has_mat(idx)


@pytest.mark.parametrize("size", [(4000, 1333), (6000, 1500), (8000, 1200)])
def test_panoramas_fill_the_panel(size):
    """Wide panoramas should crop to fill, not sit in a letterbox -- losing the
    sides of a pano is the point, and bars waste most of a 400x300 panel."""
    _, idx = render.render(jpeg(*size, color=(30, 30, 30)))
    assert not _has_mat(idx)


def test_wide_and_tall_tolerances_are_independent_under_auto():
    """A 3:1 pano (2.25x) crops while a 9:16 portrait (2.37x) mats -- the whole
    reason `auto` needs two knobs rather than one symmetric one."""
    opts = render.RenderOptions(fit=FitOptions(
        mode="auto", crop_tolerance_wide=8.0, crop_tolerance_tall=1.40))
    _, pano = render.render(jpeg(3000, 1000, color=(30, 30, 30)), opts)
    _, tall = render.render(jpeg(1080, 1920, color=(30, 30, 30)), opts)
    assert not _has_mat(pano)
    assert _has_mat(tall)


def test_wide_tolerance_can_be_lowered_to_mat_panoramas():
    opts = render.RenderOptions(
        fit=FitOptions(mode="auto", crop_tolerance_wide=1.4))
    _, idx = render.render(jpeg(3000, 1000, color=(30, 30, 30)), opts)
    assert _has_mat(idx)


@pytest.mark.parametrize("size", [(1200, 1600), (1080, 1920), (900, 1600)])
def test_portraits_are_cropped_to_fill_by_default(size):
    _, idx = render.render(jpeg(*size, color=(30, 30, 30)))
    assert not _has_mat(idx)


@pytest.mark.parametrize(
    "size",
    [(1600, 1200), (1920, 1080), (4000, 1180), (8000, 1000),  # landscape
     (1200, 1600), (1080, 1920), (1000, 2400)],               # portrait
)
def test_cover_never_leaves_bars_at_any_aspect(size):
    """The default contract: whatever the source shape, all 400x300 is photo."""
    _, idx = render.render(jpeg(*size, color=(30, 30, 30)))
    assert not _has_mat(idx)


def test_auto_mode_still_mats_portraits_when_asked_for():
    opts = render.RenderOptions(fit=FitOptions(mode="auto"))
    _, idx = render.render(jpeg(1080, 1920, color=(30, 30, 30)), opts)
    assert _has_mat(idx)


def test_contain_mode_forces_mat_even_for_landscape():
    opts = render.RenderOptions(fit=FitOptions(mode="contain"))
    _, idx = render.render(jpeg(1920, 1080, color=(30, 30, 30)), opts)
    assert _has_mat(idx)


def test_face_aware_crop_shifts_window_toward_faces():
    # A tall source cropped to 4:3 slides vertically; a face near the top
    # should pull the window up relative to the centred default.
    src_w, src_h = 1200, 1600
    centred = render._crop_box_cover(src_w, src_h, None)
    with_face = render._crop_box_cover(src_w, src_h, [(0.4, 0.05, 0.6, 0.20)])
    assert with_face[1] < centred[1]


def test_crop_box_never_leaves_the_source():
    for faces in ([(0.0, 0.0, 0.05, 0.05)], [(0.95, 0.95, 1.0, 1.0)], None):
        l, t, r, b = render._crop_box_cover(1200, 1600, faces)
        assert 0 <= l < r <= 1200 and 0 <= t < b <= 1600


# -- quantisation ----------------------------------------------------------


def test_only_palette_indices_are_emitted():
    _, idx = render.render(jpeg(1600, 1200, color=(200, 60, 40)))
    assert set(np.unique(idx)) <= {0, 1, 2, 3}


def test_dither_none_picks_nearest_colour():
    opts = render.RenderOptions(dither="none", enhance=render.EnhanceOptions(
        autocontrast=False, contrast=1.0, saturation=1.0, sharpness=1.0))
    _, idx = render.render(jpeg(1600, 1200, color=(255, 255, 255)), opts)
    assert (idx == panel.WHITE).all()


def test_dither_uses_colour_for_a_midtone_that_no_palette_entry_matches():
    """A flat mid-grey has no exact palette entry, so error diffusion must mix
    -- a result of all-one-colour would mean the dither silently no-opped."""
    opts = render.RenderOptions(enhance=render.EnhanceOptions(
        autocontrast=False, contrast=1.0, saturation=1.0, sharpness=1.0))
    _, idx = render.render(jpeg(1600, 1200, color=(128, 128, 128)), opts)
    assert len(np.unique(idx)) > 1


def test_exif_orientation_is_honoured():
    """Orientation 6 = rotate 90 CW on display, so a 1600x1200 file tagged that
    way is really a 1200x1600 portrait. Checked under `contain`, where the bars
    reveal which way the renderer read it: a portrait pillarboxes (bars on the
    sides), a landscape letterboxes (bars top and bottom)."""
    img = Image.new("RGB", (1600, 1200), (30, 30, 30))
    exif = img.getexif()
    exif[274] = 6
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)

    opts = render.RenderOptions(fit=FitOptions(mode="contain"))
    _, idx = render.render(buf.getvalue(), opts)
    pillarbox = (idx[:, 0] == panel.WHITE).all() and (idx[:, -1] == panel.WHITE).all()
    letterbox = (idx[0] == panel.WHITE).all() and (idx[-1] == panel.WHITE).all()
    assert pillarbox and not letterbox


def test_preview_png_is_decodable_and_panel_sized():
    _, idx = render.render(jpeg(1600, 1200))
    png = Image.open(io.BytesIO(render.preview_png(idx, scale=1)))
    assert png.size == (panel.WIDTH, panel.HEIGHT)
