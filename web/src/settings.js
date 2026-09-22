/* Feature switches. Every optional part of the app is listed here and can be
 * turned off in the Settings tab; the choice is kept in localStorage and can
 * also be fixed from the URL for handing a student a reduced app:
 *     ?off=tools.script,play.tempo      ?preset=student      ?reset  (forget saved choices)
 */

export const FEATURES = [
  // What is on screen
  { id: "view.score",         group: "Views",    label: "Sheet music",                        default: true },
  { id: "view.lane",          group: "Views",    label: "Note lane (scrolling notes)",         default: true },
  { id: "view.keyboard",      group: "Views",    label: "Keyboard",                           default: true },
  { id: "view.hands",         group: "Views",    label: "Hand diagram",                       default: true },
  { id: "view.lessonPanel",   group: "Views",    label: "Lesson panel (list, notes, tips)",    default: true },
  { id: "view.follow",        group: "Views",    label: "Score scrolls to follow playback",    default: true },
  // How fingering and positions are shown
  { id: "show.inferred",      group: "Fingering", label: "Grey inferred finger numbers on the score", default: true },
  { id: "show.keyFingers",    group: "Fingering", label: "Finger numbers on the keys",          default: true },
  { id: "show.restingHands",  group: "Fingering", label: "Resting hand position on the keys",   default: true },
  { id: "show.nextFinger",    group: "Fingering", label: "Outline the next finger in the hand diagram", default: true },
  { id: "show.laneFingers",   group: "Fingering", label: "Finger numbers in the note lane",     default: true },
  // Playback controls
  { id: "play.tempo",         group: "Playback", label: "Tempo control (off = lesson tempo only)", default: true },
  { id: "play.hands",         group: "Playback", label: "Hands selector (right / left alone)",  default: true },
  { id: "play.metronome",     group: "Playback", label: "Metronome",                           default: true },
  { id: "play.countIn",       group: "Playback", label: "Count-in",                            default: true },
  { id: "play.loop",          group: "Playback", label: "Practice loop and bar range",          default: true },
  { id: "play.clickKeys",     group: "Playback", label: "Click a key to hear it",              default: true },
  { id: "play.seek",          group: "Playback", label: "Click a bar to jump there",           default: true },
  // Tools
  { id: "tools.script",       group: "Tools",    label: "Script editor (write and edit lessons)", default: true },
  { id: "tools.examples",     group: "Tools",    label: "Examples menu",                       default: true },
  { id: "tools.files",        group: "Tools",    label: "Open / save lesson files, My lessons", default: true },
  { id: "tools.share",        group: "Tools",    label: "Share link",                          default: true },
  { id: "tools.export",       group: "Tools",    label: "Export MusicXML and WAV",             default: true },
  { id: "tools.print",        group: "Tools",    label: "Print / save as PDF",                 default: true },
  { id: "tools.help",         group: "Tools",    label: "Help (scripting reference)",          default: true },
  { id: "tools.ear",          group: "Tools",    label: "Ear button (opens the microphone pitch listener)", default: true },
  // App
  { id: "app.splash",         group: "App",      label: "Welcome screen at start",             default: true },
  { id: "app.settings",       group: "App",      label: "Settings tab (off hides this panel — bring it back with Ctrl+Shift+S, About → Reset app settings, or ?reset in the address bar)", default: true },
];

export const PRESETS = {
  everything: { label: "Everything", off: [] },
  student: {
    label: "Student — practise only",
    off: ["tools.script", "tools.files", "tools.share", "tools.export", "show.inferred", "app.splash"],
  },
  kiosk: {
    label: "Kiosk — listen and watch",
    off: ["tools.script", "tools.files", "tools.share", "tools.export", "tools.print", "tools.help", "tools.ear",
          "play.tempo", "play.loop", "play.countIn", "play.clickKeys", "view.lessonPanel", "app.settings"],
  },
  teacher: {
    label: "Teacher — everything, no splash",
    off: ["app.splash"],
  },
};

const KEY = "dpt.settings.v1";

export class Settings {
  constructor() {
    this.values = {};
    for (const f of FEATURES) this.values[f.id] = f.default;
    this.listeners = new Set();
    this.locked = new Set();     // ids fixed by the URL
    this.load();
    this.applyUrl();
  }

  load() {
    try {
      const saved = JSON.parse(localStorage.getItem(KEY) || "{}");
      for (const f of FEATURES) if (typeof saved[f.id] === "boolean") this.values[f.id] = saved[f.id];
    } catch (_) { /* private mode or blocked storage: defaults */ }
  }

  save() {
    try { localStorage.setItem(KEY, JSON.stringify(this.values)); } catch (_) { /* ignore */ }
  }

  applyUrl() {
    const params = new URLSearchParams(location.search);
    if (params.has("reset")) {           // the way back out of a hidden Settings tab
      try { localStorage.removeItem(KEY); } catch (_) { /* ignore */ }
      for (const f of FEATURES) this.values[f.id] = f.default;
      history.replaceState(null, "", location.pathname + location.hash);
      return;
    }
    const preset = params.get("preset");
    if (preset && PRESETS[preset]) {
      this.applyPreset(preset, false);
      for (const id of PRESETS[preset].off) this.locked.add(id);
    }
    for (const id of (params.get("off") || "").split(",").filter(Boolean)) {
      if (id in this.values) { this.values[id] = false; this.locked.add(id); }
    }
    for (const id of (params.get("on") || "").split(",").filter(Boolean)) {
      if (id in this.values) { this.values[id] = true; this.locked.add(id); }
    }
  }

  get(id) { return this.values[id] !== false; }

  set(id, on) {
    if (!(id in this.values) || this.values[id] === !!on) return;
    this.values[id] = !!on;
    this.save();
    this.emit(id);
  }

  applyPreset(name, persist = true) {
    const preset = PRESETS[name];
    if (!preset) return;
    for (const f of FEATURES) this.values[f.id] = !preset.off.includes(f.id);
    if (persist) this.save();
    this.emit(null);
  }

  reset() {
    for (const f of FEATURES) this.values[f.id] = f.default;
    this.save();
    this.emit(null);
  }

  onChange(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
  emit(id) { for (const fn of this.listeners) fn(id); }

  /* A link that opens the app with the current switches fixed. */
  shareUrl() {
    const off = FEATURES.filter((f) => !this.get(f.id)).map((f) => f.id);
    const url = new URL(location.href);
    url.search = off.length ? "?off=" + off.join(",") : "";
    url.hash = "";
    return url.href;
  }
}
