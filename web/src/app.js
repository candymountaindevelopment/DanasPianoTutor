/* Danas Tutor — one screen, three modes.
 *
 * Learn, Practice and Ear are the same screen: the staff and the keyboard
 * never move, the rail switches mode, the tuner ribbon slides in under the
 * staff and the right panel changes. Everything that is set once rather
 * than changed while playing lives in the palette (Ctrl+K).
 *
 * This module owns the document, the modes, the feature switches and every
 * control; the Python core runs in the worker behind `bridge`.
 */

import { Bridge } from "./bridge.js";
import { Transport } from "./transport.js";
import { ScoreView } from "./score.js";
import { Keyboard, Lane, Trace, Ribbon, COLORS } from "./views.js";
import { handTimeline, handState, MIN_SPAN, MAX_SPAN } from "./palms.js";
import { Listener } from "./listen.js";
import { rankAttempt, midiLabel } from "./ranking.js";
import { PASSES, runSummary, driftText } from "./run.js";
import { Settings, FEATURES, PRESETS } from "./settings.js";
import { Palette } from "./palette.js";
import { eventsToScript } from "../listen/pitch.js";
import { download, wavBlob, makeShareLink, readShareLink, store, printLesson } from "./exports.js";

const $ = (id) => document.getElementById(id);
const HAND_COLOR = { R: COLORS.now, L: COLORS.left };
const HAND_DIM = { R: COLORS.nowDim, L: COLORS.leftDim };
const DIM_FOR = { both: [], right: ["L"], left: ["R"] };
const HANDS_FOR = { both: ["R", "L"], right: ["R"], left: ["L"] };
const HANDS_LABEL = { both: "both hands", right: "right hand", left: "left hand" };
const MODE_LABEL = { learn: "Learn", practice: "Practice", ear: "Ear" };
const PANE_FOR = { learn: "pane-learn", practice: "pane-practice", ear: "pane-ear" };
const ATTEMPTS_KEY = "dpt.attempts.v1";
const PLAY_ICON = '<svg viewBox="0 0 24 24"><path d="M8 5l12 7-12 7z"/></svg>';
const PAUSE_ICON = '<svg viewBox="0 0 24 24"><path d="M7.5 5h3.5v14H7.5zM13 5h3.5v14H13z"/></svg>';

const TEMPLATE = {
  format: "raw.author", version: 1,
  lessons: [{
    name: "five_finger_walk", title: "Five-Finger Walk", tempo: 72, time: "4/4", key: "C", level: 1,
    position: { right: "C4", left: "C3" },
    instructions: "Sit tall, curve your fingers, and keep each hand in its five-finger position.",
    tips: ["Play with a relaxed wrist.", "Count 1-2-3-4 out loud."],
    right: { notes: "C4(1) D4 E4 F4 | G4(5) F4 E4 D4 | C4:4" },
    left: { notes: "C3(5) D3 E3 F3 | G3(1) F3 E3 D3 | C3:4" },
  }],
};

const DEFAULT_TIPS = [
  "Sit tall on the front half of the bench, feet flat, elbows level with the keys.",
  "Curve your fingers as if holding a ball; play on the fingertips.",
  "Keep wrists level and relaxed — no sagging, no lifting.",
  "Thumb is finger 1, little finger is 5. Say the numbers as you play.",
  "Practise slowly with the metronome; speed comes after accuracy.",
];

class App {
  constructor() {
    this.settings = new Settings();
    this.bridge = new Bridge();
    this.transport = new Transport(this.bridge);
    this.score = new ScoreView($("score"), this.bridge, this.settings);
    this.keys = new Keyboard($("keys"), this.settings);
    this.lane = new Lane($("lane"), this.settings);
    this.trace = new Trace($("trace"));
    this.ribbon = new Ribbon($("r-meter"), $("r-spark"));
    this.listener = new Listener();
    this.palette = new Palette($("palette-overlay"), $("palette-input"), $("palette-list"), this.settings);

    this.mode = "learn";
    this.docText = ""; this.cleanText = "";
    this.lessons = []; this.examples = [];
    this.index = -1; this.lesson = null;
    this.docName = "untitled";
    this.about = { name: "Danas Piano Tutor", version: "", engine: "", tagline: "" };
    this.attempt = null;
    this.run = null;             // { results: [{pass, ranking}] } while a run is on
    this.earShown = 0;
    this.syncing = false;
    this.ready = false;
  }

  /* ------------------------------------------------------------ boot */

  async boot() {
    this.wireBar();
    this.wireRail();
    this.wireHead();
    this.wireStage();
    this.wireTransport();
    this.wireScript();
    this.wireSwitches();
    this.wirePanels();
    this.wireShortcuts();
    this.applySettings();
    this.setMode("learn");       // puts the panel, the ribbon and the rail in step

    this.transport.onPosition = (d) => this.positionChanged(d);
    this.transport.onState = (playing) => {
      $("btn-play").innerHTML = playing ? PAUSE_ICON : PLAY_ICON;
      if (playing) { if (!this.transport.inRun) this.beginAttempt(); return; }
      this.finishAttempt();
      if (this.transport.endRun()) this.showRunResult();
    };
    this.transport.onPass = () => { this.finishAttempt(); this.beginAttempt(); };
    this.transport.onRunPass = (i, passes) => this.runPassChanged(i, passes);
    this.transport.onMessage = (m) => this.status(m);
    this.score.onSeek = (d) => this.transport.seek(d);
    this.score.onMapper = (m) => this.lane.setMapper(m);
    this.lane.onSeek = (d) => this.transport.seek(d);
    this.keys.onKey = (midi) => this.transport.previewNote(midi);

    fetch("assets/about.json").then((r) => r.json()).then((a) => { this.about = a; this.fillSplash(); }).catch(() => {});
    this.showSplash(this.settings.get("app.splash"));
    this.bridge.onStatus = (text, progress) => this.splashStatus(text, progress);
    await this.loadExamplesIndex();

    if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost" || location.hostname === "127.0.0.1")) {
      navigator.serviceWorker.register("sw.js").catch(() => {});
    }
    await this.bridge.about();           // resolves when the worker has booted
    this.ready = true;
    const shared = await readShareLink().catch(() => null);
    if (shared) {
      this.docName = "shared";
      await this.applyDocument(shared, true);
    } else if (!(await this.loadExample(0))) {
      await this.applyDocument(JSON.stringify(TEMPLATE, null, 2), true);
    }
    this.buildPalette();
    this.splashReady();
  }

  status(text, ms = 6000) {
    const el = $("status");
    el.textContent = text;
    clearTimeout(this._statusTimer);
    if (ms) this._statusTimer = setTimeout(() => { if (el.textContent === text) el.textContent = ""; }, ms);
  }

  /* ---------------------------------------------------------- splash */

  showSplash(withPhoto) {
    const s = $("splash");
    s.hidden = false;
    if (!withPhoto) s.querySelector(".splash-photo").style.display = "none";
    s.addEventListener("click", () => this.dismissSplash());
    document.addEventListener("keydown", (e) => {
      if (!s.hidden && s.classList.contains("ready") && e.key !== "F1") this.dismissSplash();
    });
  }

  fillSplash() {
    $("splash-name").textContent = this.about.name || "Danas Piano Tutor";
    $("splash-v").textContent = "v " + (this.about.version || "");
    $("splash-date").textContent = new Date().toISOString().slice(0, 10);
    $("splash-engine").textContent = "RAW engine " + (this.about.engine || "");
    $("splash-tagline").textContent = this.about.tagline || "";
  }

  splashStatus(text, progress) {
    const bar = $("splash-bar");
    if (progress >= 0) bar.style.width = Math.round(progress * 100) + "%";
    else bar.style.background = "var(--missed)";
    $("splash-status").textContent = text;
    if (progress < 0) this.status(text, 0);
  }

  splashReady() {
    const s = $("splash");
    s.classList.add("ready");
    $("splash-status").textContent = "Click anywhere to start";
    if (!this.settings.get("app.splash")) this.dismissSplash();
  }

  dismissSplash() {
    const s = $("splash");
    if (s.hidden || !this.ready) return;
    s.hidden = true;
    try { this.transport.ensureContext(); } catch (_) { /* needs a gesture; play() retries */ }
    this.score.relayout(true);
    this.keys.draw(); this.lane.draw();
  }

  /* --------------------------------------------------------- documents */

  async loadExamplesIndex() {
    try { this.examples = await fetch("lessons/index.json").then((r) => r.json()); }
    catch (_) { this.examples = []; }
  }

  async loadExample(i) {
    const e = (this.examples || [])[i];
    if (!e) return false;
    try {
      const text = await fetch("lessons/" + e.file).then((r) => { if (!r.ok) throw new Error(r.status); return r.text(); });
      this.docName = e.file.replace(/\.json$/, "");
      await this.applyDocument(text, true);
      return true;
    } catch (err) { this.status("Cannot load example: " + err.message); return false; }
  }

  async applyDocument(text, clean = false) {
    if (!this.ready) return null;
    this.docText = text;
    $("script").value = text;
    const result = await this.bridge.parse(text);
    this.showReport(result);
    if (result.ok) {
      this.cleanText = text;
      this.lessons = result.lessons;
      const current = this.lesson ? this.lesson.name : null;
      const row = Math.max(0, this.lessons.findIndex((l) => l.name === current));
      await this.setLesson(row);
      this.status(`${this.lessons.length} lesson(s), ${result.warnings.length} warning(s)`);
      if (clean) this.buildPalette();
    } else {
      this.status("Script has errors — press Ctrl+E to see the report", 0);
      this.showScript(true);
    }
    this.updateScriptState();
    return result;
  }

  showReport(result) {
    const pre = $("report");
    pre.replaceChildren();
    const line = (cls, text) => { const s = document.createElement("span"); s.className = cls; s.textContent = text + "\n"; pre.appendChild(s); };
    line("", `${result.lessons.length} lesson(s)`);
    for (const e of result.errors) line("err", "error: " + e);
    for (const w of result.warnings) line("warn", "warning: " + w);
  }

  async setLesson(i) {
    const lesson = this.lessons[i];
    if (!lesson) return;
    this.index = i;
    this.lesson = lesson;
    this.transport.setLesson(i, lesson);
    this.syncing = true;
    $("tempo-slider").value = 100;
    $("tempo-bpm").value = Math.round(lesson.tempo);
    $("bar-from").max = $("bar-to").max = lesson.measures;
    $("bar-from").value = 1; $("bar-to").value = lesson.measures;
    this.syncing = false;
    this.lane.setLesson(lesson);
    this.keys.fit(lesson);
    this.buildPalms();
    this.renderAttempts();
    $("result").hidden = true;
    $("practice-hint").hidden = false;
    this.showHead(lesson);
    this.showInfo(lesson);
    document.title = lesson.title + " — Danas Piano Tutor";
    await this.score.setLesson(i, lesson);
    this.positionChanged(0);
  }

  /* The engraved title is the switcher, and one line of small caps carries
   * what used to be a five-row table. */
  showHead(lesson) {
    $("lesson-title").textContent = lesson.title;
    const bits = [lesson.composer, `key ${lesson.key}`, lesson.time.join("/"), `${lesson.tempo} bpm`,
      `${lesson.measures} bars`, lesson.voice, `level ${lesson.level}`].filter(Boolean);
    $("lesson-meta").textContent = bits.join(" · ");
    const menu = $("lesson-menu");
    menu.replaceChildren();
    this.lessons.forEach((l, i) => {
      const b = document.createElement("button");
      if (i === this.index) b.className = "on";
      b.append(document.createTextNode(l.title));
      const lv = document.createElement("span");
      lv.className = "lv"; lv.textContent = "level " + l.level;
      b.appendChild(lv);
      b.onclick = () => { menu.hidden = true; this.setLesson(i); };
      menu.appendChild(b);
    });
  }

  showInfo(lesson) {
    const box = $("info");
    box.replaceChildren();
    const add = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; box.appendChild(e); return e; };
    add("h3", "Hand positions");
    const ul = add("ul");
    for (const h of ["R", "L"]) {
      const li = document.createElement("li");
      const name = document.createElement("b"); name.className = "hand-" + h; name.textContent = lesson.positions[h].name + ": ";
      li.append(name, document.createTextNode(lesson.positions[h].label));
      ul.appendChild(li);
    }
    if (lesson.instructions) { add("h3", "Instructions"); add("p", lesson.instructions); }
    add("h3", "Posture tips");
    const tl = add("ul");
    for (const t of (lesson.tips.length ? lesson.tips : DEFAULT_TIPS)) {
      const li = document.createElement("li"); li.textContent = t; tl.appendChild(li);
    }
    if (lesson.warnings.length) {
      add("h3", "Script warnings", "warn");
      const wl = add("ul");
      for (const w of lesson.warnings) { const li = document.createElement("li"); li.className = "warn"; li.textContent = w; wl.appendChild(li); }
    }
  }

  /* -------------------------------------------------------------- modes */

  setMode(mode) {
    if (!MODE_LABEL[mode]) return;
    if (mode === "practice" && !this.settings.get("play.listen")) mode = "learn";
    if (mode === "ear" && !this.settings.get("tools.ear")) mode = "learn";
    const leaving = this.mode;
    this.mode = mode;
    $("app").dataset.mode = mode;
    $("bar-mode").textContent = MODE_LABEL[mode];
    for (const b of document.querySelectorAll(".rail-btn[data-mode]")) b.classList.toggle("on", b.dataset.mode === mode);
    $("btn-switches").classList.remove("on");
    this.showPane(PANE_FOR[mode]);

    $("trace-box").hidden = mode !== "ear";
    $("score").hidden = mode === "ear" || !this.settings.get("view.score");
    $("ribbon").hidden = mode === "learn";
    // The tape belongs to the piece, so it goes away with the staff.
    document.querySelector(".tape-box").hidden = mode === "ear" || !this.settings.get("view.lane");

    if (leaving === "ear" && mode !== "ear") this.stopFreeListening();
    this.applyListening();
    if (mode === "ear") this.startFreeListening();
    else if (mode === "practice" && !$("listen").checked) this.status("Tick Listen in the top bar to be scored.", 8000);
    this.refreshKeys();
    this.lane.draw();
  }

  showPane(id) {
    for (const p of document.querySelectorAll(".pane")) p.hidden = p.id !== id;
    const pane = $(id);
    // The Learn panel is a feature switch; the others belong to their mode.
    if (id === "pane-learn" && !this.settings.get("view.lessonPanel")) pane.hidden = true;
    $("panel").hidden = pane.hidden;
    // Under 900 px the panel slides over the stage, so it needs opening.
    $("app").dataset.panel = pane.hidden ? "closed" : "open";
  }

  showSwitches() {
    this.showPane("pane-switches");
    for (const b of document.querySelectorAll(".rail-btn[data-mode]")) b.classList.remove("on");
    $("btn-switches").classList.add("on");
  }

  wireRail() {
    for (const b of document.querySelectorAll(".rail-btn[data-mode]")) b.onclick = () => this.setMode(b.dataset.mode);
    $("btn-switches").onclick = () => this.showSwitches();
  }

  /* ---------------------------------------------------------------- bar */

  wireBar() {
    $("btn-palette").onclick = () => this.palette.toggle();
    $("btn-more").onclick = () => this.palette.toggle();
    $("listen").onchange = () => this.listenChanged();
    $("btn-reset-app").onclick = (e) => { e.stopPropagation(); this.restoreSettings(); this.dismissSplash(); };
  }

  /* On a narrow screen the panel covers the stage; touching the stage
   * puts it away again. */
  wireStage() {
    document.querySelector(".stage").addEventListener("pointerdown", () => {
      if (window.innerWidth <= 900) $("app").dataset.panel = "closed";
    });
  }

  wireHead() {
    const menu = $("lesson-menu");
    $("lesson-title").onclick = (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; };
    document.addEventListener("click", () => { menu.hidden = true; });
  }

  /* ---------------------------------------------------------- transport */

  wireTransport() {
    $("btn-play").onclick = () => this.transport.toggle();
    $("tempo-slider").oninput = () => {
      if (this.syncing || !this.lesson) return;
      const bpm = Math.max(20, Math.min(300, Math.round(this.lesson.tempo * $("tempo-slider").value / 100)));
      this.syncing = true; $("tempo-bpm").value = bpm; this.syncing = false;
      this.transport.updateOptions({ tempo: bpm });
    };
    $("tempo-bpm").onchange = () => {
      if (this.syncing || !this.lesson) return;
      const bpm = Math.max(20, Math.min(300, parseInt($("tempo-bpm").value, 10) || this.lesson.tempo));
      $("tempo-bpm").value = bpm;
      this.syncing = true;
      $("tempo-slider").value = Math.max(50, Math.min(150, Math.round(100 * bpm / this.lesson.tempo)));
      this.syncing = false;
      this.transport.updateOptions({ tempo: bpm });
    };
    for (const b of document.querySelectorAll("#seg-hands button")) {
      b.onclick = () => {
        for (const o of document.querySelectorAll("#seg-hands button")) o.classList.toggle("on", o === b);
        this.handsChanged(b.dataset.hands);
      };
    }
    $("btn-metro").onclick = () => {
      const on = !$("btn-metro").classList.contains("on");
      $("btn-metro").classList.toggle("on", on);
      this.transport.updateOptions({ metronome: on });
    };
    $("btn-loop").onclick = () => {
      const on = !$("btn-loop").classList.contains("on");
      $("btn-loop").classList.toggle("on", on);
      this.transport.setLoop(on);
      this.status(on ? "Practice loop on — it repeats the bar range." : "Practice loop off.");
    };
    $("bar-from").onchange = $("bar-to").onchange = () => this.rangeChanged();
  }

  handsChanged(mode) {
    this.transport.updateOptions({ hands: mode });
    const dim = DIM_FOR[mode] || [];
    this.score.setDimHands(dim);
    this.lane.setDim(dim);
    this.refreshKeys();
  }

  rangeChanged() {
    if (this.syncing || !this.lesson) return;
    let first = parseInt($("bar-from").value, 10) || 1, last = parseInt($("bar-to").value, 10) || this.lesson.measures;
    first = Math.max(1, Math.min(first, this.lesson.measures));
    last = Math.max(first, Math.min(last, this.lesson.measures));
    this.syncing = true; $("bar-from").value = first; $("bar-to").value = last; this.syncing = false;
    const whole = first === 1 && last === this.lesson.measures;
    this.transport.updateOptions({ loop_bars: whole ? null : [first, last] });
    const m = this.lesson.measure_divisions;
    this.lane.setLoopRange(whole ? null : [(first - 1) * m, last * m]);
    if (!this.transport.playing) this.transport.seek((first - 1) * m);
  }

  nudgeTempo(delta) {
    if (!this.settings.get("play.tempo") || !this.lesson) return;
    $("tempo-bpm").value = parseInt($("tempo-bpm").value, 10) + delta;
    $("tempo-bpm").onchange();
  }

  /* --------------------------------------------------------- position */

  positionChanged(division) {
    if (!this.lesson) return;
    this.score.setPosition(division);
    this.lane.setPosition(division);
    this.refreshKeys(division);
    if (this.attempt) this.hear(division);
    const beat = this.transport.playing ? this.transport.countInBeat(division) : null;
    if (beat !== null) $("position").textContent = `count-in · ${beat}`;
    else {
      const d = Math.max(0, division), m = this.lesson.measure_divisions, b = this.lesson.beat_divisions;
      const bar = Math.floor(d / m), beatNo = Math.floor((d - bar * m) / b);
      $("position").textContent = `bar ${Math.min(bar + 1, this.lesson.measures)} · beat ${beatNo + 1}`;
    }
  }

  /* Hand span in white keys: the Set up override, else the lesson's own. */
  spanFor(hand) {
    const override = parseInt($("span").value, 10);
    if (override >= MIN_SPAN && override <= MAX_SPAN) return override;
    return (this.lesson && this.lesson.span && this.lesson.span[hand]) || MIN_SPAN;
  }

  buildPalms() {
    this.palms = this.lesson
      ? { R: handTimeline(this.lesson, "R", this.spanFor("R")), L: handTimeline(this.lesson, "L", this.spanFor("L")) }
      : null;
  }

  refreshKeys(division = this.transport.position) {
    const lesson = this.lesson;
    if (!lesson) { this.keys.update({}, {}, []); return; }
    if (this.mode === "ear") {
      // In Ear the keyboard shows what is heard, not what to play.
      const r = this.listener.lastReading;
      this.keys.update(r ? { [r.midi]: [COLORS.now, ""] } : {}, {}, []);
      return;
    }
    const hands = HANDS_FOR[this.transport.options.hands] || ["R", "L"];
    const dim = ["R", "L"].filter((h) => !hands.includes(h));
    const marks = {};
    if (this.settings.get("show.restingHands") && !this.settings.get("show.palms")) {
      for (const h of hands) {
        const p = lesson.positions[h];
        if (!p.keys) continue;
        p.keys.forEach((midi, i) => { marks[midi] = [HAND_DIM[h], p.fingers[i] ? String(p.fingers[i]) : ""]; });
      }
    }
    for (const n of lesson.notes) {
      if (hands.includes(n.hand) && n.start <= division && division < n.start + n.duration) {
        marks[n.midi] = [HAND_COLOR[n.hand], n.shown ? String(n.shown) : ""];
      }
    }
    const palms = {};
    if (this.palms && this.settings.get("show.palms")) {
      const d = Math.max(0, division);
      for (const h of ["R", "L"]) palms[h] = handState(this.palms[h], d, lesson.beat_divisions);
    }
    this.keys.update(marks, palms, dim);
  }

  /* ------------------------------------------------- listening (shared) */

  async listenChanged() {
    const on = $("listen").checked;
    if (on) {
      try {
        await this.listener.start(this.transport.ensureContext());
        this.status("Listening. Press play and play along with the metronome.", 8000);
      } catch (e) {
        $("listen").checked = false;
        this.status(e.name === "NotAllowedError"
          ? "Microphone access was refused — allow it in the address bar and tick Listen again."
          : "Could not open the microphone: " + e.message, 0);
        return;
      }
    } else {
      if (this.mode === "ear") this.stopFreeListening();
      this.listener.stop();
      $("heard").textContent = "";
      this.ribbon.clear();
      this.showHeard(null);
    }
    this.applyListening();
  }

  /* The piano is muted only where the microphone is being scored — in
   * Practice. In Learn the point is to hear the piece, and in Ear there is
   * nothing playing anyway. The click becomes the unpitched one whenever the
   * microphone is open, so it can never be read as a played note. */
  applyListening() {
    const on = $("listen").checked && this.listener.active;
    const mute = on && this.mode === "practice";
    this.transport.updateOptions({ voice_db: mute ? -100 : -12, metronome_unpitched: on });
  }

  showHeard(reading) {
    $("heard").innerHTML = reading
      ? `${midiLabel(reading.midi)}<small>${reading.cents >= 0 ? "+" : ""}${reading.cents.toFixed(0)} ¢</small>` : "";
    $("r-note").textContent = reading ? midiLabel(reading.midi) : "—";
    $("r-hz").textContent = reading ? reading.hz.toFixed(1) + " Hz" : "";
    $("r-cents").textContent = reading ? `${reading.cents >= 0 ? "+" : ""}${reading.cents.toFixed(0)} ¢` : "";
    $("r-clarity").textContent = reading ? reading.clarity.toFixed(2) : "—";
    this.ribbon.draw(reading);
  }

  /* Ear mode: free listening on a wall clock, with its own note log. */
  async startFreeListening() {
    if (!$("listen").checked) {
      $("listen").checked = true;
      await this.listenChanged();
      if (!this.listener.active) { $("listen").checked = false; return; }
    }
    this.transport.stop();
    this.trace.clear();
    this.listener.beginAttempt();
    this.earShown = 0;
    $("ear-log").tBodies[0].replaceChildren();
    this.freeT0 = performance.now();
    const loop = () => {
      if (this.mode !== "ear" || !this.listener.active) return;
      const t = (performance.now() - this.freeT0) / 1000;
      const reading = this.listener.sample(t);
      this.trace.push(t, reading);
      this.showHeard(reading);
      this.refreshKeys();
      this.drainEarEvents();
      this._raf = requestAnimationFrame(loop);
    };
    loop();
  }

  stopFreeListening() {
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
    if (this.freeT0) this.listener.endAttempt((performance.now() - this.freeT0) / 1000);
    this.drainEarEvents();
  }

  drainEarEvents() {
    while (this.earShown < this.listener.events.length) this.addEarRow(this.listener.events[this.earShown++]);
  }

  addEarRow(e) {
    const tr = document.createElement("tr");
    const cells = [e.name, e.hz.toFixed(1), (e.cents >= 0 ? "+" : "") + e.cents.toFixed(0),
      e.start.toFixed(2) + " s", ((e.end - e.start) * 1000).toFixed(0) + " ms"];
    cells.forEach((c, i) => {
      const td = document.createElement("td");
      td.textContent = c;
      if (i === 2) td.className = e.cents > 15 ? "sharp" : e.cents < -15 ? "flat" : "";
      tr.appendChild(td);
    });
    const body = $("ear-log").tBodies[0];
    body.appendChild(tr);
    tr.scrollIntoView({ block: "nearest" });
  }

  /* ------------------------------------------------------- practice */

  beginAttempt(pass = null) {
    if (!$("listen").checked || !this.listener.active || !this.lesson || this.mode === "ear") return;
    this.listener.beginAttempt();
    this.attempt = { hands: this.transport.options.hands, tempo: this.transport.options.tempo,
      range: this.transport.sectionRange(), started: Date.now(), pass };
    this.score.markResults(null);
  }

  hear(division) {
    // The detector reports a note ~80 ms after it starts (analysis window plus
    // three confirming frames); stamp heard notes that much earlier.
    const spd = this.transport.rendered ? this.transport.rendered.seconds_per_division : 0;
    this.showHeard(this.listener.sample(spd ? division - 0.08 / spd : division));
  }

  finishAttempt() {
    if (!this.attempt) return;
    const attempt = this.attempt;
    this.attempt = null;
    const events = this.listener.endAttempt(this.transport.position);
    const hands = HANDS_FOR[attempt.hands] || ["R", "L"];
    const ranking = rankAttempt(this.lesson, hands, events, attempt.range, attempt.tempo);
    if (!ranking.targets) return;
    const played = ranking.results.filter((r) => r.start < this.transport.position || r.hit).length;
    if (!played && Date.now() - attempt.started < 3000) return;
    this.saveAttempt(ranking, attempt);
    // Inside a run the passes are compared at the end, not one at a time.
    if (this.run && attempt.pass) {
      this.run.results.push({ pass: attempt.pass, ranking });
      this.showPasses(this.transport.run ? this.transport.run.index : PASSES.length);
      if (attempt.pass === "alone") this.score.markResults(ranking);
      return;
    }
    this.showResult(ranking, attempt);
    this.score.markResults(ranking);
    if (this.mode !== "practice") this.setMode("practice");
  }

  showResult(r, attempt) {
    const box = $("result");
    box.hidden = false;
    $("practice-hint").hidden = true;
    const stars = "★".repeat(r.stars) + "☆".repeat(5 - r.stars);
    const timing = r.hits ? `${r.perfect} on time · ${r.good} close · ${r.hits - r.perfect - r.good} off` : "no notes matched";
    const late = Math.abs(r.meanSigned) < 0.05 ? ""
      : ` · ${Math.abs(r.meanSigned).toFixed(2)} beat ${r.meanSigned > 0 ? "late" : "early"}`;
    const rows = r.missed.slice(0, 10).map((m) =>
      `<tr><td>bar ${m.bar}</td><td class="${m.wrong !== null ? "w" : "n"}">${m.midis.map(midiLabel).join(" ")}</td>` +
      `<td>${m.wrong !== null ? "heard " + midiLabel(m.wrong) : "not heard"}</td></tr>`).join("");
    const bars = r.bars.map((b) =>
      `<div class="barcell" title="bar ${b.bar}: ${b.hits}/${b.targets}"><i style="width:${Math.round(100 * b.hits / b.targets)}%"></i></div>`).join("");
    box.innerHTML =
      `<div class="big"><span class="score">${r.score}</span><span class="stars">${stars}</span>` +
      `<span class="of">${HANDS_LABEL[attempt.hands]} · ${Math.round(attempt.tempo)} bpm</span></div>` +
      `<div class="line"><b>${r.hits} of ${r.targets}</b> notes · ${r.extra ? r.extra + " extra" : "no extra"} · ${timing}${late}</div>` +
      `<div class="bars">${bars}</div><div class="verdict">${r.verdict}</div>` +
      (rows ? `<table>${rows}</table>` : "");
  }

  loadAttempts() {
    try { return JSON.parse(localStorage.getItem(ATTEMPTS_KEY) || "{}"); } catch (_) { return {}; }
  }

  saveAttempt(r, attempt) {
    const all = this.loadAttempts();
    const list = all[this.lesson.name] || [];
    list.unshift({ when: new Date().toISOString(), hands: attempt.hands, tempo: Math.round(attempt.tempo),
      score: r.score, stars: r.stars, hits: r.hits, targets: r.targets, pass: attempt.pass || null });
    all[this.lesson.name] = list.slice(0, 50);
    try { localStorage.setItem(ATTEMPTS_KEY, JSON.stringify(all)); } catch (_) { /* ignore */ }
    this.renderAttempts();
  }

  renderAttempts() {
    const body = $("attempts").tBodies[0];
    body.replaceChildren();
    if (!this.lesson) return;
    const list = this.loadAttempts()[this.lesson.name] || [];
    const best = Math.max(0, ...list.map((a) => a.score));
    for (const a of list) {
      const tr = document.createElement("tr");
      if (a.score === best && best > 0) tr.className = "best";
      const when = new Date(a.when);
      const cells = [
        when.toLocaleDateString(undefined, { day: "numeric", month: "short" }) + " " +
          when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        a.pass ? { along: "with click", alone: "on own" }[a.pass] : ({ both: "both", right: "R", left: "L" }[a.hands] || a.hands),
        a.tempo + " bpm", String(a.score), "★".repeat(a.stars),
      ];
      for (const c of cells) { const td = document.createElement("td"); td.textContent = c; tr.appendChild(td); }
      body.appendChild(tr);
    }
  }

  /* ------------------------------------------------------------ panels */

  /* ------------------------------------------------------ practice run */

  /* Three passes over the same exercise: hear it, play it with the click,
   * play it with nothing. Passes 2 and 3 are scored and compared. */
  async startRun() {
    if (!this.lesson || this.transport.inRun) return;
    this.setMode("practice");
    this.transport.stop();
    $("result").hidden = true;
    $("practice-hint").hidden = true;
    this.run = { results: [] };
    this.showPasses(-1);
    $("btn-run").disabled = true;
    this.status("Preparing the run…", 0);
    try {
      const prepared = await this.transport.prepareRun();
      if (!prepared) throw new Error("nothing to play");
    } catch (e) {
      this.run = null;
      $("btn-run").disabled = false;
      $("passes").hidden = true;
      this.status("Could not prepare the run: " + e.message, 0);
      return;
    }
    $("btn-run").disabled = false;
    $("btn-run").hidden = true;
    $("btn-run-stop").hidden = false;
    this.status($("listen").checked ? "Run started — pass 1 of 3."
      : "Run started. Tick Listen to have the played passes scored.", 8000);
    this.transport.playRun();
  }

  stopRun() {
    if (!this.run) return;
    this.transport.stop();          // onState(false) finishes and tidies up
  }

  runPassChanged(index, passes) {
    this.finishAttempt();           // close the pass that just ended
    const pass = passes[index];
    this.showPasses(index);
    // The piano is silent from pass 2 on; the click goes with pass 3.
    this.status(`Pass ${index + 1} of 3 · ${pass.label} — ${pass.hint}`, 0);
    if (pass.scored) this.beginAttempt(pass.id);
  }

  showPasses(current) {
    const list = $("passes");
    list.hidden = false;
    list.replaceChildren();
    PASSES.forEach((p, i) => {
      const li = document.createElement("li");
      li.className = i < current ? "done" : i === current ? "now" : "todo";
      const n = document.createElement("span"); n.className = "n"; n.textContent = String(i + 1);
      const name = document.createElement("span"); name.className = "name"; name.textContent = p.label;
      const say = document.createElement("span"); say.className = "say"; say.textContent = p.hint;
      li.append(n, name, say);
      const done = (this.run && this.run.results.find((r) => r.pass === p.id)) || null;
      if (done) { const m = document.createElement("span"); m.className = "mark"; m.textContent = String(done.ranking.score); li.appendChild(m); }
      list.appendChild(li);
    });
  }

  /* Both scored passes are in: say what the pair means. */
  showRunResult() {
    const results = this.run ? this.run.results : [];
    $("btn-run").hidden = false;
    $("btn-run-stop").hidden = true;
    $("btn-run").textContent = "Run it again";
    const along = results.find((r) => r.pass === "along");
    const alone = results.find((r) => r.pass === "alone");
    this.showPasses(PASSES.length);
    if (!along && !alone) {
      this.status($("listen").checked ? "Run finished." : "Run finished — tick Listen to be scored next time.", 8000);
      this.run = null;
      return;
    }
    const card = (r, cap) => {
      if (!r) return `<div><div class="cap">${cap}</div><div class="quiet">not scored</div></div>`;
      const k = r.ranking;
      return `<div><div class="cap">${cap}</div><div class="score">${k.score}</div>` +
        `<div class="quiet">${k.hits} of ${k.targets} · ${driftText(k.drift) || "—"}</div></div>`;
    };
    $("result").hidden = false;
    $("practice-hint").hidden = true;
    $("result").innerHTML =
      `<div class="compare">${card(along, "with the click")}${card(alone, "on your own")}</div>` +
      `<div class="verdict">${runSummary(results)}</div>`;
    this.status("Run finished.", 8000);
    this.run = null;
  }

  wirePanels() {
    $("btn-run").onclick = () => this.startRun();
    $("btn-run-stop").onclick = () => this.stopRun();
    $("btn-ear-clear").onclick = () => {
      this.listener.events = []; this.earShown = 0;
      $("ear-log").tBodies[0].replaceChildren();
      this.trace.clear();
    };
    $("btn-ear-json").onclick = () => {
      const doc = { app: "Danas Ear", recorded: new Date().toISOString(), events: this.listener.events };
      download("danas-ear-notes.json", JSON.stringify(doc, null, 1), "application/json");
    };
    $("btn-ear-script").onclick = async () => {
      const text = eventsToScript(this.listener.events, this.lesson ? this.lesson.tempo : 80);
      if (!text) { this.status("No notes heard yet."); return; }
      try { await navigator.clipboard.writeText(text); this.status(`Copied ${this.listener.events.length} notes as a lesson line.`); }
      catch (_) { window.prompt("Copy the notes:", text); }
    };
  }

  /* ---------------------------------------------------------- script */

  wireScript() {
    const ta = $("script");
    ta.oninput = () => this.updateScriptState();
    ta.onkeydown = (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); this.applyScript(); } };
    $("btn-apply").onclick = () => this.applyScript();
    $("btn-tidy").onclick = () => {
      try { ta.value = JSON.stringify(JSON.parse(ta.value), null, 2); this.updateScriptState(); }
      catch (e) { this.status("Cannot tidy: " + e.message, 0); }
    };
    $("btn-script-close").onclick = () => this.showScript(false);
    $("script-overlay").addEventListener("click", (e) => { if (e.target === $("script-overlay")) this.showScript(false); });
    $("btn-help-close").onclick = () => $("help").close();
  }

  showScript(on) {
    if (on && !this.settings.get("tools.script")) return;
    $("script-overlay").hidden = !on;
    if (on) $("script").focus();
  }

  async applyScript() {
    const result = await this.applyDocument($("script").value);
    if (result && result.ok) { this.status("Script applied"); this.showScript(false); }
  }

  updateScriptState() {
    $("script-state").textContent = $("script").value !== this.cleanText ? "unsaved changes" : "";
  }

  async showHelp() {
    if (!this.settings.get("tools.help")) return;
    if (!this._help) {
      try { this._help = await fetch("assets/LESSON_FORMAT.md").then((r) => r.text()); }
      catch (_) { this._help = "Could not load the reference."; }
    }
    $("help-text").textContent = this._help;
    $("help").showModal();
  }

  /* -------------------------------------------------------- switches */

  wireSwitches() {
    const list = $("feature-list");
    const groups = {};
    for (const f of FEATURES) (groups[f.group] = groups[f.group] || []).push(f);
    for (const [group, features] of Object.entries(groups)) {
      const h = document.createElement("h4"); h.textContent = group; list.appendChild(h);
      for (const f of features) {
        const label = document.createElement("label");
        const cb = document.createElement("input");
        cb.type = "checkbox"; cb.id = "f-" + f.id; cb.checked = this.settings.get(f.id);
        cb.disabled = this.settings.locked.has(f.id);
        if (cb.disabled) label.className = "locked";
        cb.onchange = () => this.settings.set(f.id, cb.checked);
        label.append(cb, document.createTextNode(f.label));
        list.appendChild(label);
      }
    }
    const presets = $("preset");
    for (const [key, p] of Object.entries(PRESETS)) {
      const o = document.createElement("option"); o.value = key; o.textContent = p.label; presets.appendChild(o);
    }
    presets.onchange = () => {
      if (presets.value) {
        this.settings.applyPreset(presets.value);
        if (!this.settings.get("app.settings")) {
          this.status("Set up hidden. Bring it back with Ctrl+Shift+S, About → Reset app settings, or ?reset after the address.", 0);
        }
      }
      presets.value = "";
    };
    $("btn-reset-settings").onclick = () => this.settings.reset();
    $("btn-copy-link").onclick = async () => {
      const url = this.settings.shareUrl();
      try { await navigator.clipboard.writeText(url); this.status("Link copied — it opens the app with these switches fixed."); }
      catch (_) { window.prompt("Copy this link:", url); }
    };
    try { $("span").value = localStorage.getItem("dpt.span") || ""; } catch (_) { /* ignore */ }
    $("span").onchange = () => {
      const v = parseInt($("span").value, 10);
      $("span").value = v >= MIN_SPAN && v <= MAX_SPAN ? String(v) : "";
      try { localStorage.setItem("dpt.span", $("span").value); } catch (_) { /* ignore */ }
      this.buildPalms();
      this.refreshKeys();
    };
    this.settings.onChange(() => this.applySettings());
  }

  applySettings() {
    const s = this.settings;
    for (const el of document.querySelectorAll("[data-feature]")) el.hidden = !s.get(el.dataset.feature);
    for (const f of FEATURES) {
      const cb = $("f-" + f.id);
      if (cb) {
        cb.checked = s.get(f.id);
        cb.disabled = s.locked.has(f.id);
        cb.parentElement.classList.toggle("locked", cb.disabled);
      }
    }
    // A control that is switched off falls back to a neutral value.
    if (!s.get("play.tempo") && this.lesson) this.transport.updateOptions({ tempo: this.lesson.tempo });
    if (!s.get("play.hands")) this.handsChanged("both");
    if (!s.get("play.metronome")) { $("btn-metro").classList.remove("on"); this.transport.updateOptions({ metronome: false }); }
    if (!s.get("play.loop")) { $("btn-loop").classList.remove("on"); this.transport.setLoop(false); this.transport.updateOptions({ loop_bars: null }); }
    if (!s.get("play.listen") && this.mode === "practice") this.setMode("learn");
    if (!s.get("tools.ear") && this.mode === "ear") this.setMode("learn");
    if (!s.get("tools.script")) this.showScript(false);
    if (!s.get("app.settings") && !$("pane-switches").hidden) this.showPane(PANE_FOR[this.mode]);
    else if ($("pane-switches").hidden) this.showPane(PANE_FOR[this.mode]);   // panel follows the switches
    $("score").hidden = this.mode === "ear" || !s.get("view.score");
    document.querySelector(".tape-box").hidden = this.mode === "ear" || !s.get("view.lane");
    this.score.relayout(true);
    this.lane.draw();
    this.refreshKeys();
  }

  restoreSettings() {
    this.settings.locked.clear();
    this.settings.reset();
    this.showSwitches();
    this.status("All settings restored.");
  }

  /* --------------------------------------------------------- palette */

  /* Everything that is set once rather than changed while playing. Built
   * after the document loads so the examples and kept lessons are in it. */
  buildPalette() {
    const commands = [
      { where: "Mode", label: "Learn", keys: "1", run: () => this.setMode("learn") },
      { where: "Mode", label: "Practice — score what I play", feature: "play.listen", keys: "2", run: () => this.setMode("practice") },
      { where: "Practice", label: "Start a practice run (listen, play along, on your own)", feature: "play.listen",
        run: () => this.startRun() },
      { where: "Mode", label: "Ear — free listening", feature: "tools.ear", keys: "3", run: () => this.setMode("ear") },
      { where: "Lesson", label: "Edit the script", feature: "tools.script", keys: "Ctrl+E", run: () => this.showScript(true) },
      { where: "File", label: "Open a lesson file…", feature: "tools.files", run: () => $("file-input").click() },
      { where: "File", label: "New lesson from the template", feature: "tools.files", run: async () => {
        if (!this.confirmDiscard()) return;
        this.docName = "untitled";
        await this.applyDocument(JSON.stringify(TEMPLATE, null, 2), true);
        this.showScript(true);
      } },
      { where: "File", label: "Save the script as a file", feature: "tools.files", run: () =>
        download(`${this.lesson ? this.lesson.name : this.docName}.json`, $("script").value, "application/json") },
      { where: "File", label: "Keep this lesson in the browser", feature: "tools.files", run: () => {
        const name = prompt("Keep in this browser as:", this.lesson ? this.lesson.title : this.docName);
        if (!name) return;
        if (store.save(name, $("script").value)) { this.status(`Kept "${name}" in this browser`); this.buildPalette(); }
        else this.status("Could not save — browser storage is full or blocked", 0);
      } },
      { where: "Export", label: "Print · PDF", feature: "tools.print", keys: "Ctrl+P", run: () => {
        if (this.lesson) printLesson(this.bridge, this.index, this.lesson, this.settings.get("show.inferred"));
      } },
      { where: "Export", label: "Export MusicXML", feature: "tools.export", run: async () => {
        if (!this.lesson) return;
        const xml = await this.bridge.musicxml(this.index, this.settings.get("show.inferred"));
        download(`${this.lesson.name}.musicxml`, xml, "application/vnd.recordare.musicxml+xml");
        this.status("MusicXML exported — open it in MuseScore or any notation app");
      } },
      { where: "Export", label: "Render WAV", feature: "tools.export", run: async () => {
        if (!this.lesson) return;
        this.status("Rendering…", 0);
        const r = await this.transport.ensureRendered();
        download(`${this.lesson.name}_${this.transport.options.tempo}bpm.wav`, wavBlob(r.samples, r.sample_rate));
        this.status("WAV saved");
      } },
      { where: "Export", label: "Share a link to this lesson", feature: "tools.share", run: async () => {
        try {
          const url = await makeShareLink($("script").value);
          await navigator.clipboard.writeText(url);
          this.status(`Share link copied (${Math.round(url.length / 1024 * 10) / 10} KB) — it carries the lesson itself, nothing is uploaded`);
        } catch (err) { this.status("Share: " + err.message, 0); }
      } },
      { where: "Playback", label: "Count-in: one bar", feature: "play.countIn", run: () => {
        this.transport.updateOptions({ count_in_bars: 1 }); this.status("Count-in: one bar");
      } },
      { where: "Playback", label: "Count-in: two bars", feature: "play.countIn", run: () => {
        this.transport.updateOptions({ count_in_bars: 2 }); this.status("Count-in: two bars");
      } },
      { where: "Playback", label: "Count-in: none", feature: "play.countIn", run: () => {
        this.transport.updateOptions({ count_in_bars: 0 }); this.status("Count-in off");
      } },
      { where: "Playback", label: "Play the whole piece", feature: "play.loop", run: () => {
        if (!this.lesson) return;
        this.syncing = true; $("bar-from").value = 1; $("bar-to").value = this.lesson.measures; this.syncing = false;
        this.rangeChanged();
      } },
      { where: "Playback", label: "Back to the lesson tempo", feature: "play.tempo", run: () => {
        if (!this.lesson) return;
        $("tempo-bpm").value = Math.round(this.lesson.tempo);
        $("tempo-bpm").onchange();
      } },
      { where: "App", label: "Set up — feature switches", feature: "app.settings", run: () => this.showSwitches() },
      { where: "App", label: "Writing lessons — reference", feature: "tools.help", keys: "F1", run: () => this.showHelp() },
      { where: "App", label: "About Danas Tutor", run: () => {
        const s = $("splash"); s.hidden = false; s.classList.add("ready");
        s.querySelector(".splash-photo").style.display = "";
      } },
      { where: "App", label: "Danas Ear — the standalone listener", feature: "tools.ear", run: () => window.open("listen/", "_blank", "noopener") },
    ];
    for (const [i, e] of (this.examples || []).entries()) {
      commands.push({ where: "Example", label: e.title, feature: "tools.examples", run: async () => {
        if (this.confirmDiscard()) await this.loadExample(i);
      } });
    }
    for (const d of store.list()) {
      commands.push({ where: "Kept", label: d.name, feature: "tools.files", run: async () => {
        if (this.confirmDiscard()) { this.docName = d.name; await this.applyDocument(d.text, true); }
      } });
    }
    this.palette.setCommands(commands);
  }

  confirmDiscard() {
    if ($("script").value === this.cleanText) return true;
    return confirm("The script has unsaved changes. Discard them?");
  }

  /* ------------------------------------------------------- shortcuts */

  wireShortcuts() {
    $("file-input").onchange = async () => {
      const file = $("file-input").files[0];
      if (!file) return;
      if (this.confirmDiscard()) {
        const text = await file.text();
        this.docName = file.name.replace(/\.json$/i, "");
        await this.applyDocument(text, true);
      }
      $("file-input").value = "";
    };
    document.addEventListener("keydown", (e) => {
      const meta = e.ctrlKey || e.metaKey;
      const tag = (e.target.tagName || "").toLowerCase();
      const typing = tag === "input" || tag === "textarea" || tag === "select";
      if (meta && e.shiftKey && e.key.toLowerCase() === "s") { e.preventDefault(); this.restoreSettings(); return; }
      if (meta && e.key.toLowerCase() === "k") { e.preventDefault(); this.palette.toggle(); return; }
      if (meta && e.key.toLowerCase() === "e") { e.preventDefault(); this.showScript($("script-overlay").hidden); return; }
      if (meta && e.key.toLowerCase() === "p") {
        if (!this.settings.get("tools.print") || !this.lesson) return;
        e.preventDefault();
        printLesson(this.bridge, this.index, this.lesson, this.settings.get("show.inferred"));
        return;
      }
      if (e.key === "F1") { e.preventDefault(); this.showHelp(); return; }
      if (e.key === "Escape") {
        if (!$("script-overlay").hidden) this.showScript(false);
        else if (this.palette.open) this.palette.close();
        else if (!$("lesson-menu").hidden) $("lesson-menu").hidden = true;
        else this.transport.stop();
        return;
      }
      if (typing || $("help").open || this.palette.open) return;
      if (e.key === " ") { e.preventDefault(); this.transport.toggle(); }
      else if (e.key === "Home") this.transport.seek(0);
      else if (e.key === "-") this.nudgeTempo(-5);
      else if (e.key === "=" || e.key === "+") this.nudgeTempo(5);
      else if (e.key === "1") this.setMode("learn");
      else if (e.key === "2") this.setMode("practice");
      else if (e.key === "3") this.setMode("ear");
    });
    window.addEventListener("beforeunload", (e) => {
      if ($("script").value !== this.cleanText) { e.preventDefault(); e.returnValue = ""; }
    });
    window.addEventListener("resize", () => this.lane.setMapper(this.score.mapper()));
  }
}

const app = new App();
window.tutor = app;   // handy in the console
app.boot().catch((err) => { app.status("Failed to start: " + err.message, 0); console.error(err); });
