/* Danas Piano Tutor — browser app. Wires the worker (Python core), the
 * transport (Web Audio) and the views; owns the document, the feature
 * switches and every button. */

import { Bridge } from "./bridge.js";
import { Transport } from "./transport.js";
import { ScoreView } from "./score.js";
import { Keyboard, Hands, Lane, COLORS } from "./views.js";
import { handTimeline, handState, MIN_SPAN, MAX_SPAN } from "./palms.js";
import { Listener } from "./listen.js";
import { rankAttempt, describeError, midiLabel } from "./ranking.js";

const ATTEMPTS_KEY = "dpt.attempts.v1";
import { Settings, FEATURES, PRESETS } from "./settings.js";
import { download, wavBlob, makeShareLink, readShareLink, store, printLesson } from "./exports.js";

const $ = (id) => document.getElementById(id);
const HAND_COLOR = { R: COLORS.right, L: COLORS.left };
const HAND_DIM = { R: COLORS.rightDim, L: COLORS.leftDim };
const DIM_FOR = { both: [], right: ["L"], left: ["R"] };
const HANDS_LABEL = { both: "both hands", right: "right hand", left: "left hand" };
const HANDS_FOR = { both: ["R", "L"], right: ["R"], left: ["L"] };

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

class App {
  constructor() {
    this.settings = new Settings();
    this.bridge = new Bridge();
    this.transport = new Transport(this.bridge);
    this.score = new ScoreView($("score"), this.bridge, this.settings);
    this.keys = new Keyboard($("keys"), this.settings);
    this.hands = new Hands($("hands-canvas"), this.settings);
    this.lane = new Lane($("lane"), this.settings);
    this.docText = "";
    this.cleanText = "";
    this.lessons = [];
    this.index = -1;
    this.lesson = null;
    this.docName = "untitled";
    this.about = { name: "Danas Piano Tutor", version: "", engine: "", tagline: "" };
    this.syncing = false;
    this.ready = false;
  }

  /* ------------------------------------------------------------ boot */

  async boot() {
    this.wireToolbar();
    this.wireTransport();
    this.wireTabs();
    this.wireScript();
    this.wireSettings();
    this.wirePractice();
    this.wireKeyboardShortcuts();
    this.applySettings();

    this.transport.onPosition = (d) => this.positionChanged(d);
    this.transport.onState = (playing) => {
      $("btn-play").textContent = playing ? "❚❚ Pause" : "▶ Play";
      if (playing) this.beginAttempt(); else this.finishAttempt();
    };
    this.transport.onPass = () => { this.finishAttempt(); this.beginAttempt(); };
    this.transport.onMessage = (m) => this.status(m);
    this.score.onSeek = (d) => this.transport.seek(d);
    this.lane.onSeek = (d) => this.transport.seek(d);
    this.keys.onKey = (midi) => this.transport.previewNote(midi);

    fetch("assets/about.json").then((r) => r.json()).then((a) => { this.about = a; this.fillSplash(); }).catch(() => {});
    this.showSplash(this.settings.get("app.splash"));
    this.bridge.onStatus = (text, progress) => this.splashStatus(text, progress);
    this.loadExamplesIndex();
    this.refreshMine();

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
    s.classList.toggle("minimal", !withPhoto);
    if (!withPhoto) s.querySelector(".splash-photo").style.display = "none";
    s.addEventListener("click", () => this.dismissSplash());
    document.addEventListener("keydown", (e) => { if (!s.hidden && s.classList.contains("ready") && e.key !== "F1") this.dismissSplash(); });
  }

  fillSplash() {
    $("splash-name").textContent = this.about.name || "Danas Piano Tutor";
    $("splash-v").textContent = "v " + (this.about.version || "");
    $("splash-date").textContent = "Date: " + new Date().toISOString().slice(0, 10);
    $("splash-engine").textContent = "RAW engine " + (this.about.engine || "");
    $("splash-tagline").textContent = this.about.tagline || "";
  }

  splashStatus(text, progress) {
    const bar = $("splash-bar");
    if (progress >= 0) bar.style.width = Math.round(progress * 100) + "%";
    else bar.style.background = "var(--error)";
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
    if (s.hidden) return;
    if (!this.ready) return;
    s.hidden = true;
    try { this.transport.ensureContext(); } catch (_) { /* needs a gesture; play() will retry */ }
    this.score.relayout(true);
    this.keys.draw(); this.hands.draw(); this.lane.draw();
  }

  /* -------------------------------------------------------- documents */

  async loadExamplesIndex() {
    try {
      const index = await fetch("lessons/index.json").then((r) => r.json());
      this.examples = index;
      const sel = $("examples");
      for (const [i, e] of index.entries()) {
        const opt = document.createElement("option");
        opt.value = String(i);
        opt.textContent = `${e.title} (${e.lessons.length})`;
        sel.appendChild(opt);
      }
    } catch (_) { this.examples = []; }
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
      if (clean) this.cleanText = text; else this.cleanText = text;
      this.lessons = result.lessons;
      const current = this.lesson ? this.lesson.name : null;
      const list = $("lesson-list");
      list.replaceChildren();
      this.lessons.forEach((l, i) => {
        const opt = document.createElement("option");
        opt.value = String(i);
        opt.textContent = `${l.title}  ·  level ${l.level}`;
        list.appendChild(opt);
      });
      const row = Math.max(0, this.lessons.findIndex((l) => l.name === current));
      list.value = String(row);
      await this.setLesson(row);
      this.status(`${this.lessons.length} lesson(s), ${result.warnings.length} warning(s)`);
    } else {
      this.status("Script has errors — see the report", 0);
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
    this.updateTempoLabel();
    this.lane.setLesson(lesson);
    this.hands.setLesson(lesson);
    this.keys.fit(lesson);
    this.buildPalms();
    this.renderAttempts();
    $("result").hidden = true;
    this.showInfo(lesson);
    this.updateTitle();
    await this.score.setLesson(i, lesson);
    this.positionChanged(0);
  }

  showInfo(lesson) {
    const box = $("info");
    box.replaceChildren();
    const add = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; box.appendChild(e); return e; };
    add("h2", lesson.title);
    if (lesson.composer) add("div", lesson.composer, "dim");
    const table = add("table");
    for (const [k, v] of [["Key", lesson.key], ["Time", lesson.time.join("/")], ["Tempo", lesson.tempo + " bpm"], ["Bars", lesson.measures], ["Voice", lesson.voice]]) {
      const tr = document.createElement("tr");
      const td1 = document.createElement("td"); td1.className = "dim"; td1.textContent = k;
      const td2 = document.createElement("td"); const b = document.createElement("b"); b.textContent = String(v); td2.appendChild(b);
      tr.append(td1, td2); table.appendChild(tr);
    }
    add("h3", "Hand positions");
    const ul = add("ul");
    for (const h of ["R", "L"]) {
      const li = document.createElement("li");
      const name = document.createElement("b"); name.className = "hand-" + h; name.textContent = lesson.positions[h].name + ": ";
      li.append(name, document.createTextNode(lesson.positions[h].label));
      ul.appendChild(li);
    }
    if (lesson.instructions) { add("h3", "Instructions"); add("p", lesson.instructions); }
    const tips = lesson.tips.length ? lesson.tips : DEFAULT_TIPS;
    add("h3", "Posture tips");
    const tl = add("ul");
    for (const t of tips) { const li = document.createElement("li"); li.textContent = t; tl.appendChild(li); }
    if (lesson.warnings.length) {
      add("h3", "Script warnings", "warn");
      const wl = add("ul");
      for (const w of lesson.warnings) { const li = document.createElement("li"); li.className = "warn"; li.textContent = w; wl.appendChild(li); }
    }
  }

  updateTitle() {
    document.title = (this.lesson ? this.lesson.title + " — " : "") + "Danas Piano Tutor";
  }

  /* -------------------------------------------------------- position */

  positionChanged(division) {
    if (!this.lesson) return;
    this.score.setPosition(division);
    this.lane.setPosition(division);
    this.hands.setPosition(division);
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

  /* ------------------------------------------------------- practice */

  wirePractice() {
    this.listener = new Listener();
    this.attempt = null;
    $("listen").onchange = () => this.listenChanged();
    this.renderAttempts();
  }

  async listenChanged() {
    const on = $("listen").checked;
    if (on) {
      try {
        await this.listener.start(this.transport.ensureContext());
        this.status("Listening. Press Play, then play along with the metronome.", 8000);
      } catch (e) {
        $("listen").checked = false;
        this.status(e.name === "NotAllowedError" ? "Microphone access was refused — allow it in the address bar and tick Listen again."
          : "Could not open the microphone: " + e.message, 0);
        return;
      }
    } else {
      this.listener.stop();
      $("heard").textContent = "—";
    }
    // The piano is silent while listening so the microphone only hears the student.
    this.transport.updateOptions({ voice_db: on ? -100 : -12 });
  }

  beginAttempt() {
    if (!$("listen").checked || !this.listener.active || !this.lesson) return;
    this.listener.beginAttempt();
    this.attempt = { hands: this.transport.options.hands, tempo: this.transport.options.tempo, range: this.transport.sectionRange(), started: Date.now() };
    this.score.markResults(null);
  }

  hear(division) {
    // The detector reports a note ~80 ms after it starts (analysis window +
    // three confirming frames); stamp heard notes that much earlier.
    const spd = this.transport.rendered ? this.transport.rendered.seconds_per_division : 0;
    const r = this.listener.sample(spd ? division - 0.08 / spd : division);
    $("heard").innerHTML = r ? `${midiLabel(r.midi)}<small>${r.cents >= 0 ? "+" : ""}${r.cents.toFixed(0)} ¢</small>` : "—";
  }

  finishAttempt() {
    if (!this.attempt) return;
    const attempt = this.attempt;
    this.attempt = null;
    const events = this.listener.endAttempt(this.transport.position);
    const hands = HANDS_FOR[attempt.hands] || ["R", "L"];
    const ranking = rankAttempt(this.lesson, hands, events, attempt.range);
    if (!ranking.targets) return;
    // Ignore an attempt that was stopped almost at once.
    const played = ranking.results.filter((r) => r.start < this.transport.position || r.hit).length;
    if (!played && Date.now() - attempt.started < 3000) return;
    this.showResult(ranking, attempt);
    this.score.markResults(ranking);
    this.saveAttempt(ranking, attempt);
    this.showTab("practice");
  }

  showResult(r, attempt) {
    const box = $("result");
    box.hidden = false;
    const stars = "★".repeat(r.stars) + "☆".repeat(5 - r.stars);
    const timing = r.hits ? `${r.perfect} on time · ${r.good} close · ${r.hits - r.perfect - r.good} off · average ${r.meanAbs.toFixed(2)} beat${r.meanSigned > 0.05 ? " late" : r.meanSigned < -0.05 ? " early" : ""}` : "no notes matched";
    const rows = r.missed.slice(0, 12).map((m) => `<tr><td>bar ${m.bar}</td><td class="miss">${m.midis.map(midiLabel).join(" ")}</td><td>${m.wrong !== null ? "heard " + midiLabel(m.wrong) : "not heard"}</td></tr>`).join("");
    const weakBars = r.bars.filter((b) => b.hits < b.targets).map((b) => `bar ${b.bar} (${b.hits}/${b.targets})`).join(", ");
    box.innerHTML = `
      <div class="big"><span class="score">${r.score}</span><span class="stars">${stars}</span><span class="dim">${HANDS_LABEL[attempt.hands] || ""} · ${Math.round(attempt.tempo)} bpm</span></div>
      <div><b>${r.hits} of ${r.targets}</b> notes played · ${r.extra ? r.extra + " extra" : "no extra"} · ${timing}</div>
      <div>${r.verdict}</div>
      ${weakBars ? `<div class="dim">Practise: ${weakBars}</div>` : ""}
      ${rows ? `<table>${rows}${r.missed.length > 12 ? `<tr><td colspan="3" class="dim">… and ${r.missed.length - 12} more</td></tr>` : ""}</table>` : ""}`;
  }

  loadAttempts() {
    try { return JSON.parse(localStorage.getItem(ATTEMPTS_KEY) || "{}"); } catch (_) { return {}; }
  }

  saveAttempt(r, attempt) {
    const all = this.loadAttempts();
    const key = this.lesson.name;
    const list = all[key] || [];
    list.unshift({ when: new Date().toISOString(), hands: attempt.hands, tempo: Math.round(attempt.tempo), score: r.score, stars: r.stars, hits: r.hits, targets: r.targets });
    all[key] = list.slice(0, 50);
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
      const cells = [when.toLocaleDateString() + " " + when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        HANDS_LABEL[a.hands] || a.hands, a.tempo + " bpm", `${a.score} (${a.hits}/${a.targets})`, "★".repeat(a.stars)];
      for (const c of cells) { const td = document.createElement("td"); td.textContent = c; tr.appendChild(td); }
      body.appendChild(tr);
    }
  }

  /* Hand span in white keys: the Settings override, else the lesson's own. */
  spanFor(hand) {
    const override = parseInt($("span").value, 10);
    if (override >= MIN_SPAN && override <= MAX_SPAN) return override;
    return (this.lesson && this.lesson.span && this.lesson.span[hand]) || MIN_SPAN;
  }

  buildPalms() {
    this.palms = this.lesson ? { R: handTimeline(this.lesson, "R", this.spanFor("R")), L: handTimeline(this.lesson, "L", this.spanFor("L")) } : null;
  }

  refreshKeys(division = this.transport.position) {
    const lesson = this.lesson;
    if (!lesson) { this.keys.update({}, {}, []); return; }
    const hands = HANDS_FOR[this.transport.options.hands] || ["R", "L"];
    const dim = ["R", "L"].filter((h) => !hands.includes(h));
    const marks = {};
    if (this.settings.get("show.restingHands")) {
      for (const h of hands) {
        const p = lesson.positions[h];
        if (!p.keys) continue;
        p.keys.forEach((midi, i) => { marks[midi] = [HAND_DIM[h], p.fingers[i] ? String(p.fingers[i]) : ""]; });
      }
    }
    for (const n of lesson.notes) {
      if (hands.includes(n.hand) && n.start <= division && division < n.start + n.duration) marks[n.midi] = [HAND_COLOR[n.hand], n.shown ? String(n.shown) : ""];
    }
    const palms = {};
    if (this.palms && this.settings.get("show.palms")) {
      const d = Math.max(0, division);
      for (const h of ["R", "L"]) palms[h] = handState(this.palms[h], d, lesson.beat_divisions);
    }
    this.keys.update(marks, palms, dim);
  }

  /* ------------------------------------------------------- transport */

  wireTransport() {
    $("btn-play").onclick = () => this.transport.toggle();
    $("btn-stop").onclick = () => this.transport.stop();
    $("tempo-slider").oninput = () => {
      if (this.syncing || !this.lesson) return;
      const bpm = Math.max(20, Math.min(300, Math.round(this.lesson.tempo * $("tempo-slider").value / 100)));
      this.syncing = true; $("tempo-bpm").value = bpm; this.syncing = false;
      this.updateTempoLabel(); this.transport.updateOptions({ tempo: bpm });
    };
    $("tempo-bpm").onchange = () => {
      if (this.syncing || !this.lesson) return;
      const bpm = Math.max(20, Math.min(300, parseInt($("tempo-bpm").value, 10) || this.lesson.tempo));
      $("tempo-bpm").value = bpm;
      this.syncing = true; $("tempo-slider").value = Math.max(25, Math.min(150, Math.round(100 * bpm / this.lesson.tempo))); this.syncing = false;
      this.updateTempoLabel(); this.transport.updateOptions({ tempo: bpm });
    };
    $("hands").onchange = () => this.handsChanged();
    $("metronome").onchange = () => this.transport.updateOptions({ metronome: $("metronome").checked });
    $("count-in").onchange = () => this.transport.updateOptions({ count_in_bars: parseInt($("count-in").value, 10) });
    $("loop").onchange = () => this.transport.setLoop($("loop").checked);
    $("bar-from").onchange = $("bar-to").onchange = () => this.rangeChanged();
    $("btn-whole").onclick = () => { this.syncing = true; $("bar-from").value = 1; $("bar-to").value = this.lesson.measures; this.syncing = false; this.rangeChanged(); };
  }

  updateTempoLabel() {
    if (!this.lesson) return;
    $("tempo-pct").textContent = Math.round(100 * $("tempo-bpm").value / this.lesson.tempo) + "%";
  }

  nudgeTempo(delta) {
    if (!this.settings.get("play.tempo")) return;
    $("tempo-bpm").value = parseInt($("tempo-bpm").value, 10) + delta;
    $("tempo-bpm").onchange();
  }

  handsChanged() {
    const mode = $("hands").value;
    this.transport.updateOptions({ hands: mode });
    const dim = DIM_FOR[mode];
    this.score.setDimHands(dim); this.lane.setDim(dim); this.hands.setDim(dim);
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

  /* ---------------------------------------------------------- toolbar */

  wireToolbar() {
    $("btn-open").onclick = () => $("file-input").click();
    $("file-input").onchange = async () => {
      const file = $("file-input").files[0];
      if (!file) return;
      if (!this.confirmDiscard()) return;
      const text = await file.text();
      this.docName = file.name.replace(/\.json$/i, "");
      await this.applyDocument(text, true);
      $("file-input").value = "";
    };
    $("btn-new").onclick = async () => {
      if (!this.confirmDiscard()) return;
      this.docName = "untitled";
      await this.applyDocument(JSON.stringify(TEMPLATE, null, 2), true);
      this.showTab("script");
    };
    $("btn-save").onclick = () => download(`${this.lesson ? this.lesson.name : this.docName}.json`, $("script").value, "application/json");
    $("btn-keep").onclick = () => {
      const name = prompt("Keep in this browser as:", this.lesson ? this.lesson.title : this.docName);
      if (!name) return;
      if (store.save(name, $("script").value)) { this.status(`Kept "${name}" in this browser`); this.refreshMine(); }
      else this.status("Could not save — browser storage is full or blocked", 0);
    };
    $("mine").onchange = async () => {
      const name = $("mine").value;
      $("mine").value = "";
      if (!name) return;
      if (name.startsWith(" del:")) {
        const real = name.slice(5);
        if (confirm(`Delete "${real}" from this browser?`)) { store.remove(real); this.refreshMine(); }
        return;
      }
      const doc = store.list().find((d) => d.name === name);
      if (doc && this.confirmDiscard()) { this.docName = name; await this.applyDocument(doc.text, true); }
    };
    $("examples").onchange = async () => {
      const i = $("examples").value;
      $("examples").value = "";
      if (i !== "" && this.confirmDiscard()) await this.loadExample(parseInt(i, 10));
    };
    $("btn-musicxml").onclick = async () => {
      if (!this.lesson) return;
      const xml = await this.bridge.musicxml(this.index, this.settings.get("show.inferred"));
      download(`${this.lesson.name}.musicxml`, xml, "application/vnd.recordare.musicxml+xml");
      this.status("MusicXML exported — open it in MuseScore or any notation app");
    };
    $("btn-wav").onclick = async () => {
      if (!this.lesson) return;
      const r = await this.transport.ensureRendered();
      download(`${this.lesson.name}_${this.transport.options.tempo}bpm.wav`, wavBlob(r.samples, r.sample_rate));
    };
    $("btn-print").onclick = () => { if (this.lesson) printLesson(this.bridge, this.index, this.lesson, this.settings.get("show.inferred")); };
    $("btn-share").onclick = async () => {
      try {
        const url = await makeShareLink($("script").value);
        await navigator.clipboard.writeText(url);
        this.status(`Share link copied (${Math.round(url.length / 1024 * 10) / 10} KB) — it carries the whole lesson, nothing is uploaded`);
      } catch (err) { this.status("Share: " + err.message, 0); }
    };
    $("btn-help").onclick = () => this.showHelp();
    $("btn-help-close").onclick = () => $("help").close();
    $("btn-about").onclick = () => { const s = $("splash"); s.hidden = false; s.classList.add("ready"); s.querySelector(".splash-photo").style.display = ""; };
    $("btn-reset-app").onclick = (e) => { e.stopPropagation(); this.restoreSettings(); this.dismissSplash(); };
  }

  refreshMine() {
    const sel = $("mine");
    sel.replaceChildren();
    const head = document.createElement("option"); head.value = ""; head.textContent = "My lessons…"; sel.appendChild(head);
    for (const d of store.list()) {
      const opt = document.createElement("option"); opt.value = d.name; opt.textContent = d.name; sel.appendChild(opt);
      const del = document.createElement("option"); del.value = " del:" + d.name; del.textContent = "   ✕ delete " + d.name; sel.appendChild(del);
    }
  }

  async showHelp() {
    $("help").showModal();
    if (!this._helpLoaded) {
      try { $("help-text").textContent = await fetch("assets/LESSON_FORMAT.md").then((r) => r.text()); this._helpLoaded = true; }
      catch (_) { $("help-text").textContent = "The reference could not be loaded."; }
    }
  }

  confirmDiscard() {
    if ($("script").value === this.cleanText) return true;
    return confirm("The script has edits that were not applied or saved. Discard them?");
  }

  /* -------------------------------------------------------------- tabs */

  wireTabs() {
    for (const b of document.querySelectorAll(".tabs button")) b.onclick = () => this.showTab(b.dataset.tab);
    $("lesson-list").onchange = () => this.setLesson(parseInt($("lesson-list").value, 10));
  }

  showTab(name) {
    for (const b of document.querySelectorAll(".tabs button")) b.classList.toggle("active", b.dataset.tab === name);
    for (const t of document.querySelectorAll(".tab")) t.hidden = t.id !== "tab-" + name;
  }

  wireScript() {
    const ta = $("script");
    ta.oninput = () => this.updateScriptState();
    ta.onkeydown = (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); this.applyScript(); } };
    $("btn-apply").onclick = () => this.applyScript();
    $("btn-tidy").onclick = () => {
      try { ta.value = JSON.stringify(JSON.parse(ta.value), null, 2); this.updateScriptState(); }
      catch (err) { $("report").textContent = "error: not valid JSON: " + err.message; }
    };
  }

  async applyScript() {
    const text = $("script").value;
    if (text.length > 256 * 1024) { this.status("Document is over 256 KB", 0); return; }
    await this.applyDocument(text);
  }

  updateScriptState() {
    $("script-state").textContent = $("script").value === this.cleanText ? "" : "edited — press Apply";
  }

  /* ---------------------------------------------------------- settings */

  wireSettings() {
    const list = $("feature-list");
    const groups = {};
    for (const f of FEATURES) (groups[f.group] ||= []).push(f);
    for (const [group, items] of Object.entries(groups)) {
      const h = document.createElement("h4"); h.textContent = group; list.appendChild(h);
      for (const f of items) {
        const label = document.createElement("label");
        const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = this.settings.get(f.id); cb.dataset.id = f.id;
        if (this.settings.locked.has(f.id)) { cb.disabled = true; label.className = "locked"; label.title = "Fixed by the link that opened the app"; }
        cb.onchange = () => this.settings.set(f.id, cb.checked);
        label.append(cb, " " + f.label);
        list.appendChild(label);
      }
    }
    const presets = $("preset");
    for (const [key, p] of Object.entries(PRESETS)) { const o = document.createElement("option"); o.value = key; o.textContent = p.label; presets.appendChild(o); }
    presets.onchange = () => {
      if (presets.value) {
        this.settings.applyPreset(presets.value);
        if (!this.settings.get("app.settings")) this.status("Settings tab hidden. Bring it back with Ctrl+Shift+S, About → Reset app settings, or ?reset after the address.");
      }
      presets.value = "";
    };
    $("btn-reset-settings").onclick = () => this.settings.reset();
    try { $("span").value = localStorage.getItem("dpt.span") || ""; } catch (_) { /* ignore */ }
    $("span").onchange = () => {
      const v = parseInt($("span").value, 10);
      $("span").value = v >= MIN_SPAN && v <= MAX_SPAN ? String(v) : "";
      try { localStorage.setItem("dpt.span", $("span").value); } catch (_) { /* ignore */ }
      this.buildPalms();
      this.refreshKeys();
    };
    $("btn-settings-link").onclick = async () => {
      try { await navigator.clipboard.writeText(this.settings.shareUrl()); this.status("Link with these switches copied"); }
      catch (_) { this.status(this.settings.shareUrl(), 0); }
    };
    this.settings.onChange((id) => this.applySettings(id));
  }

  applySettings(changed = null) {
    const s = this.settings;
    for (const el of document.querySelectorAll("[data-feature]")) el.hidden = !s.get(el.dataset.feature);
    for (const cb of document.querySelectorAll("#feature-list input")) cb.checked = s.get(cb.dataset.id);
    // A disabled control falls back to its neutral value.
    if (!s.get("play.tempo") && this.lesson) { $("tempo-bpm").value = Math.round(this.lesson.tempo); $("tempo-bpm").onchange(); }
    if (!s.get("play.hands") && $("hands").value !== "both") { $("hands").value = "both"; this.handsChanged(); }
    this.transport.updateOptions({
      metronome: s.get("play.metronome") && $("metronome").checked,
      count_in_bars: s.get("play.countIn") ? parseInt($("count-in").value, 10) : 0,
    });
    if (!s.get("play.loop")) { this.transport.setLoop(false); if (this.lesson && this.transport.options.loop_bars) $("btn-whole").onclick(); }
    else this.transport.setLoop($("loop").checked);
    // Nothing left in the right column: give the whole width to the views.
    const tabsVisible = Array.from(document.querySelectorAll(".tabs button")).some((b) => !b.hidden);
    document.querySelector(".right").hidden = !s.get("view.hands") && !tabsVisible;
    // Tabs: if the active tab was switched off, fall back to the first visible one.
    const active = document.querySelector(".tabs button.active");
    if (!active || active.hidden) {
      const first = Array.from(document.querySelectorAll(".tabs button")).find((b) => !b.hidden);
      if (first) this.showTab(first.dataset.tab); else for (const t of document.querySelectorAll(".tab")) t.hidden = true;
    }
    if (changed === "show.inferred" || changed === null) this.score.relayout(true);
    this.refreshKeys(); this.keys.draw(); this.hands.draw(); this.lane.draw();
    if (changed === null || changed.startsWith("view.")) setTimeout(() => this.score.relayout(true), 50);
  }

  /* ------------------------------------------------------- shortcuts */

  /* Every part of the app visible again, whatever was switched off or locked. */
  restoreSettings() {
    this.settings.locked.clear();
    this.settings.reset();
    this.showTab("settings");
    this.status("All settings restored.");
  }

  wireKeyboardShortcuts() {
    document.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === "s") { e.preventDefault(); this.restoreSettings(); return; }
      const tag = (e.target.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select" || $("help").open) {
        if (e.key === "F1") { e.preventDefault(); this.showHelp(); }
        return;
      }
      if (e.key === " ") { e.preventDefault(); this.transport.toggle(); }
      else if (e.key === "Escape") this.transport.stop();
      else if (e.key === "Home") this.transport.seek(0);
      else if (e.key === "-") this.nudgeTempo(-5);
      else if (e.key === "=" || e.key === "+") this.nudgeTempo(5);
      else if (e.key === "0" && this.lesson) { $("tempo-bpm").value = Math.round(this.lesson.tempo); $("tempo-bpm").onchange(); }
      else if (e.key === "F1") { e.preventDefault(); this.showHelp(); }
    });
    window.addEventListener("beforeunload", (e) => { if ($("script").value !== this.cleanText) { e.preventDefault(); e.returnValue = ""; } });
  }
}

const DEFAULT_TIPS = [
  "Sit tall on the front half of the bench, feet flat, elbows level with the keys.",
  "Curve your fingers as if holding a ball; play on the fingertips.",
  "Keep wrists level and relaxed — no sagging, no lifting.",
  "Thumb is finger 1, little finger is 5. Say the numbers as you play.",
  "Practise slowly with the metronome; speed comes after accuracy.",
];

const app = new App();
window.tutor = app;   // handy in the console
app.boot().catch((err) => { app.status("Failed to start: " + err.message, 0); console.error(err); });
