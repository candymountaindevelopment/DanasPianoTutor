/* Engraved score as SVG, built from the layout JSON produced by
 * raw/teach/engrave.py. Coordinates stay in staff spaces (the SVG viewBox is
 * in sp), so one drawing serves the screen at any zoom and the printed page.
 *
 * Music glyphs come from Bravura (SMuFL): glyph origins sit on the staff
 * line they belong to, so a G clef is simply a text node at the G line. */

const SVG_NS = "http://www.w3.org/2000/svg";
const NOTEHEAD_W = 1.18;          // Bravura noteheadBlack advance width in staff spaces
const STAFF_HEIGHT = 4.0;

const GLYPH = {
  treble_clef: "", bass_clef: "",
  sharp: "", flat: "", natural: "",
  "♯": "", "♭": "", "♮": "",
  noteWhole: "", noteHalf: "", noteBlack: "", dot: "",
  flagUp: ["", "", "", ""], flagDown: ["", "", "", ""],
  rest: { whole: "", half: "", quarter: "", eighth: "", "16th": "", "32nd": "" },
  digit: (d) => String.fromCharCode(0xE080 + d),
};

function el(name, attrs, text) {
  const node = document.createElementNS(SVG_NS, name);
  for (const k in attrs) if (attrs[k] !== undefined && attrs[k] !== null) node.setAttribute(k, attrs[k]);
  if (text !== undefined) node.textContent = text;
  return node;
}

function music(x, y, glyph, extra = {}) {
  return el("text", { x, y, class: "music", ...extra }, glyph);
}

/* Cursor x inside a measure: linear between onsets, like Measure.x_at. */
export function measureX(measure, division) {
  const pts = measure.slots.concat([[measure.end, measure.x1]]);
  if (!pts.length || division <= pts[0][0]) return pts.length ? pts[0][1] : measure.x0;
  for (let i = 0; i < pts.length - 1; i++) {
    const [d0, x0] = pts[i], [d1, x1] = pts[i + 1];
    if (d0 <= division && division < d1) return x0 + (x1 - x0) * ((division - d0) / Math.max(1, d1 - d0));
  }
  return measure.x1;
}

export function systemAt(layout, division) {
  const systems = layout.pages.flatMap((p) => p.systems);
  for (const s of systems) if (s.measures.length && division >= s.measures[0].start && division < s.measures[s.measures.length - 1].end) return s;
  return null;
}

/* Draw one page into an <svg>. `opts.print` uses black ink; `opts.interactive`
 * adds data attributes and click targets for the practice view. */
export function renderPage(page, opts = {}) {
  const svg = el("svg", {
    xmlns: SVG_NS, viewBox: `0 0 ${page.width} ${page.height}`,
    class: "score" + (opts.print ? " print" : ""), preserveAspectRatio: "xMinYMin meet",
  });
  if (!opts.print) svg.appendChild(el("rect", { x: 0, y: 0, width: page.width, height: page.height, class: "bg" }));
  for (const t of page.texts) svg.appendChild(textNode(t));
  for (const system of page.systems) svg.appendChild(renderSystem(system, opts));
  return svg;
}

function textNode(t) {
  const size = t.style === "small" ? t.size : t.size * 1.15;
  const anchor = t.align === "center" ? "middle" : t.align === "right" ? "end" : "start";
  return el("text", {
    x: t.x, y: t.y, "font-size": size, "text-anchor": anchor,
    class: "label " + (t.style || "normal"),
  }, t.text);
}

function renderSystem(system, opts) {
  const g = el("g", { class: "system", "data-start": system.measures[0]?.start ?? 0, "data-end": system.measures.at(-1)?.end ?? 0 });
  const tTop = system.y + 5.0, bTop = system.y + 16.0;   // TREBLE_TOP / BASS_TOP from engrave.py
  const y0 = tTop, y1 = bTop + STAFF_HEIGHT;
  // Staff lines.
  for (const top of [tTop, bTop]) for (let i = 0; i < 5; i++) {
    g.appendChild(el("line", { x1: system.x0, x2: system.x1, y1: top + i, y2: top + i, class: "staff" }));
  }
  // Left bracket and bar lines.
  g.appendChild(el("line", { x1: system.x0, x2: system.x0, y1: y0, y2: y1, class: "bar" }));
  g.appendChild(el("line", { x1: system.x0 - 0.5, x2: system.x0 - 0.5, y1: y0, y2: y1, class: "brace" }));
  system.barlines.forEach((x, i) => {
    const last = i === system.barlines.length - 1;
    if (last && system.final) {
      g.appendChild(el("line", { x1: x - 0.55, x2: x - 0.55, y1: y0, y2: y1, class: "bar" }));
      g.appendChild(el("line", { x1: x - 0.1, x2: x - 0.1, y1: y0, y2: y1, class: "bar thick" }));
    } else {
      g.appendChild(el("line", { x1: x, x2: x, y1: y0, y2: y1, class: "bar" }));
    }
  });
  // Click targets per measure (behind everything else).
  if (opts.interactive) {
    for (const m of system.measures) {
      g.appendChild(el("rect", { x: m.x0, y: system.y, width: m.x1 - m.x0, height: y1 + 5 - system.y,
        class: "measure-hit", "data-measure": m.number, "data-start": m.start }));
    }
  }
  for (const s of system.symbols) g.appendChild(symbolNode(s));
  for (const t of system.texts) g.appendChild(textNode(t));
  // Cursor placeholder (moved by the view).
  if (opts.interactive) g.appendChild(el("line", { class: "cursor", x1: 0, x2: 0, y1: tTop - 2, y2: y1 + 2, visibility: "hidden" }));
  for (const r of system.rests) g.appendChild(restNode(r));
  for (const s of system.stems) g.appendChild(stemNode(s));
  for (const h of system.noteheads) g.appendChild(headNode(h, opts));
  for (const t of system.ties) g.appendChild(tieNode(t));
  for (const f of system.fingers) {
    g.appendChild(el("text", { x: f.x, y: f.y, "font-size": 1.44, "text-anchor": "middle", "dominant-baseline": "central",
      class: "finger " + (f.written ? "written" : "inferred") + " hand-" + f.hand }, String(f.number)));
  }
  return g;
}

function symbolNode(s) {
  switch (s.kind) {
    case "treble_clef": return music(s.x, s.y + 3.0, GLYPH.treble_clef);
    case "bass_clef": return music(s.x + 0.3, s.y + 1.0, GLYPH.bass_clef);
    case "sharp": case "flat": case "natural": return music(s.x, s.y, GLYPH[s.kind]);
    case "digit": return music(s.x, s.y, GLYPH.digit(parseInt(s.text, 10) || 0));
    default: return el("g");
  }
}

function restNode(r) {
  let y = r.y;
  if (r.type_name === "whole" && !r.whole_measure) y -= 1.0;
  const g = el("g", { class: "rest" });
  g.appendChild(music(r.x - 0.5, y, GLYPH.rest[r.type_name] || GLYPH.rest.quarter));
  for (let i = 0; i < (r.dots || 0); i++) g.appendChild(music(r.x + 0.9 + i * 0.5, y - 0.5, GLYPH.dot));
  return g;
}

function stemNode(s) {
  const g = el("g", { class: "stem" });
  g.appendChild(el("line", { x1: s.x, x2: s.x, y1: s.y0, y2: s.y1 }));
  if (s.flags) {
    const glyphs = s.up ? GLYPH.flagUp : GLYPH.flagDown;
    g.appendChild(music(s.x, s.y1, glyphs[Math.min(3, s.flags)]));
  }
  return g;
}

function headNode(h, opts) {
  const g = el("g", { class: "note hand-" + h.hand, "data-start": h.start, "data-end": h.end, "data-midi": h.midi });
  for (const ly of h.ledger) g.appendChild(el("line", { x1: h.x - 0.9, x2: h.x + 0.9, y1: ly, y2: ly, class: "ledger" }));
  const glyph = h.filled ? GLYPH.noteBlack : isWhole(h) ? GLYPH.noteWhole : GLYPH.noteHalf;
  g.appendChild(music(h.x - NOTEHEAD_W / 2, h.y, glyph, { class: "music head" }));
  for (let i = 0; i < (h.dots || 0); i++) {
    g.appendChild(music(h.x + 0.9 + i * 0.5, h.y + (h.on_line ? -0.5 : 0), GLYPH.dot));
  }
  if (h.accidental) g.appendChild(music(h.x - 0.85, h.y, GLYPH[h.accidental] || h.accidental, { "text-anchor": "end" }));
  return g;
}

/* A hollow head with no stem is a whole note; the layout carries no explicit
 * type, but a stem is emitted for everything except wholes, so use duration. */
function isWhole(h) { return (h.piece_end - h.piece_start) >= 32; }

function tieNode(t) {
  const d = t.below ? 1 : -1;
  const y0 = t.y0 + 0.55 * d, y1 = t.y1 + 0.55 * d;
  const dx = t.x1 - t.x0;
  const bulge = Math.min(1.1, 0.25 * Math.abs(dx) + 0.4) * d;
  const path = `M ${t.x0} ${y0} C ${t.x0 + dx * 0.3} ${y0 + bulge}, ${t.x0 + dx * 0.7} ${y1 + bulge}, ${t.x1} ${y1} ` +
               `C ${t.x0 + dx * 0.7} ${y1 + bulge * 0.7}, ${t.x0 + dx * 0.3} ${y0 + bulge * 0.7}, ${t.x0} ${y0} Z`;
  return el("path", { d: path, class: "tie" });
}

/* ------------------------------------------------------------ the view */

export class ScoreView {
  constructor(container, bridge, settings) {
    this.container = container;
    this.bridge = bridge;
    this.settings = settings;
    this.index = null;
    this.lesson = null;
    this.layout = null;
    this.scale = 8.0;            // px per staff space
    this.position = 0;
    this.dimHands = [];
    this.onSeek = null;
    this.svg = null;
    this.heads = [];
    this.cursors = [];
    this._pending = 0;
    this._lastWidth = 0;
    this._ro = new ResizeObserver(() => this._maybeRelayout());
    this._ro.observe(container);
    container.addEventListener("click", (e) => {
      const hit = e.target.closest && e.target.closest(".measure-hit");
      if (hit && this.onSeek && this.settings.get("play.seek")) this.onSeek(parseFloat(hit.dataset.start));
    });
    container.addEventListener("wheel", (e) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      this.setScale(this.scale + (e.deltaY < 0 ? 0.5 : -0.5));
    }, { passive: false });
  }

  async setLesson(index, lesson) {
    this.index = index;
    this.lesson = lesson;
    this.position = 0;
    await this.relayout(true);
  }

  setScale(scale) {
    this.scale = Math.max(4, Math.min(16, scale));
    this.relayout(true);
  }

  _maybeRelayout() {
    const w = this.container.clientWidth;
    if (w && Math.abs(w - this._lastWidth) > 2) this.relayout();
  }

  async relayout(force = false) {
    if (this.index === null) { this.container.replaceChildren(); return; }
    const width = this.container.clientWidth || 800;
    if (!force && width === this._lastWidth) return;
    this._lastWidth = width;
    const gen = ++this._pending;
    const widthSp = Math.max(60, width / this.scale);
    const layout = await this.bridge.engrave(this.index, widthSp, null, this.settings.get("show.inferred"), 2.0);
    if (gen !== this._pending) return;
    this.layout = layout;
    const page = layout.pages[0];
    const svg = renderPage(page, { interactive: true });
    svg.setAttribute("width", Math.round(page.width * this.scale));
    svg.setAttribute("height", Math.round(page.height * this.scale));
    this.container.replaceChildren(svg);
    this.svg = svg;
    this.heads = Array.from(svg.querySelectorAll(".note")).map((node) => ({
      node, start: +node.dataset.start, end: +node.dataset.end, hand: node.classList.contains("hand-R") ? "R" : "L",
    }));
    this.cursors = Array.from(svg.querySelectorAll(".system")).map((node, i) => ({ node: node.querySelector(".cursor"), system: page.systems[i] }));
    this.applyDim();
    this.setPosition(this.position, true);
    this.markResults(this.results);
  }

  /* Colour missed targets (red) and wrong notes (amber) after an attempt. */
  markResults(ranking) {
    this.results = ranking || null;
    if (!this.heads) return;
    for (const h of this.heads) h.node.classList.remove("miss", "wrong");
    if (!ranking) return;
    for (const r of ranking.missed) {
      for (const h of this.heads) {
        if (h.start === r.start && r.midis.includes(+h.node.dataset.midi)) h.node.classList.add(r.wrong !== null ? "wrong" : "miss");
      }
    }
  }

  setDimHands(hands) { this.dimHands = hands; this.applyDim(); }

  applyDim() {
    if (!this.svg) return;
    this.svg.classList.toggle("dim-R", this.dimHands.includes("R"));
    this.svg.classList.toggle("dim-L", this.dimHands.includes("L"));
  }

  setPosition(division, force = false) {
    this.position = division;
    if (!this.svg) return;
    for (const h of this.heads) h.node.classList.toggle("on", division >= h.start && division < h.end);
    let activeSystem = null;
    for (const c of this.cursors) {
      const s = c.system;
      const inside = division >= 0 && s.measures.length && division >= s.measures[0].start && division < s.measures.at(-1).end;
      if (!c.node) continue;
      if (inside) {
        let x = s.x1;
        for (const m of s.measures) if (division >= m.start && division < m.end) { x = measureX(m, division); break; }
        c.node.setAttribute("x1", x); c.node.setAttribute("x2", x);
        c.node.setAttribute("visibility", "visible");
        activeSystem = s;
      } else {
        c.node.setAttribute("visibility", "hidden");
      }
    }
    if (activeSystem && this.settings.get("view.follow")) this.scrollTo(activeSystem, force);
  }

  scrollTo(system, force) {
    const first = this.layout && this.layout.pages[0].systems[0] === system;
    const top = first ? 0 : (system.y - 1) * this.scale, bottom = (system.y + 26) * this.scale;
    const box = this.container;
    if (force || top < box.scrollTop || bottom > box.scrollTop + box.clientHeight) {
      box.scrollTo({ top: Math.max(0, top - 12), behavior: force ? "auto" : "smooth" });
    }
  }
}
