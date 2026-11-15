#!/usr/bin/env python3
"""Turn raw clips into render-ready 1080-wide proxies for Remotion.

Remotion's OffthreadVideo breaks on 4K frames, and the gaming layout needs two
crops of the same clip. So the reframing is done here with FFmpeg and Remotion
receives a finished video:

  talking-head / podcast (face-track)  crop -> 1080x1920
  gaming                               full-frame game + small facecam overlay on top -> 1080x1920
  irl (blur-fill)                      4:5 crop over a blurred background, captions below
  screen / other                       downscaled to <=1920 wide, crop coordinates rescaled

Writes a new reframe JSON that matches the proxies; pass that (and the proxy
directory) to render.mjs instead of the originals.

Usage:
    python3 prepare_proxy.py --clips-dir clips/ --reframe reframe.json \
        --output-dir clips_proxy/ --output-reframe reframe_proxy.json
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

GAME_ZONE_H = 1150
CAM_ZONE_H = 770
CAM_MAX_W = 1000
CAM_TOP = 110
FG_TOP = 130       # blur-fill: top of the picture
CAPTION_GAP = 110  # distance from a layout edge to the caption centre


def have_nvenc():
    r = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True)
    return "h264_nvenc" in r.stdout


def encoder_args(nvenc):
    if nvenc:
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "18"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]


def run_ffmpeg(src, dst, vf, complex_filter, nvenc):
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", src]
    if complex_filter:
        cmd += ["-filter_complex", complex_filter, "-map", "[out]", "-map", "0:a?"]
    else:
        cmd += ["-vf", vf]
    cmd += encoder_args(nvenc) + ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", dst]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-500:])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clips-dir", required=True)
    ap.add_argument("--reframe", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--output-reframe", required=True)
    args = ap.parse_args()

    with open(args.reframe) as f:
        reframe = json.load(f)
    os.makedirs(args.output_dir, exist_ok=True)
    nvenc = have_nvenc()
    t0 = time.time()
    new_clips, failed = {}, []

    for clip in sorted(glob.glob(os.path.join(args.clips_dir, "clip_*.mp4"))):
        name = os.path.basename(clip)
        entry = reframe.get("clips", {}).get(name)
        if not entry:
            failed.append({"clip": name, "error": "no reframe entry"})
            continue
        dst = os.path.join(args.output_dir, name)
        sw, sh = (int(v) for v in entry["source_resolution"].split("x"))
        c = entry["crop"]
        new = dict(entry)
        try:
            if entry["strategy"] == "gaming":
                cam = entry["facecam"]
                # game fills the frame; the facecam is a small bordered overlay at
                # the top and the captions sit right below it (never over the face)
                cam_h = int(round(entry.get("cam_scale", 0.25) * 1920 / 2) * 2)
                cam_w = int(round(cam_h * cam["w"] / cam["h"] / 2) * 2)
                if cam_w > CAM_MAX_W:
                    cam_w = CAM_MAX_W
                    cam_h = int(round(cam_w * cam["h"] / cam["w"] / 2) * 2)
                fc = (f"[0:v]crop={c['w']}:{c['h']}:{c['x']}:{c['y']},"
                      f"scale=1080:1920:flags=lanczos[g];"
                      f"[0:v]crop={cam['w']}:{cam['h']}:{cam['x']}:{cam['y']},"
                      f"scale={cam_w}:{cam_h}:flags=lanczos,pad=iw+8:ih+8:4:4:color=white[c];"
                      f"[g][c]overlay=(W-w)/2:{CAM_TOP}[out]")
                new["caption_center_y"] = CAM_TOP + cam_h + 8 + CAPTION_GAP
                run_ffmpeg(clip, dst, None, fc, nvenc)
                new.update(source_resolution="1080x1920", crop={"x": 0, "y": 0, "w": 1080, "h": 1920})
                new.pop("crop_keyframes", None)
            elif entry["strategy"] == "blur-fill":
                fg_h = int(round(1080 * c["h"] / c["w"] / 2) * 2)
                fc = (f"[0:v]split[a][b];"
                      f"[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
                      f"gblur=sigma=14,eq=brightness=-0.22:saturation=0.8,scale=1080:1920:flags=bicubic[bg];"
                      f"[b]crop={c['w']}:{c['h']}:{c['x']}:{c['y']},"
                      f"scale=1080:{fg_h}:flags=lanczos[fg];"
                      f"[bg][fg]overlay=0:{FG_TOP}[out]")
                run_ffmpeg(clip, dst, None, fc, nvenc)
                new.update(source_resolution="1080x1920", crop={"x": 0, "y": 0, "w": 1080, "h": 1920})
                new.pop("crop_keyframes", None)
                new["caption_center_y"] = FG_TOP + fg_h + CAPTION_GAP
            elif entry["strategy"] == "face-track":
                vf = f"crop={c['w']}:{c['h']}:{c['x']}:{c['y']},scale=1080:1920:flags=lanczos"
                run_ffmpeg(clip, dst, vf, None, nvenc)
                new.update(source_resolution="1080x1920", crop={"x": 0, "y": 0, "w": 1080, "h": 1920})
                new.pop("crop_keyframes", None)
            else:
                f = min(1.0, 1920 / sw)
                tw, th = int(round(sw * f / 2) * 2), int(round(sh * f / 2) * 2)
                run_ffmpeg(clip, dst, f"scale={tw}:{th}:flags=lanczos", None, nvenc)
                new["source_resolution"] = f"{tw}x{th}"
                new["crop"] = {k: int(round(v * f)) for k, v in c.items()}
                if entry.get("crop_keyframes"):
                    new["crop_keyframes"] = [{**k, "x": k["x"] * f} for k in entry["crop_keyframes"]]
            new_clips[name] = new
            print(json.dumps({"action": "proxy", "clip": name, "strategy": entry["strategy"]}))
        except RuntimeError as e:
            failed.append({"clip": name, "error": str(e)})

    out = dict(reframe)
    out["clips"] = new_clips
    with open(args.output_reframe, "w") as f:
        json.dump(out, f, indent=2)
    print(json.dumps({"action": "prepare_proxy", "clips": len(new_clips), "failed": failed,
                      "nvenc": nvenc, "time_sec": round(time.time() - t0, 1)}, indent=2))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
