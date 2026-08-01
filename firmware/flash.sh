#!/usr/bin/env bash
# Flash the built firmware from the host (Docker on macOS has no USB passthrough).
# Refuses to run until backup-stock.sh has produced a verified stock dump.
#
#   ./flash.sh [/dev/cu.usbmodemXXXX]
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BUILD="$HERE/build"
BACKUP="$HERE/stock-backup/stock-verified.bin"
PORT="${1:-}"

if [[ ! -f "$BACKUP" ]]; then
  echo "No verified stock backup at:" >&2
  echo "  $BACKUP" >&2
  echo >&2
  echo "Run ./backup-stock.sh first. Flashing over the stock firmware without" >&2
  echo "a verified dump is not reversible." >&2
  echo "Set I_HAVE_A_BACKUP_ELSEWHERE=1 to override." >&2
  [[ "${I_HAVE_A_BACKUP_ELSEWHERE:-}" == "1" ]] || exit 1
fi

for f in "$BUILD/immpakt.bin" "$BUILD/bootloader/bootloader.bin" \
         "$BUILD/partition_table/partition-table.bin"; do
  [[ -f "$f" ]] || { echo "Missing $f -- run ./build.sh first." >&2; exit 1; }
done

if [[ -z "$PORT" ]]; then
  PORT="$(ls /dev/cu.usbmodem* 2>/dev/null | head -1 || true)"
  [[ -n "$PORT" ]] || { echo "No /dev/cu.usbmodem* found; plug the PicPak in." >&2; exit 1; }
fi

echo "Flashing $PORT"
# Offsets and baud match what idf.py itself prints for this project; the C3's
# console is USB-Serial-JTAG (native USB CDC), so the baud is largely nominal.
esptool.py --chip esp32c3 --port "$PORT" --baud 460800 \
  --before default_reset --after hard_reset write_flash \
  -z --flash_mode dio --flash_freq 80m --flash_size detect \
  0x0     "$BUILD/bootloader/bootloader.bin" \
  0x8000  "$BUILD/partition_table/partition-table.bin" \
  0x10000 "$BUILD/immpakt.bin"

echo
echo "Done. Watch it boot with:"
echo "  esptool.py --port $PORT --after no_reset read_mac >/dev/null; screen $PORT 115200"
echo
echo "First boot opens the setup portal: join Wi-Fi 'ImmPakt-Setup'"
echo "(password immpakt123), then open http://192.168.4.1 and set the server URL."
