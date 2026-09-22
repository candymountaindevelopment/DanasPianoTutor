/* Promise-based RPC to the Python worker. One request at a time is fine for
 * this app (renders take ~100 ms); requests queue naturally in the worker. */

export class Bridge {
  constructor() {
    this.worker = new Worker(new URL("./worker.js", import.meta.url));
    this.pending = new Map();
    this.nextId = 1;
    this.onStatus = null;
    this.worker.onmessage = (event) => {
      const msg = event.data;
      if (msg.type === "status") {
        if (this.onStatus) this.onStatus(msg.text, msg.progress);
        return;
      }
      const entry = this.pending.get(msg.id);
      if (!entry) return;
      this.pending.delete(msg.id);
      if (msg.ok) entry.resolve(msg.result);
      else entry.reject(new Error(msg.error));
    };
    this.worker.onerror = (event) => {
      if (this.onStatus) this.onStatus("Worker error: " + event.message, -1);
    };
  }

  call(op, args) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.worker.postMessage({ id, op, args });
    });
  }

  about() { return this.call("about"); }
  parse(text) { return this.call("parse", { text }); }
  toAuthor(index) { return this.call("toAuthor", { index }); }
  render(index, options) { return this.call("render", { index, options }); }
  preview(index, midi, seconds) { return this.call("preview", { index, midi, seconds }); }
  engrave(index, widthSp, pageHeightSp, showInferred, margin) {
    return this.call("engrave", { index, widthSp, pageHeightSp, showInferred, margin });
  }
  musicxml(index, includeInferred) { return this.call("musicxml", { index, includeInferred }); }
}
