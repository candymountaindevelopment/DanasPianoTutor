/* Canvas views: keyboard, hand diagram, note lane. Ports of the desktop
 * widgets; each takes the lesson summary from the worker and a division. */

export const COLORS = {
  bg: "#14161a", panel: "#1b1e24", panelAlt: "#22262e", border: "#2e343e",
  text: "#d7dce4", textDim: "#7d8695", accent: "#4fd1a5", accentDim: "#2c8b6d",
  playhead: "#e0b341", right: "#4fd1a5", left: "#5c9ce0", rightDim: "#2c6b56", leftDim: "#31527a",
};
const HAND_COLOR = { R: COLORS.right, L: COLORS.left };
const HAND_DIM = { R: COLORS.rightDim, L: COLORS.leftDim };
const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const WHITE = new Set([0, 2, 4, 5, 7, 9, 11]);
const WHITE_SEMITONES = [0, 2, 4, 5, 7, 9, 11];
const BLACK_AFTER = { 0: 1, 1: 3, 3: 6, 4: 8, 5: 10 };

export const noteName = (midi) => NOTE_NAMES[midi % 12] + (Math.floor(midi / 12) - 1);
export const isWhite = (midi) => WHITE.has(midi % 12);
const WHITE_INDEX = { 0: 0, 1: 0.5, 2: 1, 3: 1.5, 4: 2, 5: 3, 6: 3.5, 7: 4, 8: 4.5, 9: 5, 10: 5.5, 11: 6 };
const whiteIndex = (midi) => Math.floor(midi / 12) * 7 + WHITE_INDEX[midi % 12];

function setupCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return [ctx, w, h];
}

function sounding(lesson, division, hands) {
  return lesson.notes.filter((n) => hands.includes(n.hand) && n.start <= division && division < n.start + n.duration);
}

/* ------------------------------------------------------------ keyboard */

export class Keyboard {
  constructor(canvas, settings) {
    this.canvas = canvas;
    this.settings = settings;
    this.low = 48; this.octaves = 3;
    this.marks = {};
    this.palms = {};            // hand -> state from palms.handState, or null
    this.dim = [];              // hands that are muted
    this.onKey = null;
    canvas.addEventListener("click", (e) => {
      if (!this.onKey || !this.settings.get("play.clickKeys")) return;
      const rect = canvas.getBoundingClientRect();
      const midi = this.noteAt(e.clientX - rect.left, e.clientY - rect.top);
      if (midi !== null) this.onKey(midi);
    });
    new ResizeObserver(() => this.draw()).observe(canvas);
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

  setMarks(marks) { this.marks = marks; this.draw(); }
  update(marks, palms, dim) { this.marks = marks; this.palms = palms || {}; this.dim = dim || []; this.draw(); }

  whiteRects(w, h) {
    const count = 7 * this.octaves, kw = w / count, out = [];
    for (let i = 0; i < count; i++) out.push([this.low + 12 * Math.floor(i / 7) + WHITE_SEMITONES[i % 7], i * kw, 0, kw, h]);
    return out;
  }

  blackRects(w, h) {
    const count = 7 * this.octaves, kw = w / count, bw = kw * 0.62, bh = h * 0.62, out = [];
    for (let i = 0; i < count; i++) {
      const off = BLACK_AFTER[i % 7];
      if (off === undefined || i === count - 1) continue;
      out.push([this.low + 12 * Math.floor(i / 7) + off, (i + 1) * kw - bw / 2, 0, bw, bh]);
    }
    return out;
  }

  noteAt(x, y) {
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    for (const [m, rx, ry, rw, rh] of this.blackRects(w, h)) if (x >= rx && x < rx + rw && y >= ry && y < ry + rh) return m;
    for (const [m, rx, ry, rw, rh] of this.whiteRects(w, h)) if (x >= rx && x < rx + rw && y >= ry && y < ry + rh) return m;
    return null;
  }

  draw() {
    const [ctx, w, h] = setupCanvas(this.canvas);
    ctx.fillStyle = COLORS.panel; ctx.fillRect(0, 0, w, h);
    const whites = this.whiteRects(w, h), blacks = this.blackRects(w, h);
    const showAll = w / (7 * this.octaves) > 22;
    ctx.font = "9px Segoe UI, sans-serif"; ctx.textAlign = "center";
    for (const [m, x, y, kw, kh] of whites) {
      const mark = this.marks[m];
      ctx.fillStyle = mark ? mark[0] : "#e8ecf2";
      ctx.fillRect(x, y, kw, kh);
      ctx.strokeStyle = "#9aa3b0"; ctx.lineWidth = 1; ctx.strokeRect(x + 0.5, y + 0.5, kw - 1, kh - 1);
      if (m % 12 === 0 || showAll) { ctx.fillStyle = "#20242b"; ctx.fillText(noteName(m), x + kw / 2, kh - 6); }
    }
    for (const [m, x, y, bw, bh] of blacks) {
      const mark = this.marks[m];
      ctx.fillStyle = mark ? shade(mark[0], 0.8) : "#1a1d23";
      ctx.fillRect(x, y, bw, bh);
      ctx.strokeStyle = "#000"; ctx.strokeRect(x + 0.5, y + 0.5, bw - 1, bh - 1);
    }
    const palms = this.settings.get("show.palms") && Object.values(this.palms).some(Boolean);
    if (palms) for (const hand of ["L", "R"]) if (this.palms[hand]) this.drawPalm(ctx, w, h, this.palms[hand]);
    if (!this.settings.get("show.keyFingers") || palms) return;   // fingertips carry the numbers
    const size = Math.max(8, Math.min(13, h / 7));
    ctx.font = `bold ${size}px Segoe UI, sans-serif`; ctx.textBaseline = "middle";
    for (const [m, x, y, kw, kh] of [...blacks, ...whites]) {
      const mark = this.marks[m];
      if (!mark || !mark[1]) continue;
      const black = !isWhite(m), d = Math.min(kw * 0.8, 22);
      const cy = y + kh - (black ? 14 : 34) - d / 2 + d / 2;
      ctx.beginPath(); ctx.arc(x + kw / 2, cy, d / 2, 0, Math.PI * 2);
      ctx.fillStyle = "#f4f6f8"; ctx.fill(); ctx.strokeStyle = "#0d1013"; ctx.stroke();
      ctx.fillStyle = "#0d1013"; ctx.fillText(mark[1], x + kw / 2, cy + 0.5);
    }
    ctx.textBaseline = "alphabetic";
  }
}

/* One hand seen from above: a translucent palm at the near edge of the
 * keys with five fingers reaching up to their resting keys; a finger that is
 * playing reaches further, onto the key, and shows its number. */
Keyboard.prototype.drawPalm = function (ctx, w, h, state) {
  const kw = w / (7 * this.octaves);
  const base = whiteIndex(this.low);
  const xOf = (wi) => (wi - base) * kw + kw / 2;
  const hand = state.hand, dim = this.dim.includes(hand);
  const color = dim ? HAND_DIM[hand] : HAND_COLOR[hand];
  const fingers = state.offsets.map((o, k) => ({ x: xOf(state.anchor + o), number: hand === "R" ? k + 1 : 5 - k }));
  const fw = Math.max(9, Math.min(kw * 0.6, 22));
  const palmTop = h * 0.76, palmBottom = h + 14;
  const left = Math.min(...fingers.map((f) => f.x)) - fw * 0.9, right = Math.max(...fingers.map((f) => f.x)) + fw * 0.9;
  ctx.save();
  ctx.globalAlpha = dim ? 0.35 : 0.55;
  ctx.fillStyle = color; ctx.strokeStyle = "#0d1013"; ctx.lineWidth = 1;
  roundRect(ctx, left, palmTop, right - left, palmBottom - palmTop, Math.min(14, (right - left) / 3));
  ctx.fill(); ctx.stroke();
  for (const f of fingers) {
    const midi = state.pressed[f.number];
    const thumb = f.number === 1;
    let tip;
    if (midi !== undefined) tip = isWhite(midi) ? h * 0.30 : h * 0.10;
    else tip = thumb ? h * 0.60 : h * (0.42 + 0.03 * Math.abs(f.number - 3));
    const y0 = palmTop + 4;
    ctx.globalAlpha = midi !== undefined ? 0.95 : dim ? 0.35 : 0.6;
    ctx.fillStyle = color;
    roundRect(ctx, f.x - fw / 2, tip, fw, y0 - tip + fw / 2, fw / 2);
    ctx.fill(); ctx.stroke();
    if (midi !== undefined || this.settings.get("show.keyFingers")) {
      ctx.globalAlpha = 1;
      const r = fw * 0.42;
      ctx.beginPath(); ctx.arc(f.x, tip + fw / 2, r, 0, Math.PI * 2);
      ctx.fillStyle = midi !== undefined ? "#f4f6f8" : "rgba(244,246,248,0.75)"; ctx.fill();
      ctx.fillStyle = "#0d1013"; ctx.font = `bold ${Math.max(8, Math.min(12, r * 1.5))}px Segoe UI, sans-serif`;
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(String(f.number), f.x, tip + fw / 2 + 0.5);
    }
  }
  ctx.globalAlpha = 1;
  ctx.fillStyle = "#0d1013"; ctx.font = `bold ${Math.max(9, Math.min(12, fw * 0.7))}px Segoe UI, sans-serif`;
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText(hand === "R" ? "R" : "L", (left + right) / 2, h - 7);
  ctx.restore();
};

function shade(hex, factor) {
  const n = parseInt(hex.slice(1), 16);
  const r = Math.round(((n >> 16) & 255) * factor), g = Math.round(((n >> 8) & 255) * factor), b = Math.round((n & 255) * factor);
  return `rgb(${r},${g},${b})`;
}

/* --------------------------------------------------------------- hands */

const FINGER_LENGTH = { 1: 0.58, 2: 0.92, 3: 1.0, 4: 0.93, 5: 0.74 };
const FINGER_NAMES = { 1: "thumb", 2: "index", 3: "middle", 4: "ring", 5: "little" };
const HAND_NAMES = { R: "Right hand", L: "Left hand" };
const SKIN = "#3a4150", SKIN_EDGE = "#586275";

export class Hands {
  constructor(canvas, settings) {
    this.canvas = canvas; this.settings = settings;
    this.lesson = null; this.position = 0; this.dim = [];
    new ResizeObserver(() => this.draw()).observe(canvas);
  }
  setLesson(lesson) { this.lesson = lesson; this.position = 0; this.draw(); }
  setPosition(d) { this.position = d; this.draw(); }
  setDim(hands) { this.dim = hands; this.draw(); }

  active(hand) {
    const out = {};
    if (!this.lesson) return out;
    for (const n of sounding(this.lesson, this.position, [hand])) if (n.shown) (out[n.shown] ||= []).push(n.midi);
    return out;
  }

  next(hand) {
    if (!this.lesson) return null;
    let best = null;
    for (const n of this.lesson.notes) if (n.hand === hand && n.start > this.position && (!best || n.start < best.start)) best = n;
    return best;
  }

  draw() {
    const [ctx, w, h] = setupCanvas(this.canvas);
    ctx.fillStyle = COLORS.panel; ctx.fillRect(0, 0, w, h);
    const half = w / 2;
    this.drawHand(ctx, 6, 6, half - 12, h - 12, "L");
    this.drawHand(ctx, half + 6, 6, half - 12, h - 12, "R");
  }

  drawHand(ctx, bx, by, bw, bh, hand) {
    const dim = this.dim.includes(hand), color = HAND_COLOR[hand];
    const active = this.active(hand), next = this.settings.get("show.nextFinger") ? this.next(hand) : null;
    const labelH = 34, areaH = bh - labelH;
    const palmW = Math.min(bw * 0.62, areaH * 0.55), palmH = palmW * 0.95;
    const cx = bx + bw / 2, palmTop = by + areaH - palmH - 4, palmL = cx - palmW / 2;
    const fingerW = palmW / 4.6, maxLen = Math.min(palmW, palmTop - by - 14);
    roundRect(ctx, palmL, palmTop, palmW, palmH, palmW * 0.22); ctx.fillStyle = SKIN; ctx.fill(); ctx.strokeStyle = SKIN_EDGE; ctx.lineWidth = 1.2; ctx.stroke();
    const order = hand === "R" ? [2, 3, 4, 5] : [5, 4, 3, 2];
    order.forEach((finger, i) => {
      const fx = palmL + palmW * (i + 0.5) / 4, len = maxLen * FINGER_LENGTH[finger];
      this.finger(ctx, fx - fingerW / 2, palmTop - len + fingerW * 0.6, fingerW, len, finger, active[finger], next && next.shown === finger, color, dim);
    });
    // Thumb, angled outward.
    const side = hand === "L" ? 1 : -1, tLen = maxLen * FINGER_LENGTH[1];
    ctx.save();
    ctx.translate(side < 0 ? palmL + fingerW * 0.2 : palmL + palmW - fingerW * 0.2, palmTop + palmH * 0.45);
    ctx.rotate((50 * side) * Math.PI / 180);
    this.finger(ctx, -fingerW / 2, -tLen, fingerW, tLen + fingerW * 0.4, 1, active[1], next && next.shown === 1, color, dim);
    ctx.restore();
    // Captions.
    ctx.textAlign = "center"; ctx.font = "bold 12px Segoe UI, sans-serif";
    ctx.fillStyle = dim ? COLORS.textDim : COLORS.text;
    ctx.fillText(HAND_NAMES[hand] + (dim ? " (muted)" : ""), cx, by + bh - labelH + 14);
    ctx.font = "11px Segoe UI, sans-serif";
    let text, col = COLORS.textDim;
    const fingers = Object.keys(active).map(Number).sort();
    if (fingers.length) { text = fingers.map((f) => `${f} on ${active[f].sort().map(noteName).join(" ")}`).join(", "); col = dim ? COLORS.textDim : color; }
    else if (next && next.shown) text = `next: ${next.shown} (${FINGER_NAMES[next.shown]}) on ${noteName(next.midi)}`;
    else text = this.lesson ? this.lesson.positions[hand].label.split(";")[0] : "";
    ctx.fillStyle = col; ctx.fillText(text, cx, by + bh - labelH + 30);
  }

  finger(ctx, x, y, w, len, number, midis, upcoming, color, dim) {
    const r = w / 2;
    roundRect(ctx, x, y, w, len, r);
    if (midis) { ctx.fillStyle = dim ? shade(color, 0.6) : color; ctx.fill(); ctx.strokeStyle = "#ffffff55"; ctx.setLineDash([]); ctx.lineWidth = 1.5; ctx.stroke(); }
    else { ctx.fillStyle = SKIN; ctx.fill(); ctx.strokeStyle = upcoming && !dim ? color : SKIN_EDGE; ctx.setLineDash(upcoming && !dim ? [4, 3] : []); ctx.lineWidth = upcoming ? 2 : 1.2; ctx.stroke(); ctx.setLineDash([]); }
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.font = `${midis ? "bold " : ""}${Math.max(10, Math.min(16, r * 1.4))}px Segoe UI, sans-serif`;
    ctx.fillStyle = midis && !dim ? "#0d1013" : COLORS.text;
    ctx.fillText(String(number), x + w / 2, y + r + 3);
    if (midis) { ctx.font = "10px Segoe UI, sans-serif"; ctx.fillStyle = dim ? COLORS.textDim : color; ctx.fillText(midis.slice().sort().map(noteName).join(" "), x + w / 2, y - 9); }
    ctx.textBaseline = "alphabetic";
  }
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y); ctx.lineTo(x + w - r, y); ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r); ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h); ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r); ctx.quadraticCurveTo(x, y, x + r, y); ctx.closePath();
}

/* ---------------------------------------------------------------- lane */

export class Lane {
  constructor(canvas, settings) {
    this.canvas = canvas; this.settings = settings;
    this.lesson = null; this.position = 0; this.dim = []; this.loopRange = null;
    this.pxPerDivision = 5; this.playheadFraction = 0.28;
    this.onSeek = null;
    canvas.addEventListener("click", (e) => {
      if (!this.lesson || !this.onSeek || !this.settings.get("play.seek")) return;
      const rect = canvas.getBoundingClientRect();
      this.onSeek(Math.max(0, this.divisionAt(e.clientX - rect.left)));
    });
    canvas.addEventListener("wheel", (e) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      this.pxPerDivision = Math.max(1.5, Math.min(20, this.pxPerDivision * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
      this.draw();
    }, { passive: false });
    new ResizeObserver(() => this.draw()).observe(canvas);
  }
  setLesson(lesson) { this.lesson = lesson; this.position = 0; this.loopRange = null; this.draw(); }
  setPosition(d) { this.position = d; this.draw(); }
  setDim(hands) { this.dim = hands; this.draw(); }
  setLoopRange(r) { this.loopRange = r; this.draw(); }

  x(d) { return this.canvas.clientWidth * this.playheadFraction + (d - this.position) * this.pxPerDivision; }
  divisionAt(x) { return this.position + (x - this.canvas.clientWidth * this.playheadFraction) / this.pxPerDivision; }

  draw() {
    const [ctx, w, h] = setupCanvas(this.canvas);
    ctx.fillStyle = COLORS.panel; ctx.fillRect(0, 0, w, h);
    const lesson = this.lesson;
    if (!lesson) return;
    const lo = lesson.midi_range[0] - 2, hi = lesson.midi_range[1] + 2;
    const rowH = Math.max(4, (h - 22) / Math.max(8, hi - lo + 1)), top = 18;
    const yOf = (m) => h - 4 - (m - lo + 1) * rowH;
    for (let m = lo; m <= hi; m++) {
      ctx.fillStyle = isWhite(m) ? COLORS.panelAlt : COLORS.bg; ctx.fillRect(0, yOf(m), w, rowH);
      if (m % 12 === 0) { ctx.strokeStyle = "#3d4553"; ctx.beginPath(); ctx.moveTo(0, yOf(m) + rowH); ctx.lineTo(w, yOf(m) + rowH); ctx.stroke(); }
    }
    if (this.loopRange) { ctx.fillStyle = "rgba(44,139,109,0.16)"; ctx.fillRect(this.x(this.loopRange[0]), top, this.x(this.loopRange[1]) - this.x(this.loopRange[0]), h - top); }
    const M = lesson.measure_divisions, beat = lesson.beat_divisions;
    ctx.font = "10px Segoe UI, sans-serif"; ctx.textAlign = "left";
    const first = Math.floor(Math.max(0, this.divisionAt(0)) / beat) * beat, last = Math.floor(this.divisionAt(w) / beat) * beat + beat;
    for (let d = first; d <= Math.max(last, lesson.length); d += beat) {
      const x = this.x(d);
      if (x < 0 || x > w) continue;
      const bar = d % M === 0;
      ctx.strokeStyle = bar ? "#4a5261" : "#2e343e"; ctx.beginPath(); ctx.moveTo(Math.round(x) + 0.5, top); ctx.lineTo(Math.round(x) + 0.5, h); ctx.stroke();
      if (bar && d < lesson.length) { ctx.fillStyle = COLORS.textDim; ctx.fillText(String(d / M + 1), x + 3, 12); }
    }
    const endX = this.x(lesson.length);
    if (endX < w) { ctx.strokeStyle = COLORS.textDim; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(endX, top); ctx.lineTo(endX, h); ctx.stroke(); ctx.lineWidth = 1; }
    const showFingers = this.settings.get("show.laneFingers");
    ctx.font = `bold ${Math.max(7, Math.min(10, rowH * 0.9))}px Segoe UI, sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    for (const n of lesson.notes) {
      const x0 = this.x(n.start), x1 = this.x(n.start + n.duration);
      if (x1 < 0 || x0 > w) continue;
      const y = yOf(n.midi), on = n.start <= this.position && this.position < n.start + n.duration;
      const dim = this.dim.includes(n.hand);
      ctx.fillStyle = dim ? shade(HAND_DIM[n.hand], 0.7) : on ? HAND_COLOR[n.hand] : n.start + n.duration <= this.position ? HAND_DIM[n.hand] : HAND_COLOR[n.hand];
      const rw = Math.max(3, x1 - x0 - 2);
      roundRect(ctx, x0 + 1, y + 0.5, rw, rowH - 1, 2.5); ctx.fill();
      if (on && !dim) { ctx.strokeStyle = "#fff"; ctx.stroke(); }
      if (showFingers && n.shown && rw > 10 && rowH >= 7) { ctx.fillStyle = dim ? COLORS.textDim : "#0d1013"; ctx.fillText(String(n.shown), x0 + 1 + rw / 2, y + rowH / 2 + 0.5); }
    }
    ctx.textBaseline = "alphabetic";
    const px = w * this.playheadFraction;
    ctx.strokeStyle = COLORS.playhead; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(px, 0); ctx.lineTo(px, h); ctx.stroke(); ctx.lineWidth = 1;
  }
}
