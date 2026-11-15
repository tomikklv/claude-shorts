#!/usr/bin/env python3
"""Cover image (thumbnail) for a short: 1080x1920 JPG.

Two steps, because choosing the frame is a judgement call:

  1. candidates  samples the raw clip, scores frames (sharp face, big face, loud
                 moment = likely the strongest reaction) and writes a contact
                 sheet of the best ones as 9:16 crops. Look at it and pick.
  2. render      cuts that frame to 9:16 around the face, lifts contrast and
                 saturation a little, and writes the text: max 4 words, bold
                 white with a black outline, one word in yellow, in the middle
                 of the frame (top and bottom stay free for the YouTube UI).

Usage:
    python3 make_cover.py candidates CLIP --output-dir covers/ [--count 6]
    python3 make_cover.py render CLIP --time 12.4 --text "20K FOR WHAT?" \
        --highlight 20K --output short_05_cover.jpg [--face-x 0.55] [--zoom 1.2]

CLIP is the raw landscape clip (clips/clip_NN.mp4), not the captioned short.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT = os.path.join(ROOT, "remotion", "public", "fonts", "Montserrat-Bold.ttf")

W, H = 1080, 1920
TEXT_CENTER_Y = 1090      # a little below the middle, under the face
FACE_TARGET_Y = 0.36      # where the face centre should sit in the cover
TEXT_MAX_W = 960
MAX_WORDS = 4
YELLOW, WHITE, BLACK = (255, 215, 0), (255, 255, 255), (0, 0, 0)


def fail(msg):
    print(json.dumps({"error": msg}))
    sys.exit(1)


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height:format=duration", "-of", "json", path],
                       capture_output=True, text=True)
    d = json.loads(r.stdout)
    return d["streams"][0]["width"], d["streams"][0]["height"], float(d["format"]["duration"])


def loudness(path, step):
    """RMS level per `step` seconds, scaled 0-1 within the clip."""
    sr = 16000
    r = subprocess.run(["ffmpeg", "-v", "error", "-vn", "-i", path, "-ac", "1", "-ar", str(sr),
                        "-f", "s16le", "-"], capture_output=True)
    a = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    n = int(sr * step)
    if len(a) < n:
        return np.zeros(1)
    a = a[: len(a) // n * n].reshape(-1, n)
    db = 20 * np.log10(np.sqrt((a ** 2).mean(axis=1)) + 1e-6)
    lo, hi = np.percentile(db, 20), np.percentile(db, 98)
    return np.clip((db - lo) / max(hi - lo, 1e-6), 0, 1)


def crop_box(sw, sh, face, zoom, face_x=None):
    """9:16 crop of the source with the face in the upper middle."""
    ch = sh / max(zoom, 1.0)
    cw = ch * W / H
    if face:
        fx, fy, fw, fh = face
        cx, cy = (fx + fw / 2) * sw, (fy + fh / 2) * sh
    else:
        cx, cy = sw / 2, sh * FACE_TARGET_Y
    if face_x is not None:
        cx = face_x * sw
    x = min(max(cx - cw / 2, 0), sw - cw)
    y = min(max(cy - ch * FACE_TARGET_Y, 0), sh - ch)
    return int(x) // 2 * 2, int(y) // 2 * 2, int(cw) // 2 * 2, int(ch) // 2 * 2


def grab(clip, t, box, out, enhance=True, size=(W, H)):
    x, y, w, h = box
    vf = f"crop={w}:{h}:{x}:{y},scale={size[0]}:{size[1]}:flags=lanczos"
    if enhance:
        vf += ",eq=contrast=1.08:saturation=1.2,unsharp=5:5:0.7:5:5:0.0"
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", clip,
                        "-frames:v", "1", "-vf", vf, out], capture_output=True, text=True)
    if r.returncode != 0:
        fail(r.stderr.strip()[-300:])


def cmd_candidates(args):
    import cv2
    from face_detector import FaceDetector
    from PIL import Image, ImageDraw, ImageFont

    sw, sh, dur = probe(args.clip)
    step = 0.2
    loud = loudness(args.clip, step)
    det = FaceDetector(0.5)
    cap = cv2.VideoCapture(args.clip)
    rows = []
    t = 0.3
    while t < dur - 0.3:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = det.detect(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        best = None
        for (fx, fy, fw, fh) in faces:
            x0, y0 = max(int(fx * sw), 0), max(int(fy * sh), 0)
            roi = gray[y0:int((fy + fh) * sh), x0:int((fx + fw) * sw)]
            if roi.size < 400:
                continue
            sharp = cv2.Laplacian(cv2.resize(roi, (128, 128)), cv2.CV_64F).var()
            if best is None or fh * sharp > best[1] * best[2]:
                best = ((fx, fy, fw, fh), fh, sharp)
        whole = cv2.Laplacian(cv2.resize(gray, (480, 270)), cv2.CV_64F).var()
        li = min(int(t / step), len(loud) - 1)
        rows.append({"t": round(t, 2), "face": best[0] if best else None,
                     "face_size": best[1] if best else 0.0,
                     "sharp": best[2] if best else whole * 0.25, "loud": float(loud[li])})
        t += step
    cap.release()
    det.close()
    if not rows:
        fail("no frames read from the clip")

    def norm(key):
        v = np.array([r[key] for r in rows], dtype=np.float64)
        return (v - v.min()) / max(v.max() - v.min(), 1e-9)
    ns, nf = norm("sharp"), norm("face_size")
    for i, r in enumerate(rows):
        r["score"] = round(float(0.45 * ns[i] + 0.25 * nf[i] + 0.30 * r["loud"]
                                 - (0.35 if r["face"] is None else 0)), 3)
    picks = []
    for r in sorted(rows, key=lambda r: -r["score"]):
        if all(abs(r["t"] - p["t"]) >= 1.5 for p in picks):
            picks.append(r)
        if len(picks) >= args.count:
            break

    os.makedirs(args.output_dir, exist_ok=True)
    tw, th = 360, 640
    sheet = Image.new("RGB", (tw * len(picks), th), BLACK)
    font = ImageFont.truetype(FONT, 34)
    for i, p in enumerate(picks):
        tmp = os.path.join(args.output_dir, f"_cand_{i + 1}.png")
        grab(args.clip, p["t"], crop_box(sw, sh, p["face"], args.zoom), tmp, enhance=False, size=(tw, th))
        sheet.paste(Image.open(tmp), (tw * i, 0))
        os.remove(tmp)
        ImageDraw.Draw(sheet).text((tw * i + 12, 10), f"{i + 1}  t={p['t']}s", font=font,
                                   fill=WHITE, stroke_width=3, stroke_fill=BLACK)
        p["pick"] = i + 1
    base = os.path.splitext(os.path.basename(args.clip))[0]
    out = os.path.join(args.output_dir, f"{base}_cover_candidates.jpg")
    sheet.save(out, quality=88)
    print(json.dumps({"action": "cover_candidates", "clip": args.clip, "sheet": out,
                      "frames_scored": len(rows), "faces_found": sum(r["face"] is not None for r in rows),
                      "candidates": [{k: p[k] for k in ("pick", "t", "score", "face", "loud")} for p in picks]},
                     indent=2))


def layout_text(words, highlight):
    from PIL import ImageFont
    words = [w.upper() for w in words]
    hl = highlight.upper() if highlight else None
    if hl is None or hl not in words:
        digits = [w for w in words if any(c.isdigit() for c in w)]
        hl = digits[0] if digits else max(words, key=len)
    one = [words]
    splits = [one] + [[words[:i], words[i:]] for i in range(1, len(words))]
    best = None
    for lines in splits:
        size = 230
        while size > 60:
            font = ImageFont.truetype(FONT, size)
            if max(font.getlength(" ".join(l)) for l in lines) <= TEXT_MAX_W:
                break
            size -= 4
        # prefer the biggest text; two lines only when they are clearly bigger
        key = size * (1.0 if len(lines) == 1 else 0.9)
        if best is None or key > best[0]:
            best = (key, lines, size)
    return best[1], best[2], hl


def cmd_render(args):
    from PIL import Image, ImageDraw, ImageFont
    words = args.text.split()
    if not 1 <= len(words) <= MAX_WORDS:
        fail(f"cover text must be 1-{MAX_WORDS} words, got {len(words)}")
    sw, sh, dur = probe(args.clip)
    if not 0 <= args.time < dur:
        fail(f"--time must be inside the clip (0-{dur:.1f}s)")

    face = None
    if args.face_x is None:
        import cv2
        from face_detector import FaceDetector
        cap = cv2.VideoCapture(args.clip)
        cap.set(cv2.CAP_PROP_POS_MSEC, args.time * 1000)
        ok, frame = cap.read()
        cap.release()
        if ok:
            det = FaceDetector(0.5)
            faces = det.detect(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            det.close()
            if faces:
                face = max(faces, key=lambda f: f[3])
    box = crop_box(sw, sh, face, args.zoom, args.face_x)

    with tempfile.TemporaryDirectory() as tmp:
        png = os.path.join(tmp, "frame.png")
        grab(args.clip, args.time, box, png)
        img = Image.open(png).convert("RGB")

    lines, size, hl = layout_text(words, args.highlight)
    font = ImageFont.truetype(FONT, size)
    draw = ImageDraw.Draw(img)
    stroke = max(8, size // 11)
    line_h = int(size * 1.08)
    y = args.text_y - line_h * len(lines) // 2
    space = font.getlength(" ")
    for line in lines:
        x = (W - font.getlength(" ".join(line))) / 2
        for w in line:
            draw.text((x + 5, y + 6), w, font=font, fill=BLACK, stroke_width=stroke, stroke_fill=BLACK)
            draw.text((x, y), w, font=font, fill=YELLOW if w == hl else WHITE,
                      stroke_width=stroke, stroke_fill=BLACK)
            x += font.getlength(w) + space
        y += line_h
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    img.save(args.output, quality=93, subsampling=0)
    print(json.dumps({"action": "cover", "output": args.output, "time": args.time,
                      "text": [" ".join(l) for l in lines], "highlight": hl, "font_size": size,
                      "face_found": face is not None, "crop": dict(zip("xywh", box)),
                      "upscale": round(H / box[3], 2)}, indent=2))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("candidates")
    c.add_argument("clip")
    c.add_argument("--output-dir", required=True)
    c.add_argument("--count", type=int, default=6)
    c.add_argument("--zoom", type=float, default=1.15)
    r = sub.add_parser("render")
    r.add_argument("clip")
    r.add_argument("--time", type=float, required=True, help="seconds into the clip")
    r.add_argument("--text", required=True, help=f"max {MAX_WORDS} words")
    r.add_argument("--highlight", help="the word shown in yellow")
    r.add_argument("--output", required=True)
    r.add_argument("--face-x", type=float, help="horizontal centre of the crop, 0-1 (overrides face detection)")
    r.add_argument("--zoom", type=float, default=1.15, help="1 = full frame height, more = closer")
    r.add_argument("--text-y", type=int, default=TEXT_CENTER_Y)
    args = ap.parse_args()
    {"candidates": cmd_candidates, "render": cmd_render}[args.cmd](args)


if __name__ == "__main__":
    main()
