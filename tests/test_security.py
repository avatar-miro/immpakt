"""Hardening regressions. Each test corresponds to a finding from the review."""


def test_device_id_must_be_an_identifier(client):
    """Unvalidated, the id is a row anyone on the network can create and a
    string rendered into the dashboard."""
    c, st = client()
    for bad in ("<img src=x onerror=alert(1)>", "a/../b", "a b", "", "x" * 65,
                "-leading-dash", "semi;colon"):
        r = c.get("/api/frame.bin", params={"id": bad})
        assert r.status_code == 400, f"accepted {bad!r}"
    assert st.store.all() == [], "a rejected id must not create a row"


def test_good_device_ids_still_work(client):
    c, _ = client()
    for ok in ("picpak-a1b2c3", "frame.kitchen", "A1_b2", "x"):
        assert c.get("/api/frame.bin", params={"id": ok}).status_code == 200


def test_settings_reject_non_json_content_type(client):
    """text/plain is a CORS 'simple request' -- no preflight. request.json()
    parses it happily, so without a content-type check a page you merely visit
    could reconfigure your frame cross-origin."""
    c, _ = client()
    c.get("/api/frame.bin", params={"id": "picpak-aaa"})
    r = c.post("/api/devices/picpak-aaa/settings",
               content='{"interval_s":900}',
               headers={"Content-Type": "text/plain"})
    assert r.status_code == 415


def test_settings_still_accept_proper_json(client):
    c, _ = client()
    c.get("/api/frame.bin", params={"id": "picpak-aaa"})
    assert c.post("/api/devices/picpak-aaa/settings",
                  json={"interval_s": 900}).status_code == 200


def test_malformed_json_is_a_400_not_a_500(client):
    c, _ = client()
    c.get("/api/frame.bin", params={"id": "picpak-aaa"})
    r = c.post("/api/devices/picpak-aaa/settings", content="{not json",
               headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_device_key_is_enforced_and_compared_constant_time(client):
    import inspect

    from immpakt import app as app_mod

    c, _ = client(device_key="s3cret")
    assert c.get("/api/frame.bin", params={"id": "picpak-aaa"}).status_code == 403
    assert c.get("/api/frame.bin",
                 params={"id": "picpak-aaa", "key": "s3cret"}).status_code == 200
    src = inspect.getsource(app_mod.frame_bin)
    assert "compare_digest" in src, "a plain == leaks the secret to timing"


def test_all_device_id_paths_validate(client):
    c, _ = client()
    bad = "%3Cscript%3E"
    assert c.post(f"/api/devices/{bad}/next").status_code == 400
    assert c.get(f"/api/devices/{bad}/settings").status_code == 400
    assert c.post(f"/api/devices/{bad}/settings", json={}).status_code == 400
    assert c.delete(f"/api/devices/{bad}").status_code == 400
