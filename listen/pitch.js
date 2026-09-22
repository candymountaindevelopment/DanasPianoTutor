/* Pitch detection and note naming. Pure functions, no DOM, so they can be
 * tested with a synthetic signal (see selftest() at the bottom). */

/* YIN (de Cheveigné & Kawahara 2002): difference function over a window of
 * `win` samples, cumulative-mean normalisation, first dip under `threshold`,
 * parabolic refinement. Returns {hz, clarity} or null when nothing periodic
 * is found. `buf` must hold at least win + sampleRate/minHz samples. */
export function detectPitch(buf, sampleRate, opts = {}) {
  const { minHz = 40, maxHz = 2000, threshold = 0.15, win = 2048 } = opts;
  const tauMin = Math.max(2, Math.floor(sampleRate / maxHz));
  const tauMax = Math.min(Math.floor(sampleRate / minHz), buf.length - win - 1);
  if (tauMax <= tauMin) return null;

  const cmnd = new Float32Array(tauMax + 1);
  cmnd[0] = 1;
  let running = 0;
  for (let tau = 1; tau <= tauMax; tau++) {
    let d = 0;
    for (let i = 0; i < win; i++) { const v = buf[i] - buf[i + tau]; d += v * v; }
    running += d;
    cmnd[tau] = running > 0 ? (d * tau) / running : 1;
  }

  let tau = -1;
  for (let t = tauMin; t <= tauMax; t++) {
    if (cmnd[t] < threshold) {
      while (t + 1 <= tauMax && cmnd[t + 1] < cmnd[t]) t++;
      tau = t;
      break;
    }
  }
  if (tau < 0) {
    let best = tauMin;
    for (let t = tauMin; t <= tauMax; t++) if (cmnd[t] < cmnd[best]) best = t;
    if (cmnd[best] > 0.5) return null;
    tau = best;
  }
  let period = tau;
  if (tau > 0 && tau < tauMax) {
    const a = cmnd[tau - 1], b = cmnd[tau], c = cmnd[tau + 1];
    const den = a - 2 * b + c;
    if (den !== 0) period = tau + 0.5 * (a - c) / den;
  }
  return { hz: sampleRate / period, clarity: Math.max(0, Math.min(1, 1 - cmnd[tau])) };
}

export function rmsDb(buf) {
  let s = 0;
  for (let i = 0; i < buf.length; i++) s += buf[i] * buf[i];
  return 20 * Math.log10(Math.sqrt(s / buf.length) + 1e-12);
}

const NAMES = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"];

export function hzToMidi(hz, a4 = 440) { return 69 + 12 * Math.log2(hz / a4); }
export function midiToHz(midi, a4 = 440) { return a4 * Math.pow(2, (midi - 69) / 12); }

export function describe(hz, a4 = 440) {
  const m = hzToMidi(hz, a4);
  const midi = Math.round(m);
  return { midi, name: NAMES[((midi % 12) + 12) % 12], octave: Math.floor(midi / 12) - 1, cents: (m - midi) * 100, midiFloat: m };
}

export function noteName(midi) { return NAMES[((midi % 12) + 12) % 12] + (Math.floor(midi / 12) - 1); }

/* Turn a stream of per-frame readings into note events. Feed frames in
 * order; `null` means silence / no pitch. Emits events through onEvent. */
export class NoteTracker {
  constructor(onEvent, { attack = 3, release = 4 } = {}) {
    this.onEvent = onEvent;
    this.attack = attack; this.release = release;
    this.current = null;       // {midi, frames:[{t,hz,cents}], start}
    this.candidate = null;     // {midi, count, frames}
    this.silent = 0;
  }
  push(t, reading) {
    if (!reading) {
      this.candidate = null;
      if (this.current && ++this.silent >= this.release) this.close(t);
      return;
    }
    this.silent = 0;
    const midi = reading.midi;
    if (this.current && midi === this.current.midi) {
      this.current.frames.push({ t, hz: reading.hz, cents: reading.cents });
      this.candidate = null;
      return;
    }
    if (this.candidate && this.candidate.midi === midi) {
      this.candidate.count++;
      this.candidate.frames.push({ t, hz: reading.hz, cents: reading.cents });
    } else {
      this.candidate = { midi, count: 1, frames: [{ t, hz: reading.hz, cents: reading.cents }] };
    }
    if (this.candidate.count >= this.attack) {
      if (this.current) this.close(this.candidate.frames[0].t);
      this.current = { midi, frames: this.candidate.frames, start: this.candidate.frames[0].t };
      this.candidate = null;
    }
  }
  close(t) {
    const c = this.current;
    if (!c) return;
    this.current = null;
    const hzs = c.frames.map((f) => f.hz).sort((a, b) => a - b);
    const cents = c.frames.map((f) => f.cents).sort((a, b) => a - b);
    const mid = (arr) => arr[Math.floor(arr.length / 2)];
    this.onEvent({ midi: c.midi, name: noteName(c.midi), hz: mid(hzs), cents: mid(cents), start: c.start, end: t, frames: c.frames.length });
  }
  flush(t) { this.close(t); }
}

/* Quantise note events to a grid at `bpm` and write them as one RAW lesson
 * notes line ("C4 D4:2 E4:1/2 ..."). Gaps become rests ("r"). */
export function eventsToScript(events, bpm, grid = 0.5) {
  if (!events.length) return "";
  const beat = 60 / bpm;
  const q = (sec) => Math.max(grid, Math.round(sec / beat / grid) * grid);
  const fmt = (beats) => beats === 1 ? "" : Number.isInteger(beats) ? `:${beats}` : beats === 0.5 ? ":1/2" : beats === 0.25 ? ":1/4" : `:${beats}`;
  const out = [];
  let cursor = events[0].start;
  for (const e of events) {
    const gap = e.start - cursor;
    if (gap >= beat * grid * 0.75) out.push("r" + fmt(q(gap)));
    const len = q(e.end - e.start);
    out.push(e.name.replace("♯", "#") + fmt(len));
    cursor = e.end;
  }
  return out.join(" ");
}

/* Synthetic check: a 220 Hz tone with harmonics must come back as A3. */
export function selftest(sampleRate = 48000) {
  const buf = new Float32Array(4096);
  for (let i = 0; i < buf.length; i++) {
    const t = i / sampleRate;
    buf[i] = 0.5 * Math.sin(2 * Math.PI * 220 * t) + 0.6 * Math.sin(2 * Math.PI * 440 * t) + 0.2 * Math.sin(2 * Math.PI * 660 * t);
  }
  const r = detectPitch(buf, sampleRate);
  const d = r && describe(r.hz);
  return { ok: !!d && d.name === "A" && d.octave === 3 && Math.abs(d.cents) < 2, hz: r && r.hz, clarity: r && r.clarity };
}
