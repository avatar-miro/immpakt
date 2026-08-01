#!/usr/bin/env bash
# Recovery: erase saved settings and reflash, without needing button gestures.
#
# Erasing the nvs partition (0x9000, 0x6000 -- see partitions.csv) drops the
# stored Wi-Fi credentials, server URL and frame ETag. On the next boot main.c
# finds no usable SSID and enters the captive portal on its own, so a wrong
# server URL never needs a 20 s button hold to fix.
#
#   ./recover.sh [/dev/cu.usbmodemXXXX]
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BUILD="$HERE/build"
PORT="${1:-}"

if [[ -z "$PORT" ]]; then
  PORT="$(ls /dev/cu.usbmodem* 2>/dev/null | head -1 || true)"
  [[ -n "$PORT" ]] || {
    echo "No /dev/cu.usbmodem* found." >&2
    echo "Hold the front button while plugging in USB so the device stays awake" >&2
    echo "long enough to enumerate." >&2
    exit 1
  }
fi

for f in "$BUILD/immpakt.bin" "$BUILD/bootloader/bootloader.bin" \
         "$BUILD/partition_table/partition-table.bin"; do
  [[ -f "$f" ]] || { echo "Missing $f -- run ./build.sh first." >&2; exit 1; }
done

echo "Port: $PORT"
echo
echo "=== erasing nvs (saved Wi-Fi + server URL) ==="
esptool.py --chip esp32c3 --port "$PORT" --baud 460800 \
  --before default_reset --after no_reset erase_region 0x9000 0x6000

echo
echo "=== flashing firmware ==="
esptool.py --chip esp32c3 --port "$PORT" --baud 460800 \
  --before default_reset --after hard_reset write_flash \
  -z --flash_mode dio --flash_freq 80m --flash_size detect \
  0x0     "$BUILD/bootloader/bootloader.bin" \
  0x8000  "$BUILD/partition_table/partition-table.bin" \
  0x10000 "$BUILD/immpakt.bin"

echo
echo "Done. It boots straight into the setup portal (no saved Wi-Fi):"
echo "  1. join Wi-Fi 'ImmPakt-Setup'  password immpakt123"
echo "  2. open http://192.168.4.1"
echo "  3. Photo server -> Server URL: http://<your-server>:8080"
