/* Danas Ear: microphone → Web Audio analyser → YIN → note readout, pitch
 * trace, waveform, keyboard and a log of note events. Everything runs in
 * this tab; no audio leaves the browser. */

import { detectPitch, rmsDb, describe, midiToHz, noteName, NoteTracker, eventsToScript, selftest } from "./pitch.js";

const $ = (id) => document.getElementById(id);
const FFT = 4096;
const KEY_LO = 36, KEY_HI = 96;            // C2..C7 on the keyboard and trace
const TRACE_SECONDS = 12;
const CLARITY_MIN = 0.6;

const state = {
  ctx: null, analyser: null, stream: null, source: null,
  tone: null, toneGain: null,
  buf: new Float32Array(FFT),
  running: false, lastFrame: 0,
  reading: null,              // latest {hz, clarity, midi, name, octave, cents, midiFloat, db}
  trace: [],                  // [{t, midiFloat|null, clarity}]
  events: [],
  tracker: null,
  t0: 0,
};

/* ---------------------------------------------------------------- audio */

async function ensureContext() {
  if (!state.ctx) {
    state.ctx = new (window.AudioContext || window.webkitAudioContext)();
    state.analyser = state.ctx.createAnalyser();
    state.analyser.fftSize = FFT;
    state.analyser.smoothingTimeConstant = 0;
    // An analyser with no output is not always processed; pull it silently.
    const sink = state.ctx.createGain();
    sink.gain.value = 0;
    state.analyser.connect(sink);
    sink.connect(state.ctx.destination);
  }
  if (state.ctx.state === "suspended") await state.ctx.resume();
}

async function startMic() {
  await ensureContext();
  stopMic();
  const deviceId = $("device").value;
  const constraints = {
    audio: {
      echoCancellation: false, noiseSuppression: false, autoGainControl: false,
      ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
    },
  };
  state.stream = await navigator.mediaDevices.getUserMedia(constraints);
  state.source = state.ctx.createMediaStreamSource(state.stream);
  state.source.connect(state.analyser);
  await listDevices();
  const track = state.stream.getAudioTracks()[0];
  status(`Listening on "${track.label || "microphone"}" at ${state.ctx.sampleRate} Hz`);
}

function stopMic() {
  if (state.source) { try { state.source.disconnect(); } catch (_) { /* ignore */ } state.source = null; }
  if (state.stream) { for (const t of state.stream.getTracks()) t.stop(); state.stream = null; }
}

async function listDevices() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
  const devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "audioinput");
  const sel = $("device"), current = sel.value;
  sel.replaceChildren(new Option("default microphone", ""));
  for (const d of devices) sel.appendChild(new Option(d.label || `microphone ${sel.length}`, d.deviceId));
  sel.value = current;
}

/* A synthetic tone into the same analyser: lets the pipeline be checked
 * without a microphone (and lets a script verify it). */
async function toggleTone() {
  await ensureContext();
  if (state.tone) { stopTone(); return; }
  const hz = parseFloat($("tone-hz").value) || 261.63;
  const osc = state.ctx.createOscillator();
  osc.type = "triangle";
  osc.frequency.value = hz;
  const gain = state.ctx.createGain();
  gain.gain.value = 0.25;
  osc.connect(gain);
  gain.connect(state.analyser);
  const monitor = state.ctx.createGain();
  monitor.gain.value = 0.08;
  gain.connect(monitor);
  monitor.connect(state.ctx.destination);
  osc.start();
  state.tone = osc; state.toneGain = gain;
  $("tone").classList.add("active");
  $("tone").textContent = "Stop tone";
  startLoop();
}

function stopTone() {
  if (!state.tone) return;
  try { state.tone.stop(); state.tone.disconnect(); state.toneGain.disconnect(); } catch (_) { /* ignore */ }
  state.tone = null; state.toneGain = null;
  $("tone").classList.remove("active");
  $("tone").textContent = "Play tone";
}

/* ------------------------------------------------------------- analysis */

function startLoop() {
  if (state.running) return;
  state.running = true;
  state.t0 = state.t0 || performance.now();
  if (!state.tracker) state.tracker = new NoteTracker(addEvent);
  requestAnimationFrame(frame);
}

function stopLoop() {
  state.running = false;
  if (state.tracker) state.tracker.flush(now());
  state.reading = null;
  draw();
}

const now = () => (performance.now() - state.t0) / 1000;

function frame() {
  if (!state.running) return;
  const t = performance.now();
  if (t - state.lastFrame >= 24) {              // ~40 analyses per second is plenty
    state.lastFrame = t;
    analyse();
    draw();
  }
  requestAnimationFrame(frame);
}

function analyse() {
  state.analyser.getFloatTimeDomainData(state.buf);
  const db = rmsDb(state.buf);
  const gate = parseFloat($("gate").value);
  const a4 = parseFloat($("a4").value) || 440;
  let reading = null;
  if (db > gate) {
    const p = detectPitch(state.buf, state.ctx.sampleRate);
    if (p && p.clarity >= CLARITY_MIN) reading = { ...p, ...describe(p.hz, a4), db };
  }
  state.reading = reading || { db };
  const t = now();
  state.trace.push({ t, midiFloat: reading ? reading.midiFloat : null, clarity: reading ? reading.clarity : 0 });
  while (state.trace.length && state.trace[0].t < t - TRACE_SECONDS) state.trace.shift();
  state.tracker.push(t, reading);
}

function addEvent(e) {
  state.events.push(e);
  const tr = document.createElement("tr");
  const cells = [state.events.length, e.name, e.hz.toFixed(1), (e.cents >= 0 ? "+" : "") + e.cents.toFixed(0),
    e.start.toFixed(2) + " s", ((e.end - e.start) * 1000).toFixed(0) + " ms"];
  cells.forEach((c, i) => {
    const td = document.createElement("td");
    td.textContent = c;
    if (i === 3) td.className = e.cents > 15 ? "sharp" : e.cents < -15 ? "flat" : "";
    tr.appendChild(td);
  });
  const body = $("log").tBodies[0];
  body.appendChild(tr);
  tr.scrollIntoView({ block: "nearest" });
}

/* -------------------------------------------------------------- drawing */

/* Size the backing store to the CSS box × device pixel ratio. The CSS box
 * must come from the stylesheet: a canvas sized only by its attributes
 * would grow by the ratio on every frame. */
function fit(canvas) {
  const r = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(r.width * dpr)), h = Math.max(1, Math.round(r.height * dpr));
  if (w > 8192 || h > 8192) throw new Error(`canvas #${canvas.id} is not CSS-sized (${w}×${h})`);
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  const g = canvas.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  return [g, r.width, r.height];
}

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function draw() {
  const r = state.reading;
  const has = r && r.hz;
  $("note-name").textContent = has ? r.name : "—";
  $("note-oct").textContent = has ? r.octave : "";
  $("hz").textContent = has ? r.hz.toFixed(1) + " Hz" : "—";
  $("cents-val").textContent = has ? (r.cents >= 0 ? "+" : "") + r.cents.toFixed(0) + " ¢" : "—";
  $("cents-val").style.color = has ? (Math.abs(r.cents) <= 5 ? css("--accent") : Math.abs(r.cents) <= 20 ? css("--warn") : css("--error")) : "";
  $("level").textContent = r && isFinite(r.db) ? r.db.toFixed(0) + " dB" : "—";
  $("clarity").textContent = has ? (r.clarity * 100).toFixed(0) + " %" : "—";
  $("midi").textContent = has ? r.midi + " (" + r.midiFloat.toFixed(2) + ")" : "—";
  drawCents(has ? r.cents : null);
  drawTrace();
  drawScope();
  drawKeys(has ? r.midi : null, has ? r.clarity : 0);
}

function drawCents(cents) {
  const c = $("cents"), [g, w, h] = fit(c);
  g.clearRect(0, 0, w, h);
  const x0 = 10, x1 = w - 10, mid = (x0 + x1) / 2, y = h * 0.55;
  g.fillStyle = "rgba(79,209,165,0.15)";
  g.fillRect(mid - (x1 - x0) * 0.05, 8, (x1 - x0) * 0.1, h - 16);
  g.strokeStyle = css("--border"); g.lineWidth = 1;
  g.beginPath(); g.moveTo(x0, y); g.lineTo(x1, y); g.stroke();
  for (let cval = -50; cval <= 50; cval += 10) {
    const x = mid + (cval / 50) * (x1 - mid);
    g.beginPath(); g.moveTo(x, y - (cval % 50 === 0 ? 14 : cval % 20 === 0 ? 9 : 5)); g.lineTo(x, y + 5); g.stroke();
  }
  if (cents === null) return;
  const x = mid + (Math.max(-50, Math.min(50, cents)) / 50) * (x1 - mid);
  g.strokeStyle = Math.abs(cents) <= 5 ? css("--accent") : Math.abs(cents) <= 20 ? css("--warn") : css("--error");
  g.lineWidth = 3;
  g.beginPath(); g.moveTo(x, 6); g.lineTo(x, h - 6); g.stroke();
}

function drawTrace() {
  const c = $("trace"), [g, w, h] = fit(c);
  g.clearRect(0, 0, w, h);
  const yOf = (m) => h - ((m - KEY_LO) / (KEY_HI - KEY_LO)) * (h - 12) - 6;
  g.font = "10px Segoe UI, system-ui, sans-serif"; g.textBaseline = "middle";
  for (let m = KEY_LO; m <= KEY_HI; m++) {
    if (m % 12 !== 0) continue;
    const y = yOf(m);
    g.strokeStyle = css("--border"); g.lineWidth = 1;
    g.beginPath(); g.moveTo(28, y); g.lineTo(w, y); g.stroke();
    g.fillStyle = css("--text-dim"); g.fillText(noteName(m), 4, y);
  }
  const t = now();
  const xOf = (tt) => 28 + ((tt - (t - TRACE_SECONDS)) / TRACE_SECONDS) * (w - 28);
  for (const e of state.events) {
    if (e.end < t - TRACE_SECONDS) continue;
    const y = yOf(e.midi);
    g.fillStyle = "rgba(92,156,224,0.35)";
    g.fillRect(xOf(e.start), y - 4, Math.max(2, xOf(e.end) - xOf(e.start)), 8);
  }
  g.fillStyle = css("--accent");
  for (const p of state.trace) {
    if (p.midiFloat === null) continue;
    g.globalAlpha = 0.35 + 0.65 * p.clarity;
    g.fillRect(xOf(p.t) - 1, yOf(p.midiFloat) - 1.5, 2.5, 3);
  }
  g.globalAlpha = 1;
}

function drawScope() {
  const c = $("scope"), [g, w, h] = fit(c);
  g.clearRect(0, 0, w, h);
  g.strokeStyle = css("--border"); g.beginPath(); g.moveTo(0, h / 2); g.lineTo(w, h / 2); g.stroke();
  if (!state.analyser) return;
  const n = Math.min(state.buf.length, 2048);
  g.strokeStyle = state.reading && state.reading.hz ? css("--accent") : css("--text-dim");
  g.lineWidth = 1.2;
  g.beginPath();
  for (let i = 0; i < n; i++) {
    const x = (i / n) * w, y = h / 2 - state.buf[i] * h * 0.48;
    i ? g.lineTo(x, y) : g.moveTo(x, y);
  }
  g.stroke();
}

const BLACK = new Set([1, 3, 6, 8, 10]);

function drawKeys(midi, clarity) {
  const c = $("keys"), [g, w, h] = fit(c);
  g.clearRect(0, 0, w, h);
  const whites = [];
  for (let m = KEY_LO; m <= KEY_HI; m++) if (!BLACK.has(m % 12)) whites.push(m);
  const kw = w / whites.length;
  const xOf = {};
  whites.forEach((m, i) => { xOf[m] = i * kw; });
  g.font = "9px Segoe UI, system-ui, sans-serif"; g.textAlign = "center";
  whites.forEach((m, i) => {
    const x = i * kw;
    g.fillStyle = m === midi ? css("--accent") : "#e8ecf2";
    g.fillRect(x + 0.5, 0, kw - 1, h);
    if (m % 12 === 0) { g.fillStyle = m === midi ? "#14161a" : "#7d8695"; g.fillText(noteName(m), x + kw / 2, h - 5); }
  });
  for (let m = KEY_LO; m <= KEY_HI; m++) {
    if (!BLACK.has(m % 12)) continue;
    const x = xOf[m - 1] + kw * 0.65;
    g.fillStyle = m === midi ? css("--accent") : "#1b1e24";
    g.fillRect(x, 0, kw * 0.7, h * 0.6);
  }
  if (midi !== null && (midi < KEY_LO || midi > KEY_HI)) {
    g.fillStyle = css("--warn"); g.textAlign = "left"; g.font = "11px Segoe UI, system-ui, sans-serif";
    g.fillText(`${noteName(midi)} is outside the keyboard range`, 6, 14);
  }
  void clarity;
}

/* ------------------------------------------------------------------ ui */

function status(text, err = false) {
  const s = $("status");
  s.textContent = text;
  s.classList.toggle("err", err);
}

async function onStart() {
  try {
    await startMic();
    $("start").disabled = true; $("stop").disabled = false;
    startLoop();
  } catch (e) {
    status(e.name === "NotAllowedError" ? "Microphone access was refused — allow it in the address bar and try again."
      : e.name === "NotFoundError" ? "No microphone found." : "Could not open the microphone: " + e.message, true);
  }
}

function onStop() {
  stopMic();
  $("start").disabled = false; $("stop").disabled = true;
  if (!state.tone) stopLoop();
  status("Stopped.");
}

function downloadLog() {
  const doc = { app: "Danas Ear", a4: parseFloat($("a4").value) || 440, sampleRate: state.ctx ? state.ctx.sampleRate : null,
    recorded: new Date().toISOString(), events: state.events };
  const blob = new Blob([JSON.stringify(doc, null, 1)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "danas-ear-notes.json";
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

async function copyScript() {
  const bpm = parseFloat($("bpm").value) || 80;
  const text = eventsToScript(state.events, bpm);
  if (!text) { status("No notes to copy yet."); return; }
  try { await navigator.clipboard.writeText(text); status(`Copied ${state.events.length} notes: ${text.slice(0, 80)}${text.length > 80 ? "…" : ""}`); }
  catch (_) { window.prompt("Copy the script line:", text); }
}

function clearLog() {
  state.events = [];
  $("log").tBodies[0].replaceChildren();
}

function wire() {
  $("start").onclick = onStart;
  $("stop").onclick = onStop;
  $("tone").onclick = () => toggleTone().catch((e) => status("Test tone failed: " + e.message, true));
  $("tone-hz").onchange = () => { if (state.tone) state.tone.frequency.value = parseFloat($("tone-hz").value) || 261.63; };
  $("device").onchange = () => { if (state.stream) onStart(); };
  $("gate").oninput = () => { $("gate-val").textContent = $("gate").value + " dB"; };
  $("clear").onclick = clearLog;
  $("download").onclick = downloadLog;
  $("copy-script").onclick = copyScript;
  $("about-btn").onclick = () => $("about").showModal();
  $("about-close").onclick = () => $("about").close();
  window.addEventListener("resize", draw);
  if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
    navigator.mediaDevices.addEventListener?.("devicechange", listDevices);
    listDevices().catch(() => {});
  }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    status("This browser cannot access the microphone (needs https or localhost).", true);
    $("start").disabled = true;
  }
  draw();
}

wire();

/* Exposed for scripts and the console. */
window.ear = {
  state, selftest, detectPitch, describe, midiToHz, eventsToScript,
  start: onStart, stop: onStop, tone: toggleTone, stopTone,
  get reading() { return state.reading; },
  get events() { return state.events; },
};
