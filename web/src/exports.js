/* Downloads, share links, "My lessons" storage and printing. Everything stays
 * in the browser: files are Blobs, share links live in the URL fragment,
 * saved lessons in localStorage. */

import { renderPage } from "./score.js";

export function download(name, data, type) {
  const blob = data instanceof Blob ? data : new Blob([data], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/* Float32 mono samples → 16-bit PCM WAV. */
export function wavBlob(samples, sampleRate) {
  const n = samples.length, buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
  const str = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  str(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); str(8, "WAVE"); str(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, sampleRate, true); v.setUint32(28, sampleRate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  str(36, "data"); v.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++) v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, samples[i])) * 32767, true);
  return new Blob([buf], { type: "audio/wav" });
}

/* ------------------------------------------------------------ share link */

const SHARE_LIMIT = 32 * 1024;

async function deflate(text) {
  const bytes = new TextEncoder().encode(text);
  if (typeof CompressionStream === "undefined") return { bytes, raw: true };
  const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream("deflate-raw"));
  return { bytes: new Uint8Array(await new Response(stream).arrayBuffer()), raw: false };
}

async function inflate(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
  return new TextDecoder().decode(await new Response(stream).arrayBuffer());
}

const b64u = (bytes) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const unb64u = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));

export async function makeShareLink(text) {
  if (text.length > SHARE_LIMIT) throw new Error(`document is ${Math.round(text.length / 1024)} KB; share links are limited to ${SHARE_LIMIT / 1024} KB`);
  const { bytes, raw } = await deflate(text);
  const url = new URL(location.href);
  url.hash = (raw ? "#r=" : "#l=") + b64u(bytes);
  return url.href;
}

export async function readShareLink() {
  const hash = location.hash || "";
  if (hash.startsWith("#l=")) return inflate(unb64u(hash.slice(3)));
  if (hash.startsWith("#r=")) return new TextDecoder().decode(unb64u(hash.slice(3)));
  return null;
}

/* ---------------------------------------------------------- my lessons */

const STORE_KEY = "dpt.lessons.v1";
const STORE_LIMIT = 2 * 1024 * 1024;

export const store = {
  list() {
    try { return JSON.parse(localStorage.getItem(STORE_KEY) || "[]"); } catch (_) { return []; }
  },
  save(name, text) {
    const docs = this.list().filter((d) => d.name !== name);
    docs.unshift({ name, text, updated: new Date().toISOString() });
    while (JSON.stringify(docs).length > STORE_LIMIT && docs.length > 1) docs.pop();
    try { localStorage.setItem(STORE_KEY, JSON.stringify(docs)); return true; } catch (_) { return false; }
  },
  remove(name) {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(this.list().filter((d) => d.name !== name))); } catch (_) { /* ignore */ }
  },
};

/* ---------------------------------------------------------------- print */

const PAGE_WIDTH_SP = 105, PAGE_HEIGHT_SP = 155.2, MARGIN_SP = 3;

export async function printLesson(bridge, index, lesson, showInferred) {
  const layout = await bridge.engrave(index, PAGE_WIDTH_SP, PAGE_HEIGHT_SP, showInferred, MARGIN_SP);
  const holder = document.getElementById("print");
  holder.replaceChildren();
  layout.pages.forEach((page, i) => {
    const div = document.createElement("div");
    div.className = "print-page";
    const last = i === layout.pages.length - 1;
    if (last && page.systems.length) {
      // Trim the last page to its content so the handout follows on the same sheet.
      page = { ...page, height: Math.max(...page.systems.map((s) => s.y + 26)) + 2 };
    }
    const svg = renderPage(page, { print: true });
    svg.setAttribute("width", "100%");
    div.appendChild(svg);
    if (last) div.appendChild(handout(lesson));
    holder.appendChild(div);
  });
  document.body.classList.add("printing");
  const done = () => { document.body.classList.remove("printing"); window.removeEventListener("afterprint", done); };
  window.addEventListener("afterprint", done);
  window.print();
  setTimeout(done, 60000);
}

function handout(lesson) {
  const box = document.createElement("div");
  box.className = "handout";
  const add = (title, body) => {
    const h = document.createElement("h3"); h.textContent = title; box.appendChild(h);
    for (const line of [].concat(body)) { const p = document.createElement("p"); p.textContent = line; box.appendChild(p); }
  };
  if (lesson.instructions) add("Instructions", lesson.instructions);
  const positions = ["R", "L"].filter((h) => lesson.notes.some((n) => n.hand === h)).map((h) => `${lesson.positions[h].name}: ${lesson.positions[h].label}`);
  if (positions.length) add("Hand positions", positions);
  if (lesson.tips.length) add("Tips", lesson.tips.map((t) => "• " + t));
  return box;
}
