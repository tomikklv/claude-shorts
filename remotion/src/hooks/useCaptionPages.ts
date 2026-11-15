import { useMemo } from "react";
import type { Caption } from "../types";

export interface CaptionToken {
  text: string;
  fromMs: number;
  toMs: number;
}

export interface CaptionPage {
  text: string;
  startMs: number;
  durationMs: number;
  tokens: CaptionToken[];
}

const MAX_WORDS = 3;
const MAX_CHARS = 18; // keeps a page on one line at 72px in a 1080px frame
const HOLD_MS = 700; // how long a page stays after its last word

/**
 * Groups word-level captions into short pages.
 *
 * A page ends at the end of a sentence (. ? !), at a pause longer than
 * `combineMs`, after MAX_WORDS words, or before it would exceed MAX_CHARS.
 * So a page never mixes the end of one sentence with the start of the next
 * and never wraps onto a second line.
 */
export const useCaptionPages = (
  captions: Caption[],
  combineMs: number = 800
): CaptionPage[] => {
  return useMemo(() => {
    if (!captions || captions.length === 0) return [];

    const groups: Caption[][] = [];
    let cur: Caption[] = [];
    let chars = 0;
    for (const c of captions) {
      const word = c.text.trim();
      if (!word) continue;
      const prev = cur[cur.length - 1];
      const mustBreak =
        prev !== undefined &&
        (/[.?!]$/.test(prev.text.trim()) ||
          c.startMs - prev.endMs > combineMs ||
          cur.length >= MAX_WORDS ||
          chars + 1 + word.length > MAX_CHARS);
      if (mustBreak) {
        groups.push(cur);
        cur = [];
        chars = 0;
      }
      cur.push(c);
      chars += (chars ? 1 : 0) + word.length;
    }
    if (cur.length) groups.push(cur);

    return groups.map((g, i) => {
      const startMs = g[0].startMs;
      const lastEnd = g[g.length - 1].endMs;
      const nextStart = i + 1 < groups.length ? groups[i + 1][0].startMs : Infinity;
      const endMs = Math.min(nextStart, lastEnd + HOLD_MS);
      return {
        text: g.map((c) => c.text.trim()).join(" "),
        startMs,
        durationMs: Math.max(endMs - startMs, 1),
        tokens: g.map((c) => ({ text: c.text, fromMs: c.startMs, toMs: c.endMs })),
      };
    });
  }, [captions, combineMs]);
};
