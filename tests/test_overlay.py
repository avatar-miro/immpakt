import numpy as np

from immpakt import overlay, panel
from immpakt.config import OverlayConfig
from immpakt.immich import Asset

ASSET = Asset(id="x", taken_at="2020-06-01T12:00:00.000Z",
              city="Reykjavik", country="Iceland")


def blank():
    return np.full((panel.HEIGHT, panel.WIDTH), panel.BLACK, dtype=np.uint8)


def test_location_is_stacked_below_the_date():
    text = overlay.caption(ASSET, OverlayConfig())
    assert text.splitlines() == ["1 June 2020", "Reykjavik Iceland"]


def test_each_field_can_be_turned_off():
    assert overlay.caption(ASSET, OverlayConfig(show_location=False)) == "1 June 2020"
    assert overlay.caption(ASSET, OverlayConfig(show_date=False)) == "Reykjavik Iceland"
    assert overlay.caption(ASSET, OverlayConfig(show_date=False, show_location=False)) == ""


def test_missing_metadata_degrades_to_one_line_or_nothing():
    bare = Asset(id="x")
    assert overlay.caption(bare, OverlayConfig()) == ""
    assert "\n" not in overlay.caption(
        Asset(id="x", city="Lisbon"), OverlayConfig())


def test_multiline_caption_stays_inside_the_panel():
    """Regression: single-line textbbox()/text() would measure the '\\n'
    literally and push the second line off the right edge."""
    cfg = OverlayConfig(enabled=True)
    long_asset = Asset(id="x", taken_at="2020-06-01T12:00:00.000Z",
                       city="Llanfairpwllgwyngyll", country="United Kingdom")
    for pos in ("bottom-left", "bottom-right", "top-left", "top-right"):
        cfg.position = pos
        out = overlay.draw(blank(), overlay.caption(long_asset, cfg), cfg)
        assert out.shape == (panel.HEIGHT, panel.WIDTH)
        # Text must have landed, and not bled to the very edge columns.
        assert (out != panel.BLACK).any(), f"{pos}: nothing drawn"
        assert not (out[:, 0] == cfg.color).all()
        assert len(panel.pack(out)) == panel.FRAME_BYTES


def test_caption_occupies_two_bands_not_one():
    """Proof the second line is on its own row band rather than beside it."""
    cfg = OverlayConfig(enabled=True, position="top-left")
    out = overlay.draw(blank(), overlay.caption(ASSET, cfg), cfg)
    rows = np.flatnonzero((out != panel.BLACK).any(axis=1))
    # A gap between the two lines means two distinct bands of inked rows.
    gaps = np.diff(rows)
    assert (gaps > 1).any(), "expected a blank row between date and location"


def test_empty_caption_is_a_no_op():
    src = blank()
    assert np.array_equal(overlay.draw(src, "", OverlayConfig()), src)


def test_margin_insets_the_caption_from_the_edges():
    """8px read as flush against the edge on a 400px panel."""
    def left_edge(margin):
        cfg = OverlayConfig(enabled=True, position="top-left", margin=margin)
        out = overlay.draw(blank(), overlay.caption(ASSET, cfg), cfg)
        cols = np.flatnonzero((out != panel.BLACK).any(axis=0))
        return int(cols[0])

    tight, roomy = left_edge(4), left_edge(24)
    assert roomy > tight, "a larger margin must push the caption inward"
    assert roomy >= 20


def test_margin_applies_to_the_right_edge_too():
    cfg = OverlayConfig(enabled=True, position="top-right", margin=24)
    out = overlay.draw(blank(), overlay.caption(ASSET, cfg), cfg)
    cols = np.flatnonzero((out != panel.BLACK).any(axis=0))
    assert panel.WIDTH - int(cols[-1]) >= 20


def test_absurd_margin_cannot_push_the_caption_off_panel():
    cfg = OverlayConfig(enabled=True, position="bottom-right", margin=5000)
    out = overlay.draw(blank(), overlay.caption(ASSET, cfg), cfg)
    assert (out != panel.BLACK).any(), "caption must still be on the panel"


def test_long_location_shrinks_to_respect_both_margins():
    """A caption that honours the left inset but bleeds off the right edge
    looks like a bug. Real place names are wider than the panel at 16px."""
    cfg = OverlayConfig(enabled=True, position="bottom-left", margin=16)
    long_asset = Asset(id="x", taken_at="2024-10-13T12:00:00.000Z",
                       city="San Antonio", country="United States of America")
    out = overlay.draw(blank(), overlay.caption(long_asset, cfg), cfg)
    cols = np.flatnonzero((out != panel.BLACK).any(axis=0))
    assert int(cols[0]) >= 16 - 2, "left margin"
    assert panel.WIDTH - int(cols[-1]) >= 16 - 2, "right margin must hold too"


def test_short_caption_keeps_its_configured_size():
    """Shrink-to-fit must not shrink things that already fit."""
    cfg = OverlayConfig(enabled=True, position="top-left", font_size=16)
    short = Asset(id="x", city="Rome")
    tall = overlay.draw(blank(), overlay.caption(short, cfg), cfg)
    rows = np.flatnonzero((tall != panel.BLACK).any(axis=1))
    big = OverlayConfig(enabled=True, position="top-left", font_size=30)
    rows_big = np.flatnonzero(
        (overlay.draw(blank(), overlay.caption(short, big), big) != panel.BLACK).any(axis=1))
    assert len(rows_big) > len(rows), "a larger font must still render larger"
