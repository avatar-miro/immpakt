#!/usr/bin/env bash
# Dump the PicPak's stock firmware TWICE and refuse to pass unless the two
# dumps are byte-identical. This backup is the only route back to the factory
# software -- a single dump can be silently corrupted by a flaky USB cable or a
# marginal battery, and you would not find out until you needed to restore.
#
#   ./backup-stock.sh [/dev/cu.usbmodemXXXX]
set -euo pipefail

PORT="${1:-}"
OUT_DIR="$(cd "$(dirname "$0")" && pwd)/stock-backup"
FLASH_SIZE=0x1000000   # 16 MB

if [[ -z "$PORT" ]]; then
  # macOS ships bash 3.2, which has no `mapfile`.
  PORTS=$(ls /dev/cu.usbmodem* 2>/dev/null || true)
  if [[ -z "$PORTS" ]]; then
    echo "No /dev/cu.usbmodem* found. Plug the PicPak in over USB and retry." >&2
    echo "If it is plugged in, hold the front button while connecting so it" >&2
    echo "stays awake long enough to enumerate." >&2
    exit 1
  fi
  PORT=$(echo "$PORTS" | head -1)
  [[ $(echo "$PORTS" | wc -l) -gt 1 ]] && echo "note: several ports found, using $PORT"
fi

command -v esptool.py >/dev/null || { echo "esptool.py not found (brew install esptool)" >&2; exit 1; }
mkdir -p "$OUT_DIR"

echo "Port      : $PORT"
esptool.py --port "$PORT" chip_id || { echo "Could not talk to the device." >&2; exit 1; }

for pass in 1 2; do
  echo
  echo "=== dump $pass of 2 (16 MB, a few minutes) ==="
  esptool.py --port "$PORT" --baud 921600 read_flash 0 "$FLASH_SIZE" "$OUT_DIR/stock-$pass.bin"
done

echo
A=$(shasum -a 256 "$OUT_DIR/stock-1.bin" | cut -d' ' -f1)
B=$(shasum -a 256 "$OUT_DIR/stock-2.bin" | cut -d' ' -f1)
echo "dump 1: $A"
echo "dump 2: $B"

if [[ "$A" != "$B" ]]; then
  echo
  echo "MISMATCH -- the two dumps differ, so neither can be trusted." >&2
  echo "Do NOT flash. Try a different USB cable/port and run this again." >&2
  exit 1
fi

cp "$OUT_DIR/stock-1.bin" "$OUT_DIR/stock-verified.bin"
rm -f "$OUT_DIR/stock-2.bin"
echo "$A  stock-verified.bin" > "$OUT_DIR/SHA256"
echo
echo "OK -- backup verified: $OUT_DIR/stock-verified.bin"
echo "Keep a copy somewhere off this machine. Restore with:"
echo "  esptool.py --port $PORT write_flash 0x0 $OUT_DIR/stock-verified.bin"
