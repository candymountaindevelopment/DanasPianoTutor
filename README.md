# Retro Audio Workstation

A desktop procedural game-audio authoring environment. Sounds are stored as
**mathematical recipes**, not waveforms, and are only rendered when you ask for
audio.

The consequence that matters: you can change a sound's length without damaging
it. Stretching a synth asset re-evaluates its recipe over a new time axis, so
it visits exactly the same frequencies on a slower schedule. A short coin and a
long coin are one asset with one parameter different, and the ping at the front
is bit-identical in both.

## Running it

```bash
python run.py
```

The app opens with six demo sounds loaded. Select one and press **Space**.

Requirements: Python 3.12+, PyQt6, NumPy. `soundfile` adds MP3, OGG and FLAC
import and export with no external tools — install it unless you only ever use
WAV. `sounddevice` enables playback (the app runs and renders without it).
`scipy` is optional and only accelerates filtering. FFmpeg is optional: it is
used if present, for formats libsndfile does not cover and for exact MP3
bitrates.

```bash
pip install -r requirements.txt
```

## Writing audio with a chatbot

Because a sound is a recipe rather than a recording, an LLM can author one.

1. **Help → Copy Chatbot Brief** puts [docs/AUTHORING_FORMAT.md](docs/AUTHORING_FORMAT.md)
   on your clipboard. Paste it into ChatGPT, Claude, or anything else.
2. Ask for what you want — *"four SFX for a cave platformer"*, *"a 16-step boss
   pattern in F# minor at 148 BPM"*.
3. Bring the JSON back with **File → Import Authored JSON** (Ctrl+J), or point it
   at a file. The dialog has a **Check Only** button that reports problems without
   importing anything.
4. If there are warnings, paste the report back to the chatbot — the messages name
   the exact path, e.g. `sounds[sfx_hit].filters[0]: emphasis has no parameter 'q'`.

The whole import is a single undo step. `docs/raw_author_schema.json` is a JSON
Schema for the same format if you want machine validation, and
`docs/examples/boss_fight.author.json` is a complete working document.

Going the other way, **File → Export Authoring Format** writes your project back
out in the same format — useful as a worked example to show a chatbot alongside
the brief ("more like this, but darker").

Every complete example inside the brief is parsed by the test suite, so the file
you hand to a chatbot cannot drift away from what the code actually accepts.

## Danas Piano Tutor

**Danas Piano Tutor** uses the same scripting system to write **lessons for
entry-level piano students**. A lesson is a short piece written per hand with finger numbers:

```json
"right": {"notes": "E4(3) D4(2) C4(1):2 | E4 D4 C4:2"},
"left":  {"notes": "[C3 E3 G3]:4(5,3,1) | [B2 D3 G3]:4(5,2,1)"}
```

```bash
python teach.py                       # opens the bundled examples
python teach.py my_lesson.json
```

From that one document the tutor gives the student:

- **sheet music on screen** — an engraved grand staff with fingering and a
  cursor that follows playback (Ctrl+wheel zooms);
- **hands and fingers** — a keyboard coloured by hand with finger numbers on
  the keys, a hand diagram that lights the finger playing and outlines the one
  playing next, and the five-finger position spelled out;
- **listening and practising** — playback through the RAW synth, a tempo
  slider (25–150 % of the written tempo), metronome with count-in, right or
  left hand alone, and a looped bar range for drilling one passage;
- **printing** — Export PDF / Print directly, or Export MusicXML with
  fingering for MuseScore and friends;
- **scripting in place** — the Script tab holds the JSON; edit, press Apply,
  hear it. Bars that do not add up, fingers outside the hand position and
  unknown fields are reported with a path, exactly like RAW's importer.

### In the browser

The same tutor runs as a web app with nothing installed: the Python core is
executed inside the browser by Pyodide (CPython compiled to WebAssembly), so
parsing, synthesis and engraving are the very same code, and the server only
hands out static files.

```bash
python tools/build_web.py --vendor     # once: packs the core, downloads Pyodide + numpy + Bravura (17 MB)
python tools/serve_web.py              # http://127.0.0.1:8765 with the production security headers
python tools/build_web.py --dist       # clean, servable tree in dist/ (for Caddy / Cloudflare Pages)
```

Everything the desktop app does is there — score, note lane, keyboard, hand
diagram, tempo, metronome, count-in, hands, practice loop, script editor,
MusicXML / WAV export, print-to-PDF — plus a **Settings** tab where every
optional part can be switched off (views, fingering hints, controls, tools).
Presets (*Student*, *Kiosk*, *Teacher*) and a copyable link fix the switches
for a pupil: `?preset=kiosk` or `?off=tools.script,play.tempo`. Three ways
back from a hidden Settings tab: **Ctrl+Shift+S**, *About → Reset app
settings*, or `?reset` after the address. Lessons stay
in the browser (localStorage, downloads, share links in the URL fragment);
nothing is uploaded. Deployment and hardening: [docs/WEB_PLAN.md](docs/WEB_PLAN.md).

**Hosting on GitHub Pages** needs no server of your own:
`.github/workflows/pages.yml` builds `dist/` (downloading Pyodide and Bravura
during the build, so they are never committed) and publishes it on every push
to `main`. Push the repository, then in the repository's *Settings → Pages*
set *Source* to **GitHub Actions**. The tutor appears at
`https://candymountaindevelopment.github.io/DanasPianoTutor/` and Danas Ear at `…/DanasPianoTutor/listen/`.
Pages cannot send custom headers, so the security headers in
`deploy/Caddyfile` do not apply there; the app needs none of them to run.

### Danas Ear — hearing what is played

`listen/` is a second, standalone browser app: it takes the microphone and
shows the pitch it hears — note name, cents sharp or flat, hertz — with a
pitch trace, a keyboard, and a log of note events that can be downloaded as
JSON or copied as a RAW notes line (`E4:2 C4 D4 G4`) at a chosen tempo.
Detection is YIN in plain JavaScript; no Pyodide, nothing uploaded.

The tutor's **Ear** button opens it; the dev server serves it at
http://127.0.0.1:8765/listen/ (`--listen` serves it alone at the root).
It is deployed beside the tutor at `/listen/` (the Caddyfile relaxes the
microphone policy for that path only). It is the first half of "listen to
the student and rank the attempt"; the comparison against a lesson is not
built yet.

Fingers you do not write are inferred from the hand position, so a beginner
piece needs a finger on the first note, on position changes and on chords —
the way printed method books do it. The specification for a chatbot is
[docs/LESSON_FORMAT.md](docs/LESSON_FORMAT.md) (**Help → Copy Reference for a
Chatbot** in the tutor); working pieces are in `docs/examples/lessons/`.

## What is implemented

| Area | State |
|---|---|
| Versioned JSON project, v1→v2 migration | working |
| UUID asset registry, hot-swap-safe references | working |
| Undo/redo at the model level | working |
| Phase-accumulating synth, 7 oscillators | working |
| Trajectories on normalised time | working |
| Time Structure, time map, 4 stretch modes | working |
| Filter stack + Spectral Emphasis + Brightness | working |
| Instance parameter overrides + resolver | working |
| Authoring format: import, export, JSON Schema | working |
| Pattern rendering (notes → instruments → mix) | working |
| Render cache with post-process fast path | working |
| Timeline: drag-and-drop from assets, copy/cut/paste, snapping, mixdown | working |
| Transport: play/stop, BPM control, moving playhead, elapsed time | working |
| Sound slots with audible placeholders | working |
| Sample Lab: multi-slice splice bars, non-destructive slicing | working |
| Sample FX: reverse, fades, gain, normalize, EQ — non-destructive | working |
| Delay, reverb, noise suppression, sustainer (shared with the synth) | working |
| Chord Lab: keyboard, 20 chord types, bake to sample | working |
| Sheet music export (MusicXML, piano grand staff) | working |
| Fit a sample to a bar count, sample-exact, re-fits when tempo changes | working |
| Import merge — re-import an edited document to update in place | working |
| MP3 / OGG / FLAC import and export without FFmpeg | working (needs `soundfile`) |
| WAV/MP3/OGG export, game pack, manifest, code constants | working |
| Project validation | working |
| Scripting console | working (not sandboxed — see below) |
| Pattern **editor** UI (step grid) | **not built** — patterns are authored as JSON |
| Music, melody, rhythm generators | **not built** |
| Pitch/time editing for samples in the UI | **not built** (available as instance overrides) |

Patterns render and play, but there is no step-grid editor yet: you write them in
the authoring format and import them. Building that editor is the natural next
piece of work, and the override resolver underneath it is already done and tested.

## The idea, in one test

`tests/test_stretch.py::TestPitchIdentity` is the load-bearing test. Under a
uniform stretch by `s`, the total oscillator cycle count scales by exactly `s`
— which is only true if the generator revisits the same frequency values more
slowly. A resample would keep the cycle count fixed and drop the pitch by
`12·log₂(s)` semitones; there is a test asserting that contrast too.

If that test ever fails, a resample has crept into the stretch path and the
product's core claim is false.

```bash
python -m unittest discover -s tests
```

403 tests, under a minute. The timeline, transport, Chord Lab, Sample Lab and Sample FX
tests drive the real widgets.

## Layout

```
raw/
├── audio/      AudioBuffer, WAV io, playback abstraction
├── core/       project, assets, commands, overrides, renderer, authoring, validation
├── synth/      trajectory, timestructure, oscillator, envelope, filters, drift, engine
├── patterns/   note names, pattern renderer
├── export/     game pack, manifest, engine constants, MusicXML
├── teach/      Piano Tutor: lesson model, parser, player, engraver
├── ui/         docks and widgets (ui/teach: the tutor window)
└── app/        controller and entry point

docs/
├── TECHNICAL.md             architecture and internals
├── AUTHORING_FORMAT.md      the file to paste into a chatbot
├── LESSON_FORMAT.md         the same, for piano lessons
├── raw_author_schema.json   JSON Schema for the same format
└── examples/                complete working documents (examples/lessons: pieces)
```

`raw/core/project.py` is the source of truth. The GUI only mutates it through
`Command` objects, so undo behaves the same whether an edit came from a dock, a
menu, or the console.

See [docs/TECHNICAL.md](docs/TECHNICAL.md) for the architecture, data model,
render pipeline and file formats, and [EXTENDING.md](EXTENDING.md) for where to
add oscillators, filters, asset types, docks, and migrations.

## Known limitations

- **The console is not a sandbox.** Restricting builtins and imports in Python
  is not a security boundary. Scripts run with your full user permissions. Real
  isolation needs a subprocess with OS-level restrictions; until that exists,
  the honest position is: run scripts you wrote.
- **Stretching imported audio is approximate.** Only recipes stretch exactly.
  The Time Structure panel says which case you are in, deliberately.
- **Biquad filtering falls back to a Python loop** when scipy is absent. Fine
  for SFX (about 10 ms for a 0.2 s sound), slow for multi-minute renders.
- **Decoded source files are cached in memory**, up to 256 MB with LRU
  eviction. Audio is held as float64, so a four-minute mono file is about 84 MB.
  Project → Clear Render Cache drops it along with the render cache.
- **Sheet music is a piano reduction.** Every pitched note from every track lands on
  one grand staff, split at middle C; drums and slot triggers are left off. Swing is
  not notated and triplet grids are rounded. The file is MusicXML — open it in
  MuseScore (free) to see, edit and print it.
- **The timeline is still not a finished editor.** Clips drag in from the asset
  list, move, snap, copy, paste, and mix down, and every one of those is
  undoable — but there is no fade, crop, trim, or automation lane yet, and clips
  are free to overlap.
