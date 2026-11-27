#!/usr/bin/env bash
# Builds the browser page into web/: the wasm engine and the bundled songs.
# Output is web/webrender.wasm and web/songs/*.json (both gitignored);
# serve web/ with any static server, e.g. `python3 -m http.server -d web`.
set -euo pipefail
cd "$(dirname "$0")/.."
rustup target add wasm32-unknown-unknown >/dev/null 2>&1 || true
CARGO_PROFILE_RELEASE_DEBUG=0 cargo build --release --target wasm32-unknown-unknown -p webrender
mkdir -p web/songs
cp target/wasm32-unknown-unknown/release/webrender.wasm web/webrender.wasm
cp crates/engine/src/demo.json web/songs/demo.json
cp songs/firelight-reel.json songs/center-pivot.json songs/fat-cats-in-the-hay.json web/songs/
ls -l web/webrender.wasm
