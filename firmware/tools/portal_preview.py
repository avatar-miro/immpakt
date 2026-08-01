#!/usr/bin/env python3
"""Preview the captive-portal web UI without flashing or re-provisioning.

The portal's HTML lives as C string literals in ``main/provisioning.c``. This
script parses those literals out of the real source and reassembles the page the
same way ``setup_get_handler()`` does, so what you see in the browser cannot
drift from what the firmware actually serves -- it is the same bytes.

    ./portal_preview.py --serve            # http://127.0.0.1:877
    ./portal_preview.py -o portal.html     # write a file
    ./portal_preview.py --error "Couldn't join the WiFi network."
    ./portal_preview.py --page thanks      # the post-submit page
    ./portal_preview.py --no-scan          # as if the AP scan found nothing

Editing provisioning.c and refreshing is enough; no rebuild, no flash, no 20 s
button hold.
"""

from __future__ import annotations

import argparse
import http.server
import re
import socketserver
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "main" / "provisioning.c"

_ESCAPES = {'"': '"', "\\": "\\", "n": "\n", "t": "\t", "r": "\r", "0": ""}


def _unescape(s: str) -> str:
    out, i, n = [], 0, len(s)
    while i < n:
        if s[i] == "\\" and i + 1 < n:
            out.append(_ESCAPES.get(s[i + 1], s[i + 1]))
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def c_literals(source: str, name: str) -> str:
    """Return the concatenated value of `static const char <name>[] = ...;`.

    Scanned rather than regexed: one constant is dozens of adjacent quoted
    chunks, and the portal's CSS is full of semicolons *inside* the strings, so
    a lazy match up to the first `;` silently truncates the page.
    """
    m = re.search(
        rf"static\s+const\s+char\s+{re.escape(name)}\s*\[\s*\]\s*=", source
    )
    if not m:
        raise SystemExit(f"{SRC.name}: could not find literal {name!r}")

    parts: list[str] = []
    i, n = m.end(), len(source)
    while i < n:
        ch = source[i]
        if ch.isspace():
            i += 1
        elif source.startswith("//", i):
            nl = source.find("\n", i)
            i = n if nl < 0 else nl + 1
        elif source.startswith("/*", i):
            end = source.find("*/", i)
            i = n if end < 0 else end + 2
        elif ch == '"':
            j, buf = i + 1, []
            while j < n:
                if source[j] == "\\":
                    buf.append(source[j : j + 2])
                    j += 2
                    continue
                if source[j] == '"':
                    break
                buf.append(source[j])
                j += 1
            parts.append(_unescape("".join(buf)))
            i = j + 1
        elif ch == ";":
            break
        else:  # not a string, comment or terminator -- not a literal we can read
            raise SystemExit(
                f"{SRC.name}: unexpected {ch!r} while reading {name!r}"
            )
    return "".join(parts)


def fill(fmt: str, *values: str) -> str:
    """Substitute %s placeholders left to right, like snprintf does."""
    expected = fmt.count("%s")
    if expected != len(values):
        raise SystemExit(
            f"format expects {expected} %s but {len(values)} given -- "
            "provisioning.c changed; update portal_preview.py to match"
        )
    for v in values:
        fmt = fmt.replace("%s", v, 1)
    return fmt


SAMPLE_NETWORKS = [
    ("Home-WiFi", -48, True),
    ("Home-WiFi-5G", -61, True),
    ("Neighbour 2.4", -77, True),
    ("GuestNet", -70, False),
]


def scan_picker() -> str:
    """Mirror the runtime scan <select> built in setup_get_handler()."""
    opts = "".join(
        f'<option value="{s}">{s} ({r} dBm{"" if sec else ", open"})</option>'
        for s, r, sec in SAMPLE_NETWORKS
    )
    return (
        '<div class="field">'
        '<label for="ssid-pick">Or pick a nearby network</label>'
        '<select id="ssid-pick" autocomplete="off">'
        '<option value="">-- pick a network --</option>'
        f"{opts}</select></div>"
    )


def build(page: str, error: str, scan: bool, filled: bool) -> str:
    src = SRC.read_text()

    if page == "thanks":
        return c_literals(src, "k_thanks_html")

    # Same order as setup_get_handler().
    parts = [c_literals(src, "k_head")]

    if error:
        parts.append(f'<div class="err">{error}</div>')

    parts.append(
        '<div class="status">'
        '<span class="k">IP</span><span><code>192.168.4.1 (setup AP)</code></span>'
        "</div>"
    )

    ssid = "Home-WiFi" if filled else ""
    devid = "picpak-a1b2c3" if filled else ""
    server = "http://192.168.1.10:8080" if filled else ""

    parts.append(fill(c_literals(src, "k_form_wifi_fmt"),
                      ssid, scan_picker() if scan else ""))
    # transport: (mqtt_checked, rest_checked) -- REST is the default.
    parts.append(fill(c_literals(src, "k_form_transport_fmt"), "", " checked"))
    parts.append(fill(c_literals(src, "k_form_mqtt_fmt"), devid, "", ""))
    parts.append(fill(c_literals(src, "k_form_rest_fmt"), server, ""))
    parts.append(c_literals(src, "k_tail"))
    return "".join(parts)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-o", "--out", type=Path, help="write the page to a file")
    p.add_argument("--serve", nargs="?", const=877, type=int, metavar="PORT",
                   help="serve on 127.0.0.1:PORT (default 877), re-reading "
                        "provisioning.c on every request")
    p.add_argument("--page", choices=("setup", "thanks"), default="setup")
    p.add_argument("--error", default="", help="preview the red banner variant")
    p.add_argument("--no-scan", action="store_true",
                   help="as if the AP scan found no networks")
    p.add_argument("--blank", action="store_true",
                   help="empty fields, as on a factory-fresh device")
    a = p.parse_args(argv)

    render = lambda: build(a.page, a.error, not a.no_scan, not a.blank)  # noqa: E731

    if a.out:
        a.out.write_text(render())
        print(f"wrote {a.out} ({len(render())} bytes)")

    if a.serve:
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                # Re-render per request so editing provisioning.c and hitting
                # refresh is the whole loop.
                try:
                    body = render().encode()
                except SystemExit as e:
                    body = f"<pre>{e}</pre>".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.TCPServer(("127.0.0.1", a.serve), Handler) as httpd:
            print(f"portal preview: http://127.0.0.1:{a.serve}  (Ctrl-C to stop)")
            print(f"reading {SRC}")
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print()
        return 0

    if not a.out:
        sys.stdout.write(render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
