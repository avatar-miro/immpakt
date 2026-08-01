// frame_client.c — immpakt transport: one HTTP GET per wake.
// SPDX-License-Identifier: AGPL-3.0-or-later
// Derived from picpak-tesserae-client (c) 2026 varanu5,
// https://github.com/varanu5/picpak-tesserae-client
#include "frame_client.h"
#include "config_store.h"
#include "defaults.h"
#include "framebuf.h"
#include "board.h"
#include "power.h"
#include "wifi_manager.h"

#include <string.h>
#include <strings.h>   // strcasecmp
#include <stdio.h>
#include <stdlib.h>    // atoi

#include "esp_http_client.h"
#include "esp_crt_bundle.h"
#include "esp_mac.h"
#include "esp_log.h"
#include "esp_system.h"   // esp_reset_reason

static const char *TAG = "frame";

static bool s_pending = false;      // framebuf() holds a validated new frame
static char s_pending_etag[80];     // its ETag; persisted only after a good paint

typedef struct {
    uint8_t *buf;
    int      len;         // bytes accumulated into buf
    bool     overflow;    // body ran past EPD_FB_BYTES
    char     etag[80];
    int      next_wake;   // X-Next-Wake header (seconds), 0 if absent
} resp_t;

static esp_err_t http_ev(esp_http_client_event_t *e) {
    resp_t *r = (resp_t *)e->user_data;
    if (!r) return ESP_OK;

    if (e->event_id == HTTP_EVENT_ON_HEADER) {
        if (strcasecmp(e->header_key, "ETag") == 0)
            strlcpy(r->etag, e->header_value, sizeof(r->etag));
        else if (strcasecmp(e->header_key, "X-Next-Wake") == 0)
            r->next_wake = atoi(e->header_value);
    } else if (e->event_id == HTTP_EVENT_ON_DATA) {
        // Never write past the framebuffer: a misconfigured URL pointing at some
        // other server could stream megabytes at us. Keep draining so the
        // connection closes cleanly, but stop copying and flag it.
        if (r->len + e->data_len > EPD_FB_BYTES) {
            r->overflow = true;
            return ESP_OK;
        }
        memcpy(r->buf + r->len, e->data, e->data_len);
        r->len += e->data_len;
    }
    return ESP_OK;
}

// The portal's device-id field, or a MAC-derived default so an unnamed frame is
// still stable and distinguishable across reboots.
static void device_id(char *out, size_t out_sz) {
    config_get_device_id(out, out_sz);
    if (out[0]) return;
    uint8_t mac[6] = {0};
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    // Prefix names the hardware (still a PicPak), not the software, and
    // changing it would re-register the frame under a new id, orphaning
    // its history, cursor and settings on the dashboard.
    snprintf(out, out_sz, "picpak-%02x%02x%02x", mac[3], mac[4], mac[5]);
}

static const char *wake_word(bool button_refresh) {
    if (button_refresh) return "button";
    return (esp_reset_reason() == ESP_RST_DEEPSLEEP) ? "timer" : "boot";
}

int frame_client_run(bool button_refresh) {
    s_pending = false;
    s_pending_etag[0] = '\0';

    int fallback = (int)config_get_sleep_s(SLEEP_INTERVAL_DEFAULT_S);

    char server[160] = {0};
    config_get_server_url(server, sizeof(server));
    if (!server[0]) {
        ESP_LOGE(TAG, "no server URL set; hold the button 20 s to re-provision");
        return fallback;
    }
    // Trailing slashes would produce //api/frame.bin — harmless on most servers,
    // but not worth relying on.
    size_t n = strlen(server);
    while (n > 0 && server[n - 1] == '/') server[--n] = '\0';

    char dev_id[64];
    device_id(dev_id, sizeof(dev_id));

    int mv   = power_battery_mv();
    int pct  = power_battery_pct(mv);
    int rssi = wifi_rssi();

    char url[352];
    int un = snprintf(url, sizeof(url),
                      "%s/api/frame.bin?id=%s&mv=%d&pct=%d&rssi=%d&wake=%s",
                      server, dev_id, mv, pct, rssi, wake_word(button_refresh));

    // The portal's optional code doubles as immpakt's server.device_key,
    // for anyone exposing the server beyond their LAN.
    char key[16] = {0};
    config_get_pairing_code(key, sizeof(key));
    if (key[0] && un > 0 && un < (int)sizeof(url))
        snprintf(url + un, sizeof(url) - un, "&key=%s", key);

    static resp_t r;
    memset(&r, 0, sizeof(r));
    r.buf = framebuf();

    esp_http_client_config_t cfg = {
        .url = url,
        .method = HTTP_METHOD_GET,
        .timeout_ms = 20000,
        .event_handler = http_ev,
        .user_data = &r,
    };
    // CA bundle only for TLS: attaching it on plain http can mis-configure the
    // client. Publicly-trusted certificates only — a self-signed cert on your
    // own LAN will not pass, so use http:// there.
    if (strncmp(url, "https://", 8) == 0)
        cfg.crt_bundle_attach = esp_crt_bundle_attach;

    esp_http_client_handle_t c = esp_http_client_init(&cfg);
    if (!c) return fallback;

    // A refresh gesture deliberately drops If-None-Match so the server's next
    // photo always comes back 200 rather than 304.
    char etag[80] = {0};
    if (!button_refresh) {
        config_get_etag(etag, sizeof(etag));
        if (etag[0]) esp_http_client_set_header(c, "If-None-Match", etag);
    }

    esp_err_t err = esp_http_client_perform(c);
    int status = esp_http_client_get_status_code(c);
    esp_http_client_cleanup(c);

    if (status <= 0) {
        ESP_LOGW(TAG, "transport error: %s (keeping last image)", esp_err_to_name(err));
        return fallback;
    }

    // The server dictates cadence; persist it so a wake that cannot reach the
    // server still sleeps for the interval the server last asked for.
    int next = fallback;
    if (r.next_wake > 0) {
        next = r.next_wake;
        if (next < SLEEP_INTERVAL_MIN_S) next = SLEEP_INTERVAL_MIN_S;
        if (next > SLEEP_INTERVAL_MAX_S) next = SLEEP_INTERVAL_MAX_S;
        if ((uint32_t)next != config_get_sleep_s(0)) config_set_sleep_s((uint32_t)next);
    }

    if (status == 304) {
        ESP_LOGI(TAG, "304 unchanged; panel untouched, sleeping %d s", next);
        return next;
    }
    if (status != 200) {
        ESP_LOGW(TAG, "HTTP %d (keeping last image)", status);
        return next;
    }
    if (r.overflow || r.len != EPD_FB_BYTES) {
        ESP_LOGE(TAG, "bad frame: %d bytes%s, expected %d — is the URL an "
                      "ImmPakt server?",
                 r.len, r.overflow ? "+ (truncated)" : "", EPD_FB_BYTES);
        return next;
    }

    s_pending = true;
    strlcpy(s_pending_etag, r.etag, sizeof(s_pending_etag));
    ESP_LOGI(TAG, "new frame: %d bytes, batt %d mV (%d%%), rssi %d, next wake %d s",
             r.len, mv, pct, rssi, next);
    return next;
}

const uint8_t *frame_client_pending(void) {
    return s_pending ? framebuf() : NULL;
}

void frame_client_painted(void) {
    if (!s_pending) return;
    config_set_etag(s_pending_etag);
    s_pending = false;
}
