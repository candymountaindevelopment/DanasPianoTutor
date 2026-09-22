/* Moving hands on the keyboard.
 *
 * Each hand is a palm with five fingers spread over `span` white keys
 * (5 = one key per finger, the beginner's five-finger position; up to 8 for
 * a stretched hand). For every note the finger that plays it — written in
 * the script or inferred from the position — fixes where the palm must be:
 * the palm's lowest finger sits `offset(finger)` white keys below the key.
 * Between notes the palm glides to its next place during the last beat
 * before the note, so the hand is always where the student should look.
 *
 * Everything is a pure function of the lesson and the playback division, so
 * the same script always produces the same movement. */

const WHITE_INDEX = { 0: 0, 1: 0.5, 2: 1, 3: 1.5, 4: 2, 5: 3, 6: 3.5, 7: 4, 8: 4.5, 9: 5, 10: 5.5, 11: 6 };

export const MIN_SPAN = 5, MAX_SPAN = 8;

/* White-key offset of the five fingers (lowest first) for a hand covering
 * `span` white keys; matches raw/teach/score.py finger_offsets. */
export function fingerOffsets(span) {
  span = Math.max(MIN_SPAN, Math.min(MAX_SPAN, Math.round(span || MIN_SPAN)));
  return [0, 1, 2, 3, 4].map((i) => Math.floor((i * (span - 1)) / 4 + 0.5));
}

/* Position of a key counted in white keys from MIDI 0; black keys fall half
 * way between their neighbours, which is where a finger over them sits. */
export function whiteIndex(midi) {
  return Math.floor(midi / 12) * 7 + WHITE_INDEX[((midi % 12) + 12) % 12];
}

const fingerIndex = (hand, finger) => (hand === "R" ? finger - 1 : 5 - finger);   // 0 = lowest key
const fingerNumber = (hand, index) => (hand === "R" ? index + 1 : 5 - index);

/* Build the placement timeline of one hand. Returns
 *   { offsets, events: [{ start, anchor, presses: [{ finger, midi, start, end }] }] }
 * where `anchor` is the white index of the lowest finger's resting key. */
export function handTimeline(lesson, hand, span) {
  const offsets = fingerOffsets(span);
  const notes = lesson.notes.filter((n) => n.hand === hand).sort((a, b) => a.start - b.start || a.midi - b.midi);
  const events = [];
  const rest = lesson.positions[hand];
  let anchor = rest && rest.keys ? whiteIndex(rest.keys[0]) : null;
  let i = 0;
  while (i < notes.length) {
    const start = notes[i].start;
    const group = [];
    while (i < notes.length && notes[i].start === start) group.push(notes[i++]);
    const anchors = [], presses = [];
    for (const n of group) {
      const wi = whiteIndex(n.midi);
      let finger = n.shown;
      if (!finger && anchor !== null) {
        // No finger given: the nearest finger of the resting hand takes it.
        let best = 0, bestDist = Infinity;
        offsets.forEach((o, k) => { const dist = Math.abs(anchor + o - wi); if (dist < bestDist) { bestDist = dist; best = k; } });
        finger = fingerNumber(hand, best);
        if (bestDist > 0.75) anchors.push(wi - offsets[best]);   // out of reach: the hand shifts
      } else if (!finger) {
        finger = 3;
        anchors.push(wi - offsets[2]);
      } else {
        anchors.push(wi - offsets[fingerIndex(hand, finger)]);
      }
      presses.push({ finger, midi: n.midi, start: n.start, end: n.start + n.duration });
    }
    if (anchors.length) anchor = anchors.reduce((a, b) => a + b, 0) / anchors.length;
    events.push({ start, anchor, presses });
  }
  // A note group with no anchor of its own (nothing fingered, no position) keeps the previous one.
  let last = anchor;
  for (let k = events.length - 1; k >= 0; k--) { if (events[k].anchor === null) events[k].anchor = last; else last = events[k].anchor; }
  for (const e of events) if (e.anchor === null) e.anchor = whiteIndex(e.presses[0].midi) - offsets[2];
  return { hand, offsets, events };
}

const ease = (u) => (u <= 0 ? 0 : u >= 1 ? 1 : u * u * (3 - 2 * u));

/* Where the hand is at `division`: the palm anchor (white index, fractional
 * while gliding) and the fingers currently pressing, as finger → midi. */
export function handState(timeline, division, glideDivisions) {
  const ev = timeline.events;
  if (!ev.length) return null;
  let i = -1;
  while (i + 1 < ev.length && ev[i + 1].start <= division) i++;
  const cur = i >= 0 ? ev[i] : null, next = ev[i + 1] || null;
  let anchor = cur ? cur.anchor : next.anchor;
  if (cur && next && next.anchor !== cur.anchor) {
    const glide = Math.max(1, Math.min(glideDivisions, next.start - cur.start));
    const t0 = next.start - glide;
    if (division > t0) anchor = cur.anchor + (next.anchor - cur.anchor) * ease((division - t0) / glide);
  }
  const pressed = {};
  for (let k = i; k >= 0 && k > i - 8; k--) {
    for (const p of ev[k].presses) if (p.start <= division && division < p.end) pressed[p.finger] = p.midi;
  }
  return { anchor, pressed, offsets: timeline.offsets, hand: timeline.hand };
}
