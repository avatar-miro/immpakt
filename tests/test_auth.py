"""Dashboard login. The critical property is the device carve-out."""

from immpakt import auth


def _login(c):
    return c.post("/login", data={"username": "immpakt", "password": "immpakt"},
                  follow_redirects=False)


def test_device_endpoint_is_never_behind_the_login(client):
    """A sleeping frame has no browser and no cookie. If auth ever covers
    /api/frame.bin the panel silently stops updating forever."""
    c, _ = client(auth_enabled=True)
    r = c.get("/api/frame.bin", params={"id": "picpak-aaa"})
    assert r.status_code == 200
    assert len(r.content) == 30000


def test_dashboard_redirects_a_browser_to_the_login(client):
    c, _ = client(auth_enabled=True)
    r = c.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_api_client_gets_401_not_an_html_page(client):
    c, _ = client(auth_enabled=True)
    r = c.get("/api/status", follow_redirects=False)
    assert r.status_code == 401
    assert r.json()["detail"] == "authentication required"


def test_photos_are_not_served_to_anonymous_callers(client):
    """/preview.png renders the user's actual photos."""
    c, _ = client(auth_enabled=True)
    r = c.get("/preview.png", params={"id": "picpak-aaa"}, follow_redirects=False)
    assert r.status_code in (303, 401)


def test_login_then_full_access(client):
    c, _ = client(auth_enabled=True)
    r = _login(c)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert c.get("/").status_code == 200
    assert c.get("/api/status").status_code == 200


def test_wrong_password_is_rejected(client):
    c, _ = client(auth_enabled=True)
    r = c.post("/login", data={"username": "immpakt", "password": "nope"},
               follow_redirects=False)
    assert r.status_code == 401
    assert c.get("/api/status", follow_redirects=False).status_code == 401


def test_session_cookie_is_httponly_and_samesite(client):
    c, _ = client(auth_enabled=True)
    raw = _login(c).headers["set-cookie"].lower()
    assert "httponly" in raw
    assert "samesite=lax" in raw


def test_logout_revokes_access(client):
    c, _ = client(auth_enabled=True)
    _login(c)
    assert c.get("/api/status").status_code == 200
    c.get("/logout", follow_redirects=False)
    assert c.get("/api/status", follow_redirects=False).status_code == 401


def test_a_forged_cookie_is_rejected(client):
    c, _ = client(auth_enabled=True)
    c.cookies.set(auth.COOKIE, "aW1tcGFrdHw5OTk5OTk5OTk5.deadbeef")
    assert c.get("/api/status", follow_redirects=False).status_code == 401


def test_expired_session_is_rejected(client):
    c, st = client(auth_enabled=True)
    c.cookies.set(auth.COOKIE, auth.issue(st.session_secret, "immpakt", -1))
    assert c.get("/api/status", follow_redirects=False).status_code == 401


def test_auth_can_be_switched_off(client):
    c, _ = client(auth_enabled=False)
    assert c.get("/api/status").status_code == 200


def test_signing_secret_survives_a_restart(tmp_path):
    """Regenerating it each boot would log everyone out on every redeploy."""
    a = auth.load_or_create_secret(str(tmp_path))
    assert auth.load_or_create_secret(str(tmp_path)) == a
    assert len(a) >= 32
