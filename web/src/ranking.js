/* Score an attempt: the notes the microphone heard against the lesson.
 *
 * Time is in lesson divisions (the listener stamps every heard note with the
 * transport position), so tempo does not matter. The detector hears one
 * pitch at a time, so notes that start together — a chord, or both hands —
 * form one target that counts as hit when any of its keys was heard close
 * enough to the beat. Everything here is a pure function of its inputs. */

import { noteName } from "./views.js";

/* Timing tolerance around a target's onset, in beats. */
export const PERFECT = 0.15, GOOD = 0.35, LIMIT = 0.75;

export function rankAttempt(lesson, hands, events, range, tempo) {
  const beat = lesson.beat_divisions, bar = lesson.measure_divisions;
  const [from, to] = range || [0, lesson.length];
  // Targets: groups of lesson notes by onset, restricted to the hands played.
  const groups = new Map();
  for (const n of lesson.notes) {
    if (!hands.includes(n.hand) || n.start < from || n.start >= to) continue;
    if (!groups.has(n.start)) groups.set(n.start, { start: n.start, midis: new Set(), bar: Math.floor(n.start / bar) + 1 });
    groups.get(n.start).midis.add(n.midi);
  }
  const targets = Array.from(groups.values()).sort((a, b) => a.start - b.start);
  const heard = events.filter((e) => e.start >= from - LIMIT * beat && e.start < to + LIMIT * beat).map((e) => ({ ...e, used: false }));

  const results = [];
  for (const t of targets) {
    let best = null, bestErr = Infinity;
    for (const e of heard) {
      if (e.used || !t.midis.has(e.midi)) continue;
      const err = (e.start - t.start) / beat;
      if (Math.abs(err) <= LIMIT && Math.abs(err) < Math.abs(bestErr)) { best = e; bestErr = err; }
    }
    if (best) best.used = true;
    results.push({ start: t.start, bar: t.bar, midis: Array.from(t.midis), hit: !!best, error: best ? bestErr : null, wrong: null });
  }
  // A missed target with some other note heard in its place: say which.
  for (const r of results) {
    if (r.hit) continue;
    const w = nearestWrong(heard, r, beat);
    if (w) { w.used = true; r.wrong = w.midi; }
  }
  const hits = results.filter((r) => r.hit);
  const errors = hits.map((r) => r.error);
  const meanAbs = errors.length ? errors.reduce((a, b) => a + Math.abs(b), 0) / errors.length : 0;
  const meanSigned = errors.length ? errors.reduce((a, b) => a + b, 0) / errors.length : 0;
  const perfect = errors.filter((e) => Math.abs(e) <= PERFECT).length;
  const good = errors.filter((e) => Math.abs(e) > PERFECT && Math.abs(e) <= GOOD).length;
  const extra = heard.filter((e) => !e.used && e.start >= from && e.start < to && (e.end - e.start) >= beat * 0.2).length;

  const accuracy = targets.length ? hits.length / targets.length : 0;
  const timing = errors.length ? Math.max(0, 1 - meanAbs / LIMIT) : 0;
  const extraPenalty = targets.length ? Math.min(0.2, (0.5 * extra) / targets.length) : 0;
  const score = Math.round(Math.max(0, Math.min(1, accuracy * 0.7 + accuracy * timing * 0.3 - extraPenalty)) * 100);
  const stars = score >= 95 ? 5 : score >= 85 ? 4 : score >= 70 ? 3 : score >= 50 ? 2 : score > 0 ? 1 : 0;

  const bars = {};
  for (const r of results) {
    const b = bars[r.bar] || (bars[r.bar] = { bar: r.bar, targets: 0, hits: 0 });
    b.targets++; if (r.hit) b.hits++;
  }
  return {
    score, stars, targets: targets.length, hits: hits.length, perfect, good, extra,
    meanAbs, meanSigned, results, missed: results.filter((r) => !r.hit),
    drift: measureDrift(results, beat, tempo || lesson.tempo),
    bars: Object.values(bars).sort((a, b) => a.bar - b.bar),
    verdict: verdict(score, accuracy, meanSigned, extra, targets.length),
  };
}

/* Tempo drift: fit each note's timing error against the beat it fell on.
 * A steady player has a slope near zero whatever their offset; a slope of
 * +0.02 means every beat arrives 2 % late, which is playing that much
 * slower than the grid. Only meaningful once a few notes have landed. */
function measureDrift(results, beat, tempo) {
  const pts = results.filter((r) => r.hit).map((r) => [r.start / beat, r.error]);
  if (pts.length < 4) return null;
  const n = pts.length;
  const mx = pts.reduce((s, p) => s + p[0], 0) / n, my = pts.reduce((s, p) => s + p[1], 0) / n;
  let num = 0, den = 0;
  for (const [x, y] of pts) { num += (x - mx) * (y - my); den += (x - mx) * (x - mx); }
  if (den <= 0) return null;
  const slope = num / den;
  const bpm = tempo ? tempo / (1 + slope) - tempo : 0;
  return { slope, bpm, steady: Math.abs(bpm) < 2 };
}

/* Which note was played instead, when one was heard near the target. */
function nearestWrong(heard, t, beat) {
  let best = null, bestDist = Infinity;
  for (const e of heard) {
    if (e.used) continue;
    const dist = Math.abs(e.start - t.start) / beat;
    if (dist <= LIMIT && dist < bestDist) { best = e; bestDist = dist; }
  }
  return best;
}

function verdict(score, accuracy, signed, extra, targets) {
  if (!targets) return "Nothing to play in this range.";
  if (accuracy === 0) return "No notes matched — check the microphone (Ear tab) and that the piano is close to it.";
  const parts = [];
  if (score >= 95) parts.push("Excellent — every note in its place.");
  else if (score >= 85) parts.push("Very good.");
  else if (score >= 70) parts.push("Good — a few slips.");
  else if (score >= 50) parts.push("Getting there; slow the tempo down and try again.");
  else parts.push("Keep practising one hand at a time, slowly.");
  if (Math.abs(signed) > 0.12) parts.push(signed > 0 ? "You tend to play late — listen to the metronome." : "You tend to rush ahead of the beat.");
  if (extra > 2) parts.push(`${extra} extra notes were heard.`);
  return parts.join(" ");
}

export const describeError = (err) => (err === null ? "" : Math.abs(err) <= PERFECT ? "on time" : (err > 0 ? "late" : "early") + ` ${Math.abs(err).toFixed(2)} beat`);
export const midiLabel = (midi) => noteName(midi).replace("#", "♯");
