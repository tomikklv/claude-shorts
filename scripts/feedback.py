#!/usr/bin/env python3
"""Remember what the user approved, rejected and criticised, so the next
selection starts from their taste instead of from zero.

Two files in ~/.config/claude-shorts/learnings/ (never overwritten by install):

  history.jsonl    append-only log: what was offered, what was picked, what the
                   user said about the result
  PREFERENCES.md   short, curated list of standing preferences. Claude reads it
                   before choosing moments/layout and rewrites it when the log
                   shows a new pattern (keep it under ~40 lines, each rule with
                   its evidence)

Usage:
    python3 feedback.py show [--last 5]
    python3 feedback.py log --source "URL or title" --offered offered.json \
        [--approved 1,3] [--rejected 2] [--note "captions covered the face"]

offered.json: list of {"id", "start", "end", "summary", "signals"} (or the
review.json written by review_candidates.py).
"""
import argparse
import datetime
import json
import os
import sys

DIR = os.path.join(os.path.expanduser(os.environ.get("SHORTS_CONFIG", "~/.config/claude-shorts")),
                   "learnings")
HISTORY = os.path.join(DIR, "history.jsonl")
PREFS = os.path.join(DIR, "PREFERENCES.md")
PREFS_HEADER = """# Shorts preferences

Standing preferences learned from feedback. Read before choosing moments and
layout; update when history.jsonl shows a new pattern. One rule per line, with evidence.

"""


def ids(text):
    return [int(v) for v in text.split(",") if v.strip()] if text else []


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show")
    s.add_argument("--last", type=int, default=5)
    l = sub.add_parser("log")
    l.add_argument("--source", required=True)
    l.add_argument("--offered", help="JSON file with the offered candidates")
    l.add_argument("--approved", default="")
    l.add_argument("--rejected", default="")
    l.add_argument("--note", default="")
    l.add_argument("--preset", default="")
    l.add_argument("--streamer", default="")
    args = ap.parse_args()

    os.makedirs(DIR, exist_ok=True)
    if not os.path.exists(PREFS):
        with open(PREFS, "w", encoding="utf-8") as f:
            f.write(PREFS_HEADER)

    if args.cmd == "show":
        with open(PREFS, encoding="utf-8") as f:
            prefs = f.read()
        records = []
        if os.path.exists(HISTORY):
            with open(HISTORY, encoding="utf-8") as f:
                records = [json.loads(x) for x in f if x.strip()]
        print(json.dumps({"preferences_file": PREFS, "preferences": prefs,
                          "history_file": HISTORY, "runs_logged": len(records),
                          "recent": records[-args.last:]}, indent=2, ensure_ascii=False))
        return

    offered = []
    if args.offered:
        with open(args.offered, encoding="utf-8") as f:
            data = json.load(f)
        data = data.get("candidates") or data.get("segments") if isinstance(data, dict) else data
        for c in data or []:
            offered.append({
                "id": c.get("id") or c.get("rank"), "start": c.get("start"), "end": c.get("end"),
                "summary": c.get("summary") or (c.get("clip_text") or "")[:160],
                "signals": c.get("signals") or c.get("reasons"),
            })
    rec = {"date": datetime.date.today().isoformat(), "source": args.source,
           "preset": args.preset or None, "streamer": args.streamer or None,
           "offered": offered, "approved": ids(args.approved), "rejected": ids(args.rejected),
           "note": args.note or None}
    with open(HISTORY, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps({"action": "feedback_logged", "history_file": HISTORY,
                      "offered": len(offered), "approved": rec["approved"],
                      "rejected": rec["rejected"]}, indent=2))


if __name__ == "__main__":
    main()
