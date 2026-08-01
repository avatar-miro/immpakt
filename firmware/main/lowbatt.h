// lowbatt.h — RTC/hardware glue around the pure FSM in lowbatt_core.h.
// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 varanu5 <https://github.com/varanu5>
#pragma once
#include <stdint.h>
#include "lowbatt_core.h"   // pulls in <stdbool.h> for the bool params below

// Fixed thresholds (shipped constant — no NVS/console on this build).
#define LOWBATT_ARM_MV   3400   // 0% on the battpct.h curve (its floor) — the panel reads 0% exactly
                                // when the gate arms. Deliberately not lower: the ~1-2% of capacity
                                // between 3300 and 3400 buys almost no runtime and is spent entirely
                                // in the sag-and-brownout zone.
#define LOWBATT_CLR_MV   3550   // ~5% on the battpct.h curve, hysteresis above ARM
#define LOWBATT_RISE_MV  40     // "charging" if mV rose >= this over the poll window. Mostly vestigial
                                // at a daily poll (a cell on a charger clears CLR outright within one
                                // window); kept for the trickle/near-CLR case.
#define LOWBATT_STREAK   2      // consecutive sub-ARM reads before arming
#define LOWBATT_WAKE_S   86400  // 24-h low-power poll — a rare fallback. Once gated the only job
                                // is to notice a charger, and a safe button tap now re-checks the
                                // battery instantly (no forced WiFi), so polling seldom needs to
                                // fire; a long interval avoids repeatedly waking a nearly-dead cell.

// Run the gate for this wake. force_resume (a deliberate 3 s button hold) unlocks immediately;
// a plain tap and a timer wake both pass false. ARM -> caller paints the charge screen once +
// deep-sleeps lowbatt_wake_s(); STAY_LOW -> deep-sleep without a repaint; NORMAL -> fall through.
lowbatt_action_t lowbatt_gate(int batt_mv, bool force_resume);
uint32_t lowbatt_wake_s(void);

// Whether the gate is currently in the low-power lock (RTC state, pre-gate). Lets main.c refuse
// the re-provision gesture on a flat cell and force a repaint over the charge splash on recovery.
bool lowbatt_locked(void);
