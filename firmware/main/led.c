// led.c — on-board status LED (GPIO21). See led.h.
// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 varanu5 <https://github.com/varanu5>
#include "led.h"
#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define LED_GPIO      21
#define LED_ON_LEVEL  0   // active-low (hardware-confirmed); flip to 1 if the LED lights inverted

static bool s_ready;

void led_init(void) {
    if (s_ready) return;
    gpio_config_t c = {
        .pin_bit_mask = 1ULL << LED_GPIO,
        .mode         = GPIO_MODE_OUTPUT,
        .pull_up_en   = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    gpio_config(&c);
    gpio_set_level(LED_GPIO, !LED_ON_LEVEL);   // off
    s_ready = true;
}

void led_set(bool on) {
    if (!s_ready) return;
    gpio_set_level(LED_GPIO, on ? LED_ON_LEVEL : !LED_ON_LEVEL);
}

void led_ack(void) {
    if (!s_ready) return;
    led_set(true);
    vTaskDelay(pdMS_TO_TICKS(80));
    led_set(false);
}
