#!/usr/bin/env bash
# Download the chat replay of a past stream (VOD) for find_highlights.py --chat.
#
# Usage: bash fetch_chat.sh URL OUTPUT_DIR
#
#   YouTube: uses yt-dlp (live_chat subtitles). Works for finished live streams
#            whose chat replay is enabled.
#   Twitch:  needs TwitchDownloaderCLI (https://github.com/lay295/TwitchDownloader)
#            because yt-dlp cannot fetch Twitch chat.
#
# Prints the path of the chat file on success.
set -euo pipefail

URL="${1:?usage: fetch_chat.sh URL OUTPUT_DIR}"
OUT="${2:?usage: fetch_chat.sh URL OUTPUT_DIR}"
mkdir -p "$OUT"

case "$URL" in
  *youtube.com*|*youtu.be*)
    yt-dlp --skip-download --write-subs --sub-langs live_chat \
      -o "$OUT/%(id)s.%(ext)s" "$URL" >&2
    FILE=$(ls -1 "$OUT"/*.live_chat.json 2>/dev/null | head -1 || true)
    if [ -z "$FILE" ]; then
      echo "ERROR: no chat replay found (stream may not have chat replay enabled)" >&2
      exit 1
    fi
    echo "$FILE"
    ;;
  *twitch.tv/videos/*)
    if ! command -v TwitchDownloaderCLI >/dev/null 2>&1; then
      echo "ERROR: TwitchDownloaderCLI not installed (needed for Twitch chat)." >&2
      echo "       https://github.com/lay295/TwitchDownloader/releases" >&2
      exit 1
    fi
    ID=$(echo "$URL" | sed -E 's#.*/videos/([0-9]+).*#\1#')
    TwitchDownloaderCLI chatdownload --id "$ID" -o "$OUT/$ID.chat.json" >&2
    echo "$OUT/$ID.chat.json"
    ;;
  *)
    echo "ERROR: unsupported URL (expected YouTube or Twitch VOD)" >&2
    exit 1
    ;;
esac
