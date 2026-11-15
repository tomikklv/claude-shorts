#!/usr/bin/env python3
"""Fast renderer: burn word-highlighted captions into the proxies with FFmpeg.

Default renderer of the pipeline. Takes the same inputs as remotion/render.mjs
and writes the same outputs (render/short_NN.mp4), but needs no browser and
finishes a clip in seconds: captions are written as an ASS subtitle file and
burned in by libass, encoded with NVENC when available.

Captions are grouped into short one-line pages (sentence end, pause, 3 words or
18 characters); the word being spoken is highlighted. The caption height comes
from `caption_center_y` in the proxy reframe JSON.

Usage:
    python3 burn_captions.py --segments snapped_segments.json \
        --reframe reframe_proxy.json --captions transcript_cleaned.json \
        --clips-dir clips_proxy/ --output-dir render/ [--style bold|bounce|clean]
        [--only 1,3] [--no-progress-bar]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS_DIR = os.path.join(ROOT, "remotion", "public", "fonts")

MAX_WORDS = 3
MAX_CHARS = 18
HOLD_MS = 700
DEFAULT_CENTER_Y = 1525  # same place as the Remotion styles (350px above the bottom)

# ASS colours are &HAABBGGRR
STYLES = {
    "bold": dict(font="Montserrat", size=86, upper=True, combine=800,
                 text="&H00FFFFFF", active="&H0000D7FF", outline=5, shadow=0, pop=True),
    "bounce": dict(font="Bangers", size=104, upper=True, combine=600,
                   text="&H00FFFFFF", active="&H0000FFFF", outline=5, shadow=0, pop=True),
    "clean": dict(font="Inter", size=64, upper=False, combine=1500,
                  text="&H00FFFFFF", active="&H00E0E0E0", outline=2, shadow=3, pop=False),
}


def have_nvenc():
    r = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True)
    return "h264_nvenc" in r.stdout


def probe_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    return float(r.stdout.strip())


def ass_time(ms):
    ms = max(0, int(round(ms)))
    cs = ms // 10
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def make_pages(words, combine_ms):
    """Same paging rules as remotion/src/hooks/useCaptionPages.ts."""
    pages, cur, chars = [], [], 0
    for w in words:
        text = w["text"].strip()
        if not text:
            continue
        if cur:
            prev = cur[-1]
            if (re.search(r"[.?!]$", prev["text"].strip())
                    or w["startMs"] - prev["endMs"] > combine_ms
                    or len(cur) >= MAX_WORDS
                    or chars + 1 + len(text) > MAX_CHARS):
                pages.append(cur)
                cur, chars = [], 0
        cur.append(w)
        chars += (1 if chars else 0) + len(text)
    if cur:
        pages.append(cur)
    return pages


def esc(text):
    return text.replace("\\", "").replace("{", "(").replace("}", ")")


def build_ass(words, style, center_y):
    st = STYLES[style]
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{st['font']},{st['size']},{st['text']},{st['text']},&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,{st['outline']},{st['shadow']},5,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    pages = make_pages(words, st["combine"])
    for pi, page in enumerate(pages):
        next_start = pages[pi + 1][0]["startMs"] if pi + 1 < len(pages) else float("inf")
        page_end = min(next_start, page[-1]["endMs"] + HOLD_MS)
        for wi, w in enumerate(page):
            start = w["startMs"] if wi else page[0]["startMs"]
            end = page[wi + 1]["startMs"] if wi + 1 < len(page) else page_end
            if end <= start:
                continue
            parts = []
            for wj, w2 in enumerate(page):
                t = esc(w2["text"].strip())
                t = t.upper() if st["upper"] else t
                # highlight only while the word is actually being spoken
                parts.append(f"{{\\c{st['active']}}}{t}{{\\c{st['text']}}}" if wj == wi else t)
            tag = f"{{\\an5\\pos(540,{center_y})"
            if st["pop"] and wi == 0:
                tag += "\\fscx78\\fscy78\\t(0,90,\\fscx104\\fscy104)\\t(90,160,\\fscx100\\fscy100)"
            tag += "}"
            lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Cap,,0,0,0,,{tag}{' '.join(parts)}")
    return head + "\n".join(lines) + "\n", len(pages)


def ff_path(p):
    """Escape a path for use inside an FFmpeg filter argument."""
    return p.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--segments", required=True)
    ap.add_argument("--reframe", required=True)
    ap.add_argument("--captions", required=True)
    ap.add_argument("--clips-dir", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--style", default="bold", choices=sorted(STYLES))
    ap.add_argument("--only", help="comma list of segment ids to render")
    ap.add_argument("--no-progress-bar", action="store_true")
    args = ap.parse_args()

    with open(args.segments) as f:
        segments = json.load(f)["segments"]
    with open(args.reframe) as f:
        reframe = json.load(f).get("clips", {})
    with open(args.captions) as f:
        captions = json.load(f)["captions"]
    only = {int(v) for v in args.only.split(",")} if args.only else None
    os.makedirs(args.output_dir, exist_ok=True)
    nvenc = have_nvenc()
    enc = (["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "18"] if nvenc
           else ["-c:v", "libx264", "-preset", "medium", "-crf", "18"])
    results, failed = [], 0

    for i, seg in enumerate(segments):
        seg_id = seg.get("id", i + 1)
        if only and seg_id not in only:
            continue
        name = f"clip_{seg_id:02d}.mp4"
        clip = os.path.join(args.clips_dir, name)
        out = os.path.join(args.output_dir, f"short_{seg_id:02d}.mp4")
        ass = os.path.join(args.output_dir, f"short_{seg_id:02d}.ass")
        if os.path.exists(out):
            os.remove(out)  # never leave an older render behind
        t0 = time.time()
        try:
            if not os.path.isfile(clip):
                raise RuntimeError(f"clip not found: {clip}")
            entry = reframe.get(name, {})
            if entry.get("source_resolution", "1080x1920") != "1080x1920":
                raise RuntimeError(f"{name} is not a 1080x1920 proxy (run prepare_proxy.py; "
                                   "screen/cursor-track layouts need the Remotion renderer)")
            s_ms, e_ms = seg["start"] * 1000, seg["end"] * 1000
            words = [{"text": c["text"], "startMs": c["startMs"] - s_ms, "endMs": c["endMs"] - s_ms}
                     for c in captions if c["startMs"] >= s_ms and c["endMs"] <= e_ms]
            text, n_pages = build_ass(words, args.style, entry.get("caption_center_y") or DEFAULT_CENTER_Y)
            with open(ass, "w", encoding="utf-8") as f:
                f.write(text)
            dur = probe_duration(clip)
            vf = f"[0:v]ass='{ff_path(ass)}':fontsdir='{ff_path(FONTS_DIR)}'[v]"
            if args.no_progress_bar:
                fc = vf.replace("[v]", "[out]")
            else:
                fc = (vf + f";color=c=white@0.9:s=1080x6:r=30[bar];"
                      f"[v][bar]overlay=x='-W+W*t/{dur:.3f}':y=H-6:shortest=1[out]")
            cmd = (["ffmpeg", "-y", "-v", "error", "-i", clip, "-filter_complex", fc,
                    "-map", "[out]", "-map", "0:a?"] + enc +
                   ["-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", out])
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip()[-400:])
            res = {"action": "render", "segment": seg_id, "output": out, "pages": n_pages,
                   "duration": f"{dur:.1f}s", "render_time_sec": round(time.time() - t0, 1),
                   "file_size_mb": round(os.path.getsize(out) / 1048576, 1), "status": "complete"}
        except Exception as e:  # report and keep going with the other clips
            failed += 1
            if os.path.exists(out):
                os.remove(out)
            res = {"action": "render", "segment": seg_id, "output": out,
                   "status": "failed", "error": str(e)}
        results.append(res)
        print(json.dumps(res), flush=True)

    print(json.dumps({"action": "render_complete", "renderer": "ffmpeg", "nvenc": nvenc,
                      "segments_rendered": len(results) - failed, "failed": failed}))
    sys.exit(1 if failed or not results else 0)


if __name__ == "__main__":
    main()
