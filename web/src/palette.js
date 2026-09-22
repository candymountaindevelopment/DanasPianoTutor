/* ⌘K — where every control that is set once rather than changed while
 * playing lives: open and save, the exports, print, share, count-in, voice,
 * the bar range, help.
 *
 * The app hands over a list of commands; each carries the feature switch it
 * belongs to, so a command whose part of the app is switched off is not in
 * the list at all. Nothing here runs code from the document. */

export class Palette {
  constructor(overlay, input, list, settings) {
    this.overlay = overlay; this.input = input; this.list = list; this.settings = settings;
    this.commands = [];
    this.shown = [];
    this.sel = 0;

    overlay.addEventListener("click", (e) => { if (e.target === overlay) this.close(); });
    input.addEventListener("input", () => this.filter());
    input.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.preventDefault(); this.close(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); this.move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); this.move(-1); }
      else if (e.key === "Enter") { e.preventDefault(); this.run(this.shown[this.sel]); }
    });
  }

  /* [{ id, label, where, keys, feature, when, run }] */
  setCommands(commands) { this.commands = commands; }

  get open() { return !this.overlay.hidden; }

  toggle() { this.open ? this.close() : this.show(); }

  show() {
    this.overlay.hidden = false;
    this.input.value = "";
    this.filter();
    this.input.focus();
  }

  close() {
    this.overlay.hidden = true;
    this.input.blur();
  }

  available() {
    return this.commands.filter((c) => (!c.feature || this.settings.get(c.feature)) && (!c.when || c.when()));
  }

  filter() {
    const q = this.input.value.trim().toLowerCase();
    const scored = [];
    for (const c of this.available()) {
      const label = c.label.toLowerCase(), where = (c.where || "").toLowerCase();
      if (!q) { scored.push([0, c]); continue; }
      const at = label.indexOf(q);
      if (at === 0) scored.push([-2, c]);
      else if (at > 0) scored.push([-1, c]);
      else if (where.includes(q) || subsequence(label, q)) scored.push([1, c]);
    }
    scored.sort((a, b) => a[0] - b[0]);
    this.shown = scored.map((x) => x[1]);
    this.sel = 0;
    this.render();
  }

  move(delta) {
    if (!this.shown.length) return;
    this.sel = (this.sel + delta + this.shown.length) % this.shown.length;
    this.render();
  }

  render() {
    this.list.replaceChildren();
    if (!this.shown.length) {
      const empty = document.createElement("div");
      empty.className = "palette-empty";
      empty.textContent = "Nothing matches.";
      this.list.appendChild(empty);
      return;
    }
    this.shown.forEach((c, i) => {
      const b = document.createElement("button");
      b.className = "palette-item" + (i === this.sel ? " sel" : "");
      const where = document.createElement("span");
      where.className = "where";
      where.textContent = c.where || "";
      const label = document.createElement("span");
      label.textContent = c.label;
      b.append(where, label);
      if (c.keys) {
        const k = document.createElement("span");
        k.className = "kbd";
        k.textContent = c.keys;
        b.appendChild(k);
      }
      b.onmousemove = () => { if (this.sel !== i) { this.sel = i; this.render(); } };
      b.onclick = () => this.run(c);
      this.list.appendChild(b);
    });
    const sel = this.list.children[this.sel];
    if (sel && sel.scrollIntoView) sel.scrollIntoView({ block: "nearest" });
  }

  run(command) {
    if (!command) return;
    this.close();
    try { command.run(); } catch (e) { console.error(e); }
  }
}

/* "prt" matches "Print · PDF". */
function subsequence(text, q) {
  let i = 0;
  for (const ch of text) if (ch === q[i]) i++;
  return i === q.length;
}
