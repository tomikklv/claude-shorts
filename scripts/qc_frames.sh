#!/usr/bin/env bash
# Quality check: one contact sheet (6 frames) per short, for a visual review
# of layout, caption position and picture quality before delivery.
# Usage: bash qc_frames.sh SHORTS_DIR [OUTPUT_DIR]
set -euo pipefail
DIR="${1:?usage: qc_frames.sh SHORTS_DIR [OUTPUT_DIR]}"
OUT="${2:-$DIR/qc}"
mkdir -p "$OUT"
for f in "$DIR"/short_*.mp4; do
    [ -f "$f" ] || continue
    dur=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$f")
    sheet="$OUT/$(basename "$f" .mp4).png"
    # 6 frames spread over the clip, skipping the first/last half second
    ffmpeg -y -v error -ss 0.5 -i "$f" \
        -vf "fps=6/($dur-1),scale=360:-1,tile=6x1" -frames:v 1 "$sheet"
    echo "$sheet"
done
