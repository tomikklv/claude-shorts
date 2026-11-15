#!/usr/bin/env python3
"""Material for judging candidates BEFORE anything is cut or rendered.

For every candidate (highlights.json candidates or a segments JSON) writes

  * a contact sheet of the source video (8 frames across the candidate), so
    the picture can be judged too: reactions without speech, what is on
    screen, which layout fits, whether a facecam is where it is expected
  * the transcript of the candidate plus the 10 seconds before the start and
    after the end, so the cut points can be checked: the clip must open on a
    line that makes sense by itself and end on a finished thought or reaction

Usage:
    python3 review_candidates.py INPUT_VIDEO --candidates highlights.json \
        --transcript transcript.json --output-dir review/ [--context 10]
"""
import argparse
import json
import os
import subprocess
import sys


def text_between(segments, a, b):
    out = []
    for s in segments:
        words = s.get("words") or []
        if words:
            ws = [w["word"].strip() for w in words if a <= w["start"] < b]
            if ws:
                out.append(" ".join(ws))
        elif s["end"] > a and s["start"] < b:
            out.append(s["text"].strip())
    return " ".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("--candidates", required=True,
                    help="highlights.json or a segments JSON ({'segments': [...]})")
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--context", type=float, default=10.0)
    ap.add_argument("--frames", type=int, default=8)
    args = ap.parse_args()

    with open(args.candidates) as f:
        data = json.load(f)
    cands = data.get("candidates") or data.get("segments") or []
    with open(args.transcript) as f:
        segs = json.load(f)["segments"]
    os.makedirs(args.output_dir, exist_ok=True)
    report = []

    for i, c in enumerate(cands):
        cid = c.get("rank") or c.get("id") or i + 1
        a, b = float(c["start"]), float(c["end"])
        sheet = os.path.join(args.output_dir, f"cand_{int(cid):02d}.png")
        n = args.frames
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{a:.2f}", "-t", f"{b - a:.2f}", "-i", args.input,
             "-vf", f"fps={n}/{b - a:.2f},scale=480:-2,tile={n // 2}x2", "-frames:v", "1", sheet],
            capture_output=True, text=True)
        words = text_between(segs, a, b)
        report.append({
            "id": cid, "start": a, "end": b, "duration": round(b - a, 1),
            "signals": c.get("reasons"),
            "contact_sheet": sheet if r.returncode == 0 else None,
            "before_start": text_between(segs, a - args.context, a),
            "clip_text": words,
            "after_end": text_between(segs, b, b + args.context),
            "word_count": len(words.split()),
        })

    out = os.path.join(args.output_dir, "review.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(json.dumps({"action": "review_candidates", "candidates": len(report), "report": out,
                      "sheets": [r["contact_sheet"] for r in report]}, indent=2))
    sys.exit(0 if report else 1)


if __name__ == "__main__":
    main()
