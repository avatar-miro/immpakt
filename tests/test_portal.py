"""The captive-portal page, exercised through the same code that previews it.

portal_preview.py parses the real C string literals out of provisioning.c, so
these tests run against the bytes the firmware actually serves rather than a
copy that can drift.
"""

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "firmware" / "main" / "provisioning.c"


def _load():
    spec = importlib.util.spec_from_file_location(
        "portal_preview", ROOT / "firmware" / "tools" / "portal_preview.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pp = _load()
SOURCE = SRC.read_text()


# -- the parser ------------------------------------------------------------


def test_literals_are_not_truncated_by_css_semicolons():
    """Regression: a lazy regex up to the first `;` silently cut k_head from
    4.8 KB to 186 bytes, because the portal's CSS is full of semicolons inside
    the string literals."""
    head = pp.c_literals(SOURCE, "k_head")
    assert len(head) > 3000, f"k_head is only {len(head)} bytes; parser truncated it"
    assert head.count("{") > 25, "stylesheet body missing"


def test_every_literal_parses():
    for name in ("k_head", "k_form_wifi_fmt", "k_form_transport_fmt",
                 "k_form_mqtt_fmt", "k_form_rest_fmt", "k_tail", "k_thanks_html"):
        assert pp.c_literals(SOURCE, name), f"{name} came back empty"


def test_a_missing_literal_fails_loudly():
    with pytest.raises(SystemExit):
        pp.c_literals(SOURCE, "k_does_not_exist")


# -- the C contract --------------------------------------------------------


def test_placeholder_counts_match_the_snprintf_call_sites():
    """Each format string is filled by a snprintf in setup_get_handler. If the
    two drift, the portal renders garbage or overruns; the preview would too."""
    expected = {"k_form_wifi_fmt": 2, "k_form_transport_fmt": 2,
                "k_form_mqtt_fmt": 3, "k_form_rest_fmt": 2}
    for name, n in expected.items():
        got = pp.c_literals(SOURCE, name).count("%s")
        assert got == n, f"{name} has {got} %s, snprintf passes {n}"


def test_fill_rejects_a_wrong_argument_count():
    with pytest.raises(SystemExit):
        pp.fill("a %s b %s", "only-one")


# -- the rendered page -----------------------------------------------------


def test_page_is_a_complete_html_document():
    html = pp.build("setup", "", scan=True, filled=True)
    assert html.lstrip().lower().startswith("<!doctype html")
    assert html.rstrip().lower().endswith("</html>")
    for tag in ("<head", "<body", "</body>", "<form"):
        assert tag in html.lower(), f"missing {tag}"


def test_the_fields_needed_to_provision_are_present():
    html = pp.build("setup", "", scan=True, filled=True)
    for field in ('name="ssid"', 'name="pass"', 'name="server_url"',
                  'name="device_id"'):
        assert field in html, f"{field} missing from the portal form"


def test_server_url_field_is_labelled_for_this_project():
    html = pp.build("setup", "", scan=True, filled=True)
    assert "Photo server" in html
    assert "Tesserae server" not in html


def test_error_banner_renders_when_given_one():
    msg = "Couldn&rsquo;t join the WiFi network."
    assert msg in pp.build("setup", msg, scan=True, filled=True)
    assert msg not in pp.build("setup", "", scan=True, filled=True)


def test_scan_picker_appears_only_when_networks_were_found():
    assert "pick a nearby network" in pp.build("setup", "", scan=True, filled=True)
    assert "pick a nearby network" not in pp.build("setup", "", scan=False, filled=True)


def test_blank_device_has_empty_prefills():
    html = pp.build("setup", "", scan=True, filled=False)
    assert 'id="server_url" name="server_url" maxlength="159" autocomplete="off" value=""' \
        in re.sub(r"\s+", " ", html)


def test_thanks_page_renders():
    assert "</html>" in pp.build("thanks", "", scan=False, filled=False).lower()


# -- branding --------------------------------------------------------------


def test_portal_does_not_ship_tesserae_brand_assets():
    """The upstream project's logo was inlined here. Renaming the wordmark but
    keeping their icon would ship someone else's trademark under our name."""
    html = pp.build("setup", "", scan=True, filled=True)
    for marker in ("tess-bg", "tess-inner", "#0d8c7e", "#0a6f63"):
        assert marker not in html, f"Tesserae brand asset {marker} still present"


def test_portal_uses_the_immpakt_mark():
    html = pp.build("setup", "", scan=True, filled=True)
    for hexcode in ("#1E83F7", "#FFB400", "#ED79B5", "#FA2921", "#18C249"):
        assert hexcode in html, f"{hexcode} missing from the portal mark"


def test_portal_and_web_ui_draw_the_same_mark():
    """One icon, defined once. If they diverge the device and the dashboard
    stop looking like the same product."""
    from immpakt import auth
    html = pp.build("setup", "", scan=True, filled=True)
    for shape in ('rx="25.6"', 'rx="10.24"', 'r="19.2"', 'rx="5.38"'):
        assert shape in html, f"portal mark missing {shape}"
        assert shape in auth.ICON_SVG, f"web mark missing {shape}"


def test_ap_credentials_shown_match_the_firmware_defaults():
    """The splash and the portal both advertise the AP; if defaults.h changes
    and these do not, users are told to join a network that does not exist."""
    ssid = re.search(r'#define PROVISION_AP_SSID\s+"([^"]+)"',
                     (ROOT / "firmware/main/defaults.h").read_text()).group(1)
    assert ssid == "ImmPakt-Setup"
    gen = (ROOT / "firmware/tools/gen_splash.py").read_text()
    assert f'AP_SSID = "{ssid}"' in gen, "splash generator is out of sync with defaults.h"
