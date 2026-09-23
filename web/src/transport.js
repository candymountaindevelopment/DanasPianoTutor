/* Playback with Web Audio. Position comes from AudioContext.currentTime, so
 * the cursor is sample-accurate. A looped practice range plays as two
 * stages, like the desktop app: count-in + first pass once, then the section
 * alone with loop=true, so the count-in is heard exactly once. */

import { PASSES, passAt, buildRunSamples } from "./run.js";

export class Transport {
  constructor(bridge) {
    this.bridge = bridge;
    this.ctx = null;
    this.index = null;
    this.lesson = null;
    this.options = { tempo: 80, hands: "both", metronome: true, count_in_bars: 1, loop_bars: null,
                     voice_db: -12, metronome_unpitched: false };
    this.onPass = null;          // called with the pass number each time a loop wraps
    this.pass = 0;
    this.run = null;             // a practice run in progress (see run.js)
    this.onRunPass = null;       // called with the pass index as the run moves on
    this.loop = false;
    this.rendered = null;          // {samples, sampleRate, frames, count_in_seconds, ...}
    this.buffer = null;            // AudioBuffer: count-in + section
    this.section = null;           // AudioBuffer: section only
    this.dirty = true;
    this.playing = false;
    this.phase = "first";
    this.source = null;
    this.startedAt = 0;
    this.offset = 0;
    this.position = 0;
    this.gain = null;
    this.onPosition = null;
    this.onState = null;
    this.onMessage = null;
    this.rendering = null;
    this._raf = null;
    this._generation = 0;
  }

  /* Must be called from a user gesture (click) the first time. */
  ensureContext() {
    if (!this.ctx) {
      this.ctx = new (window.AudioContext || window.webkitAudioContext)();
      this.gain = this.ctx.createGain();
      this.gain.connect(this.ctx.destination);
    }
    if (this.ctx.state === "suspended") this.ctx.resume();
    return this.ctx;
  }

  setLesson(index, lesson) {
    this.stop();
    this.index = index;
    this.lesson = lesson;
    this.options = { ...this.options, tempo: lesson ? lesson.tempo : 80, loop_bars: null };
    this.dirty = true;
    this.position = 0;
    this.emitPosition();
  }

  updateOptions(changes) {
    const next = { ...this.options, ...changes };
    if (JSON.stringify(next) === JSON.stringify(this.options)) return;
    this.options = next;
    this.dirty = true;
    if (this.playing) this.play(this.barStart(this.position), false);
  }

  setLoop(on) { this.loop = !!on; }

  sectionRange() {
    if (!this.lesson) return [0, 0];
    const m = this.lesson.measure_divisions;
    const lb = this.options.loop_bars;
    if (lb) {
      const first = Math.max(1, Math.min(lb[0], this.lesson.measures));
      const last = Math.max(first, Math.min(lb[1], this.lesson.measures));
      return [(first - 1) * m, last * m];
    }
    return [0, this.lesson.length];
  }

  barStart(division) {
    if (!this.lesson) return 0;
    const m = this.lesson.measure_divisions;
    const [start, end] = this.sectionRange();
    const d = Math.floor(Math.max(start, Math.min(division, end - 1)) / m) * m;
    return Math.max(start, d);
  }

  async ensureRendered() {
    if (this.index === null) return null;
    if (!this.dirty && this.rendered) return this.rendered;
    if (this.rendering) return this.rendering;
    const generation = ++this._generation;
    this.rendering = this.bridge.render(this.index, this.options).then((r) => {
      this.rendering = null;
      if (generation !== this._generation) return this.rendered;
      const ctx = this.ensureContext();
      const buffer = ctx.createBuffer(1, r.samples.length, r.sample_rate);
      buffer.copyToChannel(r.samples, 0);
      const countFrames = Math.round(r.count_in_seconds * r.sample_rate);
      const section = ctx.createBuffer(1, Math.max(1, r.samples.length - countFrames), r.sample_rate);
      section.copyToChannel(r.samples.subarray(countFrames), 0);
      this.rendered = r;
      this.buffer = buffer;
      this.section = section;
      this.dirty = false;
      for (const w of r.warnings || []) if (this.onMessage) this.onMessage(w);
      return r;
    });
    return this.rendering;
  }

  secondsAt(division) {
    const r = this.rendered;
    return r.count_in_seconds + (division - r.start_division) * r.seconds_per_division;
  }

  divisionAt(seconds) {
    const r = this.rendered;
    return r.start_division + (seconds - r.count_in_seconds) / r.seconds_per_division;
  }

  async play(fromDivision = null, countIn = true) {
    const r = await this.ensureRendered();
    if (!r) return;
    this.ensureContext();
    this._stopSource();
    const [start] = this.sectionRange();
    const division = fromDivision === null || fromDivision <= start ? start : this.barStart(fromDivision);
    const offset = countIn && division === start ? 0 : this.secondsAt(division);
    this.phase = "first";
    this.pass = 0;
    this.offset = offset;
    this._startSource(this.buffer, false, offset);
    this.playing = true;
    if (this.onState) this.onState(true);
    this._tick();
  }

  /* ------------------------------------------------------------- run */

  /* Render the three passes into one buffer. Returns the run, or null when
   * there is nothing to play. The caller may show progress while it works. */
  async prepareRun() {
    if (this.index === null || !this.lesson) return null;
    const ctx = this.ensureContext();
    const countIn = Math.max(1, this.options.count_in_bars || 0);
    const base = { ...this.options, count_in_bars: countIn, loop_bars: this.options.loop_bars };
    const full = await this.bridge.render(this.index, { ...base, voice_db: -12 });
    const clicks = await this.bridge.render(this.index, { ...base, voice_db: -100 });
    const rate = full.sample_rate;
    const countInFrames = Math.round(full.count_in_seconds * rate);
    // A pass is the count-in plus the section: a whole number of beats. What
    // the render has beyond that is the release tail, which belongs to the
    // next pass's count-in, not between the passes.
    const sectionSeconds = (full.end_division - full.start_division) * full.seconds_per_division;
    const passFrames = Math.round((full.count_in_seconds + sectionSeconds) * rate);
    const { samples } = buildRunSamples(full.samples, clicks.samples, countInFrames, passFrames);
    const buffer = ctx.createBuffer(1, samples.length, rate);
    buffer.copyToChannel(samples, 0);
    this.run = {
      buffer, rate, passFrames,
      passSeconds: passFrames / rate,
      countInSeconds: full.count_in_seconds,
      secondsPerDivision: full.seconds_per_division,
      startDivision: full.start_division,
      endDivision: full.end_division,
      index: -1,
      passes: PASSES,
    };
    this.rendered = full;          // the position mapping belongs to one pass
    return this.run;
  }

  /* Play a prepared run from its first pass. */
  playRun() {
    if (!this.run) return;
    this._stopSource();
    this.loopBeforeRun = this.loop;
    this.loop = false;
    this.phase = "run";
    this.run.index = -1;
    this.offset = 0;
    this._startSource(this.run.buffer, false, 0);
    this.playing = true;
    if (this.onState) this.onState(true);
    this._tick();
  }

  get inRun() { return !!this.run && this.phase === "run"; }

  endRun() {
    const had = !!this.run;
    this.run = null;
    if (this.loopBeforeRun !== undefined) { this.loop = this.loopBeforeRun; this.loopBeforeRun = undefined; }
    if (this.phase === "run") this.phase = "first";
    return had;
  }

  stop() {
    if (!this.playing) return;
    this._stopSource();
    this.playing = false;
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
    const [start] = this.sectionRange();
    this.position = start;
    if (this.onState) this.onState(false);
    this.emitPosition();
  }

  toggle() { if (this.playing) this.stop(); else this.play(); }

  seek(division) {
    if (this.playing) this.play(division, false);
    else {
      this.position = this.lesson ? this.barStart(division) : 0;
      this.emitPosition();
    }
  }

  async previewNote(midi) {
    if (this.index === null || this.playing) return;
    const ctx = this.ensureContext();
    const r = await this.bridge.preview(this.index, midi, 0.7);
    const buffer = ctx.createBuffer(1, r.samples.length, r.sampleRate);
    buffer.copyToChannel(r.samples, 0);
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(this.gain);
    src.start();
  }

  _startSource(buffer, loop, offset) {
    const src = this.ctx.createBufferSource();
    src.buffer = buffer;
    src.loop = loop;
    src.connect(this.gain);
    src.start(0, offset || 0);
    this.source = src;
    this.startedAt = this.ctx.currentTime;
  }

  _stopSource() {
    if (this.source) {
      try { this.source.stop(); } catch (_) { /* already stopped */ }
      this.source.disconnect();
      this.source = null;
    }
  }

  _tick() {
    if (!this.playing || !this.rendered) return;
    const elapsed = this.ctx.currentTime - this.startedAt;
    let pos;
    if (this.phase === "run") {
      const run = this.run;
      const t = this.offset + elapsed;
      if (t >= run.passSeconds * run.passes.length) { this.stop(); return; }
      const { index, offset } = passAt(t, run.passSeconds);
      if (index !== run.index) { run.index = index; if (this.onRunPass) this.onRunPass(index, run.passes); }
      this.position = this.divisionAt(offset);
      this.emitPosition();
      this._raf = requestAnimationFrame(() => this._tick());
      return;
    }
    if (this.phase === "first") {
      pos = this.offset + elapsed;
      if (pos >= this.buffer.duration) {
        if (this.loop) {
          this._stopSource();
          this.phase = "loop";
          this._startSource(this.section, true, 0);
          pos = this.rendered.count_in_seconds;
        } else {
          this.stop();
          return;
        }
      }
    }
    if (this.phase === "loop") {
      const e = this.ctx.currentTime - this.startedAt;
      const dur = Math.max(1e-6, this.section.duration);
      const pass = Math.floor(e / dur) + 1;
      if (pass !== this.pass) { this.pass = pass; if (this.onPass) this.onPass(pass); }
      pos = this.rendered.count_in_seconds + (e % dur);
    }
    this.position = this.divisionAt(pos);
    this.emitPosition();
    this._raf = requestAnimationFrame(() => this._tick());
  }

  emitPosition() { if (this.onPosition) this.onPosition(this.position); }

  countInBeat(division) {
    if (!this.lesson || !this.rendered) return null;
    const start = this.rendered.start_division;
    if (division >= start) return null;
    const beatsBefore = (start - division) / this.lesson.beat_divisions;
    const perBar = this.lesson.measure_divisions / this.lesson.beat_divisions;
    const total = this.options.count_in_bars * perBar;
    return (Math.floor(total - beatsBefore) % perBar) + 1;
  }
}
