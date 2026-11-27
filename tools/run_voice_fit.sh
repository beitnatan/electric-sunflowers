#!/bin/bash
# run_voice_fit.sh: renders the demo and center-pivot songs with one voice fit (stems example,
# --voice-fit) and measures them with voice_target, harmonic_prominence and vowel_open_h12.
# usage: run_voice_fit.sh NAME "fit spec"  -> $SCRATCH/vx/NAME/{demo,cp}/{lead.wav,notes.json}
# Environment: REPO (repository root; default: the parent of this script's directory),
# SCRATCH (default: ${TMPDIR:-/tmp}/sunflower-eval), PYTHON (default: python3), SINGMOS_DIR
# (not used here; eval_artic.sh only).
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${REPO:-$(cd "$HERE/.." && pwd)}"
SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/sunflower-eval}"
PY="${PYTHON:-python3}"
cd "$REPO"
n=$1; spec=$2; d=$SCRATCH/vx/$n; rm -rf "$d"; mkdir -p "$d"
for sg in demo cp; do
  a=""; [ $sg = cp ] && a="--song songs/center-pivot.json"
  if [ -n "$spec" ]; then target/release/examples/stems --seed 1234 $a --voice-fit "$spec" --out $d/$sg >/dev/null 2>&1
  else target/release/examples/stems --seed 1234 $a --out $d/$sg >/dev/null 2>&1; fi
  find $d/$sg -type f ! -name lead.wav ! -name notes.json -delete
done
"$PY" -I tools/voice_target.py eng $d/demo $d/cp --target tools/targets/voice_vocadito_male.json > $d/vt.txt 2>&1
"$PY" -I "$HERE/harmonic_prominence.py" eng $n $d/demo $d/cp > $d/pk.txt 2>&1
"$PY" -I "$HERE/vowel_open_h12.py" eng $n $d/demo $d/cp > $d/h12.txt 2>&1
