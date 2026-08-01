"""Dashboard-edited per-device overrides."""


def test_saved_interval_reaches_the_device_as_x_next_wake(client):
    """The whole point: an edit in the browser changes what the frame is told
    to do on its next wake. There is no push channel -- this header IS the
    delivery mechanism."""
    c, _ = client()
    c.get("/api/frame.bin", params={"id": "picpak-aaa"})
    assert c.get("/api/frame.bin", params={"id": "picpak-aaa"}).headers["x-next-wake"] == "21600"

    r = c.post("/api/devices/picpak-aaa/settings", json={"interval_s": 900})
    assert r.status_code == 200
    assert r.json()["overrides"]["interval_s"] == 900

    assert c.get("/api/frame.bin", params={"id": "picpak-aaa"}).headers["x-next-wake"] == "900"


def test_overrides_persist_and_merge_rather_than_replace(client):
    c, st = client()
    c.get("/api/frame.bin", params={"id": "picpak-aaa"})
    c.post("/api/devices/picpak-aaa/settings", json={"interval_s": 3600})
    c.post("/api/devices/picpak-aaa/settings", json={"overlay.enabled": True})

    saved = st.store.get_settings("picpak-aaa")
    assert saved["interval_s"] == 3600, "second save must not drop the first"
    assert saved["overlay"]["enabled"] is True


def test_overrides_beat_the_config_file(client):
    c, st = client()
    c.get("/api/frame.bin", params={"id": "picpak-aaa"})
    st.cfg.devices["picpak-aaa"] = {"interval_s": 111600}
    assert c.get("/api/frame.bin", params={"id": "picpak-aaa"}).headers["x-next-wake"] == "111600"

    c.post("/api/devices/picpak-aaa/settings", json={"interval_s": 7200})
    assert c.get("/api/frame.bin", params={"id": "picpak-aaa"}).headers["x-next-wake"] == "7200"


def test_only_whitelisted_fields_are_settable(client):
    c, _ = client()
    c.get("/api/frame.bin", params={"id": "picpak-aaa"})
    for bad in ({"palette": [[0, 0, 0]]}, {"dither": "none"}, {"rotate": 90}):
        assert c.post("/api/devices/picpak-aaa/settings", json=bad).status_code == 400


def test_values_are_range_checked(client):
    c, _ = client()
    c.get("/api/frame.bin", params={"id": "picpak-aaa"})
    assert c.post("/api/devices/picpak-aaa/settings", json={"interval_s": 5}).status_code == 400
    assert c.post("/api/devices/picpak-aaa/settings",
                  json={"interval_s": 99999999}).status_code == 400
    assert c.post("/api/devices/picpak-aaa/settings",
                  json={"overlay.position": "middle"}).status_code == 400
    assert c.post("/api/devices/picpak-aaa/settings",
                  json={"overlay.font_size": "big"}).status_code == 400


def test_settings_for_unknown_device_is_404(client):
    c, _ = client()
    assert c.post("/api/devices/ghost/settings", json={"interval_s": 900}).status_code == 404


def test_checkbox_style_truthy_values_are_coerced(client):
    c, st = client()
    c.get("/api/frame.bin", params={"id": "picpak-aaa"})
    c.post("/api/devices/picpak-aaa/settings", json={"overlay.enabled": "on"})
    assert st.store.get_settings("picpak-aaa")["overlay"]["enabled"] is True
    c.post("/api/devices/picpak-aaa/settings", json={"overlay.enabled": "false"})
    assert st.store.get_settings("picpak-aaa")["overlay"]["enabled"] is False


def test_peek_shows_the_next_photo_without_advancing(client):
    c, st = client()
    c.get("/api/frame.bin", params={"id": "picpak-aaa"})
    before = st.store.get("picpak-aaa").cursor
    assert c.get("/preview.png", params={"id": "picpak-aaa", "peek": 1}).status_code == 200
    assert st.store.get("picpak-aaa").cursor == before


def test_skip_advances_what_is_up_next(client):
    c, st = client()
    c.get("/api/frame.bin", params={"id": "picpak-aaa"})
    before = st.store.get("picpak-aaa").cursor
    assert c.post("/api/devices/picpak-aaa/next").status_code == 200
    assert st.store.get("picpak-aaa").cursor == before + 1


def test_deleting_a_device_drops_its_settings(client):
    c, st = client()
    c.get("/api/frame.bin", params={"id": "picpak-aaa"})
    c.post("/api/devices/picpak-aaa/settings", json={"interval_s": 900})
    c.delete("/api/devices/picpak-aaa")
    assert st.store.get_settings("picpak-aaa") == {}
