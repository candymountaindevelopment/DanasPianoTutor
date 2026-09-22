/* Web Worker: runs the tutor's Python core inside Pyodide, off the UI thread.
 *
 * Messages in:  {id, op, args}   ops: parse, render, preview, engrave, musicxml, toAuthor, about
 * Messages out: {id, ok, result} | {id, ok:false, error} | {type:'status', text, progress}
 * Audio samples travel as transferred ArrayBuffers, never copied through JSON.
 */
"use strict";

const PYODIDE_DIR = new URL("../vendor/pyodide/", self.location.href).href;
const CORE_ZIP = new URL("../core.zip", self.location.href).href;

importScripts(PYODIDE_DIR + "pyodide.js");

let api = null;
const ready = boot();

function status(text, progress) {
  self.postMessage({ type: "status", text, progress });
}

async function boot() {
  status("Loading Python runtime…", 0.1);
  const pyodide = await loadPyodide({ indexURL: PYODIDE_DIR });
  status("Loading numpy…", 0.45);
  await pyodide.loadPackage("numpy", { messageCallback: () => {} });
  status("Loading the tutor…", 0.8);
  const zip = await fetch(CORE_ZIP).then((r) => {
    if (!r.ok) throw new Error("core.zip: HTTP " + r.status);
    return r.arrayBuffer();
  });
  pyodide.unpackArchive(zip, "zip");
  api = pyodide.pyimport("raw.teach.web_api");
  status("Ready", 1.0);
  return api;
}

function bytesToFloat32(pyBytes) {
  // toJs() gives a Uint8Array copy; slice() guarantees a 4-byte aligned buffer.
  const u8 = pyBytes.toJs().slice();
  pyBytes.destroy();
  return new Float32Array(u8.buffer, 0, u8.byteLength >> 2);
}

const ops = {
  about: () => JSON.parse(api.about()),
  parse: ({ text }) => JSON.parse(api.parse(text)),
  toAuthor: ({ index }) => api.to_author(index),
  render: ({ index, options }) => {
    const meta = JSON.parse(api.render(index, JSON.stringify(options || {})));
    const samples = bytesToFloat32(api.last_samples());
    return { value: { ...meta, samples }, transfer: [samples.buffer] };
  },
  preview: ({ index, midi, seconds }) => {
    const samples = bytesToFloat32(api.note_preview(index, midi, seconds || 0.7));
    return { value: { samples, sampleRate: 44100 }, transfer: [samples.buffer] };
  },
  engrave: ({ index, widthSp, pageHeightSp, showInferred, margin }) =>
    JSON.parse(api.engrave_json(index, widthSp, pageHeightSp ?? null, showInferred !== false, margin ?? 2.0)),
  musicxml: ({ index, includeInferred }) => api.musicxml(index, !!includeInferred),
};

self.onmessage = async (event) => {
  const { id, op, args } = event.data;
  try {
    await ready;
    const fn = ops[op];
    if (!fn) throw new Error("unknown op " + op);
    const out = fn(args || {});
    if (out && out.transfer) {
      self.postMessage({ id, ok: true, result: out.value }, out.transfer);
    } else {
      self.postMessage({ id, ok: true, result: out });
    }
  } catch (err) {
    self.postMessage({ id, ok: false, error: String(err && err.message ? err.message : err) });
  }
};

ready.catch((err) => status("Failed to start: " + (err && err.message ? err.message : err), -1));
