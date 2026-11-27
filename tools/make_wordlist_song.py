#!/usr/bin/env python3
"""Write one schema-3 song per voice that sings tools/ttswords.json.

  make_wordlist_song.py [OUTDIR]     default out/wordlist

One word per line, one line per bar group, 4/4 at 60 bpm, no band (the guitar pattern has no "none"; only lead.wav is used),
no drums. Every note is hinted with a `tune` at the voice's mid pitch (all
tokens `d` in a key chosen so that the tonic is near the voice's comfortable
centre), so the pitch is fixed and the vowel is the only variable. The engine
may still transpose the whole song by whole semitones to fit the voice range.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# voice -> (key, tune token for one note). Mid pitch: baritone G3 55, tenor C4 60,
# alto F4 65, soprano Bb4 70 (register_of puts the tonic nearest MIDI 60).
VOICES = {
    "baritone": ("G", "d"),
    "tenor": ("C", "d"),
    "alto": ("F", "d"),
    "soprano": ("Bb", "d'"),
}


def song_for(voice, items):
    key, tok = VOICES[voice]
    lines = []
    for it in items:
        syl = "-".join(("*" if i == it["stress"] else "") + s
                       for i, s in enumerate(it["syl"].split("-")))
        lines.append({
            "syl": syl,
            "ph": "|".join(it["ph"]),
            "chords": [key],
            "tune": " ".join([tok] * it["syllables"]),
        })
    return {
        "schema_version": 3,
        "title": "Word List (%s)" % voice,
        "note": "A TTS test word list, one word per line, sung on one pitch.",
        "key": key, "mode": "major", "meter": "4/4", "tempo": 60,
        "guitar": "fingerpick", "voice": voice,
        "band": {"drums": "none", "bass": False, "harmonyGuitar": False,
                 "harp": False, "violin": False, "choir": False,
                 "harmonies": False, "doubles": False},
        "sections": [{"type": "verse", "lines": lines}],
    }


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "out/wordlist"
    items = json.load(open(os.path.join(HERE, "ttswords.json")))["items"]
    os.makedirs(out, exist_ok=True)
    for v in VOICES:
        p = os.path.join(out, v + ".json")
        json.dump(song_for(v, items), open(p, "w"), indent=1)
        print(p, len(items), "words")


if __name__ == "__main__":
    main()
