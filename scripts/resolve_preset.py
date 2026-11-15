#!/usr/bin/env python3
"""Resolve '/shorts <preset> <link> <streamer>' into one config for the pipeline.

Merges a preset (presets/<name>.json, or ~/.config/claude-shorts/presets/<name>.json)
with the streamer's entry in ~/.config/claude-shorts/streamers.json and, when a URL
is given, asks yt-dlp for the stream title to name the output folder:

    <output_base>/<stream name>/

Usage:
    python3 resolve_preset.py gta URL examplestreamer
    python3 resolve_preset.py --list            # presets and streamers
"""
import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USER_DIR = os.path.expanduser(os.environ.get("SHORTS_CONFIG", "~/.config/claude-shorts"))
STREAMERS = os.path.join(USER_DIR, "streamers.json")
PRESET_DIRS = [os.path.join(USER_DIR, "presets"), os.path.join(ROOT, "presets")]


def fail(msg, **extra):
    print(json.dumps({"error": msg, **extra}, indent=2, ensure_ascii=False))
    sys.exit(1)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def presets():
    found = {}
    for d in reversed(PRESET_DIRS):  # user presets override built-in ones
        if os.path.isdir(d):
            for fn in os.listdir(d):
                if fn.endswith(".json"):
                    found[fn[:-5]] = os.path.join(d, fn)
    return found


def streamers():
    if not os.path.isfile(STREAMERS):
        return {}
    return {k: v for k, v in load_json(STREAMERS).items() if not k.startswith("_")}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def find_streamer(name, table):
    for key, entry in table.items():
        names = [key, entry.get("name", "")] + entry.get("aliases", [])
        if norm(name) in {norm(n) for n in names if n}:
            return key, entry
    return None, None


def slug(text, limit=60):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return text[:limit].rstrip("-") or "stream"


def probe(url):
    r = subprocess.run(
        ["yt-dlp", "--js-runtimes", "node", "--no-warnings", "--skip-download",
         "--print", "%(id)s\t%(title)s\t%(live_status)s\t%(duration)s\t%(extractor_key)s", url],
        capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        fail("yt-dlp could not read the link", detail=r.stderr.strip()[-300:])
    vid, title, live, dur, site = r.stdout.strip().splitlines()[-1].split("\t")
    return {"id": vid, "title": title, "live_status": live,
            "duration": None if dur in ("NA", "None") else float(dur), "site": site}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("preset", nargs="?")
    ap.add_argument("url", nargs="?")
    ap.add_argument("streamer", nargs="?")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    all_presets, all_streamers = presets(), streamers()
    if args.list or not args.preset:
        print(json.dumps({"presets": sorted(all_presets), "streamers": sorted(all_streamers),
                          "streamers_file": STREAMERS}, indent=2))
        return

    if args.preset not in all_presets:
        fail(f"Unknown preset '{args.preset}'", presets=sorted(all_presets))
    cfg = load_json(all_presets[args.preset])
    cfg["preset"] = args.preset

    if args.streamer:
        key, entry = find_streamer(args.streamer, all_streamers)
        if entry is None:
            fail(f"Unknown streamer '{args.streamer}'. Add an entry to {STREAMERS} "
                 "(facecam 'x,y,w,h' as fractions, language, twitch, youtube).",
                 streamers=sorted(all_streamers))
        cfg["streamer"] = {"key": key, **entry}
        cfg["facecam"] = entry.get("facecam")
        cfg["language"] = entry.get("language")
    else:
        cfg["streamer"] = None
        cfg.setdefault("facecam", None)
        cfg.setdefault("language", None)

    base = os.environ.get("SHORTS_OUTPUT_BASE", cfg.get("output_base", "./shorts"))
    if args.url:
        info = probe(args.url)
        cfg["source"] = {"url": args.url, **info}
        name = slug(info["title"])
        h = cfg.get("download_height", 1080)
        work = os.path.expanduser(f"~/shorts-work/{info['id']}")
        cfg["work_dir"] = work
        cfg["video_path"] = f"{work}/video.mp4"
        cfg["download_cmd"] = (
            f"yt-dlp --js-runtimes node --no-warnings --no-progress "
            f"-f 'bv*[height<={h}][vcodec^=avc]+ba[ext=m4a]/bv*[height<={h}]+ba/b[height<={h}]' "
            f"--merge-output-format mp4 -o '{work}/video.%(ext)s' '{args.url}'")
        cfg["chat_available"] = info["live_status"] == "was_live"
    else:
        name = "stream"
    cfg["output_dir"] = os.path.join(base, name)
    print(json.dumps(cfg, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
