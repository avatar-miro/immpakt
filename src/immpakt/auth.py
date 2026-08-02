"""Session login for the dashboard and management API.

Cookie sessions rather than HTTP Basic: Basic cannot be styled, cannot be
logged out of without closing the browser, and re-sends the password on every
single request including every thumbnail.

The device endpoint is deliberately NOT covered -- a sleeping e-ink frame
cannot log in. See PUBLIC_PATHS.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Paths that never require a session. Everything else does.
PUBLIC_PATHS = frozenset({"/login", "/logout", "/favicon.ico", "/favicon.svg"})
# The frame's own endpoint: gated by server.device_key, never by a session.
DEVICE_PREFIX = "/api/frame.bin"

COOKIE = "immpakt_session"

# The mark, as a standalone document for /favicon.svg. The same geometry is
# inlined into the pages; res/icon/immpakt.svg is the canonical source.
ICON_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
            ' 256 256"><defs><mask id="mk"><rect width="256" height="256" fill="#fff"/><rect x="16.13" y="16.13" width="223.74" height="164.86" rx="20.99"/><circle cx="55.04" cy="216.32" r="29.95"/><rect x="85.25" y="196.61" width="154.62" height="39.42" rx="16.13"/></mask></defs><rect x="2.56" y="2.56" width="250.88" height="250.88" rx="25.6" fill="#1E83F7" stroke="#FFB400" stroke-width="5.12" mask="url(#mk)"/><rect x="26.88" y="26.88" width="202.24" height="143.36" rx="10.24" fill="#ED79B5"/><circle cx="55.04" cy="216.32" r="19.2" fill="#FA2921"/><rect x="96" y="207.36" width="133.12" height="17.92" rx="5.38" fill="#18C249"/></svg>')
DEFAULT_PASSWORD = "immpakt"


def load_or_create_secret(data_dir: str) -> bytes:
    """Persist the signing key so sessions survive a restart.

    Regenerating it on every boot would silently log everyone out each time the
    container is redeployed.
    """
    p = Path(data_dir) / "session.key"
    if p.exists():
        raw = p.read_bytes().strip()
        if len(raw) >= 32:
            return raw
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = secrets.token_bytes(48)
    p.write_bytes(raw)
    try:
        p.chmod(0o600)
    except OSError:  # e.g. a volume that does not support chmod
        pass
    return raw


def _sign(secret: bytes, payload: str) -> str:
    sig = hmac.new(secret, payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode().rstrip("=")


def issue(secret: bytes, username: str, hours: int) -> str:
    """A cookie value carrying its own expiry, signed so it cannot be edited."""
    payload = f"{username}|{int(time.time()) + hours * 3600}"
    return f"{base64.urlsafe_b64encode(payload.encode()).decode().rstrip('=')}.{_sign(secret, payload)}"


def verify(secret: bytes, cookie: str | None) -> str | None:
    """Return the username if the cookie is intact and unexpired, else None."""
    if not cookie or "." not in cookie:
        return None
    body, _, sig = cookie.rpartition(".")
    try:
        payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    # compare_digest: a plain == leaks the signature a byte at a time.
    if not hmac.compare_digest(sig, _sign(secret, payload)):
        return None
    username, _, exp = payload.partition("|")
    try:
        if int(exp) < time.time():
            return None
    except ValueError:
        return None
    return username


def check_password(cfg_user: str, cfg_pass: str, user: str, password: str) -> bool:
    """Constant-time on both fields, so neither leaks by timing."""
    return hmac.compare_digest(user, cfg_user) & hmac.compare_digest(password, cfg_pass)


def warn_if_default(username: str, password: str) -> None:
    if password == DEFAULT_PASSWORD:
        log.warning(
            "dashboard is using the DEFAULT password (%s/%s) -- anyone who can "
            "reach this server can change your frames. Set server.auth.password "
            "in config.yaml or IMMPAKT_PASSWORD in the environment.",
            username, DEFAULT_PASSWORD,
        )


LOGIN_CSS = """
body{margin:0;min-height:100vh;display:grid;place-items:center;
  background:var(--surface-0);color:var(--text-1);
  font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
.box{background:var(--surface-1);border:1px solid var(--border);border-radius:12px;
  padding:1.75rem;width:min(92vw,22rem)}
.brand{display:flex;align-items:center;gap:.6rem;margin-bottom:.2rem}
.mark{width:24px;height:24px;display:block;flex:0 0 auto}
h1{font-size:1.05rem;font-weight:650;margin:0}
p.sub{color:var(--text-3);font-size:.82rem;margin:.1rem 0 1.2rem}
label{display:block;font-size:.78rem;color:var(--text-2);font-weight:600;
  margin:.8rem 0 .25rem}
input{width:100%;font:inherit;padding:.5rem .6rem;border-radius:8px;
  border:1px solid var(--border);background:var(--surface-0);color:var(--text-1)}
input:focus{outline:2px solid var(--series-1);outline-offset:1px}
button{width:100%;margin-top:1.2rem;font:inherit;font-weight:650;cursor:pointer;
  padding:.55rem;border-radius:8px;border:0;background:var(--series-1);color:#fff}
.err{background:#d03b3b12;border:1px solid var(--critical);color:var(--critical);
  border-radius:8px;padding:.5rem .7rem;font-size:.82rem;margin-top:1rem}
.hint{font-size:.74rem;color:var(--text-3);margin-top:1rem;line-height:1.45}
"""


def login_page(base_css: str, error: str = "", default_creds: bool = False) -> str:
    err = f'<div class="err">{error}</div>' if error else ""
    hint = (
        '<p class="hint">Default login is <code>immpakt</code> / '
        "<code>immpakt</code>. Change it in <code>config.yaml</code> under "
        "<code>server.auth</code>.</p>"
        if default_creds else ""
    )
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in &middot; ImmPakt</title>
<link rel="icon" href="/favicon.svg" type="image/svg+xml"><style>{base_css}{LOGIN_CSS}</style></head><body>
<form class="box" method="post" action="/login">
  <div class="brand">
    <svg class="mark" viewBox="0 0 256 256" aria-hidden="true"><defs><mask id="mk"><rect width="256" height="256" fill="#fff"/><rect x="16.13" y="16.13" width="223.74" height="164.86" rx="20.99"/><circle cx="55.04" cy="216.32" r="29.95"/><rect x="85.25" y="196.61" width="154.62" height="39.42" rx="16.13"/></mask></defs><rect x="2.56" y="2.56" width="250.88" height="250.88" rx="25.6" fill="#1E83F7" stroke="#FFB400" stroke-width="5.12" mask="url(#mk)"/><rect x="26.88" y="26.88" width="202.24" height="143.36" rx="10.24" fill="#ED79B5"/><circle cx="55.04" cy="216.32" r="19.2" fill="#FA2921"/><rect x="96" y="207.36" width="133.12" height="17.92" rx="5.38" fill="#18C249"/></svg>
    <h1>ImmPakt</h1>
  </div>
  <p class="sub">Sign in to manage your frames</p>
  <label for="u">Username</label>
  <input id="u" name="username" autocomplete="username" autofocus required>
  <label for="p">Password</label>
  <input id="p" name="password" type="password" autocomplete="current-password" required>
  <button type="submit">Sign in</button>
  {err}{hint}
</form></body></html>"""
