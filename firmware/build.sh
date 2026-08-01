#!/usr/bin/env bash
# Build the firmware in the official ESP-IDF container, so nothing is installed
# on the host. Docker on macOS cannot pass through USB, so this only builds --
# flash from the host with ./flash.sh, which uses the native esptool.py.
#
#   ./build.sh          # build
#   ./build.sh clean    # wipe build/ first
set -euo pipefail

IDF_IMAGE="${IDF_IMAGE:-espressif/idf:release-v5.3}"
HERE="$(cd "$(dirname "$0")" && pwd)"

docker image inspect "$IDF_IMAGE" >/dev/null 2>&1 || {
  echo "Pulling $IDF_IMAGE (~2.5 GB, one time)..."
  docker pull "$IDF_IMAGE"
}

[[ "${1:-}" == "clean" ]] && rm -rf "$HERE/build" "$HERE/sdkconfig"

# `set-target` regenerates sdkconfig from scratch, so it must run ONCE on a
# fresh tree and never again — re-running it on an existing build tree leaves
# the config half-written and the next compile fails on a missing sdkconfig.h.
if [[ -d "$HERE/build" ]]; then
  CMD="idf.py build"
else
  CMD="idf.py set-target esp32c3 build"
fi

LOG="$HERE/build.log"
set +e
docker run --rm -v "$HERE":/project -w /project -e HOME=/tmp "$IDF_IMAGE" \
  sh -c "$CMD" > "$LOG" 2>&1
rc=$?
set -e
if [[ $rc -ne 0 ]]; then
  echo "BUILD FAILED (full log: $LOG)" >&2
  grep -E "error:|fatal error|undefined reference|FAILED:" "$LOG" | head -20 >&2
  exit $rc
fi
grep -E "Project build complete|bytes|Successfully created" "$LOG" | tail -8

echo
echo "Artifacts in $HERE/build:"
ls -l "$HERE"/build/immpakt.bin "$HERE"/build/bootloader/bootloader.bin \
      "$HERE"/build/partition_table/partition-table.bin 2>/dev/null || true
echo
echo "Next: ./flash.sh   (backup-stock.sh first if you have not already)"
