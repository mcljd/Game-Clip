#!/usr/bin/env bash
# Download the pinned ffmpeg.wasm files into web/vendor/ (not committed: ~32 MB).
set -euo pipefail
cd "$(dirname "$0")"
FFMPEG=0.12.15 CORE=0.12.10
tmp=$(mktemp -d)
( cd "$tmp" && npm pack --silent "@ffmpeg/ffmpeg@$FFMPEG" "@ffmpeg/core@$CORE" >/dev/null \
  && for t in *.tgz; do tar xzf "$t" && mv package "${t%.tgz}"; done )
mkdir -p vendor
cp "$tmp"/ffmpeg-ffmpeg-*/dist/umd/ffmpeg.js "$tmp"/ffmpeg-ffmpeg-*/dist/umd/814.ffmpeg.js vendor/
cp "$tmp"/ffmpeg-core-*/dist/umd/ffmpeg-core.js "$tmp"/ffmpeg-core-*/dist/umd/ffmpeg-core.wasm vendor/
rm -rf "$tmp"
ls -la vendor
