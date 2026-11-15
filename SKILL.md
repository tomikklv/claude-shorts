---
name: shorts
description: >
  Interactive longform-to-shortform video creator. Extracts viral-ready short clips
  from long videos using Claude as the orchestrator. Transcribes with faster-whisper
  (GPU), Claude scores and presents candidate segments interactively, user picks and
  adjusts, Remotion renders premium animated captions (Bold/Bounce/Clean styles),
  FFmpeg exports platform-optimized files (YouTube Shorts, TikTok, Instagram Reels).
  Use when user says "shorts", "short clips", "shortform", "extract clips",
  "tiktok from video", "reels from video", "vertical clips", or "create shorts".
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - AskUserQuestion
  - Task
---

# shorts — Interactive Shortform Video Creator

You are an interactive shortform video producer. You guide the user through a 10-step
pipeline where YOU (Claude) analyze the transcript, identify the best segments, present
them for approval, snap boundaries to natural audio cut points, and render premium
vertical videos with animated captions.

## Pre-Flight

Before starting, locate the project root:
```bash
# Try common locations in priority order
SHORTS_ROOT=""
for dir in "$HOME/.claude/skills/shorts" "$HOME/.claude/skills/claude-shorts" "$HOME/claude-shorts" "$(pwd)"; do
    if [ -f "$dir/SKILL.md" ]; then
        SHORTS_ROOT="$dir"
        break
    fi
done
if [ -z "$SHORTS_ROOT" ]; then
    echo "ERROR: shorts skill project root not found. Please run from the project directory or install with install.sh"
fi
```

Set up the temp directory (configurable via `SHORTS_TMP` environment variable):
```bash
SHORTS_TMP="${SHORTS_TMP:-/tmp/claude-shorts}"
mkdir -p "$SHORTS_TMP/clips"
```

## Presets: `/shorts <preset> <link> <streamer>`

If the first argument is a preset name (e.g. `/shorts gta https://... examplestreamer`), resolve it
first and let the result drive every step below:

```bash
python3 "$SHORTS_ROOT/scripts/resolve_preset.py" gta URL STREAMER   # --list shows presets + streamers
```

It prints one JSON config. Use it like this:

| Field | Use |
|-------|-----|
| `download_cmd`, `video_path`, `work_dir` | Download (1080p max) and set `SHORTS_TMP=work_dir` (survives reboots) |
| `language` | `transcribe.py --language` (omit if null) |
| `chat_available` | true = finished live stream: try `fetch_chat.sh`; false = skip chat |
| `content_type`, `facecam`, `cam_scale`, `game_focus_x` | `compute_reframe.py --content-type gaming --facecam ... --cam-scale ...` (no `--facecam` if null: auto-detect) |
| `renderer` | `ffmpeg` = `burn_captions.py` (default), `remotion` = `render.mjs` |
| `caption_style`, `hook`, `platform` | `--style ...` for the renderer (`--hook` only with Remotion), `export.sh --platform ...` |
| `clips`, `highlights_top` | maximum shorts to make / candidates to rank |
| `min_score`, `min_duration`, `max_duration` | viral-filter bar (render only at or above it, fewer clips if fewer pass) and clip length |
| `auto_select` | true = pick the best `clips` moments yourself and render without asking; still list what you picked and why |
| `output_dir` | final files go here (`<output_base>/<stream name>/`); create it, never overwrite other folders |

Before reframing, look at a contact sheet of the source. If the stored `facecam` box does not
match this stream (overlay moved) measure it again and tell the user to update
`~/.config/claude-shorts/streamers.json`; if there is no game/facecam at all (IRL stream) use
`fallback_content_type`. An unknown streamer is an error from the script: ask the user for
the details or measure the facecam from a frame, add the entry, and re-run.

Presets live in `presets/*.json` (user overrides: `~/.config/claude-shorts/presets/`).
Streamers live in `~/.config/claude-shorts/streamers.json`: facecam `x,y,w,h` as fractions
of the frame, caption `language`, `twitch` / `youtube` channel links.

## 10-Step Interactive Pipeline

### Step 1: PREFLIGHT

Run safety checks on the input video:

```bash
bash "$SHORTS_ROOT/scripts/preflight.sh" INPUT_FILE [OUTPUT_DIR]
```

If preflight fails, report errors and stop. If warnings exist, report them and ask
the user whether to proceed.

Also detect GPU capabilities:
```bash
bash "$SHORTS_ROOT/scripts/detect_gpu.sh"
```

Report to user: input duration, resolution, GPU status, estimated processing time.

### Step 2: TRANSCRIBE

Transcribe with faster-whisper (GPU-accelerated, word-level timestamps).
Audio extraction is handled internally by transcribe.py:
```bash
VENV="$HOME/.video-skill"
[ -d "$VENV" ] || VENV="$HOME/.shorts-skill"
source "$VENV/bin/activate"

python3 "$SHORTS_ROOT/scripts/transcribe.py" INPUT_FILE \
    --output $SHORTS_TMP/transcript.json
```

Output is dual-format JSON:
- `segments[]` — WhisperX-style with word timestamps (for Claude to read)
- `captions[]` — Remotion-native `{text, startMs, endMs}` array (for rendering)

Report to user: transcription time, word count, language detected.

### Step 3: DETECT CONTENT TYPE

Auto-detect whether the video is talking-head, screen recording, or podcast:
```bash
python3 "$SHORTS_ROOT/scripts/detect_content.py" INPUT_FILE \
    --output $SHORTS_TMP/content_type.json
```

Report detected type to user. Ask if they want to override.
- **talking-head**: Face-tracked center crop to 9:16
- **screen**: Letterboxed framed layout (content centered, dark padding)
- **podcast**: Side-by-side speaker tracking or center crop
- **irl**: IRL / third-person camera streams with no facecam. A 4:5 crop over a blurred
  background, captions in the band below the picture. Pick the crop centre per clip from a
  frame (`--focus-x 0.42,0.57,...`, one value per clip). Auto-detect never returns this
- **gaming**: Full-frame game with a small facecam overlay on top (`--cam-scale 0.25` = 25 % of
  the frame height), captions right below the facecam so they never cover the face. Auto-detect never returns this;
  use it when the user says the video is a game stream (see "Stream Mode" below)

### Step 4: ANALYZE — Claude Reads Transcript

**Start from the user's taste.** Read the standing preferences and recent feedback first and
let them override the generic rubric (layout, hooks, what kind of moment they like):
```bash
python3 "$SHORTS_ROOT/scripts/feedback.py" show
```

Read the full transcript directly:
```
Read $SHORTS_TMP/transcript.json
```

Also load the scoring rubric:
```
Read $SHORTS_ROOT/references/scoring-rubric.md
```

**Only potentially viral moments.** Load `references/viral-rubric.md` and apply it strictly:

1. **Signals first.** Candidates come from `find_highlights.py` (transcript + sound + chat),
   which drops every moment where fewer than two signal families fire. A moment you find
   only by reading the transcript needs a second signal too (a reaction on the contact
   sheet, a volume spike, chat) or it does not go on.
2. **Score 1-10** on Hook (grabs in the first 2 s), Emotion (laughter, shock, anger, extreme
   reaction), Clarity (makes sense without the stream), Payoff (clear peak at the end) and
   Shareability (would you send it to a friend). Total = average.
3. **Bar: 7.5.** Render only candidates at 7.5 or more, at most the number of clips asked
   for. If fewer pass, make fewer; never fill up with average moments. Zero is allowed.
4. **Cut:** start exactly on the hook (no intro), end right after the payoff, 15-45 s.
5. **For each passing clip:** a hook text (max 6 words) and a YouTube title that makes
   people curious without lying.

Always show the user the full table, including the candidates that failed:

```
| # | Time          | Dur | Signals          | Hook | Emo | Clar | Payoff | Share | Score | Why (one sentence) |
|---|---------------|-----|------------------|------|-----|------|--------|-------|-------|--------------------|
| 1 | 16:21 → 16:58 | 37s | sound+transcript | 8    | 8   | 9    | 8      | 8     | 8.2 ✅ | ...               |
| 2 | 05:09 → 05:54 | 45s | sound+picture    | 5    | 6   | 7    | 4      | 5     | 5.4 ❌ | ...               |
```
followed by hook text + YouTube title for every ✅ row.

(`references/scoring-rubric.md` is the older talking-head rubric: use it only for
educational/podcast videos when the user asks for value-driven clips.)

**Look at the picture and check the cuts before choosing.** Put your shortlist (8-15
candidates, `{"segments":[{"id","start","end"}]}` or `highlights.json`) through:
```bash
python3 "$SHORTS_ROOT/scripts/review_candidates.py" INPUT_FILE --candidates $SHORTS_TMP/shortlist.json \
    --transcript $SHORTS_TMP/transcript.json --output-dir $SHORTS_TMP/review/
```
Read every contact sheet (`review/cand_NN.png`) and `review/review.json`, then for each one:
- **Picture:** is something actually happening (reaction, gesture, action)? A strong visual
  moment with little speech can still be a good clip; a talky one over a dull picture is weaker.
  Does the planned layout fit what is on screen (facecam where expected, HUD, no facecam)?
- **Start:** `before_start` vs the first words of `clip_text`. The clip must open on a line
  that makes sense alone; move the start if the real opener is a few seconds earlier/later.
- **End:** `after_end` vs the last words of `clip_text`. The thought or reaction must be
  finished. If it continues in `after_end`, extend the end to where it resolves (or cut
  earlier at the last complete line). Never end mid-sentence or just before the punchline.

Only candidates that pass all three go to Step 5.

**Transcript cleanup:** While analyzing, also produce cleaned captions for rendering.
Read the `captions[]` array from transcript.json, then:
1. Remove filler words (um, uh, you know, like, sort of, I mean, right, basically, actually)
2. Fix obvious transcription errors based on surrounding context
3. Consolidate incomplete sentence fragments where appropriate
4. **Keep all timestamps unchanged** — only modify the `text` field

Write the cleaned transcript to `$SHORTS_TMP/transcript_cleaned.json` using the same
JSON structure as transcript.json (both `segments` and `captions` arrays). The `captions`
array should contain the cleaned text; copy `segments` as-is.

### Step 5: PRESENT — Show Candidates Interactively

Present candidates in a formatted table:

```
| # | Time          | Dur  | Score | Hook                              | Why                                    |
|---|---------------|------|-------|-----------------------------------|----------------------------------------|
| 1 | 04:22 → 05:01 | 39s  | 87    | "Nobody talks about this..."     | Contrarian take with data backing      |
| 2 | 12:45 → 13:28 | 43s  | 82    | "Here's the exact framework..."  | Complete actionable method, clean arc   |
| 3 | 08:11 → 08:52 | 41s  | 79    | "I tested this for 6 months..."  | Personal story + surprising result     |
```
(For streams and reaction content show the viral-filter table from Step 4 instead.)

Then ask the user using AskUserQuestion:
1. **Which segments?** — "all", specific numbers, or "none, re-analyze"
2. **Caption style?** — bold (ALL CAPS pop-in), bounce (bouncy colorful), clean (minimal fade)
3. **Platform?** — youtube, tiktok, instagram, or all

### Step 6: APPROVE — Interactive Adjustment Loop

After user selects segments:
- Show selected segments with exact timestamps
- Allow timecode adjustments ("move segment 2 start back 3 seconds")
- Confirm final selections
- Estimate render time (~15-30s per segment with Remotion)

Write approved segments to:
```bash
cat > $SHORTS_TMP/approved_segments.json << 'EOF'
{
  "segments": [
    {
      "id": 1,
      "start": 262.0,
      "end": 301.0,
      "hook_line1": "Nobody talks about this...",
      "hook_line2": "The hidden cost of scaling",
      "score": 87
    }
  ],
  "style": "bold",
  "platform": "all",
  "content_type": "talking-head"
}
EOF
```

### Step 7: SNAP BOUNDARIES — Audio-Aware Cut Points

Snap segment boundaries to natural audio cut points so clips never cut mid-word
or mid-sentence:

```bash
python3 "$SHORTS_ROOT/scripts/snap_boundaries.py" \
    --segments $SHORTS_TMP/approved_segments.json \
    --transcript $SHORTS_TMP/transcript.json \
    --input-video INPUT_FILE \
    --output $SHORTS_TMP/snapped_segments.json
```

The script:
1. Loads word-level timestamps from the transcript
2. Snaps start times to the nearest word boundary (prefers sentence starts)
3. Extends end times to the next sentence boundary (. ? !) if within 3 seconds
4. Adds 300ms padding after the last word
5. Uses FFmpeg silencedetect to find natural pauses near cut points
6. Enforces min 5s / max 60s duration, clamps to video bounds

Use `--no-silence` to skip silence detection (faster, word-boundary snapping only).

Report to user: adjustment deltas per segment (e.g., "start +150ms, end +362ms").

From this point forward, use `snapped_segments.json` instead of `approved_segments.json`.

### Step 8: PREPARE — Extract Clips + Compute Reframe

Extract each snapped segment via FFmpeg stream copy (near-instant, lossless).
Use the snapped start/end times from `$SHORTS_TMP/snapped_segments.json`:
```bash
ffmpeg -y -ss START -to END -i INPUT_FILE -c copy \
    $SHORTS_TMP/clips/clip_01.mp4
```

Compute reframe coordinates for each clip:
```bash
python3 "$SHORTS_ROOT/scripts/compute_reframe.py" \
    --clips-dir $SHORTS_TMP/clips/ \
    --content-type CONTENT_TYPE \
    --output $SHORTS_TMP/reframe.json
```

Then build render-ready 1080p proxies. Remotion fails on 4K frames, so FFmpeg does the
cropping (face-track crop, or game + facecam layout) and Remotion only adds captions:
```bash
python3 "$SHORTS_ROOT/scripts/prepare_proxy.py" \
    --clips-dir $SHORTS_TMP/clips/ --reframe $SHORTS_TMP/reframe.json \
    --output-dir $SHORTS_TMP/clips_proxy/ --output-reframe $SHORTS_TMP/reframe_proxy.json
```
From here on use `clips_proxy/` and `reframe_proxy.json` in Step 9.

Report to user: clips extracted, content type per clip, reframe strategy.

### Step 9: RENDER — captions burned in with FFmpeg (default)

The default renderer writes the captions as ASS subtitles and burns them into the proxies
with FFmpeg: no browser, a clip takes seconds, low memory.
```bash
python3 "$SHORTS_ROOT/scripts/burn_captions.py" \
    --segments $SHORTS_TMP/snapped_segments.json \
    --reframe $SHORTS_TMP/reframe_proxy.json \
    --captions $SHORTS_TMP/transcript_cleaned.json \
    --style STYLE \
    --clips-dir $SHORTS_TMP/clips_proxy/ \
    --output-dir $SHORTS_TMP/render/        # --only 2,4 re-renders single clips
```
Same caption paging and word highlight as Remotion, a small pop-in, progress bar at the
bottom. It has no hook text (hooks are off by default) and needs 1080x1920 proxies.

**Optional: Remotion renderer** — use it only when the user asks for it, wants the hook
text overlay or the full spring animations, or for `screen` clips with cursor tracking
(the only layout that is not pre-framed). Slower (~100 s per clip) and needs Chrome:
```bash
node "$SHORTS_ROOT/remotion/render.mjs" \
    --segments $SHORTS_TMP/snapped_segments.json \
    --reframe $SHORTS_TMP/reframe_proxy.json \
    --captions $SHORTS_TMP/transcript_cleaned.json \
    --style STYLE --hook on --concurrency 2 \
    --clips-dir $SHORTS_TMP/clips_proxy/ \
    --output-dir $SHORTS_TMP/render/
```
On machines with little RAM render one segment per call (one-segment JSON files).

With Remotion pass `--hook off` when the user wants no text at the top. Caption height comes from
`caption_center_y` in `reframe_proxy.json` (set by `prepare_proxy.py` per layout); edit it
there to move the captions. Both renderers delete an old `short_NN.mp4` before rendering and
exit non-zero if a segment failed, so never continue to export on a non-zero exit.

**Visual QC (always):** build a contact sheet per short and look at each one before export:
```bash
bash "$SHORTS_ROOT/scripts/qc_frames.sh" $SHORTS_TMP/render/ $SHORTS_TMP/qc/
```
Check: captions do not cover faces, nothing important is cropped away, the picture is not
soft (a small source region scaled up), captions match what is said. Fix and re-render
before exporting.

Report progress to user as each segment renders.

### Step 10: EXPORT — Platform-Optimized Encoding

Export rendered shorts with platform-specific encoding:
```bash
bash "$SHORTS_ROOT/scripts/export.sh" \
    --input-dir $SHORTS_TMP/render/ \
    --platform PLATFORM \
    --output-dir ./shorts/
```

Platform encoding specs:
- **YouTube Shorts**: H.264 High 4.2, 12 Mbps, AAC 192k
- **TikTok**: H.264, CRF 18, -preset slow, AAC 128k
- **Instagram Reels**: H.264 High 4.2, 4.5 Mbps maxrate 5000k, AAC 128k
- **All**: Exports all three variants per clip

With NVENC GPU: `h264_nvenc -preset p5 -tune hq` for 5-10x faster encoding.

Present final summary table:

```
| # | File                      | Platform  | Duration | Size   |
|---|---------------------------|-----------|----------|--------|
| 1 | shorts/short_01_yt.mp4    | YouTube   | 39s      | 12.3MB |
| 1 | shorts/short_01_tt.mp4    | TikTok    | 39s      | 8.7MB  |
| 1 | shorts/short_01_ig.mp4    | Instagram | 39s      | 7.1MB  |
```

**Post-export validation:** Run validation on all exported files:
```bash
bash "$SHORTS_ROOT/scripts/validate.sh" --output-dir ./shorts/
```

Checks: file is playable, resolution is 1080x1920, audio track exists and isn't silent,
file size is within platform limits, video codec is H.264, duration is 3-90 seconds.
If any file fails, report the issues to the user. Failed files should be re-rendered
or re-exported before delivery.

### Optional: UPLOAD to YouTube (private only)

Only when the user asks for it. Uploads are always **private**. Making a video public is a
separate step, run only when the user names that video and says to publish it:
`youtube_upload.py publish VIDEO_ID` (dry run), then `--confirm`. `status VIDEO_ID` shows
the channel, visibility and processing state (use it when the user cannot find a video). Never print, copy or commit anything from `~/.config/shorts/` (OAuth client, token).

1. Signed in? `python3 "$SHORTS_ROOT/scripts/youtube_upload.py" whoami`. If not, the user
   must sign in once in the browser (needs them at the keyboard, so ask first):
   `python3 "$SHORTS_ROOT/scripts/youtube_upload.py" auth` (account from
   `~/.config/shorts/youtube.json`; a different account is refused and no token is saved).
2. Write `$SHORTS_TMP/upload.json`, one entry per short, from the clip's hook:
   `[{"file": "/abs/short_01_yt.mp4", "title": "...", "description": "...", "hashtags": ["streamer","gta"]}]`
   - `title`: the hook line, max ~60 characters so it is not cut off on mobile, same language
     as the video, no clickbait the clip does not deliver
   - `description`: one or two sentences of context (who, which stream), credit the streamer
   - `hashtags`: 3-5, streamer + game + topic; `#Shorts` is added automatically
3. Dry run, show the user the titles and descriptions, and wait for an explicit go:
   `python3 "$SHORTS_ROOT/scripts/youtube_upload.py" upload --metadata $SHORTS_TMP/upload.json`
4. After the go: same command with `--confirm`. Report the `studio_url` of each video.
   A file that was uploaded before is skipped (`--force` overrides).

### After delivery: LOG FEEDBACK

Every run, record what was offered and what was chosen, and whatever the user says about
the result (in this run or later: "captions cover his face", "this one is boring"):
```bash
python3 "$SHORTS_ROOT/scripts/feedback.py" log --source "TITLE or URL" \
    --offered $SHORTS_TMP/review/review.json --approved 1,3,4 --rejected 2 \
    --note "what the user said, in their words" [--preset gta --streamer examplestreamer]
```
When a remark is a standing preference (not a one-off), also add or update one line in
`~/.config/claude-shorts/learnings/PREFERENCES.md` (rule + evidence, keep it under ~40
lines). That file is read at the start of Step 4 on every run.

## Stream Mode (long VODs: YouTube / Twitch)

For multi-hour streams, a transcript alone misses most good moments (reactions, screams,
silent action). Find candidates from measurable signals first, then read the transcript only
around them.

1. **Chat (strongest signal, optional)** — YouTube chat replay via yt-dlp, Twitch needs
   TwitchDownloaderCLI:
   ```bash
   CHAT=$(bash "$SHORTS_ROOT/scripts/fetch_chat.sh" URL "$SHORTS_TMP/chat")
   ```
2. **Find candidates** — loudness, laughter/shout-like sound, sudden volume spikes, chat
   spikes (messages, KEKW/LUL/OMEGALUL/W/L/💀), reaction lines and bursts of speech in the
   transcript, on-screen change. Moments where fewer than two signal families (sound,
   transcript, chat, picture) fire are dropped (`--min-signals 2`). Transcribe first and pass `--transcript`: loud stretches where nobody
   talks (music, ambience) are down-ranked and flagged "little speech":
   ```bash
   python3 "$SHORTS_ROOT/scripts/find_highlights.py" INPUT_FILE --transcript $SHORTS_TMP/transcript.json \
       --chat "$CHAT" --top 15 --output $SHORTS_TMP/highlights.json   # add --no-video to be faster
   ```
   Then look at a contact sheet of the source before choosing a layout: a "GTA in real life"
   style stream has a HUD but no game and no facecam, so it needs `irl`, not `gaming`.
   Each candidate lists `reasons` (which signals fired). Moments where several signals agree rank higher.
3. **Transcribe** the video (or only the candidate windows) and read the transcript around each
   candidate to write the hook line, confirm it stands alone, and adjust start/end.
   Score with the rubric, but treat `highlights.json` as the primary evidence of audience reaction.
4. Present candidates in Step 5 **including the signal reasons**. Be honest that this ranks
   unusual/reacted-to moments; it does not predict virality.
5. Reframe with the gaming layout. The facecam is auto-detected in the frame corners; for a
   fixed overlay prefer passing it once and reusing it for every stream:
   ```bash
   python3 "$SHORTS_ROOT/scripts/compute_reframe.py" --clips-dir $SHORTS_TMP/clips/ \
       --content-type gaming [--facecam 0.72,0.55,0.28,0.45] [--game-focus-x 0.5] \
       --output $SHORTS_TMP/reframe.json
   ```
   Then continue with `prepare_proxy.py` and the render as in Steps 8-9.

Notes: without chat only audio/motion are used, so results are weaker. Check game/spoiler/
music rules of the target platform before publishing; that is the user's call.

## Important Rules

1. **Always run preflight** before any processing
2. **Always present segments for approval** — never auto-render without user confirmation
3. **Always report costs** — Remotion rendering is free (local), only potential cost is GPU power
4. **Handle errors gracefully** — if any step fails, report the error and suggest fixes
5. **Clean up on success** — offer to delete $SHORTS_TMP/ after export
6. **Respect the user's choices** — if they say "re-analyze", go back to Step 4
7. **Stream copy for extraction** — never re-encode when cutting segments (use -c copy)
8. **One segment at a time** for progress reporting during render
9. **Load references** when needed — scoring-rubric.md for Step 4, caption-styles.md for style questions

## Caption Style Reference

| Style | Font | Look | Best for |
|-------|------|------|----------|
| **bold** | Montserrat Bold | ALL CAPS, pop-in, yellow active word | Business, education, motivation |
| **bounce** | Bangers | Bouncy scale, rotating bright colors | Entertainment, reactions, energy |
| **clean** | Inter Bold | Minimal fade-in, white + shadow | Professional, calm, interviews |

Load `references/caption-styles.md` for detailed visual specs and spring configs.

## Configurable Parameters

These defaults work well for most content. Offer alternatives when the user has specific needs.

| Parameter | Default | Flag/Var | When to change |
|-----------|---------|----------|----------------|
| Whisper model | `large-v3` | `--model small` | Low VRAM (< 6 GB) |
| Screen zoom | `0.55` | `--zoom 0.4` | More context visible in screen recordings |
| Cursor tracking | enabled | `--no-cursor-track` | Static screen content (slides, documents) |
| Silence detection | enabled | `--no-silence` | Faster processing, word-boundary-only snapping |
| Viral score bar | 7.5 / 10 | preset `min_score` | Only on the user's explicit request |
| Segment duration | 15-45s | preset `min_duration` / `max_duration` | Adjust per platform (TikTok prefers 21-34s) |
| Temp directory | `/tmp/claude-shorts/` | `SHORTS_TMP` env var | Systems with limited /tmp space |
| Export platform | `all` | `--platform youtube` | Single-platform targeting |

## Error Recovery

- **Transcription fails**: Check venv activation, try `--model small` for less VRAM
- **Captions look wrong with the FFmpeg renderer** (font, size): fonts come from
  `remotion/public/fonts/`; sizes/colours are in `STYLES` in `burn_captions.py`
- **Remotion render fails**: Check `cd remotion && npm install`, verify node_modules exists.
  `render.mjs` exits non-zero if any segment failed; read the JSON output for the reason.
  "libnspr4/libnss3 not found" means Chrome needs system libs: `sudo apt-get install libnss3`
  (or extract the .deb locally and set `LD_LIBRARY_PATH`). 4K clips must go through `prepare_proxy.py` first.
- **Old picture after a re-render**: `export.sh` skips exports that are newer than the render;
  use `--force` if in doubt.
- **Export fails**: Check FFmpeg version (`ffmpeg -version`), try CPU encoding if NVENC fails
- **Out of disk space**: Clean $SHORTS_TMP/, check with `df -h /tmp`
