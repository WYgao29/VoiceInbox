#!/bin/bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
src="$root/public/brand/voiceinbox-mark.svg"
work="$(mktemp -d)"
python3 - "$src" "$work/icon.svg" << 'PY'
import pathlib, sys
svg = pathlib.Path(sys.argv[1]).read_text()
svg = svg.replace('width="22"', 'width="1024"', 1).replace('height="22"', 'height="1024"', 1)
pathlib.Path(sys.argv[2]).write_text(svg)
PY
sips -s format png "$work/icon.svg" --out "$work/icon-1024.png" >/dev/null
iconset="$work/icon.iconset"
mkdir -p "$iconset"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$work/icon-1024.png" --out "$iconset/icon_${size}x${size}.png" >/dev/null
  dbl=$((size * 2))
  sips -z "$dbl" "$dbl" "$work/icon-1024.png" --out "$iconset/icon_${size}x${size}@2x.png" >/dev/null
done
mkdir -p "$root/build"
iconutil -c icns "$iconset" -o "$root/build/icon.icns"
sips -z 32 32 "$work/icon-1024.png" --out "$root/public/favicon.png" >/dev/null
rm -rf "$work"
