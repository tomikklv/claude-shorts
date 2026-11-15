#!/usr/bin/env python3
"""Find highlight candidates in long videos/streams from measurable signals.

Signals (each is turned into a robust z-score per second, i.e. "how unusual
is this second compared with the surrounding minutes"):

  audio   loudness (RMS dB) - shouting, loud reactions
  voice   share of high-frequency energy - laughter, screams, excited speech
  motion  frame-to-frame change in the picture - action, explosions, scene cuts
  chat    chat messages/sec plus hype tokens (LUL, KEKW, OMG, emoji, ...)
  spike   sudden jump in volume against the seconds before it
  speech  words per second from the transcript (--transcript); also used to
          down-rank loud stretches where nobody talks (music, ambience)
  reaction  reaction lines in the transcript ("oh my god", "what the", "no way")

Signals belong to families (sound, transcript, chat, picture). A candidate is
only passed on when at least --min-signals families (default 2) fire together.

Peaks where several signals agree rank highest. This does NOT predict
virality; it finds moments that are statistically unusual and that audiences
reacted to. Final selection is still made by Claude/the user.

Usage:
    python3 find_highlights.py INPUT_VIDEO --output highlights.json \
        [--chat chat.json] [--top 12] [--no-video] [--min-gap 45]

Chat file formats (auto-detected):
  * YouTube live chat replay: yt-dlp --write-subs --sub-langs live_chat
    (*.live_chat.json, one JSON object per line)
  * Twitch: TwitchDownloaderCLI chatdownload JSON ({"comments": [...]})
"""
import argparse
import json
import math
import re
import subprocess
import sys
import time

import numpy as np

SR = 16000
HYPE_RE = re.compile(
    r"(lul|kekw|omegalul|lmao|lmfao|lol|haha|omg|wtf|pog|poggers|clip|insane|"
    r"holy|no way|bruh|rip|dead|xd|"
    r"[\U0001F602\U0001F923\U0001F631\U0001F480\U0001F62D\U0001F525\U0001F44F])",
    re.IGNORECASE,
)


# W / L spam ("W", "WWWW", "L") only counts in upper case and as a whole token
WL_RE = re.compile(r"(?<![A-Za-z0-9])(?:W+|L+)(?![A-Za-z0-9])")
# Reaction lines in the transcript (matched at the start of a 3-word window)
REACTION_RE = re.compile(
    r"^(oh my god|oh my gosh|omg|what the|no way|oh shit|oh no|oh hell|holy|wtf|"
    r"bro what|bro no|hell no|let'?s go+|yo+ |wow|damn|crazy|insane|stop|run|"
    r"wait wait|no no)")
# Signals grouped into independent families: a moment needs at least
# --min-signals families firing together, otherwise it is not passed on.
FAMILY = {"audio": "sound", "voice": "sound", "spike": "sound",
          "speech": "transcript", "reaction": "transcript",
          "chat": "chat", "motion": "picture"}
FIRE_Z = 2.0


def probe_duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True)
    return float(r.stdout.strip())


def audio_features(path):
    """Per-second loudness (dB) and high-frequency energy ratio."""
    cmd = ["ffmpeg", "-v", "error", "-vn", "-i", path, "-ac", "1",
           "-ar", str(SR), "-f", "s16le", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    rms_db, hf_ratio = [], []
    freqs = np.fft.rfftfreq(SR, 1.0 / SR)
    hi_mask = freqs >= 1500
    buf = b""
    bytes_per_sec = SR * 2
    while True:
        chunk = p.stdout.read(bytes_per_sec * 60)
        if not chunk:
            break
        buf += chunk
        n = len(buf) // bytes_per_sec
        if n == 0:
            continue
        arr = np.frombuffer(buf[: n * bytes_per_sec], dtype=np.int16)
        arr = arr.astype(np.float32).reshape(n, SR) / 32768.0
        buf = buf[n * bytes_per_sec:]
        rms = np.sqrt((arr ** 2).mean(axis=1) + 1e-10)
        rms_db.extend(20 * np.log10(rms))
        spec = np.abs(np.fft.rfft(arr, axis=1)) ** 2
        hf_ratio.extend(spec[:, hi_mask].sum(axis=1) / (spec.sum(axis=1) + 1e-10))
    p.wait()
    return np.array(rms_db), np.array(hf_ratio)


def motion_features(path, fps=2, w=64, h=36):
    """Per-second mean absolute frame difference on a tiny grayscale video."""
    cmd = ["ffmpeg", "-v", "error", "-an", "-i", path, "-vf",
           f"fps={fps},scale={w}:{h},format=gray", "-f", "rawvideo", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    size = w * h
    prev = None
    diffs = []
    while True:
        raw = p.stdout.read(size)
        if len(raw) < size:
            break
        cur = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
        diffs.append(0.0 if prev is None else float(np.abs(cur - prev).mean()))
        prev = cur
    p.wait()
    diffs = np.array(diffs)
    n = len(diffs) // fps
    if n == 0:
        return np.array([])
    return diffs[: n * fps].reshape(n, fps).mean(axis=1)


def load_chat(path, n_seconds):
    """Return per-second chat activity (messages + weighted hype tokens)."""
    msgs = np.zeros(n_seconds)
    hype = np.zeros(n_seconds)

    def add(t, text):
        i = int(t)
        if 0 <= i < n_seconds:
            msgs[i] += 1
            hype[i] += len(HYPE_RE.findall(text or "")) + len(WL_RE.findall(text or ""))

    with open(path, encoding="utf-8") as f:
        raw = f.read()
    try:
        data = json.loads(raw)
        for c in data.get("comments", []):
            body = (c.get("message") or {}).get("body", "")
            add(c.get("content_offset_seconds", -1), body)
        return msgs + 2 * hype
    except (json.JSONDecodeError, AttributeError):
        pass

    for line in raw.splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        action = obj.get("replayChatItemAction")
        if not action:
            continue
        t = int(action.get("videoOffsetTimeMsec", -1)) / 1000.0
        for a in action.get("actions", []):
            item = (a.get("addChatItemAction") or {}).get("item", {})
            r = item.get("liveChatTextMessageRenderer")
            if not r:
                continue
            text = "".join(run.get("text", run.get("emoji", {}).get("emojiId", ""))
                           for run in r.get("message", {}).get("runs", []))
            add(t, text)
    return msgs + 2 * hype


def load_words(path, n_seconds):
    """Per second: words spoken, and reaction lines ("oh my god", "what the", ...)."""
    w = np.zeros(n_seconds)
    react = np.zeros(n_seconds)
    with open(path, encoding="utf-8") as f:
        caps = json.load(f).get("captions", [])
    clean = [re.sub(r"[^a-z' ]", "", c["text"].lower()).strip() for c in caps]
    for j, c in enumerate(caps):
        i = int(c["startMs"] / 1000)
        if 0 <= i < n_seconds:
            w[i] += 1
            if REACTION_RE.match(" ".join(clean[j:j + 3]) + " "):
                react[i] += 1
    return w, react


def volume_spikes(rms_db, lookback=6):
    """How far each second jumps above the average of the seconds before it."""
    x = np.asarray(rms_db, dtype=np.float64)
    out = np.zeros(len(x))
    for t in range(1, len(x)):
        out[t] = max(0.0, x[t] - x[max(0, t - lookback):t].mean())
    return out


def smooth(x, k):
    x = np.asarray(x, dtype=np.float64)
    k = min(int(k), len(x))
    if k <= 1:
        return x
    # edge-padded moving average, same length as the input
    left = k // 2
    padded = np.pad(x, (left, k - 1 - left), mode="edge")
    return np.convolve(padded, np.ones(k) / k, mode="valid")


def robust_z(x, baseline_win=300):
    """z-score against a local baseline so a loud stretch doesn't hide peaks."""
    x = np.asarray(x, dtype=np.float64)
    if len(x) == 0:
        return x
    base = smooth(x, baseline_win)
    resid = x - base
    mad = np.median(np.abs(resid - np.median(resid))) * 1.4826
    if mad < 1e-9:
        mad = resid.std() + 1e-9
    return resid / mad


def fit(x, n):
    x = np.asarray(x, dtype=np.float64)
    if len(x) >= n:
        return x[:n]
    return np.concatenate([x, np.zeros(n - len(x))])


def describe(name, z, raw):
    return {
        "audio": f"loud (+{z:.1f}σ, {raw:.0f} dB)",
        "voice": f"laughter/shouting-like sound (+{z:.1f}σ)",
        "motion": f"big change on screen (+{z:.1f}σ)",
        "chat": f"chat spike (+{z:.1f}σ, ~{raw:.0f} msgs+hype/s)",
        "speech": f"burst of talking (+{z:.1f}σ)",
        "spike": f"sudden jump in volume (+{z:.1f}σ)",
        "reaction": f"reaction lines in the transcript ({raw * 5:.0f} within 5 s)",
    }[name]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("--output", required=True)
    ap.add_argument("--chat", help="chat replay file (YouTube live_chat.json or Twitch JSON)")
    ap.add_argument("--transcript", help="transcript.json from transcribe.py: adds a speech "
                    "signal and down-ranks loud moments with no talking (music, ambience)")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--min-signals", type=int, default=2,
                    help="signal families (sound, transcript, chat, picture) that must fire "
                    "together; candidates below this are dropped (default 2)")
    ap.add_argument("--min-gap", type=float, default=45.0,
                    help="minimum seconds between two candidate peaks")
    ap.add_argument("--pre", type=float, default=20.0, help="seconds before the peak")
    ap.add_argument("--post", type=float, default=25.0, help="seconds after the peak")
    ap.add_argument("--no-video", action="store_true", help="skip motion analysis (faster)")
    ap.add_argument("--weights", default="audio=1,voice=0.7,spike=0.6,motion=0.5,chat=1.5,speech=0.6,reaction=0.8",
                    help="signal weights, e.g. audio=1,voice=0.7,motion=0.5,chat=1.5")
    args = ap.parse_args()

    t0 = time.time()
    duration = probe_duration(args.input)
    n = int(math.ceil(duration))
    weights = {k: float(v) for k, v in (p.split("=") for p in args.weights.split(","))}

    raw, z = {}, {}
    print(json.dumps({"action": "audio", "status": "starting"}), file=sys.stderr)
    rms_db, hf = audio_features(args.input)
    raw["audio"], raw["voice"] = fit(rms_db, n), fit(hf, n)
    # Ignore silence when judging loudness
    raw["audio"] = np.where(raw["audio"] < -60, -60, raw["audio"])
    raw["spike"] = volume_spikes(raw["audio"])

    if not args.no_video:
        print(json.dumps({"action": "motion", "status": "starting"}), file=sys.stderr)
        raw["motion"] = fit(motion_features(args.input), n)
    if args.chat:
        raw["chat"] = fit(load_chat(args.chat, n), n)

    words = None
    if args.transcript:
        words, react = load_words(args.transcript, n)
        raw["speech"] = smooth(words, 5)
        raw["reaction"] = smooth(react, 5)

    for k, v in raw.items():
        z[k] = robust_z(smooth(v, 3))
    if "reaction" in raw:
        # Reaction lines are rare, so a z-score explodes on a single hit. Count them
        # instead: one line in 5 s is nothing special, two or more is a reaction.
        z["reaction"] = np.clip(1.25 * raw["reaction"] * 5, 0, 6)

    used = [k for k in raw if weights.get(k, 0) > 0]
    wsum = sum(weights[k] for k in used)
    combined = np.zeros(n)
    agree = np.zeros(n)
    for k in used:
        zc = np.clip(z[k], 0, 6)
        combined += weights[k] * zc
        agree += (z[k] > 2.0)
    combined /= wsum
    # reward moments where several independent signals fire together
    combined += 0.25 * np.maximum(agree - 1, 0)
    combined = smooth(combined, 5)
    if words is not None:
        # words/sec over the surrounding ~30 s; below ~1 word/s the moment is
        # probably music or ambience, not a reaction or a line worth clipping
        density = smooth(words, 30)
        combined *= 0.35 + 0.65 * np.clip(density / 1.0, 0, 1)

    work = combined.copy()
    candidates, dropped, dropped_list = [], 0, []
    gap = int(args.min_gap)
    edge = int(args.pre)
    work[:edge] = 0
    work[max(0, n - int(args.post)):] = 0
    while len(candidates) < args.top:
        peak = int(np.argmax(work))
        if work[peak] <= 0.3:
            break
        score = float(work[peak])
        work[max(0, peak - gap): peak + gap] = 0
        # a line and the reaction to it are often several seconds apart
        lo, hi = max(0, peak - 8), min(n, peak + 13)
        signals, reasons, families = {}, [], set()
        for k in used:
            i = lo + int(np.argmax(z[k][lo:hi]))
            signals[k] = round(float(z[k][i]), 2)
            if z[k][i] > 1.5:
                reasons.append(describe(k, z[k][i], raw[k][i]))
            if z[k][i] > FIRE_Z:
                families.add(FAMILY.get(k, k))
        if len(families) < args.min_signals:
            dropped += 1  # a single signal is not enough evidence of a moment
            dropped_list.append({"peak": peak, "score": round(score, 2),
                                 "signal_families": sorted(families)})
            continue
        if words is not None:
            nwords = int(words[max(0, peak - int(args.pre)): peak + int(args.post)].sum())
            if nwords < 15:
                reasons.append(f"little speech ({nwords} words) - check it is not just music")
        start = max(0.0, peak - args.pre)
        end = min(duration, peak + args.post)
        candidates.append({
            "rank": len(candidates) + 1,
            "peak": peak,
            "start": round(start, 1),
            "end": round(end, 1),
            "score": round(score, 2),
            "signal_families": sorted(families),
            "signals": signals,
            "reasons": reasons,
        })

    out = {
        "source": args.input,
        "duration": round(duration, 1),
        "signals_used": used,
        "min_signal_families": args.min_signals,
        "dropped_below_min_signals": dropped_list,
        "note": ("Candidates are statistically unusual moments, not a virality "
                 "prediction. Review each one before rendering."),
        "candidates": candidates,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(json.dumps({
        "action": "find_highlights",
        "signals_used": used,
        "candidates": len(candidates),
        "dropped_below_min_signals": dropped,
        "time_sec": round(time.time() - t0, 1),
        "output": args.output,
    }, indent=2))


if __name__ == "__main__":
    main()
