/* A practice run: the same exercise three times, with the support taken
 * away one layer at a time.
 *
 *   1  Listen       the tutor plays it, with the click
 *   2  Play along   the piano stops; you play, with the click
 *   3  On your own  the click stops too; you keep the rhythm
 *
 * Every pass is preceded by a bar of clicks, so the change is unmistakable
 * and each pass starts together. That also makes all three passes exactly
 * the same length, which is what lets one buffer carry the whole run and
 * `passAt` say where in it we are. Passes 2 and 3 are scored separately:
 * comparing them is the point of the drill — pass 2 says whether the notes
 * are known, pass 3 whether the rhythm is. */

export const PASSES = [
  { id: "listen", label: "Listen", scored: false, piano: true, metronome: true,
    hint: "Watch and listen — the tutor plays it." },
  { id: "along", label: "Play along", scored: true, piano: false, metronome: true,
    hint: "Your turn: play it with the click." },
  { id: "alone", label: "On your own", scored: true, piano: false, metronome: false,
    hint: "After the count-in the click stops — keep the rhythm yourself." },
];

/* Which pass a run time falls in, and the time within that pass. */
export function passAt(seconds, passSeconds) {
  if (!(passSeconds > 0)) return { index: 0, offset: 0 };
  const index = Math.floor(seconds / passSeconds);
  return { index: Math.min(PASSES.length - 1, Math.max(0, index)), offset: seconds - index * passSeconds };
}

/* One buffer holding all three passes, built from two renders:
 *   full   piano + metronome        → pass 1
 *   clicks metronome only           → pass 2, and pass 3 once the section
 *                                     after the count-in is silenced.
 * The count-in of pass 3 is kept: the student needs the tempo before the
 * click goes away.
 *
 * `passFrames` is the count-in plus the section — a whole number of beats.
 * A render is longer than that: it ends with a tail of silence so the last
 * note's release can finish. The renders are therefore *mixed in* at their
 * pass positions rather than laid end to end, so a release rings on over
 * the next pass's count-in and the beat never stops. */
export function buildRunSamples(full, clicks, countInFrames, passFrames) {
  const alone = clicks.slice();
  alone.fill(0, Math.min(countInFrames, alone.length));
  const sources = [full, clicks, alone];
  const tail = Math.max(0, Math.max(full.length, clicks.length) - passFrames);
  const out = new Float32Array(passFrames * PASSES.length + tail);
  sources.forEach((src, pass) => {
    const at = pass * passFrames;
    const n = Math.min(src.length, out.length - at);
    for (let i = 0; i < n; i++) out[at + i] += src[i];
  });
  return { samples: out, passFrames };
}

/* The run's own summary: what each scored pass said, and what the pair says
 * together. `results` is [{ pass, ranking }] for the scored passes. */
export function runSummary(results) {
  const along = results.find((r) => r.pass === "along");
  const alone = results.find((r) => r.pass === "alone");
  if (!along || !alone) return "";
  const a = along.ranking, b = alone.ranking;
  const drop = a.score - b.score;
  if (!a.targets) return "";
  if (b.score >= a.score) return "As good without the click as with it — the rhythm is yours.";
  if (drop <= 8) return "Barely a change without the click: the rhythm is holding.";
  if (drop <= 20) return "A little looser without the click. Run it again before moving on.";
  return "The click was doing the counting. Slow the tempo down and run it again.";
}

/* "steady", or how far the tempo wandered. */
export function driftText(drift) {
  if (!drift) return "";
  if (drift.steady) return "tempo steady";
  return `tempo drifted ${drift.bpm > 0 ? "+" : ""}${Math.round(drift.bpm)} bpm (${drift.bpm > 0 ? "speeding up" : "slowing down"})`;
}
