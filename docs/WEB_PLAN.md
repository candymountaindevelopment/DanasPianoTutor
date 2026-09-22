# Danas Piano Tutor on the web — building plan and safety measures

Goal: a public URL (e.g. `https://piano.example.com`) served from a home
laptop. The server hands the browser the app's code once; after that the
tutor runs **entirely inside the visitor's browser** — parsing, synthesis,
engraving, playback, printing. The server never runs a user's data.

Status (2026-09-21): **phases 1–3 are implemented** in `web/`, `tools/` and
`raw/teach/web_api.py` — the app runs in the browser with feature parity and
a Settings panel of per-feature switches; `deploy/` holds the Caddy and
tunnel configuration for phase 4 and `tools/check_headers.py` verifies a
deployment. Phases 4–5 (the VM, the domain, the go-live checklist) are the
operator's steps. Sections 1–3 are the design decisions, 4 the phased
build, 5 the security design, 6 the go-live checklist, 7 estimates and risks.

---

## 1. Architecture decision

### 1.1 What runs where

```
 visitor's browser                                   home laptop
┌───────────────────────────────────────────┐      ┌─────────────────────────┐
│ index.html + app.js (UI, Web Audio, SVG)  │◄─────│ static files only:      │
│ worker.js ─► Pyodide (CPython in WASM)    │  GET │ HTML, JS, CSS, WASM,    │
│              + numpy + raw/teach core     │      │ lesson JSON, photo      │
│ localStorage: the user's own lessons      │      │ no database, no login,  │
│                                           │      │ no code path takes      │
│ nothing is uploaded                       │      │ user input              │
└───────────────────────────────────────────┘      └─────────────────────────┘
                                                             ▲
                                              Cloudflare Tunnel (outbound only,
                                              no open router ports, TLS, cache,
                                              rate limits, bot filtering)
```

**The server is a static file server.** That single decision removes almost
every class of web vulnerability: no injection, no auth to break, no sessions,
no uploads, no server-side execution of anything a visitor sends. The laptop
only ever answers `GET` for files in one build directory.

### 1.2 Reuse the Python core through Pyodide (recommended)

Verified on 2026-09-20: `raw.teach.authoring`, `raw.teach.player`,
`raw.teach.engrave`, `raw.teach.sheet` and the RAW synth import and run with
**numpy only** (Qt and sounddevice blocked). Pyodide ships numpy. So:

| Layer | Desktop (today) | Web |
|---|---|---|
| Lesson model, parser, fingering inference, key spelling | `raw/teach/score.py`, `authoring.py` | **same code**, in Pyodide |
| Synthesis, metronome, count-in, note cache | `raw/synth`, `raw/teach/player.py` | **same code**; returns a Float32Array to Web Audio |
| Engraving geometry | `raw/teach/engrave.py` | **same code**; layout JSON → SVG |
| MusicXML | `raw/teach/sheet.py`, `raw/export/sheet.py` | **same code**; download as Blob |
| Painter, views, transport, window, splash, PDF | `raw/ui/teach/*` (PyQt6, ~1 900 lines) | **rewritten in JS**: SVG score, Canvas keyboard/hands/lane, Web Audio transport, print CSS |

Why not port everything to TypeScript? It would load faster (no 12 MB WASM
runtime) but creates a second implementation of the engine and engraver to
keep in sync. Pyodide first; a TS port of the core is a later optimisation if
load time turns out to matter (see 7.3).

Cost of Pyodide: ~12 MB (runtime + numpy, compressed) on first visit, cached
afterwards by the browser and a service worker. Startup 2–4 s on a laptop,
5–10 s on a phone. Acceptable for a lesson app; the UI shows the splash while
it loads (which is exactly what the splash is for).

### 1.3 Things deliberately **out** of version 1

- Accounts, saved progress on the server, sharing between users, comments.
- Uploads of any kind (audio, images, lessons) to the server.
- Any API endpoint. If one is ever needed it goes in a separate, later phase
  with its own threat model (section 5.7).

Users keep their own lessons in `localStorage` and by downloading the JSON;
a lesson can be shared as a URL **fragment** (`#lesson=<base64>`), which the
browser never sends to the server.

---

## 2. Repository layout (new parts)

```
web/
├── index.html            shell: splash, layout, <noscript> message
├── src/
│   ├── app.js            boot, state, wiring
│   ├── bridge.js         talks to the worker (parse / render / engrave / musicxml)
│   ├── worker.js         loads Pyodide + numpy + raw core; runs requests off the UI thread
│   ├── transport.js      Web Audio playback, position from AudioContext.currentTime
│   ├── score.js          layout JSON → SVG (port of ui/teach/painter.py)
│   ├── keyboard.js       Canvas keyboard with hand colours / finger discs
│   ├── hands.js          Canvas hand diagram
│   ├── lane.js           Canvas note lane
│   ├── editor.js         script editor (textarea + line numbers; no eval, no CodeMirror CDN)
│   ├── export.js         MusicXML / JSON downloads, print
│   └── storage.js        localStorage lessons, size-capped
├── styles.css            includes @media print for the score
├── lessons/              copies of docs/examples/lessons/*.json (build step)
├── vendor/pyodide/       self-hosted, pinned Pyodide release (never from a CDN)
└── sw.js                 service worker: cache-first for vendor/, network-first for app
tools/
├── build_web.py          copies raw/ core subset → web/core.zip, examples, photo; writes dist/
└── check_headers.py      curls the live site and asserts the security headers (section 5.4)
deploy/
├── Caddyfile             static server + headers + logging
└── cloudflared.yml       tunnel config
```

`tools/build_web.py` packs only what the browser needs from `raw/`:
`__init__`, `audio/buffer.py`, `synth/*`, `patterns/notes.py`,
`patterns/chords.py`, `export/sheet.py`, `core/authoring.py` (for
`parse_sound`), `core/ids.py`, `teach/{score,authoring,voice,player,engrave,sheet}.py`.
No UI, no file I/O modules.

---

## 3. Browser app design

### 3.1 Worker bridge (the only API)

The UI thread never runs Python. `worker.js` exposes four pure functions;
every call is a plain JSON message, so there is nothing to inject into:

| call | in | out |
|---|---|---|
| `parse(documentText)` | lesson JSON as text (≤ 256 KB) | `{lessons:[{name,title,…,notes:[…]}], warnings, errors}` |
| `render(lessonIndex, options)` | tempo, hands, metronome, countIn, loopBars | `{samples: Float32Array (transferred), sampleRate, countInSeconds, startDivision, endDivision, secondsPerDivision}` |
| `engrave(lessonIndex, widthSp, pageHeightSp?)` | | layout JSON (systems, noteheads, stems, ties, fingers, symbols, texts) |
| `musicxml(lessonIndex, includeInferred)` | | XML string |

Implementation on the Python side is a ~120-line `raw/teach/web_api.py`
that turns the dataclasses into dicts (`dataclasses.asdict`) — testable on
the desktop with the normal test suite.

### 3.2 Playback

Web Audio, not `<audio>`: `AudioBufferSourceNode` from the rendered
Float32Array; position = `ctx.currentTime - startedAt` (sample-accurate,
better than the desktop's wall clock). Looping: first pass = count-in +
section as one buffer with `onended` → start the section buffer with
`loop = true`, same two-stage scheme as `LessonTransport`. Tempo/hands
changes re-render in the worker (the note cache lives there).

Browsers require a user gesture before audio starts: the splash's
"Click anywhere to start" doubles as that gesture and creates the
`AudioContext`.

### 3.3 Score

The layout JSON becomes an inline `<svg>` with one `<g>` per system and
`data-start`/`data-end`/`data-hand` attributes on noteheads. Highlighting is
a class toggle; the cursor is one `<line>` moved per animation frame. The
same SVG prints: `@media print` hides everything but the score and the
handout text (instructions, positions, tips), so **Print → Save as PDF** in
the browser replaces `QPdfWriter`. Page breaks: `break-inside: avoid` per
system.

Clef and rest glyphs: the desktop relies on Segoe UI Symbol; the web must
not. Ship **Bravura** (SIL Open Font License) as a subset WOFF2 (~40 KB for
the dozen glyphs needed), self-hosted.

### 3.4 Storage and sharing

- `localStorage["dpt.lessons"]`: array of `{id, name, text, updated}`, hard
  cap 2 MB total, oldest-first eviction with a warning.
- Download/upload JSON via `<a download>` and `<input type=file>` — the file
  is read in the browser only.
- Share link: `#l=` + base64url(deflate(JSON)); refused above 32 KB. The
  fragment is never transmitted to the server; the app parses it with the
  same strict parser as any other document.

### 3.5 Trust boundary inside the browser

Lesson documents are **data, not code** at every step: the parser only
reads keys it knows, numbers are clamped, strings are shown as text. On
the JS side every user-derived string goes through `textContent`, never
`innerHTML`; SVG text nodes are created with `createElementNS` +
`textContent`. No `eval`, no `new Function`, no dynamic `import()` of user
input. `JSON.parse` only. This is what makes the strict CSP in 5.4 possible.

---

## 4. Build phases

| Phase | Deliverable | Done when |
|---|---|---|
| **0 Prepare** | domain registered; Cloudflare account with the domain's DNS; KVM installed on the Ubuntu laptop and the `tutor` VM created (5.3.1); `deploy/` folder | `ssh admin@<vm-ip>` works and `cloudflared tunnel list` works inside the VM |
| **1 Core in the browser** | `raw/teach/web_api.py` + tests; `tools/build_web.py`; `web/worker.js` loading Pyodide and `core.zip`; a bare page that parses an example and plays it | Ode to Joy plays in Chrome/Firefox/Safari from `python -m http.server` |
| **2 UI** | score SVG, keyboard, hands, lane, transport bar, lesson panel, script editor, exports, print CSS, splash, Bravura subset | feature parity with the desktop except PDF-via-Qt (replaced by print) |
| **3 Package** | service worker, pinned Pyodide in `vendor/`, `dist/` build, Lighthouse pass, size budget (< 15 MB first load, < 300 KB app code) | `dist/` runs offline after first visit |
| **4 Serve** | Caddy on the laptop serving `dist/` on `127.0.0.1:8080`; cloudflared tunnel to it; headers; access logs | `tools/check_headers.py` passes against the public URL |
| **5 Harden & launch** | section 6 checklist; Cloudflare Access (email login) on for a private beta, off for public launch; uptime monitor | checklist signed off |
| **6 Later** | accounts / sharing backend, MIDI keyboard input (Web MIDI), TS port of the core if load time matters | separate plans |

Phases 1–3 need no server and no domain; build and test locally first.

---

## 5. Safety measures

Ordered by how much each one removes. 5.1–5.4 are mandatory for launch.

### 5.1 Expose nothing but the tunnel

- **No port forwarding, no UPnP, no DMZ.** The laptop makes an *outbound*
  connection to Cloudflare (`cloudflared`); Cloudflare terminates TLS and
  forwards only HTTP to `127.0.0.1:8080`. Your home IP is never published
  and nothing on the router is opened.
- Turn UPnP **off** in the router; check the router's admin page is not
  reachable from the internet; change its default password.
- Cloudflare settings: SSL mode *Full (strict)*, *Always use HTTPS*, HSTS
  on, *Bot fight mode* on, a rate-limit rule (e.g. 100 requests / minute per
  IP) and *Under attack mode* available as a panic switch.
- Alternative with the same property: Tailscale Funnel. Avoid ngrok free
  tier for a public product (random URLs, limits).
- If you ever refuse the tunnel idea and forward a port anyway: only 443,
  Caddy with automatic Let's Encrypt, fail2ban-style rate limiting, and a
  dynamic-DNS name — but understand that this publishes your home IP and
  makes your router part of the attack surface.

### 5.2 Serve only static files, from a separate build directory

- Caddy `file_server` rooted at `dist/` **only** — never the repository, never
  `docs/`, `tests/` or anything containing scripts. `dist/` is produced by
  `tools/build_web.py` and contains no secrets, no `.git`, no `.env`, no
  source maps in production.
- Directory listing off. Unknown paths return the app's `404.html`, not a
  listing. No PUT/POST/DELETE handling exists in a static server (Caddy
  returns 405).
- The server process runs as the `tutor-serve` user, which has read-only
  access to `dist/` and no access to your documents, the repo, or admin
  rights. Inside the VM of 5.3 this is `useradd --system tutor-serve` plus
  `chown -R root:tutor-serve /srv/tutor/dist && chmod -R 750 /srv/tutor/dist`;
  Caddy and cloudflared run as systemd services under that user.

### 5.3 Host: an isolated virtual machine on the Ubuntu laptop (recommended)

The serving laptop runs Ubuntu. Put the whole server side in a **KVM
virtual machine** (Ubuntu's built-in hypervisor, via libvirt) rather than on
the laptop's own system. The VM has its own kernel, disk, users and network
stack; the tunnel credential, the web server and every access log live only
inside it. Nothing that happens in the VM can read your home directory, and
resetting the server means restoring a snapshot.

Why a VM and not a container: Docker/LXD containers share the host kernel
— good for packaging, weaker for isolation. KVM is hardware-enforced
isolation and still light: this workload needs 1–2 vCPU, 1–2 GB RAM, 10 GB
disk, because the VM only serves ~15 MB of static files.

**5.3.1 Create the VM (once, on the laptop)**

```bash
# hypervisor + management tools
sudo apt install qemu-kvm libvirt-daemon-system virtinst virt-manager
sudo adduser "$USER" libvirt && newgrp libvirt
# Ubuntu Server cloud image, unattended install via cloud-init
wget https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
qemu-img create -f qcow2 -F qcow2 -b noble-server-cloudimg-amd64.img tutor.qcow2 10G
cat > user-data <<'EOF'
#cloud-config
hostname: tutor
users:
  - name: admin
    groups: sudo
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys:
      - <your public ssh key>
package_update: true
package_upgrade: true
packages: [ufw, unattended-upgrades, rsync]
EOF
cloud-localds seed.iso user-data
virt-install --name tutor --memory 2048 --vcpus 2 --os-variant ubuntu24.04 \
  --disk tutor.qcow2 --disk seed.iso,device=cdrom \
  --network network=default --import --noautoconsole
virsh autostart tutor          # starts with the laptop
```

`--network network=default` is libvirt's NAT bridge: the VM can reach the
internet (for cloudflared, apt) and the host can reach the VM by its
private address (`virsh domifaddr tutor`), but **nothing outside the laptop
can reach the VM** — no forwarding rule exists and none is needed, because
the tunnel is an outbound connection from inside the VM.

**5.3.2 Inside the VM**

```bash
ssh admin@<vm-ip>
sudo ufw default deny incoming && sudo ufw default allow outgoing
sudo ufw allow from 192.168.122.0/24 to any port 22   # SSH from the host only
sudo ufw enable
sudo dpkg-reconfigure -plow unattended-upgrades       # security updates by themselves
# Caddy (static server on 127.0.0.1:8080) and cloudflared
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
curl -fsSL https://pkg.cloudflare.com/cloudflared-stable-linux-amd64.deb -o cloudflared.deb && sudo dpkg -i cloudflared.deb
sudo useradd --system --home /srv/tutor --shell /usr/sbin/nologin tutor-serve
sudo mkdir -p /srv/tutor/dist && sudo chown -R root:tutor-serve /srv/tutor && sudo chmod -R 750 /srv/tutor
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile && sudo systemctl enable --now caddy
cloudflared tunnel login && cloudflared tunnel create tutor \
  && cloudflared tunnel route dns tutor piano.example.com \
  && sudo cloudflared service install        # credential stays in the VM (/etc/cloudflared)
```

`/etc/caddy/Caddyfile` binds `127.0.0.1:8080`, serves `/srv/tutor/dist`,
and sets the headers of 5.4. The systemd unit for caddy runs as
`tutor-serve` (`User=`/`Group=` drop-in); cloudflared's unit as its own
`cloudflared` user.

**5.3.3 Deploying a build**

From the laptop, never from inside the VM: build `dist/` as your normal
user, then push it read-only over SSH:

```bash
python tools/build_web.py
rsync -av --delete -e ssh dist/ admin@<vm-ip>:/tmp/dist-new/ \
  && ssh admin@<vm-ip> 'sudo rsync -a --delete /tmp/dist-new/ /srv/tutor/dist/ && sudo systemctl reload caddy'
```

Keep a snapshot before each change: `virsh snapshot-create-as tutor pre-deploy-$(date +%F)`.
Rollback = `virsh snapshot-revert`. No shared folders between host and VM
(a writable share would be the one path back into the laptop).

**5.3.4 The laptop itself**

- Full-disk encryption (LUKS) so a stolen laptop leaks neither your data
  nor the VM image; automatic security updates (`unattended-upgrades`) on
  the host too, since the hypervisor is host code.
- Host firewall `ufw default deny incoming`; there is nothing to allow —
  the VM's NAT and the tunnel need no inbound rules.
- Do not run cloudflared, Caddy, or the credential on the host at all.
- Power: `HandleLidSwitch=ignore` in `/etc/systemd/logind.conf`, sleep off
  on AC, so the VM keeps running with the lid closed. The VM autostarts on
  boot; after a host reboot the tunnel reconnects by itself.
- Backups: the repo is on OneDrive; export the VM definition
  (`virsh dumpxml tutor`) and keep `deploy/` (without credentials) with it.
  The VM disk itself needs no backup — it is rebuilt from 5.3.1 in ten
  minutes.

**What the VM does not protect against:** a compromised laptop (malware
with root can read the VM's disk), and the rare hypervisor escape — both
mitigated by keeping the host updated and encrypted. What it does protect
against is everything that starts from the server side: a bug in Caddy or
cloudflared, a leaked credential, a misconfiguration — those stop at the
VM boundary.

### 5.4 HTTP security headers (set in Caddy, verified by `tools/check_headers.py`)

```
Content-Security-Policy:
  default-src 'none';
  script-src 'self' 'wasm-unsafe-eval';
  worker-src 'self';
  style-src 'self';
  font-src 'self';
  img-src 'self' data: blob:;
  media-src blob:;
  connect-src 'self';
  manifest-src 'self';
  base-uri 'none'; form-action 'none'; frame-ancestors 'none';
  upgrade-insecure-requests
Strict-Transport-Security: max-age=31536000; includeSubDomains; preload
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=(), midi=(self)
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Resource-Policy: same-origin
Cache-Control: vendor/ and hashed assets = immutable, 1 year; index.html = no-cache
```

Consequences the design already accepts: no inline scripts or styles, no
CDNs (Pyodide, fonts and everything else are self-hosted — also removes a
supply-chain dependency at runtime), `'wasm-unsafe-eval'` is the single
relaxation Pyodide needs. `midi=(self)` is reserved for the later Web MIDI
feature. Test with securityheaders.com and Mozilla Observatory; target A+.

### 5.5 Supply chain and build integrity

- Pin the Pyodide release (e.g. `0.27.x`) and record the SHA-256 of the
  downloaded archive in `tools/build_web.py`; the build fails if it differs.
- No npm dependencies at runtime. If a bundler is used at build time (Vite),
  lock the versions (`package-lock.json`) and run `npm audit` in the build.
- `build_web.py` refuses to run if `dist/` would contain `.py` files outside
  `core.zip`, any `.json` outside `lessons/`, or any file over 20 MB.
- The service worker only caches same-origin responses with status 200.

### 5.6 Privacy and legal

- No analytics, no cookies, no third-party requests: the CSP enforces it.
  If you later want visit counts, Cloudflare's server-side analytics need
  no script on the page.
- Publish a one-paragraph privacy note (nothing collected; lessons stay in
  your browser) and an imprint if your jurisdiction requires one.
- Content rights: all bundled tunes are public domain (traditional, Beethoven,
  Pierpont, Petzold); keep it that way for anything added. The splash photo:
  make sure you have the photographer's and the subject's permission for
  public web use. Bravura is OFL-licensed (include the licence file).
- Cloudflare access logs contain visitor IPs; set retention to the minimum
  you need (Caddy: rotate logs weekly, keep 4 weeks).

### 5.7 If a backend is ever added (phase 6 — not now)

The moment the server accepts data, the plan changes: authentication
(passkeys or an identity provider, not home-grown passwords), a database
with backups, input validation server-side, per-user rate limits, abuse
reporting, and probably moving off the laptop to a small VPS. Treat it as a
new project with its own threat model; do not bolt it onto the static site.

---

## 6. Go-live checklist

Network
- [ ] Router UPnP off, admin interface not reachable from WAN, default password changed
- [ ] No port forwards exist; `cloudflared` runs as a service and reconnects after reboot
- [ ] Cloudflare: Full (strict) TLS, Always HTTPS, HSTS, Bot fight mode, rate-limit rule, WAF managed rules on

Host
- [ ] Server side runs only inside the `tutor` KVM VM on libvirt NAT; `virsh autostart tutor` set; a snapshot exists
- [ ] In the VM: `tutor-serve` system user owns nothing writable; Caddy runs as it and binds `127.0.0.1:8080`; cloudflared runs as its own user; ufw deny-incoming except SSH from the host subnet
- [ ] On the laptop: LUKS full-disk encryption, ufw deny-incoming, unattended-upgrades on host **and** VM; no cloudflared, Caddy or credential on the host
- [ ] External port scan of your home IP (from a phone off Wi-Fi) shows nothing open
- [ ] No shared folders between host and VM; deploys go through `rsync` over SSH
- [ ] Tunnel credential only in the VM (`/etc/cloudflared`), not in the repo; rotation procedure written down
- [ ] Power settings: no sleep on AC, lid close does nothing

Application
- [ ] `dist/` contains only built assets; no source maps, no `.git`, no docs/tests
- [ ] `tools/check_headers.py` passes; securityheaders.com A+; Mozilla Observatory ≥ A
- [ ] CSP has no `unsafe-inline`; the app works with the CSP on in Chrome, Firefox, Safari (desktop + mobile)
- [ ] Pyodide hash pinned; no runtime CDN requests (check the Network tab: everything same-origin)
- [ ] Pasting a 1 MB document, a document with 10 000 notes, and malformed JSON all fail gracefully with a message
- [ ] Share link over 32 KB is refused; a share link with garbage content shows the parser's errors, nothing else
- [ ] Service worker: page loads offline on a second visit; update path works (new build shows after reload)

Content and legal
- [ ] Privacy note and imprint pages exist and are linked from the splash/about
- [ ] Photo permission confirmed; Bravura OFL and Pyodide MPL licences included in `dist/licenses/`

Operations
- [ ] Uptime monitor (UptimeRobot or Cloudflare health check) on the public URL
- [ ] Deploy script (`build → rsync over SSH → caddy reload`) tested; rollback = redeploy the previous `dist/` zip
- [ ] Incident runbook: how to disable the tunnel in one command (`cloudflared tunnel route dns` removal or *Under attack mode*), whom to contact at the domain registrar

---

## 7. Estimates, risks, alternatives

### 7.1 Effort (one developer, working days)

| Phase | Days |
|---|---|
| 1 Core in browser (web_api, build script, worker, bare page) | 2–3 |
| 2 UI (SVG score is the largest piece, ~2 days of it) | 6–8 |
| 3 Packaging, service worker, cross-browser fixes | 2 |
| 4 Server + tunnel + headers | 1 |
| 5 Hardening, checklist, beta | 1–2 |
| **Total** | **12–16** |

### 7.2 Risks

| Risk | Mitigation |
|---|---|
| Pyodide first-load size on mobile data | splash with a progress bar; service worker; consider the TS port later |
| Safari Web Audio quirks (context suspended until gesture, no `loop` glitches) | the splash click creates and resumes the context; loop by re-scheduling buffers instead of `loop=true` if Safari clicks |
| Home upload bandwidth (12 MB per new visitor) | Cloudflare caches `vendor/` at the edge; set the cache rule to *Cache everything* for `/vendor/*` and `/lessons/*` |
| Laptop offline (power, kernel-update reboot) | uptime monitor alerts; nothing is lost — visitors who loaded the app keep using it offline |
| Someone pastes a malicious lesson URL | the fragment is data through a strict parser under a strict CSP; the worst case is a parser error message |
| Photo/tune rights | section 5.6 |

### 7.3 Alternatives worth knowing

- **Skip the laptop entirely**: because the site is static, Cloudflare Pages
  or GitHub Pages can host `dist/` for free with better uptime and zero
  exposure of your home. The tunnel plan is still valid if you want the
  laptop to be the server, but the same `dist/` deploys to Pages unchanged
  — a good fallback or a way to start while the tunnel is set up.
- **TypeScript port of the core** (~2 500 lines): removes Pyodide (load
  drops from ~12 MB to ~300 KB), costs a second implementation. Do it only
  after real users show that load time matters; keep the Python tests as
  the reference behaviour and port them too.
- **Electron / Tauri** desktop packaging of the same web build: gives an
  installer without the Qt dependency, later.
