"""The firmware<->server wire contract.

These tests read the actual format string out of firmware/main/frame_client.c so
that changing the request on the device side without changing the server (or
vice versa) fails here rather than on a frame that silently stops updating.
"""

import re
from pathlib import Path

import pytest

from immpakt import panel

FRAME_CLIENT = Path(__file__).resolve().parents[1] / "firmware/main/frame_client.c"
pytestmark = pytest.mark.skipif(
    not FRAME_CLIENT.exists(), reason="firmware sources not present"
)


def firmware_request_format() -> str:
    """The snprintf format the device builds its GET from."""
    src = FRAME_CLIENT.read_text()
    m = re.search(r'"(%s/api/frame\.bin\?[^"]+)"', src)
    assert m, "could not find the frame.bin request format in frame_client.c"
    return m.group(1)


def firmware_headers() -> set[str]:
    src = FRAME_CLIENT.read_text()
    return {
        *re.findall(r'esp_http_client_set_header\(c,\s*"([^"]+)"', src),
        *re.findall(r'strcasecmp\(e->header_key,\s*"([^"]+)"\)', src),
    }


def test_device_url_matches_the_server_endpoint():
    fmt = firmware_request_format()
    assert fmt.startswith("%s/api/frame.bin?")
    # Every query key the firmware sends must be one the server accepts.
    keys = set(re.findall(r"[?&]([a-z_]+)=", fmt))
    assert keys == {"id", "mv", "pct", "rssi", "wake"}


def test_device_sends_and_reads_the_right_headers():
    hdrs = {h.lower() for h in firmware_headers()}
    assert "if-none-match" in hdrs, "device must send If-None-Match for the 304 path"
    assert "etag" in hdrs, "device must read ETag to remember what it painted"
    assert "x-next-wake" in hdrs, "device must read its sleep interval"


def test_device_key_is_appended_as_the_key_param():
    src = FRAME_CLIENT.read_text()
    assert re.search(r'"&key=%s"', src), "optional device key must append &key="


def test_firmware_expects_the_panel_frame_size():
    """EPD_FB_BYTES in board.h must equal what the renderer emits."""
    board = (FRAME_CLIENT.parent / "board.h").read_text()
    fb = int(re.search(r"#define EPD_FB_BYTES\s+(\d+)", board).group(1))
    w = int(re.search(r"#define EPD_W\s+(\d+)", board).group(1))
    h = int(re.search(r"#define EPD_H\s+(\d+)", board).group(1))
    assert (fb, w, h) == (panel.FRAME_BYTES, panel.WIDTH, panel.HEIGHT)


def test_a_real_device_request_is_served(client):
    """Replay the exact URL shape the firmware builds, including the quoted
    ETag round-trip: the server emits ETag: "abc" and the device stores and
    returns that value verbatim, quotes included."""
    c, _ = client(n_assets=1)
    fmt = firmware_request_format()
    path = fmt.replace("%s/", "/", 1) % (
        "picpak-a1b2c3", 4164, 96, -63, "timer",
    )

    first = c.get(path)
    assert first.status_code == 200
    assert len(first.content) == panel.FRAME_BYTES
    assert first.headers["x-next-wake"].isdigit()

    etag = first.headers["etag"]
    assert etag.startswith('"') and etag.endswith('"'), "server quotes its ETag"

    again = c.get(path, headers={"If-None-Match": etag})
    assert again.status_code == 304, "verbatim quoted ETag must be recognised"
    assert again.headers["x-next-wake"].isdigit()
