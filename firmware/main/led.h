// led.h — on-board status LED (GPIO21).
// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 varanu5 <https://github.com/varanu5>
//
// With the console routed to USB-Serial-JTAG (see sdkconfig), GPIO21 no longer
// carries UART0 TX traffic, so it is a clean, dedicated status LED. Every wake
// gives one acknowledge blink; a held button adds the gesture progression (see
// power_boot_gesture). The API no-ops until led_init() runs.
#pragma once
#include <stdbool.h>

void led_init(void);   // configure GPIO21 as an output; LED off
void led_set(bool on); // drive the LED on/off
void led_ack(void);    // one short blink (~80 ms), then off
