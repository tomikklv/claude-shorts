# Viral filter: scoring candidates 1-10

Score every candidate that survived `find_highlights.py` (at least two signal families)
on five criteria. Total = plain average, one decimal. Only 7.5+ gets rendered.

| Criterion | Question | 9-10 | 7-8 | 5-6 | 1-4 |
|-----------|----------|------|-----|-----|-----|
| Hook | Does it grab in the first 2 seconds? | Opens on a shout, a wild line or something visibly happening | Clear, interesting first line | Normal talking, gets going after 3-5 s | Dead air, setup, "so, um" |
| Emotion | Laughter, shock, anger, an extreme reaction? | Screaming, losing it, visible shock | Clear laugh or surprise | Mild amusement | Flat |
| Clarity | Understandable with no stream context? | A stranger gets it instantly | One second of orientation needed | Needs to know the stream/bit | Inside joke, refers to earlier events |
| Payoff | A clear peak right at the end? | Punchline, twist or reaction lands and the clip ends | Ends on a finished thought | Fizzles out | No ending, cut mid-action |
| Shareability | Would someone send it to a friend? | "You have to see this" | Fun, would like it | Fine, would scroll on | No reason to share |

Rules:
- Be strict. A clip that is merely pleasant is a 6. Most candidates from an average stream
  should fail; that is the point.
- Judge from the transcript AND the contact sheet AND the signals. Loud music, crowd noise
  or a scene change alone is not a moment.
- If you cannot tell what makes it funny/shocking from text and frames (tone of voice,
  timing), say so in the reason and do not score Emotion or Payoff above 7 on a guess.
- Fewer than 5 candidates at 7.5+? Make fewer. Two strong shorts beat five average ones.
  Zero is a valid result: say so and show the table.

Cut (after scoring, before rendering):
- Start on the hook itself: the first word of the line/reaction that grabs. No intro, no
  greeting, no "okay so". If the hook needs one short setup line, keep only that line.
- End right after the payoff: last word of the punchline or the peak of the reaction plus
  about half a second. Do not keep the wind-down.
- 15-45 seconds. Shorter than 15 s: not a clip. Longer than 45 s: trim or drop it.

For each clip that passes also write:
- Hook text: max 6 words, in the language of the video. It names the tension, not the answer.
- YouTube title: makes people curious and is true. Nothing that does not happen in the clip,
  no fake quotes, no "GONE WRONG". ~60 characters.
