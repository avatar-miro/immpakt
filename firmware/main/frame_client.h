// frame_client.h — immpakt transport: one HTTP GET per wake.
// SPDX-License-Identifier: AGPL-3.0-or-later
// Derived from picpak-tesserae-client (c) 2026 varanu5,
// https://github.com/varanu5/picpak-tesserae-client
#pragma once
#include <stdint.h>
#include <stdbool.h>

// Run one wake cycle (WiFi must already be up):
//
//   GET <server>/api/frame.bin?id=&mv=&pct=&rssi=&wake=   [If-None-Match: <etag>]
//     200 -> 30000 bytes buffered for painting, ETag remembered
//     304 -> nothing changed; skip the 13-22 s repaint
//
// Telemetry rides up in the query string and the next sleep interval rides down
// in the X-Next-Wake response header, so there is no JSON on the wake path and
// no pairing handshake — an unknown device id self-registers server-side.
//
// Network I/O only: a new frame is buffered, not painted (see
// frame_client_pending). Returns the deep-sleep seconds to use this cycle.
//
// button_refresh: a 3 s front-button hold this wake. Drops If-None-Match so the
// server's next photo always comes back as a 200.
int frame_client_run(bool button_refresh);

// After frame_client_run: the validated new frame to paint, or NULL (304 /
// error). Paint it with the radio already off, then call frame_client_painted()
// to persist its ETag — only after a successful paint, so a failed paint
// re-fetches as a 200 next wake instead of 304-skipping forever.
const uint8_t *frame_client_pending(void);
void frame_client_painted(void);
