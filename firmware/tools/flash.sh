#!/usr/bin/env bash
# flash.sh: compile + upload swarm_member to the Neurick board with the spec board settings (00_CONTEXT 21).
#   bash tools/flash.sh [COM6] [compile-only]
set -euo pipefail
PORT="${1:-COM6}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SKETCH="$HERE/../swarm_member"
CLI="/c/Users/ADMIN/Arduino IDE/resources/app/lib/backend/resources/arduino-cli.exe"
FQBN="esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc,FlashSize=16M,PSRAM=opi,PartitionScheme=default,EraseFlash=none"
BUILD="${TEMP:-/tmp}/swarm_member_build"
"$CLI" compile --fqbn "$FQBN" --warnings all --build-path "$BUILD" "$SKETCH"
[ "${2:-}" = "compile-only" ] && exit 0
"$CLI" upload --fqbn "$FQBN" -p "$PORT" --input-dir "$BUILD" "$SKETCH"
