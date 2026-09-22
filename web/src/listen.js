/* The tutor's ear: microphone → analyser → YIN (shared with Danas Ear).
 * While an attempt runs the app calls sample() every frame and feeds the
 * reading to a NoteTracker stamped with the transport division, so heard
 * notes line up with the lesson without any clock conversion. */

import { detectPitch, rmsDb, describe, NoteTracker } from "../listen/pitch.js";

const FFT = 4096, CLARITY_MIN = 0.6;

export class Listener {
  constructor() {
    this.ctx = null; this.analyser = null; this.stream = null; this.source = null;
    this.buf = new Float32Array(FFT);
    this.gateDb = -55;
    this.a4 = 440;
    this.tracker = null;
    this.events = [];
    this.lastReading = null;
  }

  get active() { return !!this.analyser && (!!this.stream || !!this.injected); }

  /* Open the microphone on the transport's AudioContext (one context, so
   * currentTime is shared). Throws the getUserMedia error on refusal. */
  async start(ctx) {
    this.ctx = ctx;
    if (!this.analyser) {
      this.analyser = ctx.createAnalyser();
      this.analyser.fftSize = FFT;
      this.analyser.smoothingTimeConstant = 0;
    }
    if (this.stream || this.injected) return;
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
    });
    this.source = ctx.createMediaStreamSource(this.stream);
    this.source.connect(this.analyser);
  }

  /* Feed any node instead of the microphone (self-test, scripted checks). */
  inject(ctx, node) {
    this.ctx = ctx;
    if (!this.analyser) { this.analyser = ctx.createAnalyser(); this.analyser.fftSize = FFT; this.analyser.smoothingTimeConstant = 0; }
    node.connect(this.analyser);
    this.injected = node;
  }

  stop() {
    if (this.source) { try { this.source.disconnect(); } catch (_) { /* ignore */ } this.source = null; }
    if (this.stream) { for (const t of this.stream.getTracks()) t.stop(); this.stream = null; }
    if (this.injected) { try { this.injected.disconnect(); } catch (_) { /* ignore */ } this.injected = null; }
  }

  beginAttempt() {
    this.events = [];
    this.tracker = new NoteTracker((e) => this.events.push(e));
  }

  /* One analysis frame at transport `division`. Returns the reading shown live. */
  sample(division) {
    if (!this.analyser) return null;
    this.analyser.getFloatTimeDomainData(this.buf);
    const db = rmsDb(this.buf);
    let reading = null;
    if (db > this.gateDb) {
      const p = detectPitch(this.buf, this.ctx.sampleRate);
      if (p && p.clarity >= CLARITY_MIN) reading = { ...p, ...describe(p.hz, this.a4), db };
    }
    if (this.tracker) this.tracker.push(division, reading);
    this.lastReading = reading;
    return reading;
  }

  endAttempt(division) {
    if (this.tracker) this.tracker.flush(division);
    this.tracker = null;
    return this.events;
  }
}
