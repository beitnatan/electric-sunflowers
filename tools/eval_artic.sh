#!/bin/bash
# eval_artic.sh: articulation report for the reel and center-pivot songs: consonant depth,
# Whisper word errors, SingMOS-Pro and Audiobox Aesthetics on the lead alone.
# usage: eval_artic.sh NAME [STEMS_BIN] [SUNFLOWER_BIN] [REPO]  -> $SCRATCH/evalart/NAME/report.txt
# Environment: REPO (repository root; default: the parent of this script's directory),
# SCRATCH (default: ${TMPDIR:-/tmp}/sunflower-eval), PYTHON (interpreter with numpy, jiwer,
# Whisper, torch; default: python3), SINGMOS_DIR (SingMOS source and weights; default:
# $SCRATCH/singmos).
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${REPO:-$(cd "$HERE/.." && pwd)}"
SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/sunflower-eval}"
PY="${PYTHON:-python3}"
export SINGMOS_DIR="${SINGMOS_DIR:-$SCRATCH/singmos}"
REPO=${4:-$REPO}; STEMS=${2:-$REPO/target/release/examples/stems}; SUN=${3:-$REPO/target/release/sunflower}
D=$SCRATCH/evalart/$1; rm -rf "${D:?}"; mkdir -p $D; cd $REPO
for n in reel cp; do f=songs/firelight-reel.json; [ $n = cp ] && f=songs/center-pivot.json
  $SUN sheet $f --seed 1234 --json > $D/$n.sheet.json 2>/dev/null
  $STEMS --seed 1234 --song $f --out $D/$n >/dev/null 2>&1
  find $D/$n -type f -name '*.wav' ! -name lead.wav ! -name lead_b.wav ! -name choir.wav ! -name mix.wav -delete; done
{ for n in reel cp; do echo "== $n consonant depth"; "$PY" -I "$HERE/consonant_depth.py" $D/$n; done
  for n in reel cp; do echo "== $n Whisper (small.en), voice and mix"; "$PY" tools/wer_song.py $D/$n.sheet.json $D/$n --model small.en --show > $D/$n.wer.txt 2>&1; grep -E '^WER' $D/$n.wer.txt; "$PY" "$HERE/consonant_survival.py" $D/$n.wer.txt | grep -E 'stop|nasal|glide|liquid|fric|^  h '; done
  echo "== SingMOS-Pro, lead alone"; cd $D && "$PY" "$HERE/singmos_score.py" $D/smos.json reel/lead.wav cp/lead.wav 2>&1 | grep windows
  echo "== Audiobox, lead alone"; "$PY" "$HERE/aes_compare.py" score $D/aes.json reel/lead.wav cp/lead.wav 2>&1 | grep windows
} > $D/report.txt 2>&1
find $D -name mix.wav -delete; find $D -name choir.wav -delete
cat $D/report.txt
