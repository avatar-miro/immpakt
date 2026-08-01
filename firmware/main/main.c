// main.c — PicPak custom firmware: immpakt wake loop (one HTTP GET).
// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 varanu5 <https://github.com/varanu5>
#include "config_store.h"
#include "defaults.h"
#include "board.h"
#include "power.h"
#include "epd_driver.h"
#include "wifi_manager.h"
#include "frame_client.h"
#include "provisioning.h"
#include "splash.h"
#include "lowbatt.h"
#include "led.h"

#include <time.h>
#include <string.h>

#include "esp_log.h"
#include "esp_system.h"
#include "esp_sleep.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "picpak";

// Authorship, baked into the binary's .rodata (find it with
// `strings picpak-tesserae-client.bin | grep varanu5`) and printed once per
// boot so any serial log identifies the firmware's origin.
static const char k_credit[] =
    "ImmPakt firmware - derived from picpak-tesserae-client (c) 2026 varanu5 "
    "- https://github.com/varanu5/picpak-tesserae-client";

// On this board, a WiFi-time voltage sag shows up as a brownout OR (with the
// brownout threshold lowered) as an interrupt/task watchdog reset. Treat all of
// them as "power too low right now -> back off and let the battery recover".
static bool is_power_fault_reset(esp_reset_reason_t r) {
    return r == ESP_RST_BROWNOUT || r == ESP_RST_INT_WDT ||
           r == ESP_RST_WDT      || r == ESP_RST_TASK_WDT;
}

void app_main(void) {
    esp_reset_reason_t reason = esp_reset_reason();
    ESP_LOGI(TAG, "%s", k_credit);
    ESP_LOGI(TAG, "PicPak custom fw %s boot (panel %dx%d, wake=%d)",
             FW_VERSION, EPD_W, EPD_H, (int)reason);

    ESP_ERROR_CHECK(config_init());

    // One clean blink on every wake (timer or button): a screen-free "the frame
    // woke and is working" pulse. A held button then adds the gesture progression
    // in power_boot_gesture(). GPIO21 is a free LED now that the console is on
    // USB-Serial-JTAG (see led.h / sdkconfig).
    led_init();
    led_ack();

    // Provisioning: a 20s button-hold at wake, or no usable WiFi SSID, enters the
    // captive portal. A shorter 3-20s hold is a refresh gesture (classified on
    // release; see power_boot_gesture). (Also runs the flash-hold window.)
    btn_gesture_t gesture = power_boot_gesture();
    bool want_provision = (gesture == BTN_GESTURE_PROVISION);
    bool want_refresh   = (gesture == BTN_GESTURE_REFRESH);
    if (!want_provision) {
        char ssid[33], pass[65];
        want_provision = !config_get_wifi(ssid, sizeof ssid, pass, sizeof pass);
    }
    // A locked (flat) cell can't afford AP-mode radio for a whole setup session, and a brownout
    // mid-provisioning would lose the creds. Refuse the deliberate 20 s re-provision gesture while
    // locked -> show the charge splash instead. Guarded on the gesture only, so a genuinely
    // unprovisioned device (no saved creds -> want_provision) still reaches setup: a fresh/reflashed
    // device is never in the locked state (RTC lock clears on cold boot).
    if (want_provision && gesture == BTN_GESTURE_PROVISION && lowbatt_locked()) {
        ESP_LOGW(TAG, "provision gesture ignored: battery locked, charge first");
        splash_show_lowbatt();
        power_deep_sleep(lowbatt_wake_s());            // no return
    }
    if (want_provision) {
        splash_show_setup();                           // panel shows AP name/password while you provision
        if (provisioning_run_blocking(NULL) == ESP_OK) {
            esp_restart();                             // saved -> re-enter normal path with new creds
        }
        power_deep_sleep(SLEEP_INTERVAL_DEFAULT_S);   // timeout: sleep, retry portal next wake
    }

    // Brownout-aware boot: if we reset from a brownout, the battery is too low to
    // safely run the WiFi radio. Defer and deep-sleep so it can recover instead of
    // rapid reset-looping (which drains faster than it charges).
    if (is_power_fault_reset(reason)) {
        ESP_LOGW(TAG, "power-fault wake (reason=%d): deferring %d s to let battery recover",
                 (int)reason, BROWNOUT_RECOVERY_SLEEP_S);
        power_deep_sleep(BROWNOUT_RECOVERY_SLEEP_S);   // no return
    }

    // Low-battery gate. Measure now: the button (shared with the battery ADC on GPIO2) has been
    // released by power_boot_gesture, so GPIO2 reads the cell cleanly — and it's still before
    // WiFi/EPD load the rail. At 0% (3400 mV, bottom of the battpct.h curve) -> charge screen
    // + a daily low-power poll until the voltage recovers (charging). A button-wake resumes
    // NORMAL so the device is always recoverable — and is the fast way back after plugging in.
    power_measure_battery();
    bool force_resume = want_refresh;              // a 3 s hold is the deliberate override when locked
    bool was_locked   = lowbatt_locked();          // read before the gate mutates RTC state
    switch (lowbatt_gate(power_battery_mv(), force_resume)) {
        case LOWBATT_ARM:
            ESP_LOGW(TAG, "battery low: charge screen + %lu s low-power poll",
                     (unsigned long)lowbatt_wake_s());
            splash_show_lowbatt();
            power_deep_sleep(lowbatt_wake_s());     // no return
            break;
        case LOWBATT_STAY_LOW:
            ESP_LOGW(TAG, "battery still low: %lu s low-power poll", (unsigned long)lowbatt_wake_s());
            power_deep_sleep(lowbatt_wake_s());     // no return
            break;
        case LOWBATT_NORMAL:
        default:
            break;   // healthy / recovered / override -> normal cycle
    }
    // Just recovered from the locked state via an automatic route (a tap that charged, or the daily
    // poll) — not a 3 s hold. The charge splash was painted outside the ETag machinery, so a plain
    // re-fetch could return 304 and skip the paint, stranding the splash. Clear the ETag to force a
    // 200 repaint of the live photo. A 3 s hold (want_refresh) is left alone: its refresh path
    // already drops If-None-Match and pulls fresh content — identical to the good-battery gesture.
    if (was_locked && !want_refresh) config_set_etag("");

    // One-shot after provisioning: paint the "waiting for first frame" splash and
    // clear the ETag so the first /frame returns 200 and the real photo repaints
    // over the splash. Deliberately after the power-fault and low-battery gates:
    // the 13-22 s EPD refresh is the heaviest rail load we have, and taking the
    // flag earlier would consume it just before a brownout could kill the paint —
    // gated here, the splash intent survives to the next healthy boot.
    bool just_provisioned = config_take_paired_pending();
    if (just_provisioned) {
        config_set_etag("");
        splash_show_paired();
        ESP_LOGI(TAG, "paired_pend: painted paired splash + cleared ETag");
    }

    // One HTTP GET per wake against the ImmPakt server. Network-I/O only —
    // a new frame is buffered, not painted.
    int next = SLEEP_INTERVAL_DEFAULT_S;
    bool wifi_ok = false;
    vTaskDelay(pdMS_TO_TICKS(WIFI_SETTLE_MS));   // let the rail settle before the radio
    if (wifi_start_sta() == ESP_OK) {
        wifi_ok = true;
        // https cert validation needs a plausible wall clock (validity window
        // check). The C3 RTC persists across deep sleep, so this normally fires
        // once per power-on; a plain-http LAN server never syncs at all.
        char srv[160] = {0};
        config_get_server_url(srv, sizeof srv);
        if (strncmp(srv, "https://", 8) == 0 && time(NULL) < CLOCK_SANE_EPOCH)
            wifi_sync_ntp();
        next = frame_client_run(want_refresh);
    } else {
        ESP_LOGW(TAG, "WiFi failed; keeping last image, retry next wake");
    }
    wifi_stop();

    // One-shot after provisioning (any transport): WiFi itself failed with a
    // recognisable misconfiguration signature — reopen the portal with the
    // matching banner while the user is still nearby (docs item 1). Wrong
    // password and no-such-network get distinct messages so the user fixes the
    // right field. Anything else (transient outage) keeps the silent retry
    // loop, so a provisioned device never drops to AP mode on a router blip.
    if (just_provisioned && !wifi_ok) {
        const char *note = NULL;
        if (wifi_fail_looks_like_bad_password()) {
            note = "Couldn&rsquo;t join the WiFi network &mdash; the password "
                   "looks wrong. Please re-enter it.";
        } else if (wifi_fail_looks_like_no_ap()) {
            note = "Couldn&rsquo;t find the WiFi network &mdash; check the "
                   "network name. If the network has no password, leave the "
                   "password field blank.";
        }
        if (note) {
            ESP_LOGW(TAG, "just provisioned and WiFi failed with a config signature; reopening portal");
            splash_show_setup();
            if (provisioning_run_blocking(note) == ESP_OK) {
                esp_restart();                          // saved -> retry with new settings
            }
            power_deep_sleep(SLEEP_INTERVAL_DEFAULT_S); // timeout: normal cycle next wake
        }
    }

    // Deliberately NO banner when WiFi is fine but the server/broker is
    // unreachable (removed 2026-07-19, docs item 14): the backend being down at
    // the exact first boot is usually the user's own doing (restarting the
    // Tesserae docker container mid-setup) — bouncing a correctly configured
    // frame back into the portal for that is a false alarm. The frame keeps
    // retrying on its own (30 s discover cadence while unpaired, heartbeat
    // every wake once paired) and catches up when the backend returns; a
    // genuinely wrong URL is recovered via the 20 s button-hold portal.

    // Transport-agnostic contract: all network I/O finishes (incl. a graceful
    // MQTT stop — no LWT), then radio off, then paint. Radio + EPD refresh
    // together is the worst-case rail load on a board that browns out on radio
    // spikes, and the radio idling through a 13-22 s refresh burns ~80 mA for
    // nothing. EPD init is lazy for the same reason: most wakes end unchanged
    // and the panel never powers up at all.
    const uint8_t *fb = frame_client_pending();
    if (fb) {
        if (epd_init() == ESP_OK) {
            ESP_LOGI(TAG, "painting new frame (radio off)");
            epd_display(fb);
            frame_client_painted();   // persist the ETag only after a good paint
            epd_sleep();
        } else {
            ESP_LOGW(TAG, "epd_init failed; keeping last image, retry next wake");
        }
    }
    power_deep_sleep((uint32_t)next);   // no return
}
