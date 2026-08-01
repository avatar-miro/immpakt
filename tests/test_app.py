from immpakt import panel

# FakeClient / FakePool / the `client` fixture live in conftest.py so the
# firmware-contract tests can share them.


def test_frame_returns_30000_bytes_with_protocol_headers(client):
    c, _ = client()
    r = c.get("/api/frame.bin", params={"id": "picpak-aaa", "mv": 4164, "pct": 96, "rssi": -63})
    assert r.status_code == 200
    assert len(r.content) == panel.FRAME_BYTES
    assert r.headers["x-next-wake"] == "21600"
    assert r.headers["etag"]


def test_device_self_registers_and_telemetry_is_recorded(client):
    c, st = client()
    c.get("/api/frame.bin", params={"id": "picpak-new", "mv": 3900, "pct": 55,
                                    "rssi": -70, "wake": "button"})
    dev = st.store.get("picpak-new")
    assert dev is not None
    assert (dev.battery_mv, dev.battery_pct, dev.rssi) == (3900, 55, -70)
    assert dev.wake_reason == "button"
    assert dev.wakes == 1


def test_each_wake_advances_to_a_different_photo(client):
    c, _ = client()
    etags = {
        c.get("/api/frame.bin", params={"id": "picpak-aaa"}).headers["etag"]
        for _ in range(5)
    }
    assert len(etags) == 5


def test_304_when_the_resolved_photo_is_already_painted(client):
    """With a single-photo pool every wake resolves to the same frame, so the
    device must be told to skip the 13-22s repaint rather than redraw it."""
    c, st = client(n_assets=1)
    first = c.get("/api/frame.bin", params={"id": "picpak-aaa"})
    assert first.status_code == 200
    before = st.client.image_calls

    again = c.get("/api/frame.bin", params={"id": "picpak-aaa"},
                  headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["x-next-wake"] == "21600"
    assert st.client.image_calls == before, "render cache should absorb the repeat"


def test_device_key_is_enforced_when_configured(client):
    c, _ = client(device_key="s3cret")
    assert c.get("/api/frame.bin", params={"id": "picpak-aaa"}).status_code == 403
    assert c.get("/api/frame.bin", params={"id": "picpak-aaa", "key": "nope"}).status_code == 403
    assert c.get("/api/frame.bin", params={"id": "picpak-aaa", "key": "s3cret"}).status_code == 200


def test_empty_pool_reports_503_rather_than_a_blank_frame(client):
    c, _ = client(n_assets=0)
    assert c.get("/api/frame.bin", params={"id": "picpak-aaa"}).status_code == 503


def test_per_device_overrides_apply(client):
    c, st = client()
    st.cfg.devices["picpak-portrait"] = {"interval_s": 43200, "rotate": 90}
    r = c.get("/api/frame.bin", params={"id": "picpak-portrait"})
    assert r.headers["x-next-wake"] == "43200"
    plain = c.get("/api/frame.bin", params={"id": "picpak-aaa"})
    assert plain.headers["x-next-wake"] == "21600"


def test_preview_does_not_advance_the_device(client):
    c, st = client()
    c.get("/api/frame.bin", params={"id": "picpak-aaa"})
    cursor = st.store.get("picpak-aaa").cursor
    r = c.get("/preview.png", params={"id": "picpak-aaa"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert st.store.get("picpak-aaa").cursor == cursor


def test_dashboard_and_status_render(client):
    c, _ = client()
    c.get("/api/frame.bin", params={"id": "picpak-aaa", "mv": 4100, "pct": 90})
    assert "picpak-aaa" in c.get("/").text
    body = c.get("/api/status").json()
    assert body["pool"]["count"] == 8
    assert body["devices"][0]["battery_mv"] == 4100


def test_preview_does_not_register_a_phantom_device(client):
    """Viewing a preview must not mint a device row. The old behaviour was
    self-sustaining: the dashboard renders an <img> per device, so a phantom
    kept re-registering itself on every page refresh and could never be
    deleted while anyone had the page open."""
    c, st = client()
    r = c.get("/preview.png", params={"id": "preview"})
    assert r.status_code == 200
    assert st.store.get("preview") is None
    assert st.store.all() == []


def test_deleting_a_device_removes_it_and_its_telemetry(client):
    c, st = client()
    c.get("/api/frame.bin", params={"id": "picpak-aaa", "mv": 4000, "pct": 88})
    assert st.store.get("picpak-aaa") is not None

    assert c.delete("/api/devices/picpak-aaa").status_code == 200
    assert st.store.get("picpak-aaa") is None
    assert st.store.history("picpak-aaa") == []
    assert c.delete("/api/devices/picpak-aaa").status_code == 404


def test_history_is_returned_oldest_first_for_plotting(client):
    c, st = client()
    for mv in (4200, 4100, 4000):
        st.store.touch("picpak-aaa", battery_mv=mv, battery_pct=mv // 45)
    mvs = [r["battery_mv"] for r in st.store.history("picpak-aaa")]
    assert mvs == [4200, 4100, 4000], "sparkline plots left-to-right in time"


def test_legacy_database_is_adopted_not_abandoned(tmp_path):
    """The DB was picpak.db before the rename. Starting fresh instead of
    adopting it would drop every device, its telemetry and its settings."""
    from immpakt.store import Store

    old = Store(tmp_path / "picpak.db")
    old.touch("picpak-aaa", battery_mv=4000, battery_pct=88)
    old.set_settings("picpak-aaa", {"interval_s": 900})
    old.close()

    new = Store(tmp_path / "immpakt.db")
    dev = new.get("picpak-aaa")
    assert dev is not None and dev.battery_mv == 4000
    assert new.get_settings("picpak-aaa")["interval_s"] == 900
    assert not (tmp_path / "picpak.db").exists()
