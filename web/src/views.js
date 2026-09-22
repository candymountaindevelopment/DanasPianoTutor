/* Canvas views: the keyboard (with the hands drawn on the keys), the note
 * tape under the staff, the pitch trace and the tuner ribbon.
 *
 * Every canvas is sized from a wrapper element and positioned out of the
 * flow (see setupCanvas), so a canvas can never size itself. */

export const COLORS = {
  ground: "#0E1214", keybed: "#0A0D0E", hairline: "#1E2629",
  ink: "#E9EEF0", quiet: "#96A3A8", now: "#46D7A1", nowDim: "#24705a",
  wrong: "#E9B44C", missed: "#F08079", left: "#5C9CE0", leftDim: "#2f5378",
  whiteKey: "#E9EEF0", blackKey: "#12181a",
};
const HAND_COLOR = { R: COLORS.now, L: COLORS.left };
const HAND_DIM = { R: COLORS.nowDim, L: COLORS.leftDim };
const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const WHITE = new Set([0, 2, 4, 5, 7, 9, 11]);
const WHITE_SEMITONES = [0, 2, 4, 5, 7, 9, 11];
const BLACK_AFTER = { 0: 1, 1: 3, 3: 6, 4: 8, 5: 10 };
const WHITE_INDEX = { 0: 0, 1: 0.5, 2: 1, 3: 1.5, 4: 2, 5: 3, 6: 3.5, 7: 4, 8: 4.5, 9: 5, 10: 5.5, 11: 6 };

export const FONT_SANS = '"InstrumentSans", "Segoe UI", system-ui, sans-serif';
export const FONT_MONO = '"JetBrainsMono", Consolas, monospace';

export const noteName = (midi) => NOTE_NAMES[midi % 12] + (Math.floor(midi / 12) - 1);
export const isWhite = (midi) => WHITE.has(midi % 12);
const whiteIndex = (midi) => Math.floor(midi / 12) * 7 + WHITE_INDEX[midi % 12];
const MAX_SIDE = 8192;

/* Size the backing store to the wrapper's box times the device pixel ratio.
 * The canvas itself never decides how big it is. */
function setupCanvas(canvas) {
  const box = canvas.parentElement || canvas;
  const w = Math.max(1, Math.min(MAX_SIDE, box.clientWidth));
  const h = Math.max(1, Math.min(MAX_SIDE, box.clientHeight));
  const dpr = Math.min(3, window.devicePixelRatio || 1);
  const bw = Math.max(1, Math.min(MAX_SIDE, Math.round(w * dpr)));
  const bh = Math.max(1, Math.min(MAX_SIDE, Math.round(h * dpr)));
  if (canvas.width !== bw || canvas.height !== bh) { canvas.width = bw; canvas.height = bh; }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(bw / w, 0, 0, bh / h, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return [ctx, w, h];
}

function roundRect(ctx, x, y, w, h, r) {
  const rr = Math.max(0, Math.min(r, w / 2, h / 2));
  ctx.beginPath();
  ctx.moveTo(x + rr, y); ctx.lineTo(x + w - rr, y); ctx.quadraticCurveTo(x + w, y, x + w, y + rr);
  ctx.lineTo(x + w, y + h - rr); ctx.quadraticCurveTo(x + w, y + h, x + w - rr, y + h);
  ctx.lineTo(x + rr, y + h); ctx.quadraticCurveTo(x, y + h, x, y + h - rr);
  ctx.lineTo(x, y + rr); ctx.quadraticCurveTo(x, y, x + rr, y); ctx.closePath();
}

function shade(hex, factor) {
  const n = parseInt(hex.slice(1), 16);
  const r = Math.round(((n >> 16) & 255) * factor), g = Math.round(((n >> 8) & 255) * factor), b = Math.round((n & 255) * factor);
  return `rgb(${r},${g},${b})`;
}

/* ------------------------------------------------------------ keyboard */

export class Keyboard {
  constructor(canvas, settings) {
    this.canvas = canvas;
    this.settings = settings;
    this.low = 48; this.octaves = 3;
    this.marks = {};
    this.palms = {};
    this.dim = [];
    this.onKey = null;
    canvas.addEventListener("click", (e) => {
      if (!this.onKey || !this.settings.get("play.clickKeys")) return;
      const rect = canvas.getBoundingClientRect();
      const midi = this.noteAt(e.clientX - rect.left, e.clientY - rect.top);
      if (midi !== null) this.onKey(midi);
    });
    new ResizeObserver(() => this.draw()).observe(canvas.parentElement || canvas);
  }

  fit(lesson) {
    let [lo, hi] = lesson.midi_range;
    for (const hand of ["R", "L"]) {
      const keys = lesson.positions[hand].keys;
      if (keys) { lo = Math.min(lo, keys[0]); hi = Math.max(hi, keys[keys.length - 1]); }
    }
    this.low = Math.floor(lo / 12) * 12;
    this.octaves = Math.min(5, Math.max(2, Math.floor((hi - this.low) / 12) + 1));
    this.draw();
  }

  update(marks, palms, dim) { this.marks = marks || {}; this.palms = palms || {}; this.dim = dim || []; this.draw(); }

  whiteRects(w, h) {
    const count = 7 * this.octaves, kw = w / count, out = [];
    for (let i = 0; i < count; i++) out.push([this.low + 12 * Math.floor(i / 7) + WHITE_SEMITONES[i % 7], i * kw, 0, kw, h]);
    return out;
  }

  blackRects(w, h) {
    const count = 7 * this.octaves, kw = w / count, bw = kw * 0.6, bh = h * 0.6, out = [];
    for (let i = 0; i < count; i++) {
      const off = BLACK_AFTER[i % 7];
      if (off === undefined || i === count - 1) continue;
      out.push([this.low + 12 * Math.floor(i / 7) + off, (i + 1) * kw - bw / 2, 0, bw, bh]);
    }
    return out;
  }

  noteAt(x, y) {
    const box = this.canvas.parentElement || this.canvas;
    const w = box.clientWidth, h = box.clientHeight;
    for (const [m, rx, ry, rw, rh] of this.blackRects(w, h)) if (x >= rx && x < rx + rw && y >= ry && y < ry + rh) return m;
    for (const [m, rx, ry, rw, rh] of this.whiteRects(w, h)) if (x >= rx && x < rx + rw && y >= ry && y < ry + rh) return m;
    return null;
  }

  draw() {
    const [ctx, w, h] = setupCanvas(this.canvas);
    ctx.fillStyle = COLORS.keybed; ctx.fillRect(0, 0, w, h);
    const whites = this.whiteRects(w, h), blacks = this.blackRects(w, h);
    const kw = w / (7 * this.octaves);
    const labels = kw > 21;
    ctx.font = `9px ${FONT_MONO}`; ctx.textAlign = "center";
    for (const [m, x, , width, kh] of whites) {
      const mark = this.marks[m];
      ctx.fillStyle = mark ? mark[0] : COLORS.whiteKey;
      roundRect(ctx, x + 0.5, -6, width - 1, kh + 6, 4); ctx.fill();
      if (labels || m % 12 === 0) { ctx.fillStyle = "rgba(99,112,116,0.55)"; ctx.fillText(noteName(m), x + width / 2, kh - 8); }
    }
    for (const [m, x, , bw, bh] of blacks) {
      const mark = this.marks[m];
      ctx.fillStyle = mark ? shade(mark[0], 0.75) : COLORS.blackKey;
      roundRect(ctx, x, -6, bw, bh + 6, 3); ctx.fill();
    }
    if (this.settings.get("show.palms")) {
      for (const hand of ["L", "R"]) if (this.palms[hand]) this.drawPalm(ctx, w, h, this.palms[hand]);
    } else if (this.settings.get("show.keyFingers")) {
      ctx.textBaseline = "middle";
      for (const [m, x, , width, kh] of [...blacks, ...whites]) {
        const mark = this.marks[m];
        if (!mark || !mark[1]) continue;
        const d = Math.min(width * 0.8, 22), cy = isWhite(m) ? kh - 34 : kh - 16;
        ctx.beginPath(); ctx.arc(x + width / 2, cy, d / 2, 0, Math.PI * 2);
        ctx.fillStyle = COLORS.keybed; ctx.fill();
        ctx.fillStyle = COLORS.ink; ctx.font = `500 ${Math.max(9, d * 0.55)}px ${FONT_MONO}`;
        ctx.fillText(mark[1], x + width / 2, cy + 0.5);
      }
      ctx.textBaseline = "alphabetic";
    }
  }

  /* A hand seen from above: the palm at the near edge of the keys, five
   * fingers reaching to their resting keys, a pressing finger on its key. */
  drawPalm(ctx, w, h, state) {
    const kw = w / (7 * this.octaves);
    const base = whiteIndex(this.low);
    const xOf = (wi) => (wi - base) * kw + kw / 2;
    const hand = state.hand, dim = this.dim.includes(hand);
    const color = dim ? HAND_DIM[hand] : HAND_COLOR[hand];
    const fingers = state.offsets.map((o, k) => ({ x: xOf(state.anchor + o), number: hand === "R" ? k + 1 : 5 - k }));
    const fw = Math.max(9, Math.min(kw * 0.58, 22));
    const palmTop = h * 0.80, palmBottom = h + 16;
    const left = Math.min(...fingers.map((f) => f.x)) - fw * 0.85, right = Math.max(...fingers.map((f) => f.x)) + fw * 0.85;
    ctx.save();
    ctx.globalAlpha = dim ? 0.3 : 0.5;
    ctx.fillStyle = color;
    roundRect(ctx, left, palmTop, right - left, palmBottom - palmTop, 14); ctx.fill();
    for (const f of fingers) {
      const midi = state.pressed[f.number];
      const pressing = midi !== undefined;
      const thumb = f.number === 1;
      const tip = pressing ? (isWhite(midi) ? h * 0.30 : h * 0.10) : thumb ? h * 0.64 : h * (0.46 + 0.03 * Math.abs(f.number - 3));
      ctx.globalAlpha = pressing ? 0.92 : dim ? 0.3 : 0.55;
      ctx.fillStyle = color;
      roundRect(ctx, f.x - fw / 2, tip, fw, palmTop + 6 - tip, fw / 2); ctx.fill();
      ctx.globalAlpha = 1;
      const r = fw * 0.44;
      ctx.beginPath(); ctx.arc(f.x, tip + fw * 0.62, r, 0, Math.PI * 2);
      ctx.fillStyle = pressing ? COLORS.keybed : "rgba(10,13,14,0.65)"; ctx.fill();
      ctx.fillStyle = pressing ? color : "rgba(233,238,240,0.7)";
      ctx.font = `500 ${Math.max(9, Math.min(13, r * 1.5))}px ${FONT_MONO}`;
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(String(f.number), f.x, tip + fw * 0.62 + 0.5);
    }
    ctx.textBaseline = "alphabetic";
    ctx.restore();
  }
}

/* ----------------------------------------------------------------- tape */

/* The note lane, drawn on the staff's own x-axis when the score supplies one
 * (so bar 2 sits under bar 2), and on its own scrolling axis when it cannot. */
export class Lane {
  constructor(canvas, settings) {
    this.canvas = canvas; this.settings = settings;
    this.lesson = null; this.position = 0; this.dim = []; this.loopRange = null;
    this.mapper = null;               // { from, to, xOf(division) } from the score
    this.pxPerDivision = 5; this.playheadFraction = 0.28;
    this.onSeek = null;
    canvas.addEventListener("click", (e) => {
      if (!this.lesson || !this.onSeek || !this.settings.get("play.seek")) return;
      const rect = canvas.getBoundingClientRect();
      this.onSeek(Math.max(0, this.divisionAt(e.clientX - rect.left)));
    });
    new ResizeObserver(() => this.draw()).observe(canvas.parentElement || canvas);
  }
  setLesson(lesson) { this.lesson = lesson; this.position = 0; this.loopRange = null; this.draw(); }
  setPosition(d) { this.position = d; this.draw(); }
  setDim(hands) { this.dim = hands; this.draw(); }
  setLoopRange(r) { this.loopRange = r; this.draw(); }
  setMapper(m) { this.mapper = m; this.draw(); }

  x(d) {
    if (this.mapper) return this.mapper.xOf(d);
    return this.canvas.clientWidth * this.playheadFraction + (d - this.position) * this.pxPerDivision;
  }
  divisionAt(x) {
    if (this.mapper) {
      const { from, to } = this.mapper, x0 = this.mapper.xOf(from), x1 = this.mapper.xOf(to);
      return from + (to - from) * ((x - x0) / Math.max(1, x1 - x0));
    }
    return this.position + (x - this.canvas.clientWidth * this.playheadFraction) / this.pxPerDivision;
  }

  draw() {
    const [ctx, w, h] = setupCanvas(this.canvas);
    const lesson = this.lesson;
    if (!lesson) return;
    // Rows are white keys, not semitones: two octaves then fit in 74 px with
    // bars thick enough to read, and a black key sits between its neighbours.
    const lo = whiteIndex(lesson.midi_range[0]), hi = whiteIndex(lesson.midi_range[1]);
    const rowH = Math.max(4, (h - 8) / Math.max(5, hi - lo + 1));
    const top = Math.max(4, (h - (hi - lo + 1) * rowH) / 2);
    const yOf = (m) => top + (hi - whiteIndex(m)) * rowH;
    const M = lesson.measure_divisions;
    const view = this.mapper ? [this.mapper.from, this.mapper.to] : [this.divisionAt(0), this.divisionAt(w)];

    if (this.loopRange) {
      const a = this.x(Math.max(view[0], this.loopRange[0])), b = this.x(Math.min(view[1], this.loopRange[1]));
      if (b > a) { ctx.fillStyle = "rgba(70,215,161,0.07)"; ctx.fillRect(a, 0, b - a, h); }
    }
    // Bar lines only: the tape is a reminder, not a second score.
    ctx.strokeStyle = COLORS.hairline; ctx.lineWidth = 1;
    for (let d = Math.floor(view[0] / M) * M; d <= view[1]; d += M) {
      const x = Math.round(this.x(d)) + 0.5;
      if (x < 0 || x > w) continue;
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    }
    const showFingers = this.settings.get("show.laneFingers");
    ctx.font = `500 ${Math.max(7, Math.min(10, rowH * 1.1))}px ${FONT_MONO}`;
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    for (const n of lesson.notes) {
      if (n.start + n.duration < view[0] || n.start > view[1]) continue;
      const x0 = this.x(n.start), x1 = this.x(n.start + n.duration);
      if (x1 < -4 || x0 > w + 4) continue;
      const y = yOf(n.midi), on = n.start <= this.position && this.position < n.start + n.duration;
      const dim = this.dim.includes(n.hand);
      ctx.fillStyle = dim ? shade(HAND_DIM[n.hand], 0.8) : on ? HAND_COLOR[n.hand] : shade(HAND_COLOR[n.hand], 0.55);
      const rw = Math.max(3, x1 - x0 - 2);
      roundRect(ctx, x0 + 1, y + 0.5, rw, Math.max(2, rowH - 1), 2); ctx.fill();
      if (showFingers && n.shown && rw > 11 && rowH >= 8) {
        ctx.fillStyle = on ? COLORS.keybed : "rgba(10,13,14,0.75)";
        ctx.fillText(String(n.shown), x0 + 1 + rw / 2, y + rowH / 2 + 0.5);
      }
    }
    ctx.textBaseline = "alphabetic";
    const px = this.x(this.position);
    if (px >= 0 && px <= w) {
      ctx.strokeStyle = COLORS.now; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(px, 0); ctx.lineTo(px, h); ctx.stroke();
    }
  }
}

/* ---------------------------------------------------------------- trace */

/* The pitch trace: what Danas Ear draws, here taking the staff's place in
 * Ear mode. Points are {t, midiFloat|null, clarity}. */
export class Trace {
  constructor(canvas) {
    this.canvas = canvas;
    this.points = [];
    this.seconds = 20;
    this.lo = 48; this.hi = 84;
    new ResizeObserver(() => this.draw()).observe(canvas.parentElement || canvas);
  }
  clear() { this.points = []; this.lo = 48; this.hi = 84; this.draw(); }
  push(t, reading) {
    this.points.push({ t, midi: reading ? reading.midiFloat : null, clarity: reading ? reading.clarity : 0 });
    while (this.points.length && this.points[0].t < t - this.seconds) this.points.shift();
    if (reading) {
      this.lo = Math.min(this.lo, Math.floor(reading.midiFloat) - 4);
      this.hi = Math.max(this.hi, Math.ceil(reading.midiFloat) + 4);
    }
    this.draw();
  }
  draw() {
    const [ctx, w, h] = setupCanvas(this.canvas);
    const lo = this.lo, hi = this.hi;
    const yOf = (m) => h - 12 - ((m - lo) / Math.max(1, hi - lo)) * (h - 28);
    ctx.font = `10px ${FONT_MONO}`; ctx.textBaseline = "middle"; ctx.textAlign = "left";
    for (let m = Math.ceil(lo / 12) * 12; m <= hi; m += 12) {
      const y = yOf(m);
      ctx.strokeStyle = COLORS.hairline; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(36, y); ctx.lineTo(w - 6, y); ctx.stroke();
      ctx.fillStyle = COLORS.quiet; ctx.fillText(noteName(m), 8, y);
    }
    const now = this.points.length ? this.points[this.points.length - 1].t : 0;
    const xOf = (t) => 36 + ((t - (now - this.seconds)) / this.seconds) * (w - 42);
    ctx.fillStyle = COLORS.now;
    for (const p of this.points) {
      if (p.midi === null) continue;
      ctx.globalAlpha = 0.3 + 0.7 * p.clarity;
      ctx.fillRect(xOf(p.t) - 1, yOf(p.midi) - 1.5, 2.5, 3);
    }
    ctx.globalAlpha = 1;
    ctx.textBaseline = "alphabetic";
    if (!this.points.some((p) => p.midi !== null)) {
      ctx.fillStyle = COLORS.quiet; ctx.textAlign = "center"; ctx.font = `13.5px ${FONT_SANS}`;
      ctx.fillText("Play a note — the pitch you sound is drawn here.", w / 2, h / 2);
    }
  }
}

/* --------------------------------------------------------------- ribbon */

/* Tuner ribbon: the cents meter and the clarity sparkline under the staff. */
export class Ribbon {
  constructor(meterCanvas, sparkCanvas) {
    this.meter = meterCanvas; this.spark = sparkCanvas;
    this.history = [];
    this.last = null;
    const redraw = () => this.paint();
    new ResizeObserver(redraw).observe(meterCanvas.parentElement || meterCanvas);
    new ResizeObserver(redraw).observe(sparkCanvas.parentElement || sparkCanvas);
  }
  draw(reading) {
    this.last = reading;
    this.history.push(reading ? reading.clarity : 0);
    if (this.history.length > 200) this.history.shift();
    this.paint();
  }
  clear() { this.history = []; this.last = null; this.paint(); }
  paint() {
    const reading = this.last;
    const [m, mw, mh] = setupCanvas(this.meter);
    const mid = mw / 2, y = mh / 2;
    m.strokeStyle = COLORS.hairline; m.lineWidth = 1;
    m.beginPath(); m.moveTo(0, y); m.lineTo(mw, y); m.stroke();
    for (let c = -50; c <= 50; c += 10) {
      const x = mid + (c / 50) * (mw / 2 - 4);
      const tall = c === 0 ? 9 : c % 50 === 0 ? 6 : 3;
      m.beginPath(); m.moveTo(x, y - tall); m.lineTo(x, y + tall); m.stroke();
    }
    if (reading) {
      const x = mid + (Math.max(-50, Math.min(50, reading.cents)) / 50) * (mw / 2 - 4);
      m.strokeStyle = Math.abs(reading.cents) <= 5 ? COLORS.now : Math.abs(reading.cents) <= 20 ? COLORS.wrong : COLORS.missed;
      m.lineWidth = 2.5;
      m.beginPath(); m.moveTo(x, 3); m.lineTo(x, mh - 3); m.stroke();
    }
    const [s, sw, sh] = setupCanvas(this.spark);
    if (this.history.length > 1) {
      s.strokeStyle = COLORS.now; s.lineWidth = 1.2; s.beginPath();
      this.history.forEach((c, i) => {
        const x = (i / (this.history.length - 1)) * sw, yy = sh - 3 - c * (sh - 6);
        i ? s.lineTo(x, yy) : s.moveTo(x, yy);
      });
      s.stroke();
    }
  }
}
