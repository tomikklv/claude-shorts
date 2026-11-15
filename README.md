# claude-shorts (modified fork)

A Claude Code skill that turns long videos and game streams into vertical shorts
(1080x1920) with word-highlighted captions.

## Original project and license

This is a modified copy of **claude-shorts** by **Daniel Agrici**:
https://github.com/AgriciDaniel/claude-shorts

The original is released under the MIT License (see `LICENSE`, which must stay
with every copy). All credit for the original pipeline goes to its author; the
changes below are not endorsed by him.

## What was added in this copy

- Stream mode: highlight detection from sound, transcript, chat and picture
  (`find_highlights.py`), with a two-signal rule and a 1-10 viral score
  (`references/viral-rubric.md`)
- Layouts for game streams (full-frame game + small facecam) and IRL streams
  (4:5 crop over a blurred background)
- Fast caption burn-in with FFmpeg/libass (`burn_captions.py`); Remotion stays
  as the optional renderer
- Candidate review sheets, visual QC, a feedback log, presets per streamer
  (`streamers.example.json`), cover images (`make_cover.py`)
- Optional private YouTube upload via the Data API (`youtube_upload.py`)

## Setup notes

- Needs FFmpeg, Python 3.10+ (`pip install -r requirements.txt`) and Node.js 18+
  (`cd remotion && npm install`).
- `SKILL.md` mentions `setup.sh`, `install.sh` and `presets/`, which are not part
  of this copy. Copy the skill to `~/.claude/skills/shorts/` by hand (scripts/,
  references/, remotion/, SKILL.md).
- YouTube upload needs your own OAuth client (Google Cloud, "Desktop app") saved
  as `~/.config/shorts/client_secret.json`. It is never part of the repository.
- Copy `streamers.example.json` to `~/.config/claude-shorts/streamers.json` and
  fill in your streamers.
