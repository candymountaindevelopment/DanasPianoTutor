# Retro Audio Workstation — Technical Documentation

Version 0.2.0 · project format v2 · authoring format `raw.author` v1

This document describes how the application is built: its data model, the
synthesis and rendering pipeline, the command/undo system, the file formats it
reads and writes, the UI architecture, and how the pieces are tested. It is
written for someone who has to modify or extend the code. For the user-facing
overview see [README.md](../README.md); for step-by-step recipes for adding
oscillators, filters, asset types and docks see [EXTENDING.md](../EXTENDING.md);
for the chatbot-facing authoring language see [AUTHORING_FORMAT.md](AUTHORING_FORMAT.md).

---

## 1. Purpose and design thesis

RAW is a desktop authoring environment for procedural game audio. Its one
load-bearing idea:

> A sound is stored as a **recipe** (parameters and curves), not as a waveform.
> Audio only exists when something asks for it.

Everything else follows from that. Because the recipe is evaluated over a
normalised time axis, a sound can be re-rendered at any length without changing
the frequencies it visits (§6.4). Because rendering is a pure function of the
recipe plus its inputs, renders can be cached by a hash of those inputs (§8.2).
Because the recipe is plain JSON, a language model can write one (§13).

Imported audio (WAV/MP3/OGG/FLAC) is a first-class asset too, but it is stored
as a **reference plus a list of non-destructive edits** — never as modified
samples. The source file is read on demand and the edits are replayed.

## 2. System overview

```
┌──────────────────────────────────────────────────────────────────────┐
│ raw/ui            PyQt6 docks and widgets. Display the model,        │
│                   never mutate it directly.                          │
├──────────────────────────────────────────────────────────────────────┤
│ raw/app           Controller: owns Project, CommandStack, Renderer,  │
│                   playback backend; exposes Qt signals.              │
├──────────────────────────────────────────────────────────────────────┤
│ raw/core          Project, assets, registry, commands, overrides,    │
│                   renderer + cache, slots, authoring, bake,          │
│                   validation, migrations.                            │
├───────────────────────┬──────────────────────┬───────────────────────┤
│ raw/synth             │ raw/patterns         │ raw/export            │
│ engine, trajectory,   │ notes, pattern       │ game pack, constants, │
│ timestructure, osc,   │ renderer, chords     │ MusicXML sheet        │
│ envelope, drift,      │                      │                       │
│ filters               │                      │                       │
├───────────────────────┴──────────────────────┴───────────────────────┤
│ raw/audio         AudioBuffer, file io (wav / soundfile / ffmpeg),   │
│                   playback backend abstraction                       │
└──────────────────────────────────────────────────────────────────────┘
```

Dependency direction is strictly downward. `raw/core/renderer.py` imports
`raw.patterns.renderer` lazily inside a function so that `core → patterns`
remains one-way. Nothing below `raw/app` imports Qt except `raw/ui`.

### Threading

Everything runs on the Qt main thread except optional work submitted through
`Controller.run_async(fn, on_done, on_fail)`, which uses a `QThreadPool` with a
`Worker(QRunnable)` and marshals results back through signals. Rendering of
individual sounds is fast enough (milliseconds) that the UI calls it
synchronously; timeline mixdown and game-pack export are the async users.

## 3. Runtime and dependencies

| Package | Role | Required |
|---|---|---|
| Python ≥ 3.12 | language (developed on 3.14) | yes |
| PyQt6 ≥ 6.6 | GUI | yes |
| numpy ≥ 1.26 | all DSP | yes |
| soundfile ≥ 0.12 | MP3/OGG/FLAC/AIFF read+write via bundled libsndfile | strongly recommended |
| sounddevice ≥ 0.4 | playback | recommended (app runs without it; `NullBackend`) |
| scipy | `lfilter` for biquads | optional; pure-Python fallback |
| FFmpeg (on PATH or `imageio-ffmpeg`) | extra formats, exact MP3 bitrate | optional |

Entry point: `run.py` → `raw.app.main.main()` → creates `QApplication`,
`Controller`, `MainWindow`.

Tests: `python -m unittest discover -s tests` (430 tests, under a minute; UI
tests instantiate the real widgets with an offscreen `QApplication`).

## 4. Repository layout

```
raw/
├── __init__.py          __version__, PROJECT_VERSION
├── app/                 controller.py (Controller, Worker), main.py
├── audio/               buffer.py, io.py, playback.py
├── core/                assets.py, authoring.py, bake.py, commands.py, ids.py,
│                        migrations.py, overrides.py, project.py, renderer.py,
│                        slots.py, validation.py
├── export/              game_pack.py, sheet.py
├── patterns/            notes.py, renderer.py, chords.py
├── teach/               score.py, authoring.py, voice.py, player.py, engrave.py,
│                        sheet.py, main.py  (Piano Tutor, §14.5)
├── synth/               engine.py, trajectory.py, timestructure.py, oscillator.py,
│                        envelope.py, drift.py, filters.py
└── ui/                  main_window.py, timeline.py, asset_manager.py, synth_dock.py,
                         timestructure_dock.py, sample_lab.py, sample_fx.py,
                         chord_lab.py, piano.py, console_dock.py, import_dialog.py,
                         waveform.py, dnd.py, theme.py, teach/ (tutor window)
docs/
├── TECHNICAL.md         this file
├── AUTHORING_FORMAT.md  chatbot brief (its examples are parsed by the tests)
├── LESSON_FORMAT.md     the same for Piano Tutor lessons
├── raw_author_schema.json
└── examples/            *.author.json, *.musicxml, lessons/*.json
tests/                   test_*.py (unittest)
assets/                  demo audio shipped with the repo
```

## 5. Data model

### 5.1 Project

`raw/core/project.py::Project` is the single source of truth. It is a dataclass
serialised to one JSON file (`*.raw.json`).

```jsonc
{
  "project_version": 2,
  "metadata": {"name": "...", "author": "", "created": "...", "modified": "..."},
  "settings": {
    "sample_rate": 44100, "tempo": 120.0, "time_signature": [4, 4],
    "fps": 60, "auto_normalize": false, "master_gain_db": 0.0
  },
  "assets":   { "<uid>": { "type": "SynthAsset", ... }, ... },
  "slots":    { "kick": {"asset": "<uid>|null", "overrides": {}, "description": ""} },
  "timeline": { "tracks": [...], "clips": [ {clip}, ... ] },
  "generators": {}, "export_profiles": {}, "editor_state": {}
}
```

`Project.path` (not serialised) is the location of the JSON file; `Project.root`
is its parent directory and `resolve_asset_path(rel)` resolves audio references
against it. Audio imported before the project has been saved is stored as an
absolute path; audio generated by *bake* (§14.4) is always relative under
`assets/generated/`.

`Project.load()` runs `migrations.migrate()` first (§16) and returns
`(project, notes)`.

### 5.2 Assets and the registry

All assets derive from `raw/core/assets.py::Asset`:

| Field | Meaning |
|---|---|
| `uid` | 32-hex UUID, stable for the asset's life; every reference in the project is by uid |
| `name` | identifier-style, unique within the project (`ids.is_valid_identifier`); references in the authoring format are by name |
| `tags`, `description`, `meta` | free metadata; `meta` is where generators (Chord Lab) keep their recipe |
| `created`, `modified` | ISO timestamps |

Concrete types are registered with `@asset_type("Name")`, which also lets
`asset_from_dict` dispatch on `"type"`. Unknown types load as `UnknownAsset` and
round-trip verbatim.

| Type | Key fields | Renders as |
|---|---|---|
| `SynthAsset` | `params: SynthParams` | one-shot sound effect |
| `InstrumentAsset` | `params: SynthParams` | the same recipe, but intended to be pitched by patterns (`root_note`) |
| `AudioAsset` | `source_path`, `sample_rate`, `channels`, `duration`, `stretch_method`, `time_structure`, `missing`; slice: `trim_start`, `trim_length`; FX: `reverse`, `fade_in`, `fade_out`, `gain_db`, `normalize`, `filters`; `fit_bars` | imported/baked audio after its edit chain |
| `PatternAsset` | `tempo`, `steps`, `step_division` ("1/16"…), `swing`, `tracks[]`, `defaults{overrides, slots}` | a mixed loop of instrument renders |

Every asset exposes:

- `render_key()` — the dict of fields that affect audio. Cosmetic fields (name,
  tags, description, timestamps) are excluded so renaming never invalidates a
  cached render.
- `dependencies(project)` — uids this asset needs (patterns → instruments and
  bound slot targets). Used transitively by the render cache.

`AssetRegistry` is an ordered dict of uid → asset with `add` (auto-renames on
clash), `remove`, `get`, `by_name`, `by_type`, `replace` (keeps uid, swaps
object), `rename`, `sorted`.

### 5.3 Slots

A **slot** (`raw/core/slots.py::Slot`) is a named role — `kick`, `lead`,
`pickup` — that a pattern track can reference instead of an instrument. Slots
live at project level (`project.slots`) and a pattern can override the binding
locally in `pattern.defaults["slots"]`. `resolve_slot(name, pattern, project)`
checks the pattern first, then the project.

An **unbound** slot still renders: `placeholder_params(name)` produces a short
blip whose pitch is derived from a hash of the slot name (C-major scale over two
octaves from C4), so a groove can be auditioned before any sound is chosen and
each role is distinguishable by ear. The pattern renderer emits a warning per
unbound slot.

### 5.4 Timeline

`project.timeline` holds `tracks` (list of `{name}`) and `clips`. A clip is:

```json
{"id": "8hex", "asset": "<uid>", "track": 0, "start": 1.5, "duration": 0.75, "overrides": {}}
```

`start` and `duration` are seconds. `TimelineView.sync_clip_durations()` keeps
a clip's duration equal to its asset's current rendered length (so a sample
re-fitted to a different bar count updates in place). Overlapping clips are
permitted and simply summed in mixdown.

## 6. Synthesis engine (`raw/synth`)

### 6.1 SynthParams

```python
@dataclass
class SynthParams:
    oscillator: str = "square"          # see §6.5
    duration: float = 0.25              # nominal length, seconds
    pitch: Trajectory                   # Hz over u∈[0,1]; default exp ramp 880→220
    duty: Trajectory                    # 0..1 over u; square/pulse only
    amplitude: float = 0.8
    pan: float = 0.0
    brightness: float = 0.5             # 0.5 = identity; see §6.7
    envelope: ADSR                      # attack, decay, sustain, release (s, s, level, s)
    drift: Drift                        # analogue instability, seeded
    filters: list[FilterNode]
    seed: int = 0
    stretch_mode: str = "preserve_impact"
    time_structure: TimeStructure | None = None   # None → derived from envelope
    root_note: str | None = None        # note at which the recipe sounds as authored
```

`structure()` returns a **fresh** `TimeStructure` synced to `duration`, deriving
one from the envelope when none is stored. It never mutates `self`; a render
must be side-effect-free or the cache key would change between the first and
second render of the same asset (this was a real bug).

### 6.2 Trajectories

`Trajectory(points=[(u, value), ...], curve)` with `curve ∈ {linear,
exponential, step}`. `evaluate(u)` interpolates on normalised time `u ∈ [0, 1]`.
A scalar is coerced to a single-point constant trajectory. Any filter parameter
may also be a trajectory (§6.7).

### 6.3 Time structure and time map

`TimeStructure(nominal_duration, regions)` tiles `[0, 1]` with `Region`s, each
with a role (`attack`, `transient`, `body`, `tail`, `custom`), an `elasticity`
(0 = rigid, 1 = fully stretches), `min_duration`/`max_duration` in seconds, and
a `mode`:

- `stretch` — region is resampled in time proportionally to its elasticity;
- `sustain` — when lengthened, hold the entry value then traverse at rate 1;
- `loop` — when lengthened, cycle the region's nominal span.

`from_envelope(a, d, r, duration)` is the default derivation: attack → rigid
attack region, decay → rigid transient, then body, then tail with elasticity
0.35.

Stretch presets set elasticities by role:

| preset | attack | transient | body | tail |
|---|---|---|---|---|
| `uniform` | 1 | 1 | 1 | 1 |
| `preserve_attack` | 0 | 1 | 1 | 1 |
| `preserve_impact` | 0 | 0 | 1 | 0.35 |
| `custom` | as stored on the asset |

`build_map(target_seconds)` distributes the requested length change across
regions in proportion to elasticity × extent, honouring min/max (per-region
factor is clamped to `[0.05, 20]`) and returns a `TimeMap` plus warnings.
`TimeMap.to_normalized(t)` maps each output sample's time to `u`.

### 6.4 The render function

`engine.render(params, sample_rate, overrides, tempo, preview) -> RenderResult`:

```
1. mode      = overrides.stretch_mode or params.stretch_mode
   ts        = params.structure(mode)
   target    = resolve_duration(overrides.duration, params.duration, tempo)
   time_map  = ts.build_map(target)
2. t = arange(n)/sr ;  u = time_map.to_normalized(t)
3. rng = default_rng(seed + seed_offset)                 # deterministic
4. f = pitch.evaluate(u) · 2^(pitch_offset/12) · drift.pitch_multiplier(t,u)
   f = clip(f, 1 Hz, 0.98·Nyquist)
   phase = cumsum(2π f / sr)                             # phase integral
5. wave = oscillator(phase, duty.evaluate(u), rng)
6. amp  = envelope.evaluate(u, nominal_duration) · drift.amp_multiplier
   y    = wave · amp · amplitude  (+ drift noise floor · amp)
7. nodes = brightness_chain(brightness) + params.filters (+ spectral overrides)
   y = apply_stack(y, nodes, sr, u)                       # preview drops sr_reduce
8. y → AudioBuffer ; volume_db, pan applied ; peak > 1 warns (no clipping)
```

**Pitch identity.** Because `f` is evaluated at `u` and phase is the integral
of `f`, a uniform stretch by `s` multiplies the total cycle count by exactly
`s`. A resample would keep the cycle count and shift pitch by `12·log₂(s)`
semitones. `tests/test_stretch.py::TestPitchIdentity` asserts both facts and is
the test that guards the product's core claim.

`resolve_duration(value, nominal, tempo)` accepts seconds, `{"musical": "1/8"}`,
or a bar/beat string, so pattern events can specify musical lengths that follow
tempo.

### 6.5 Oscillators

Registered in `raw/synth/oscillator.py` with `@oscillator(name)`; each is a
pure function `(phase, duty, rng) -> samples` and never sees time. Built in:
`sine`, `square`, `triangle`, `saw`, `noise` (phase-clocked), `white`,
`pulse_pair`.

### 6.6 Envelope and drift

`ADSR.evaluate(u, duration)` is defined on normalised time; if a+d+r exceeds
the nominal duration the timed stages are shrunk proportionally (`fitted`).

`Drift` adds seeded analogue instability: a pitch LFO (`pitch_lfo_hz`,
`pitch_depth` semitones), amplitude wobble, and a noise floor, all scaled by
`authenticity`. `time_domain` chooses whether the LFO runs in absolute seconds
or in `u` (so it stretches with the sound).

### 6.7 Filters

`raw/synth/filters.py` defines the shared filter stack used by synth recipes
and by Sample FX. A `FilterNode(type, params, enabled, id)`; any numeric
parameter may be a `Trajectory`, re-evaluated every 128 samples.

| type | params (defaults) | implementation |
|---|---|---|
| `lowpass`, `highpass` | cutoff, resonance | RBJ biquad |
| `bandpass`, `notch` | center, width (octaves → Q) | biquad |
| `low_shelf`, `high_shelf` | corner, gain_db | biquad |
| `emphasis` | center, width, amount (dB) | peaking biquad (the "Spectral Emphasis" of the spec) |
| `bitcrush` | bits | quantise |
| `sr_reduce` | target_rate | sample-and-hold (skipped in preview renders) |
| `drive` | amount | tanh saturation |
| `delay` | time, feedback, mix | feedback echo; lengthens output |
| `reverb` | size, damping, mix, predelay | FFT convolution with a synthetic exponentially-decaying noise IR (fixed seed 20240817, so it is deterministic and cacheable); lengthens output |
| `denoise` | amount, floor | spectral subtraction; noise profile = 25th-percentile magnitude per bin, over-subtraction `1 + 2·amount`, relative floor; input padded both ends to avoid edge artefacts |
| `sustain` | amount, attack, release | control-rate compressor with makeup gain (capped ×4) |

`LENGTHENING_TYPES = (delay, reverb)`: after one of these the stack recomputes
its `u` axis so later trajectory-valued nodes stay in step with the longer
signal. `FILTER_PARAMS` and `FILTER_RANGES` drive the generic filter editor in
the GUI; adding a type needs no UI work.

`brightness_chain(b)` maps the single 0..1 `brightness` control to a low-pass +
high-shelf pair with `b = 0.5` as the identity, so v1 projects (which had no
brightness) render unchanged after migration.

Biquads use `scipy.signal.lfilter` when available and a pure-Python loop
otherwise (~10 ms for a 0.2 s sound; slow for multi-minute renders).

### 6.8 Presets

`engine.preset(name)` for `laser, coin, jump, hit, explosion, blip, pad, drone`.
`pad` and `drone` are sustained voices intended for Chord Lab and instruments.

## 7. Instance overrides (`raw/core/overrides.py`)

An **override** changes how one *use* of an asset renders without touching the
asset. Overrides are dicts `{param: value}` or `{param: {"mode": m, "value":
v}}` with `mode ∈ {set, add, mul}`. Each parameter has a `ParamSpec` giving its
default mode and range:

| param | default mode | range / kind |
|---|---|---|
| `duration` | set | 0.001–600 s, or musical spec |
| `stretch_mode` | set | enum |
| `pitch_offset` | add | −48..48 semitones |
| `volume_db` | add | −60..12 dB |
| `pan` | add | −1..1 |
| `brightness` | set | 0..1 |
| `drift` | mul | 0..4 (scales authenticity) |
| `spectral_center` | mul | 0.05..20 × |
| `spectral_width` | set | 0.05..6 octaves |
| `spectral_amount` | add | −24..24 dB |
| `duty` | set | 0.01..0.99 |
| `seed_offset` | add | int |

`resolve(base, layers)` folds an ordered list of `(layer_name, overrides)` into
one effective dict; `explain(...)` returns the trace for a parameter (used by
the UI to show where a value came from).

**Post-process fast path.** `POST_PROCESS_PARAMS = {volume_db, pan}` can be
applied to an already-rendered buffer. `Renderer` strips them out before
computing the cache key, so changing a clip's volume never triggers a
re-render. `structural_subset(ov)` is the remainder.

## 8. Rendering and caching (`raw/core/renderer.py`)

### 8.1 Renderer

```python
Renderer.render_asset(asset, project, overrides=None, preview=False, use_cache=True) -> AudioBuffer
Renderer.render_placeholder(slot_name, project, overrides=None, preview=False) -> AudioBuffer
Renderer.last_warnings: list[str]
```

Dispatch in `_render_base`:

- `SynthAsset` / `InstrumentAsset` → `engine.render`
- `AudioAsset` → `_render_sample` (§8.3)
- `PatternAsset` → `patterns.renderer.render_pattern` (§9)

`preview=True` produces a cheaper render (currently: drops `sr_reduce`) and is
keyed separately in the cache.

### 8.2 Cache key

```python
key = sha256(json({
    "uid": asset.uid,
    "asset": asset.render_key(),
    "deps": dependency_keys(asset, project),   # transitive render_keys of dependencies
    "ov": structural_subset(overrides),
    "sr": sample_rate, "tempo": tempo, "preview": preview,
}))
```

`dependency_keys` walks `asset.dependencies(project)` transitively (cycles
tolerated). This is what makes "write the pattern now, pick the sound later"
work: editing an instrument or rebinding a slot changes the pattern's key even
though the pattern's own dict is unchanged.

`RenderCache` is a 256 MB LRU in memory (`stats()` is shown in the status bar;
**Project → Clear Render Cache** empties it together with the decoded-source
cache in `audio.io`).

### 8.3 Sample pipeline

For an `AudioAsset`:

```
read_audio_cached(path)                  decoded float64, 256 MB LRU (audio/io.py)
→ slice  [trim_start, trim_start+trim_length]   if is_slice
→ apply_sample_effects: reverse → fade_in/out → gain_db → filters → normalize
→ pitch_offset override (resample ratio 2^(st/12))
→ target length = overrides.duration | fit_bars·bar_seconds(project) | as is
→ _stretch_sample if target ≠ current
```

Normalize runs **last** so that a requested peak is the delivered peak
regardless of filters. Missing source files set `asset.missing = True` and
render 0.25 s of silence with a warning rather than failing.

`_stretch_sample` methods:

- `tape` (default) — `AudioBuffer.resampled_frames(round(target·sr))`, i.e. a
  plain speed change; sample-exact in length, pitch shifts.
- `wsola` — overlap-add time stretch; pitch preserved, approximate.
- `loop_body` — uses the asset's `TimeStructure`: rigid regions are copied
  verbatim, elastic ones WSOLA-stretched, then cross-faded together.

All three add the warning *"imported audio stretched with '…' — this is
approximate"*; only recipes stretch exactly.

Tempo helpers: `bar_seconds(project)`, `bars_for(duration, project)`,
`implied_tempo(duration, bars, project)`. `fit_bars` makes a sample re-fit
automatically when the project tempo changes (the cache key includes tempo).

## 9. Patterns (`raw/patterns`)

### 9.1 Schema

```jsonc
{
  "type": "PatternAsset", "name": "pattern_x",
  "tempo": 120, "steps": 16, "step_division": "1/16", "swing": 0.0,
  "defaults": {"overrides": {...}, "slots": {"kick": "<uid>"}},
  "tracks": [
    {"name": "lead", "instrument": "<uid>" | "slot": "kick", "mute": false,
     "overrides": {...},
     "events": [{"step": 0, "note": "C4", "length": 2 | "1/8", "velocity": 0.8, "overrides": {}}]}
  ]
}
```

`step_seconds = 60/tempo · (4 · numerator/denominator of step_division)`.
Swing delays odd steps by `swing · step/2`.

### 9.2 Event → overrides

`event_overrides` turns a musical event into engine overrides:

- `note` → `pitch_offset = midi(note) − instrument_root_midi(instrument)`, where
  the root is `params.root_note` or, if unset, the pitch trajectory at `u = 0`.
- `length` → `duration`: number = multiples of the step; string = musical spec.
- `velocity` → `volume_db = 20·log10(v)`.

### 9.3 Layer chain (lowest precedence first)

```
pattern.defaults.overrides → instance (clip/pattern-as-a-whole) → slot → track → note → event
```

A slot sits below the track because it is the role's default level and tuning;
a specific pattern or note may still push against it. `duration` is removed
from instance-level overrides because a pattern's length is defined by its
steps and tempo.

### 9.4 Mixing

Each event renders through `Renderer` (so per-event renders are cached and
shared across identical notes), is placed at `round(start·sr)`, and everything
is summed with `AudioBuffer.mix`. The result is padded to `steps · step_seconds`
so loops line up, and normalised with a warning if the sum exceeds 0 dBFS.

### 9.5 Note names and compact strings

`raw/patterns/notes.py`: `C4 = 60`, sharps `#`/`s`, flats `b`, negative
octaves allowed; `note_to_midi`, `midi_to_hz` (A4 = 440). Token classes for the
authoring format's `notes` strings: rest `.`, hold `-` (extends the previous
event's length by one step), trigger `x`, accent `X` (velocity 1.0), `:len`
suffix, `|` ignored as a bar separator.

### 9.6 Chords

`raw/patterns/chords.py`: 20 chord qualities (`CHORDS`), `chord_notes(root,
quality, inversion)`, `render_chord(params, midis, ...)` renders each note via
the engine with a per-note `pitch_offset` from `params_root_midi`, optional
strum offset, sums them and limits to `CEILING = 0.95` (with a warning) —
coherent partials add linearly, so a `1/√n` gain would clip.

## 10. Audio I/O (`raw/audio`)

### 10.1 AudioBuffer

Immutable-by-convention wrapper around a float64 numpy array `(frames,)` or
`(frames, channels)` plus `sample_rate`. Operations return new buffers:
`gain`, `gain_db`, `normalized`, `panned`, `reversed`, `slice_frames`,
`slice_seconds` (copies), `faded`, `mono`, `stereo`, `resampled(rate)`,
`resampled_frames(n)` (linear interpolation to an exact frame count), and the
static `silence`, `concat`, `mix([(buffer, offset_frames)])`. `peaks(columns)`
supplies min/max envelopes for waveform views.

### 10.2 File backends

`read_audio(path)` chooses by extension: `.wav` → built-in `wave`-module reader
(8/16/24/32-bit PCM and float); everything in `SOUNDFILE_EXTENSIONS` → `soundfile`;
fallback → FFmpeg subprocess decoding to raw float32. `write_audio` mirrors this
(`write_wav` is always available; MP3/OGG/FLAC need soundfile or FFmpeg;
`quality` maps to a bitrate for FFmpeg). `backend_summary()`,
`readable_extensions()`, `import_filter()` / `export_filter()` feed the file
dialogs and status bar.

`read_audio_cached(path)` keys on `(path, mtime, size)` and holds up to 256 MB
of decoded audio; a long MP3 sliced twenty times in Sample Lab is decoded once.

### 10.3 Playback

`PlaybackBackend` (`play(buffer, loop)`, `stop`, `available`, `name`) with
`SoundDeviceBackend` and a `NullBackend` fallback. Playback is fire-and-forget;
the *position* shown in the UI is not read back from the device but computed on
a wall clock in the Controller (§11.2).

## 11. Controller (`raw/app/controller.py`)

The Controller owns `project`, `stack: CommandStack`, `renderer`, `playback`,
the current selection and the clip clipboard. UI code talks only to it.

### 11.1 Signals

| signal | payload | emitted when |
|---|---|---|
| `projectChanged` | — | project object replaced (new/open) |
| `assetsChanged` | — | membership or names changed |
| `assetModified` | uid | one asset's content changed |
| `selectionChanged` | uid or `""` | selection moved |
| `timelineChanged` | — | clips/tracks changed |
| `slotsChanged` | — | slot table changed |
| `dirtyChanged` | bool | unsaved-changes state |
| `statusMessage` | text, ms | for the status bar |
| `playbackStarted` | origin, end_seconds | play() called |
| `playbackPosition` | origin, seconds | ~30 Hz while playing |
| `playbackStopped` | origin | natural end or stop() |

### 11.2 Playback progress

`play(buffer, loop=False, origin="preview", offset=0.0)` starts the backend and
a `QTimer`; `_tick_playback` emits positions from `perf_counter`. Looping
playback wraps the position modulo the buffer length and only ends on `stop()`.
`origin` (`"timeline"`, `"preview"`, `"sample_lab"`, `"chord_lab"`) lets each
widget ignore progress that is not its own and reset its play button when
another widget takes over.

### 11.3 Public operations

`new_project`, `open_project`, `save_project`, `push(command)`, `undo`, `redo`,
`add_synth_asset`, `duplicate_asset`, `delete_asset(s)`, `rename_asset`,
`modify_asset(uid, command)`, `import_sample(path)`, `import_authored(doc,
merge=True)`, `bind_slot`, `remove_slot`, `export_authored()`, `render(asset,
overrides, preview)`, `preview(asset, overrides)` (render + play), `play`,
`stop`, `run_async`, `validate`.

## 12. Commands and undo (`raw/core/commands.py`)

Every mutation of the project is a `Command` with `execute(project)` and
`undo(project)`, pushed onto a `CommandStack` (undo/redo, dirty flag, labels).

| command | effect |
|---|---|
| `AddAsset`, `RemoveAsset` | registry membership; RemoveAsset snapshots the asset dict for undo. Timeline clips pointing at a removed asset are left in place and reported by validation |
| `ReplaceAsset` | swap the object under the same uid (used by import-merge) |
| `RenameAsset` | name change (references are by uid, so nothing else needs rewriting) |
| `SetAssetField(uid, field, value)` | one attribute; stores the previous value |
| `SetSettings` | project settings |
| `SetSlot`, `RemoveSlot` | slot table |
| `AddClips`, `RemoveClips`, `MoveClips` | timeline |
| `Composite([...], label)` | one undo step for a batch (an authored import is one Composite) |

**Rule for UI code:** widgets keep a *working copy* of what they edit and build
a command from it; they never mutate the live asset before pushing. Pre-mutating
makes the command's "previous value" snapshot equal to the new value and undo
becomes a no-op. Sample FX (`_filters` + `_staged()`) and Chord Lab are the
reference implementations.

## 13. Authoring format (`raw/core/authoring.py`)

A deliberately small JSON dialect (`"format": "raw.author", "version": 1`) for
humans and language models. Full specification: [AUTHORING_FORMAT.md](AUTHORING_FORMAT.md);
JSON Schema: [raw_author_schema.json](raw_author_schema.json).

```jsonc
{
  "format": "raw.author", "version": 1,
  "project":     {"name": "...", "tempo": 120},
  "slots":       {"kick": {"description": "..."}},
  "sounds":      [ { "name", "from_preset", "oscillator", "pitch", "envelope", "filters", ... } ],
  "instruments": [ { ... same keys ..., "root_note": "C4" } ],
  "patterns":    [ { "name", "tempo", "steps", "step", "swing",
                     "tracks": [ {"name", "instrument"|"slot", "notes": "C4 . E4 - x X", "overrides"} ] } ]
}
```

`parse_document(doc, project, merge)` returns an `AuthorResult` with
`assets` (new), `updated` (replaced), `slots`, `warnings` and `errors`.
Design points:

- Every problem is reported with a JSON-path-style label
  (`sounds[sfx_hit].filters[0]: ...`) so it can be pasted back to the chatbot.
- Out-of-range values are clamped with a warning, unknown keys warn, unknown
  filter parameters warn — the import almost never hard-fails.
- **Merge** (default from the GUI): an incoming asset whose name and type match
  an existing one is applied via `ReplaceAsset`, keeping the uid so timeline
  clips and slot bindings survive a re-import.
- `document_from_project` / `sound_to_author` / `pattern_to_author` go the
  other way (File → Export Authoring Format); compact `notes` strings are
  regenerated from events.
- Every complete example in `AUTHORING_FORMAT.md` is extracted and parsed by
  `tests/test_authoring.py`, so the brief cannot drift from the parser.

## 14. Export

### 14.1 Game pack (`raw/export/game_pack.py`)

`ExportProfile` (formats, bit depth, sample rate, channels, normalise, target
peak, folder layout) with built-ins Generic, Godot, Unity PC, Unity Mobile, Web,
Retro Hardware. `export_game_pack` renders every asset, writes files into the
profile's layout, and emits a `manifest.json`. `export_constants(project, path,
language)` writes an identifier → filename table as Python, C# or GDScript.

### 14.2 Single asset

File → Export Selected Audio renders with the current overrides and writes via
`write_audio` in any writable format.

### 14.3 Sheet music (`raw/export/sheet.py`)

`pattern_to_musicxml(pattern, project, split=60)` and `chord_to_musicxml(midis,
title, project)` return a `SheetResult(xml, note_count, measures, warnings)`;
`write_musicxml` saves it. Implementation:

- MusicXML 4.0 *partwise*, one part "Piano" with two staves (treble/bass);
  `DIVISIONS = 8` per quarter (32nd-note resolution).
- Event times are quantised from steps; musical `length` strings pass through
  `musical_to_divisions`; triplet grids are rounded with a warning.
- Notes with MIDI ≥ `split` go to staff 1, below to staff 2 (a piano reduction
  of all pitched tracks; drum/trigger events and unbound slots are skipped).
- `allocate_voices` packs overlapping notes into up to 4 voices per staff,
  greedily by start time; simultaneous equal-length notes become chords.
- `decompose` splits a duration into tie-able standard note values (dotted
  allowed); notes crossing a barline are tied; gaps are filled with rests;
  `<backup>` separates voices within a measure.
- Key signature is always C major; time signature and tempo come from project
  settings / the pattern. No fingering, dynamics or articulation are written.

Verification in `tests/test_sheet.py` includes a round-trip MusicXML reader,
and the Walking Hands example was cross-checked by rendering with Verovio.

### 14.4 Bake (`raw/core/bake.py`)

`bake_buffer(project, buffer, name, bit_depth=16, meta, tags, description)`
writes `assets/generated/<slug>.wav` next to the project file (suffixing `_1`,
`_2` on collision) and returns an `AudioAsset` referencing it relatively. Raises
`BakeError` if the project has never been saved, because a relative reference
needs a root. Chord Lab uses this; the chord recipe is kept in `asset.meta`.

## 14.5 Danas Piano Tutor (`raw/teach`, `raw/ui/teach`)

A second application over the same engine, launched with `teach.py` (or
**Help → Danas Piano Tutor** in RAW). It turns a *lesson* — a beginner piece
written per hand with fingering — into playback, a lit-up keyboard and hand
diagram, an engraved score, and printable output. Specification for authors:
[LESSON_FORMAT.md](LESSON_FORMAT.md).

```
raw/teach/
├── score.py      Note / Lesson model on the DIVISIONS grid; keys, spelling,
│                 five-finger positions, inferred fingering, problem checks
├── authoring.py  the `lessons` dialect of raw.author: tokenizer for
│                 `C4:2(3)`, `[C3 E3 G3](5,3,1)`, `.`, `-`, `|`; parallel
│                 `fingers` line; bar-length check; round-trip serialiser
├── voice.py      built-in voices (piano, music_box, organ, chip) and clicks
├── player.py     render a lesson to an AudioBuffer: tempo, hands, metronome,
│                 count-in, bar range; per-note render cache
├── engrave.py    layout of a grand staff in staff-space units (no Qt)
├── sheet.py      lesson -> MusicXML through export/sheet.build_musicxml
└── main.py       entry point
raw/ui/teach/
├── painter.py    draws an engrave.Layout with QPainter (screen and print)
├── staff_view.py, lane_view.py, hands_view.py   the three views
├── transport.py  LessonTransport: wall-clock position, two-stage looping
├── script_editor.py, export.py (PDF via QPdfWriter, QPrinter), splash.py, window.py
```

Design points:

- **Time base.** Lessons live on `export.sheet.DIVISIONS` (8 per quarter).
  Seconds are derived from the chosen tempo at render time, so slowing a piece
  down lengthens every note musically (`duration` override) instead of
  resampling. `Lesson.length` is padded to whole measures.
- **Fingering inference.** `Lesson.resolve_fingers()` walks each hand in time,
  moving a five-finger position whenever a written finger implies one, and
  fills `Note.inferred` for unfingered notes under the hand. The score shows
  inferred fingers in grey and only where they change; MusicXML export can
  include or omit them.
- **Spelling.** `score.spell(midi, fifths, prefer)` spells in-key notes as the
  key signature does, honours an accidental the author typed (`Bb4` stays a
  B-flat in C major), then prefers naturals, then the key's own accidental
  family. `export.sheet.build_musicxml` gained `fifths`, `spellings`,
  `composer`, `NoteEvent.finger` and `NoteEvent.staff` for this; the
  per-measure chunking (`voice_measure_pieces`) is shared with the engraver so
  ties, rests and dots agree between screen, PDF and MusicXML.
- **Engraving.** `engrave.Engraver` allocates voices per staff with
  `allocate_voices`, gives each onset a width by `slot_width(divs)`, breaks
  lines greedily and stretches them to the margin, then places noteheads,
  stems (direction by distance from the middle line; forced apart for two
  voices), flags, dots, ledger lines, accidentals (tracked per measure),
  ties (including across systems), fingering (RH above, LH below, stacked in
  pitch order) and key/time signatures. Output is geometry in staff spaces;
  `ui/teach/painter.py` draws it at any scale, on screen with the RAW palette
  or in black on white for `QPdfWriter` / `QPrinter`. Clefs and rests use the
  system's music glyphs (Segoe UI Symbol on Windows), fitted by measured ink
  bounds; a drawn fallback exists for fonts without them. Beams are not drawn
  (eighths get flags), which is adequate for beginner material.
- **Playback.** `player.render_lesson` mixes one engine render per note (cached
  by voice, pitch, length and gain) plus metronome clicks on the beat grid,
  with an optional count-in bar prefixed. `LessonTransport` plays the count-in
  and the first pass once, then hands the section alone to the backend with
  `loop=True`, so a practice loop counts in exactly once. Position is derived
  from `perf_counter`, as in the main app.

Tests: `tests/test_teach.py` (parser, fingering inference, examples parse with
zero warnings, render timing, engraving geometry, MusicXML content, a window
test under the offscreen platform including PDF output).

## 15. User interface (`raw/ui`)

### 15.1 Window layout

`MainWindow` sets `TimelineDock` as the central widget and creates seven
`QDockWidget`s:

| dock | area | widget | purpose |
|---|---|---|---|
| ASSET MANAGER | left | `AssetManager` | list/filter/rename/drag source |
| SYNTHESIZER | right (tab 1) | `SynthDock` | edit `SynthParams` with live preview |
| TIME STRUCTURE | right (tab 2) | `TimeStructureDock` | regions, elasticity, stretch preview |
| SAMPLE LAB | bottom (tab 1) | `SampleLab` | splice bars: start/length sliders, loop play, store as slice |
| SAMPLE FX | bottom (tab 2) | `SampleFx` | reverse, fades, gain, normalize, filter stack, fit-to-bars, autofit |
| CHORD LAB | bottom (tab 3) | `ChordLab` | keyboard, chord picker, voice, bake, sheet export |
| CONSOLE | bottom (tab 4) | `ConsoleDock` | Python REPL over the controller |

Docks are hidden or disabled when the selected asset is of the wrong type (the
synth dock for an `AudioAsset`, Sample Lab for a `SynthAsset`), so they never
display stale values.

Menus: File (project, import sample / authored JSON, export audio / pack /
constants / authoring / sheet), Edit (undo/redo, duplicate, delete, timeline
clip actions), View (dock toggles, reset layout), Project (settings, validate,
clear cache), Audio (new sound from preset, preview `Space`, stop `Ctrl+.`,
render timeline, demo sounds), Help (copy chatbot brief, shortcuts, about).

### 15.2 Timeline

`TimelineView(QGraphicsView)` draws bars/beats from `project.settings`, clips
as `ClipItem`s and a playhead. Snapping uses `snap` (nearest) for moves and
`snap_up` (ceil) for placement after a drop so a dropped clip never lands on top
of the one before it. Drag-and-drop from the Asset Manager carries uids in a
custom MIME type (`dnd.py`). Copy/cut/paste keep a clipboard of clip dicts plus
the source track so paste lands on the track it came from. `render_timeline`
mixes all clips (`Renderer` per clip with clip overrides) and plays with
`origin="timeline"`; the transport shows a BPM regulator that targets the
selected pattern's tempo if a pattern is selected, else the project's, and a
progress readout driven by `playbackPosition`. The playhead returns to the play
start position on stop.

### 15.3 Sample Lab and Sample FX

Sample Lab shows `SpliceBar`s for the selected `AudioAsset`: two sliders
(start to 1 ms, length), a loop-play toggle, and **Store**, which creates a new
`AudioAsset` sharing the same `source_path` with `trim_start`/`trim_length`
set — nothing is copied. Sample FX edits the non-destructive chain (§8.3) on a
working copy and pushes one `SetAssetField` per commit; **Fit to bars** and the
autofit ▲/▼ buttons walk a bar ladder (`BAR_LADDER`) and set `fit_bars`, showing
the implied tempo.

### 15.4 Console

A REPL with a namespace bound to the controller: `presets`, `new`, `find`,
`assets`, `render`, `play`, `stretch`, `set`, `slots`/`bind`/`unbind`, `sheet`,
`author`/`author_file`/`export_author`, `save`/`load`, plus `project`,
`controller`, `engine`, `np`. It is explicitly **not a sandbox**; everything it
does goes through commands and is undoable.

### 15.5 Theme

`theme.py` holds `COLORS` and the Qt stylesheet. It has been customised by hand
and should be treated as user content.

## 16. Validation and migrations

`validation.validate(project) -> list[Issue(level, where, message)]` checks
name uniqueness/validity, unknown asset types, synth parameter and
time-structure sanity, missing audio files, pattern track references, and slots
bound to assets that no longer exist.
**Project → Validate Project** shows the summary.

`migrations.MIGRATIONS` is an ordered list of `(from_version, fn)`.
`_v1_to_v2` adds `filters`, `brightness = 0.5`, `stretch_mode`,
`time_structure = None`, `stretch_method = "tape"`, empty `overrides` dicts —
each chosen so a v1 project renders bit-identically after migration. A project
from a newer build loads with a note and unknown data preserved.

## 17. Testing

`tests/` (unittest, 430 tests):

| file | covers |
|---|---|
| `test_stretch` | time map, presets, **pitch identity**, resample contrast |
| `test_core` | registry, commands/undo, cache hits, project round-trip, migration |
| `test_authoring` | parser, warnings, merge, every example in the brief |
| `test_timeline`, `test_merge_transport` | drop/paste placement, clipboard, BPM regulator, playhead |
| `test_slots` | binding, placeholders, dependency-driven cache invalidation |
| `test_sample_lab`, `test_sample_fx`, `test_fit_to_bars`, `test_time_effects` | slicing, effect order, normalize-last, tape stretch exactness, delay/reverb/denoise/sustain behaviour |
| `test_chord_lab` | chord tables, ceiling limiter, bake freshness |
| `test_sheet` | MusicXML structure, voices, ties, round-trip reader |
| `test_teach` | Piano Tutor: lesson parser, fingering inference, examples, player timing, engraving, MusicXML with fingering, window + PDF |

UI tests construct the real widgets under an offscreen `QApplication` and drive
them through their public methods.

## 18. Extension points

See [EXTENDING.md](../EXTENDING.md). In short: oscillators and asset types are
decorator registries; filters are three table entries plus a branch in
`apply_filter`; overridable parameters are a `ParamSpec`; docks take the
controller and subscribe to its signals; migrations append to a list.

## 19. Known limitations and roadmap

Not built:

- Step-grid **pattern editor** — patterns are authored as JSON or via console.
- Music/melody/rhythm **generators** (`project.generators` is reserved).
- **Collect Project Assets** (copy absolute-path sources into the project
  folder) and a relocate-missing-file dialog.
- Timeline → sheet export (only patterns and chords), key signatures other than
  C, fingering/dynamics in MusicXML.
- Zero-crossing snapping in Sample Lab; clip fades/crop/automation on the
  timeline.
- MIDI import/export.

Inherent:

- Only recipes stretch exactly; imported audio stretches approximately and says
  so.
- The console runs with full user permissions.
- Biquads are slow without scipy on long material.

## 20. Glossary

| term | meaning |
|---|---|
| **recipe** | a `SynthParams` — the description from which audio is rendered |
| **u** | normalised nominal time in `[0, 1]`; trajectories and envelopes are defined on it |
| **time structure / time map** | region model of a sound and the monotonic output-time → nominal-time mapping built from it for a target length |
| **elasticity** | how much of a length change a region absorbs (0 rigid … 1 fully) |
| **override** | per-use parameter change (`set`/`add`/`mul`) resolved through a layer chain |
| **post-process param** | an override applied to the finished buffer (`volume_db`, `pan`) |
| **slot** | a named role a pattern references before a sound is chosen; unbound slots render a placeholder |
| **slice** | an `AudioAsset` with `trim_start`/`trim_length` over a shared source |
| **fit_bars** | a sample length expressed in bars, re-derived from tempo |
| **bake** | render a generated sound to a WAV under the project and register it as an `AudioAsset` |
| **render key** | the audio-affecting subset of an asset's fields, hashed for the cache |
