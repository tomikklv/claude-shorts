#!/usr/bin/env python3
"""Compute reframe coordinates for vertical (9:16) video from landscape clips.

Supports three reframing strategies based on content type:
- talking-head: Face-tracked center crop (follows the speaker's face)
- screen: Cursor-tracked pan with moderate zoom (follows mouse cursor)
- podcast: Center crop or dominant-speaker tracking

Usage:
    python3 compute_reframe.py --clips-dir /tmp/claude-shorts/clips/ \
        --content-type screen --output reframe.json

Output (reframe.json):
{
    "clips": {
        "clip_01.mp4": {
            "strategy": "cursor-track",
            "source_resolution": "3840x2160",
            "crop": {"x": 800, "y": 0, "w": 2112, "h": 2160},
            "crop_keyframes": [
                {"t": 0.0, "x": 800},
                {"t": 1.0, "x": 950}
            ],
            "output_resolution": "1080x1920"
        }
    }
}
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time


def get_video_info(path):
    """Get video width, height, fps, and duration."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", "-select_streams", "v:0", path],
        capture_output=True, text=True
    )
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    fmt = data.get("format", {})
    w = int(stream["width"])
    h = int(stream["height"])
    duration = float(fmt.get("duration", stream.get("duration", 0)))
    fps_str = stream.get("r_frame_rate", "30/1")
    num, den = fps_str.split("/")
    fps = round(float(num) / float(den))
    return w, h, fps, duration


def compute_face_track(clip_path, src_w, src_h, num_samples=5):
    """Track face positions across sampled frames using MediaPipe."""
    import cv2
    import tempfile
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from face_detector import FaceDetector

    _, _, _, duration = get_video_info(clip_path)

    detector = FaceDetector(min_confidence=0.5)

    face_positions = []
    timestamps = [duration * (i + 0.5) / num_samples for i in range(num_samples)]

    tmpdir = tempfile.mkdtemp()

    for ts in timestamps:
        frame_path = os.path.join(tmpdir, f"f_{ts:.2f}.jpg")
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(ts), "-i", clip_path,
             "-vframes", "1", "-q:v", "2", frame_path],
            capture_output=True
        )

        if not os.path.exists(frame_path):
            continue

        img = cv2.imread(frame_path)
        if img is None:
            continue

        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        boxes = detector.detect(rgb)

        if boxes:
            xmin, ymin, bw, bh = max(boxes, key=lambda b: b[2] * b[3])
            cx = xmin + bw / 2
            cy = ymin + bh / 2
            face_positions.append({
                "t": round(ts, 2),
                "x_center": round(cx, 4),
                "y_center": round(cy, 4),
            })

        os.unlink(frame_path)

    detector.close()

    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)

    return face_positions


def detect_cursor_positions(clip_path, src_w, src_h, sample_interval=0.5):
    """Detect mouse cursor positions via frame differencing.

    Compares consecutive frames to find small changed regions (cursor movement).
    Returns list of {t, x_norm, y_norm} positions.
    """
    import cv2
    import numpy as np
    import tempfile

    _, _, _, duration = get_video_info(clip_path)

    if duration < sample_interval * 2:
        return []

    tmpdir = tempfile.mkdtemp()

    # Extract all sample frames in a single FFmpeg call (much faster than per-frame seeks)
    fps_rate = 1.0 / sample_interval
    subprocess.run(
        ["ffmpeg", "-y", "-i", clip_path,
         "-vf", f"fps={fps_rate}", "-q:v", "2",
         os.path.join(tmpdir, "f_%04d.jpg")],
        capture_output=True
    )

    # Read extracted frames in order (FFmpeg numbers from 0001)
    import shutil
    frame_files = sorted(glob.glob(os.path.join(tmpdir, "f_*.jpg")))
    frames = []
    for i, frame_path in enumerate(frame_files):
        ts = i * sample_interval
        if ts >= duration:
            break
        img = cv2.imread(frame_path, cv2.IMREAD_GRAYSCALE)
        frames.append((ts, img))

    shutil.rmtree(tmpdir, ignore_errors=True)

    if len(frames) < 2:
        return []

    cursor_positions = []
    last_known_x = 0.5  # default to center

    for i in range(1, len(frames)):
        ts, curr = frames[i]
        _, prev = frames[i - 1]

        if curr is None or prev is None:
            cursor_positions.append({"t": round(ts, 2), "x_norm": last_known_x})
            continue

        # Compute absolute difference
        diff = cv2.absdiff(prev, curr)

        # Threshold to find changed pixels
        _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)

        # Find contours of changed regions
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # Filter for cursor-sized changes (small moving elements)
        # Cursor is typically 10-60px in each dimension
        cursor_candidates = []
        for c in contours:
            area = cv2.contourArea(c)
            x, y, w, h = cv2.boundingRect(c)
            # Cursor-sized: 50-5000 px² area, aspect ratio not too extreme
            if 50 < area < 5000 and 0.2 < (w / max(h, 1)) < 5.0:
                cx = (x + w / 2) / src_w
                cy = (y + h / 2) / src_h
                cursor_candidates.append({
                    "x_norm": cx,
                    "y_norm": cy,
                    "area": area,
                    "dist_from_last": abs(cx - last_known_x),
                })

        if cursor_candidates:
            # Pick the candidate closest to last known position (smooth tracking)
            best = min(cursor_candidates, key=lambda c: c["dist_from_last"])
            last_known_x = best["x_norm"]
            cursor_positions.append({"t": round(ts, 2), "x_norm": round(best["x_norm"], 4)})
        else:
            # No cursor-sized change detected; could be:
            # - cursor not moving (use last known)
            # - large scroll event (ignore, keep position)
            cursor_positions.append({"t": round(ts, 2), "x_norm": round(last_known_x, 4)})

    # Add initial position (use first detected or center)
    if cursor_positions:
        cursor_positions.insert(0, {"t": 0.0, "x_norm": cursor_positions[0]["x_norm"]})

    return cursor_positions


def smooth_positions(positions, window=5):
    """Apply moving average smoothing to cursor positions."""
    if len(positions) < window:
        return positions

    import numpy as np
    x_vals = np.array([p["x_norm"] for p in positions])

    # Pad edges for convolution
    kernel = np.ones(window) / window
    smoothed = np.convolve(x_vals, kernel, mode="same")

    # Fix edges (use original values for first/last few)
    half = window // 2
    smoothed[:half] = x_vals[:half]
    smoothed[-half:] = x_vals[-half:]

    result = []
    for i, p in enumerate(positions):
        result.append({"t": p["t"], "x_norm": round(float(smoothed[i]), 4)})

    return result


def compute_crop_screen_with_cursor(src_w, src_h, cursor_positions, zoom=0.55):
    """Compute cursor-tracked crop for screen recordings.

    Args:
        src_w, src_h: Source video dimensions
        cursor_positions: List of {t, x_norm} from detect_cursor_positions
        zoom: Fraction of source width to show (0.55 = 55% of screen visible)

    Returns:
        (crop, crop_keyframes) where:
        - crop: static fallback {x, y, w, h}
        - crop_keyframes: list of {t, x} for animated panning
    """
    crop_h = src_h
    crop_w = int(src_w * zoom)
    crop_w = min(crop_w, src_w)

    # Compute crop_x for each cursor position
    keyframes = []
    for pos in cursor_positions:
        cursor_x_px = pos["x_norm"] * src_w
        crop_x = int(cursor_x_px - crop_w / 2)
        crop_x = max(0, min(crop_x, src_w - crop_w))
        keyframes.append({"t": round(pos["t"], 2), "x": crop_x})

    # Deduplicate: remove keyframes where x hasn't changed much
    if len(keyframes) > 2:
        filtered = [keyframes[0]]
        for i in range(1, len(keyframes) - 1):
            prev_x = filtered[-1]["x"]
            curr_x = keyframes[i]["x"]
            # Keep keyframe if position changed by more than 2% of crop width
            if abs(curr_x - prev_x) > crop_w * 0.02:
                filtered.append(keyframes[i])
        filtered.append(keyframes[-1])
        keyframes = filtered

    # Static fallback: average position
    if keyframes:
        avg_x = int(sum(k["x"] for k in keyframes) / len(keyframes))
    else:
        avg_x = (src_w - crop_w) // 2

    avg_x = max(0, min(avg_x, src_w - crop_w))

    crop = {"x": avg_x, "y": 0, "w": crop_w, "h": crop_h}

    return crop, keyframes


def compute_crop_screen_static(src_w, src_h, zoom=0.55):
    """Static center crop for screen recordings (no cursor tracking)."""
    crop_h = src_h
    crop_w = int(src_w * zoom)
    crop_w = min(crop_w, src_w)

    center_x = int(src_w * 0.50)
    crop_x = center_x - crop_w // 2
    crop_x = max(0, min(crop_x, src_w - crop_w))

    return {"x": crop_x, "y": 0, "w": crop_w, "h": crop_h}


def compute_crop_face_track(src_w, src_h, face_positions):
    """Compute 9:16 crop centered on average face position."""
    crop_h = src_h
    crop_w = int(crop_h * 9 / 16)
    crop_w = min(crop_w, src_w)

    if face_positions:
        avg_cx = sum(fp["x_center"] for fp in face_positions) / len(face_positions)
        center_x_px = int(avg_cx * src_w)
    else:
        center_x_px = src_w // 2

    crop_x = center_x_px - crop_w // 2
    crop_x = max(0, min(crop_x, src_w - crop_w))

    return {"x": crop_x, "y": 0, "w": crop_w, "h": crop_h}


def compute_crop_center(src_w, src_h):
    """Simple center crop to 9:16."""
    crop_h = src_h
    crop_w = int(crop_h * 9 / 16)
    crop_w = min(crop_w, src_w)
    crop_x = (src_w - crop_w) // 2

    return {"x": crop_x, "y": 0, "w": crop_w, "h": crop_h}


# Gaming layout: game on top, facecam below (output 1080x1920).
GAME_ZONE_H = 1150
CAM_ZONE_H = 770  # GAME_ZONE_H + CAM_ZONE_H == 1920


def detect_facecam_box(clip_path, src_w, src_h, num_samples=6):
    """Find a streamer facecam: a face sitting in one of the frame corners.

    Faces in a facecam are small relative to the full frame, so each corner
    region is searched separately (and upscaled by the detector).
    Returns (x, y, w, h) of the face in source pixels, or None.
    """
    import cv2
    import tempfile
    import shutil
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from face_detector import FaceDetector

    _, _, _, duration = get_video_info(clip_path)
    detector = FaceDetector(min_confidence=0.5)
    rw, rh = int(src_w * 0.45), int(src_h * 0.55)
    corners = [(0, 0), (src_w - rw, 0), (0, src_h - rh), (src_w - rw, src_h - rh)]
    hits = []
    tmpdir = tempfile.mkdtemp()
    try:
        for i in range(num_samples):
            ts = duration * (i + 0.5) / num_samples
            fp = os.path.join(tmpdir, f"f{i}.jpg")
            subprocess.run(["ffmpeg", "-y", "-ss", str(ts), "-i", clip_path,
                            "-vframes", "1", "-q:v", "2", fp], capture_output=True)
            img = cv2.imread(fp)
            if img is None:
                continue
            for cx, cy in corners:
                region = cv2.cvtColor(img[cy:cy + rh, cx:cx + rw], cv2.COLOR_BGR2RGB)
                for (bx, by, bw, bh) in detector.detect(region):
                    hits.append((cx + bx * rw, cy + by * rh, bw * rw, bh * rh))
    finally:
        detector.close()
        shutil.rmtree(tmpdir, ignore_errors=True)
    if not hits:
        return None
    # Facecam is static: take the median box of the most frequent corner
    quad = {}
    for b in hits:
        quad.setdefault((b[0] > src_w / 2, b[1] > src_h / 2), []).append(b)
    best = max(quad.values(), key=len)
    import numpy as np
    return tuple(float(np.median([b[k] for b in best])) for k in range(4))


def facecam_rect_from_face(face, src_w, src_h):
    """Expand a face box into a facecam crop with the cam-zone aspect ratio."""
    fx, fy, fw, fh = face
    aspect = 16 / 9  # facecam is shown as a small 16:9 overlay
    cw = fw * 2.6
    ch = cw / aspect
    if ch < fh * 1.9:
        ch = fh * 1.9
        cw = ch * aspect
    cw, ch = min(cw, src_w), min(ch, src_h)
    cx = fx + fw / 2 - cw / 2
    cy = fy + fh / 2 - ch * 0.45
    cx = max(0, min(cx, src_w - cw))
    cy = max(0, min(cy, src_h - ch))
    return {"x": int(cx), "y": int(cy), "w": int(cw), "h": int(ch)}


def compute_gaming_layout(src_w, src_h, facecam, game_focus_x=0.5):
    """Game crop (cover-fit into the top zone) + facecam crop (bottom zone)."""
    gw = min(src_w, int(src_h * 1080 / 1920))  # game fills the whole frame
    gx = int(max(0, min(game_focus_x * src_w - gw / 2, src_w - gw)))
    return {"x": gx, "y": 0, "w": gw, "h": src_h}, facecam


def main():
    parser = argparse.ArgumentParser(description="Compute reframe coordinates")
    parser.add_argument("--clips-dir", required=True, help="Directory with clip files")
    parser.add_argument("--content-type", required=True,
                        choices=["talking-head", "screen", "podcast", "gaming", "irl"],
                        help="Content type for reframing strategy")
    parser.add_argument("--facecam", help="gaming: facecam rectangle as fractions of "
                        "the frame 'x,y,w,h' (e.g. 0.72,0.55,0.28,0.45). "
                        "Skips auto-detection; reuse it for every stream with the same overlay.")
    parser.add_argument("--game-focus-x", type=float, default=0.5,
                        help="gaming: horizontal centre of the game crop, 0-1 (default 0.5)")
    parser.add_argument("--cam-scale", type=float, default=0.25,
                        help="gaming: facecam height as a fraction of the 1920px frame (default 0.25)")
    parser.add_argument("--focus-x", default="0.5",
                        help="irl: horizontal centre of the 4:5 crop, 0-1. One value, or a "
                        "comma list with one value per clip (in clip order)")
    parser.add_argument("--output", required=True, help="Output JSON file")
    parser.add_argument("--zoom", type=float, default=0.55,
                        help="Screen zoom level: fraction of source width to show (default: 0.55)")
    parser.add_argument("--no-cursor-track", action="store_true",
                        help="Disable cursor tracking for screen content")

    args = parser.parse_args()

    if not os.path.isdir(args.clips_dir):
        print(json.dumps({"error": f"Clips directory not found: {args.clips_dir}"}))
        sys.exit(1)

    start = time.time()

    clips = sorted(glob.glob(os.path.join(args.clips_dir, "clip_*.mp4")))
    if not clips:
        print(json.dumps({"error": f"No clip_*.mp4 files found in {args.clips_dir}"}))
        sys.exit(1)

    results = {}
    focus_list = [float(v) for v in args.focus_x.split(",")]

    for clip_idx, clip_path in enumerate(clips):
        clip_name = os.path.basename(clip_path)
        src_w, src_h, fps, duration = get_video_info(clip_path)

        game_crop = facecam_crop = None
        if args.content_type == "gaming":
            if args.facecam:
                fx, fy, fw, fh = (float(v) for v in args.facecam.split(","))
                facecam_crop = {"x": int(fx * src_w), "y": int(fy * src_h),
                                "w": int(fw * src_w), "h": int(fh * src_h)}
            else:
                face = detect_facecam_box(clip_path, src_w, src_h)
                if face is None:
                    print(json.dumps({"error": f"No facecam found in {clip_name}. "
                                      "Pass --facecam x,y,w,h (fractions of the frame)."}))
                    sys.exit(1)
                facecam_crop = facecam_rect_from_face(face, src_w, src_h)
            game_crop, facecam_crop = compute_gaming_layout(
                src_w, src_h, facecam_crop, args.game_focus_x)
            crop = game_crop
            strategy = "gaming"
            face_positions = []
            crop_keyframes = []
        elif args.content_type == "irl":
            # IRL / third-person camera: static 4:5 crop, shown over a blurred
            # background with the captions in the band below the picture.
            fx = focus_list[min(clip_idx, len(focus_list) - 1)]
            cw = min(src_w, int(src_h * 4 / 5) // 2 * 2)
            cx = int(max(0, min(fx * src_w - cw / 2, src_w - cw)))
            crop = {"x": cx, "y": 0, "w": cw, "h": src_h}
            strategy = "blur-fill"
            face_positions = []
            crop_keyframes = []
        elif args.content_type == "talking-head":
            face_positions = compute_face_track(clip_path, src_w, src_h)
            crop = compute_crop_face_track(src_w, src_h, face_positions)
            strategy = "face-track"
            crop_keyframes = []
        elif args.content_type == "screen":
            if args.no_cursor_track:
                crop = compute_crop_screen_static(src_w, src_h, zoom=args.zoom)
                crop_keyframes = []
                strategy = "framed"
            else:
                # Detect cursor positions
                cursor_positions = detect_cursor_positions(
                    clip_path, src_w, src_h, sample_interval=0.5
                )
                # Smooth the trajectory
                cursor_positions = smooth_positions(cursor_positions, window=5)

                if len(cursor_positions) >= 2:
                    crop, crop_keyframes = compute_crop_screen_with_cursor(
                        src_w, src_h, cursor_positions, zoom=args.zoom
                    )
                    strategy = "cursor-track"
                else:
                    crop = compute_crop_screen_static(src_w, src_h, zoom=args.zoom)
                    crop_keyframes = []
                    strategy = "framed"
            face_positions = []
        else:  # podcast
            face_positions = compute_face_track(clip_path, src_w, src_h)
            crop = compute_crop_face_track(src_w, src_h, face_positions)
            strategy = "face-track"
            crop_keyframes = []

        entry = {
            "strategy": strategy,
            "source_resolution": f"{src_w}x{src_h}",
            "crop": crop,
            "output_resolution": "1080x1920",
            "face_positions": face_positions if args.content_type in ("talking-head", "podcast") else [],
            "duration": round(duration, 2),
        }

        if facecam_crop:
            entry["facecam"] = facecam_crop
            entry["cam_scale"] = args.cam_scale
        if crop_keyframes:
            entry["crop_keyframes"] = crop_keyframes

        results[clip_name] = entry

    elapsed = time.time() - start

    output = {
        "content_type": args.content_type,
        "clip_count": len(results),
        "computation_time_sec": round(elapsed, 2),
        "clips": results,
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)

    # Print summary
    summary = {
        "action": "compute_reframe",
        "content_type": args.content_type,
        "clips_processed": len(results),
        "computation_time_sec": round(elapsed, 2),
    }
    for name, data in results.items():
        s = {
            "strategy": data["strategy"],
            "crop": data["crop"],
        }
        if "crop_keyframes" in data:
            s["keyframe_count"] = len(data["crop_keyframes"])
        if data.get("face_positions"):
            s["faces_detected"] = len(data["face_positions"])
        summary[name] = s
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
