# RAW Authoring Format — specification for writing game audio as JSON

You are writing sound effects and music for **Retro Audio Workstation (RAW)**, a
retro game-audio tool. In RAW a sound is not a recording — it is a **recipe**: an
oscillator, a pitch curve, an envelope, and some filters. You write the recipe as
JSON and RAW synthesises the audio.

This means you can author real, playable game audio in a chat window. Produce a
single JSON document in the format below; the user pastes it into RAW with
**File → Import Authored JSON** (Ctrl+J).

Output **one JSON object, nothing else** — no markdown fence commentary inside
the JSON, no trailing commas, no comments. Explain your choices in prose *outside*
the JSON if you want to.

---

## 1. Quick card

The smallest useful document:

```json
{
  "format": "raw.author",
  "version": 1,
  "sounds": [
    {"name": "sfx_jump", "from_preset": "jump"}
  ]
}
```

Everything has a default. `format`, `version`, and a `name` on each entry are the
only things you should always write.

---

## 2. Document skeleton

```json
{
  "format": "raw.author",
  "version": 1,
  "project":     { "name": "Cave Crawler", "tempo": 132, "sample_rate": 44100, "fps": 60 },
  "sounds":      [ ... ],
  "instruments": [ ... ],
  "patterns":    [ ... ]
}
```

| Key | Meaning |
|---|---|
| `project` | optional; sets project name, tempo (BPM), sample rate, game FPS |
| `sounds` | one-shot SFX — jumps, hits, pickups, lasers |
| `instruments` | recipes meant to be **played at many pitches** by patterns |
| `patterns` | musical sequences that trigger instruments |

`sounds` and `instruments` take **exactly the same fields**. The only difference
is intent: a pattern can reference either, but `instruments` is where you put
things like `square_lead` or `bass_pulse`.

---

## 3. Sounds and instruments

```json
{
  "name": "player_laser",
  "from_preset": "laser",
  "oscillator": "square",
  "duration": 0.18,
  "pitch": {"from": 900, "to": 120, "curve": "exponential"},
  "duty": 0.25,
  "envelope": {"attack": 0.001, "decay": 0.02, "sustain": 0.7, "release": 0.06},
  "brightness": 0.6,
  "drift": 0.2,
  "amplitude": 0.8,
  "pan": 0.0,
  "stretch": "preserve_attack",
  "seed": 0,
  "root_note": "A5",
  "filters": [{"type": "emphasis", "center": 3200, "width": 0.5, "amount": 9}],
  "tags": ["weapon", "player"]
}
```

| Field | Type | Range | Default | Notes |
|---|---|---|---|---|
| `name` | string | `[a-z_][a-z0-9_]*` | **required** | becomes the variable name in the game |
| `from_preset` | string | see §8 | — | start from a built-in recipe, then override fields |
| `oscillator` | string | see §8 | `square` | |
| `duration` | number | 0.005–60 | 0.25 | seconds |
| `pitch` | see §4 | 1–20000 Hz | 880→220 ramp | |
| `duty` | number | 0.01–0.99 | 0.5 | square/pulse only; 0.125 and 0.25 are the classic chip values |
| `envelope` | object/array | see §5 | short pluck | |
| `brightness` | number | 0–1 | 0.5 | tone tilt. **0.5 changes nothing**; below is duller, above is brighter |
| `drift` | number/object | 0–1 | 0.25 | hardware imperfection. 0 = clinical, 0.3 = characterful, 0.7+ = broken |
| `amplitude` | number | 0–1.5 | 0.8 | lower it if you add boosting filters |
| `pan` | number | −1–1 | 0 | −1 left, +1 right |
| `stretch` | string | see §8 | `preserve_impact` | how the sound behaves when a pattern asks for a different length |
| `regions` | array | see §7 | derived | advanced; usually omit |
| `seed` | integer | ≥ 0 | 0 | changes the noise/drift realisation, not the pitch |
| `root_note` | note | e.g. `"A4"` | derived | see §9 — **set this on instruments** |
| `filters` | array | see §6 | `[]` | |
| `tags` | array of strings | | `[]` | e.g. `weapon`, `ui`, `enemy`, `music` |
| `description` | string | | `""` | |

### Designing a sound, in practice

- **Downward pitch sweep** = laser, zap, fall. **Upward** = jump, powerup, alert.
- **Very short attack** (0.001–0.005 s) is what makes something read as percussive.
- **`noise` oscillator with a downward sweep** = impact, explosion, footstep.
- **Two-step pitch jump** (see the `coin` preset) = pickup, coin, confirm.
- Retro crunch comes from `bitcrush` (5–8 bits) and `sr_reduce` (4000–16000 Hz).

---

## 4. Pitch

Five accepted forms. Frequencies may be written as numbers (hertz) **or note
names** — `"A4"` and `440` are the same thing.

```json
"pitch": 440                                            // constant
"pitch": "C5"                                           // constant, by note
"pitch": {"from": 900, "to": 120, "curve": "exponential"}   // sweep
"pitch": {"from": "A5", "to": "B2"}                     // sweep, by note
"pitch": {"points": [{"at": 0.0, "hz": 988},
                     {"at": 0.2, "hz": 988},
                     {"at": 0.22, "hz": 1319},
                     {"at": 1.0, "hz": 1319}],
          "curve": "step"}                              // multi-point
```

`at` is **normalised position, 0.0 to 1.0**, not seconds. That is deliberate: it
is what lets RAW change a sound's length without changing its pitch.

`curve` is `exponential` (default — musically even, use for sweeps), `linear`, or
`step` (holds each value until the next point; use for the two-tone coin shape).

---

## 5. Envelope

```json
"envelope": {"attack": 0.002, "decay": 0.03, "sustain": 0.6, "release": 0.08}
"envelope": [0.002, 0.03, 0.6, 0.08]
```

Attack, decay, and release are **seconds**; sustain is a **level** from 0 to 1.
If the three times exceed `duration` they are scaled down proportionally.

---

## 6. Filters

An ordered list. Each entry needs a `type`; its parameters are listed below.

```json
"filters": [
  {"type": "lowpass", "cutoff": 4200, "resonance": 0.9},
  {"type": "emphasis", "center": 1800, "width": 1.2, "amount": 6.5},
  {"type": "bitcrush", "bits": 6}
]
```

| `type` | Parameters | Use it for |
|---|---|---|
| `lowpass` | `cutoff` Hz, `resonance` 0.05–20 | darkening, muffling |
| `highpass` | `cutoff` Hz, `resonance` | thinning, removing rumble |
| `bandpass` | `center` Hz, `width` octaves | telephone, radio |
| `notch` | `center` Hz, `width` | hollowing |
| `low_shelf` | `corner` Hz, `gain_db` ±24 | body, weight |
| `high_shelf` | `corner` Hz, `gain_db` ±24 | air, sizzle |
| `emphasis` | `center` Hz, `width` octaves, `amount` dB ±24 | **character** — see below |
| `bitcrush` | `bits` 1–24 | retro quantisation grit |
| `sr_reduce` | `target_rate` Hz | retro aliasing |
| `drive` | `amount` ≥ 0 | saturation, thickness |

**Spectral emphasis** is the main character control. A resonant bump is the
difference between a thin laser and a fat one:

| Character | center | width | amount |
|---|---|---|---|
| Metallic | 3200 | 0.5 | +9 |
| Thick | 180 | 1.5 | +6 |
| Nasal | 1100 | 0.4 | +11 |
| Hollow | 800 | 0.8 | −10 |
| Glassy | 6400 | 0.6 | +7 |

Put `bitcrush` and `sr_reduce` **last** — their artefacts are the point, and
filtering after them removes what you asked for.

Any filter number may instead be a sweep, using the same syntax as pitch:

```json
{"type": "lowpass", "cutoff": {"from": 6000, "to": 400}, "resonance": 0.9}
```

---

## 7. Regions (advanced — usually omit)

RAW splits a sound into regions so that changing its length does not destroy its
character. By default this is derived from the envelope and you never think about
it. `stretch` picks the behaviour:

| `stretch` | Effect |
|---|---|
| `preserve_impact` | **default.** Onset and impact stay fixed; the body absorbs length changes. Use for hits, coins, jumps. |
| `preserve_attack` | Only the onset stays fixed. Use for lasers, engines, risers. |
| `uniform` | Everything scales together. Use for tones and drones. |
| `custom` | You supply `regions`. |

Only if you need `custom`:

```json
"regions": [
  {"role": "attack",    "length": 1,  "elasticity": 0},
  {"role": "transient", "length": 3,  "elasticity": 0},
  {"role": "body",      "length": 10, "elasticity": 1.0, "mode": "sustain"},
  {"role": "tail",      "length": 6,  "elasticity": 0.35}
]
```

`length` values are relative weights and are normalised for you. `elasticity` 0
means the region never stretches; 1.0 means it absorbs change fully. `mode` is
`stretch` (traverse more slowly), `sustain` (hold, then continue — good for pads),
or `loop` (repeat the region's content).

---

## 8. Vocabulary

```
oscillator:   sine  square  triangle  saw  noise  white  pulse_pair
from_preset:  laser  coin  jump  hit  explosion  blip  pad  drone
stretch:      uniform  preserve_attack  preserve_impact  custom
curve:        exponential  linear  step
region role:  attack  transient  body  tail  custom
region mode:  stretch  sustain  loop
```

`noise` is *pitched* noise (it follows the pitch curve, like a chip LFSR).
`white` is plain white noise. For explosions use `white`; for snares and hits use
`noise` with a downward sweep.

`pad` and `drone` are the sustained presets, for background beds rather than
one-shots. `pad` swells in and out over four seconds with a slow filter sweep;
`drone` is flat-topped and constant so it loops cleanly. Both use
`"stretch": "uniform"`, so asking for a longer duration simply gives you more of
the same rather than a stretched envelope.

---

## 9. Instruments and transposition — read this

When a pattern plays a note, it does **not** replace the instrument's pitch
curve. It **transposes** it. A laser played at C5 is the same sweep, an octave up.

`root_note` is the note at which the instrument sounds exactly as written. If you
omit it, RAW derives it from the pitch at the start of the curve.

**Always set `root_note` explicitly on instruments you intend to play melodically.**

```json
{
  "name": "square_lead",
  "oscillator": "square",
  "duty": 0.25,
  "pitch": "C4",
  "root_note": "C4",
  "duration": 0.25,
  "envelope": [0.005, 0.04, 0.7, 0.05],
  "stretch": "preserve_attack",
  "brightness": 0.6
}
```

For **drums and percussion**, omit `note` on the events entirely — the instrument
plays at its own pitch. Do not write `"note": "C4"` on a kick drum; it will
transpose the kick.

---

## 10. Patterns

```json
{
  "name": "pattern_boss_intro",
  "tempo": 140,
  "steps": 16,
  "step": "1/16",
  "swing": 0.0,
  "tracks": [
    {"name": "lead",  "instrument": "square_lead",  "notes": "C4 . E4 . G4 - - . C5 . G4 . E4 . C4 ."},
    {"name": "bass",  "instrument": "bass_pulse",   "notes": "C2 . . C2 G2 . . G2 C2 . . C2 A1 . . A1"},
    {"name": "drums", "instrument": "drum_kick",    "notes": "x . . . x . . . x . . . x . x ."},
    {"name": "snare", "instrument": "drum_snare",   "notes": ". . . . X . . . . . . . X . . ."}
  ]
}
```

| Field | Default | Notes |
|---|---|---|
| `name` | **required** | |
| `tempo` | 120 | BPM, 20–400 |
| `steps` | 16 | pattern length in steps |
| `step` | `"1/16"` | musical value of one step |
| `swing` | 0 | 0–1; delays every second step |
| `tracks` | **required** | |
| `overrides` | — | applied to every event in the pattern (see §12) |

### Track

| Field | Notes |
|---|---|
| `name` | label only |
| `instrument` | **the `name` of a sound or instrument** in this document or already in the project |
| `notes` | compact string, see below |
| `events` | explicit list, see below — may be used together with `notes` |
| `mute` | boolean |
| `overrides` | applied to every event on this track |

### The compact `notes` string

One token per step, separated by spaces. `|` is ignored, so you may use it as a
bar separator for readability.

| Token | Meaning |
|---|---|
| `C4`, `F#3`, `Bb5` | play that note |
| `.` | rest |
| `-` | hold — extends the previous note by one step |
| `x` | trigger at the instrument's own pitch (**use for drums**) |
| `X` | accented trigger (velocity 1.0) |
| `C4:1/8` | play C4 with an explicit length of an eighth note |
| `C4:4` | play C4 for 4 steps |

```
"notes": "C4 . E4 . | G4 - - . | C5 . . . | G4 . E4 ."
```

### Slots — leaving the sounds for later

A track may name a **slot** instead of an instrument. A slot is a role — `kick`,
`lead` — that gets bound to a real sound later, in the app.

This is often the better way to write music. Rhythm and structure are the easy
part; designing four instruments that sit well together is the hard part. If the
user has their own samples, or hasn't decided on sounds yet, **write slots and
skip the instruments entirely.**

```json
{
  "format": "raw.author",
  "version": 1,
  "project": {"name": "Slot Sketch", "tempo": 132},
  "slots": {"kick": null, "snare": null, "hat": null, "lead": null},
  "patterns": [
    {
      "name": "groove", "tempo": 132, "steps": 16, "step": "1/16",
      "tracks": [
        {"name": "kick",  "slot": "kick",  "overrides": {"volume_db": -6},
         "notes": "x . . . x . . . | x . . x . . x ."},
        {"name": "snare", "slot": "snare", "overrides": {"volume_db": -9},
         "notes": ". . . . X . . . | . . . . X . . ."},
        {"name": "hat",   "slot": "hat",   "overrides": {"volume_db": -14},
         "notes": "x . x . x . x . | x . x . x . x ."},
        {"name": "lead",  "slot": "lead",  "overrides": {"volume_db": -10},
         "notes": "C4 . D#4 . G4 - . F4 | D#4 . C4 . A#3 - . ."}
      ]
    }
  ]
}
```

An unbound slot is **not silent** — it plays a short placeholder click, pitched
from the slot name so each role sounds different. The groove above is fully
audible the moment it is imported, with no sounds defined at all.

`"slots"` at the top of the document declares the roles. Give a sound's `name`
to bind one immediately, or `null` to leave it open:

```json
"slots": {
  "kick": "sfx_thump",
  "snare": null,
  "lead": {"sound": "square_lead", "overrides": {"volume_db": -4}}
}
```

Slot names are yours to choose; `kick`, `snare`, `hat`, `bass`, `lead`, `pad`,
`arp` are conventional and will be recognisable to the user.

Use `slot` **or** `instrument` on a track, not both.

### Explicit `events`

Use these when you need per-note control:

```json
"events": [
  {"step": 0,  "note": "C4", "length": "1/8", "velocity": 0.9},
  {"step": 4,  "note": "E4", "velocity": 0.7, "overrides": {"brightness": 0.8}},
  {"step": 11, "note": "G4", "overrides": {"pitch_offset": 0.25, "spectral_amount": 4}}
]
```

| Field | Notes |
|---|---|
| `step` | integer step index, 0-based |
| `note` | note name; **omit for drums** |
| `length` | `"1/8"` musical, or a number of steps. **Omit and the sound plays at its own natural length** — which is what you want for drums and one-shots |
| `velocity` | 0–1, becomes a volume change (1.0 = unchanged) |
| `overrides` | see §12 |

---

## 11. Note names

`C4` is middle C (MIDI 60). `A4` = 440 Hz. Sharps `C#4`, flats `Bb3`. Octave
numbers may be negative. Useful ranges:

```
bass          C1 – C3
lead / melody C4 – C6
sparkle / UI  C6 – C7
```

---

## 12. Per-event overrides

Any event, track, or pattern may override the instrument's parameters without
changing the instrument itself. This is how you get variation from one recipe.

| Parameter | Applied as | Range |
|---|---|---|
| `duration` | replaces | seconds, or `{"musical": "1/8"}`, or `{"ratio": 2.5}` |
| `pitch_offset` | **adds** semitones | −48 to 48 (fractions allowed for detune) |
| `volume_db` | **adds** dB | −60 to 12 |
| `pan` | **adds** | −1 to 1 |
| `brightness` | replaces | 0 to 1 |
| `drift` | **multiplies** | 0 to 4 |
| `spectral_center` | **multiplies** | 0.05 to 20 |
| `spectral_width` | replaces | 0.05 to 6 octaves |
| `spectral_amount` | **adds** dB | −24 to 24 |
| `duty` | replaces | 0.01 to 0.99 |
| `seed_offset` | **adds** | integer — a different noise realisation, same recipe |
| `stretch_mode` | replaces | see §8 |

`seed_offset` is the cheapest way to stop repeated hits sounding identical: give
each one a different integer.

---

## 13. Worked example — a small SFX pack

```json
{
  "format": "raw.author",
  "version": 1,
  "project": {"name": "Cave Crawler", "tempo": 132},
  "sounds": [
    {
      "name": "sfx_jump",
      "oscillator": "square",
      "duration": 0.14,
      "duty": 0.125,
      "pitch": {"from": "A3", "to": "E5", "curve": "exponential"},
      "envelope": [0.001, 0.03, 0.6, 0.05],
      "drift": 0.15,
      "stretch": "preserve_attack",
      "tags": ["player", "movement"]
    },
    {
      "name": "sfx_coin",
      "oscillator": "square",
      "duration": 0.16,
      "duty": 0.5,
      "pitch": {"points": [{"at": 0.0, "note": "B5"}, {"at": 0.18, "note": "B5"},
                           {"at": 0.2, "note": "E6"}, {"at": 1.0, "note": "E6"}],
                "curve": "step"},
      "envelope": [0.002, 0.02, 0.8, 0.09],
      "brightness": 0.62,
      "tags": ["ui", "pickup"]
    },
    {
      "name": "sfx_hit",
      "oscillator": "noise",
      "duration": 0.2,
      "amplitude": 0.6,
      "pitch": {"from": 1800, "to": 200},
      "envelope": [0.001, 0.05, 0.25, 0.12],
      "drift": 0.3,
      "brightness": 0.45,
      "filters": [{"type": "emphasis", "center": 900, "width": 1.2, "amount": 5}],
      "tags": ["enemy", "combat"]
    },
    {
      "name": "sfx_door",
      "oscillator": "saw",
      "duration": 0.9,
      "pitch": {"from": 120, "to": 60},
      "envelope": [0.05, 0.3, 0.5, 0.4],
      "drift": 0.4,
      "filters": [
        {"type": "lowpass", "cutoff": {"from": 3000, "to": 300}, "resonance": 2.0},
        {"type": "bitcrush", "bits": 7}
      ],
      "stretch": "uniform",
      "tags": ["environment"]
    }
  ]
}
```

## 14. Worked example — instruments and a pattern

```json
{
  "format": "raw.author",
  "version": 1,
  "project": {"name": "Boss Fight", "tempo": 148},
  "instruments": [
    {
      "name": "square_lead",
      "oscillator": "square", "duty": 0.25,
      "pitch": "C4", "root_note": "C4",
      "duration": 0.22, "amplitude": 0.6,
      "envelope": [0.004, 0.05, 0.7, 0.06],
      "brightness": 0.62, "drift": 0.12,
      "stretch": "preserve_attack"
    },
    {
      "name": "bass_pulse",
      "oscillator": "square", "duty": 0.5,
      "pitch": "C2", "root_note": "C2",
      "duration": 0.2, "amplitude": 0.6,
      "envelope": [0.002, 0.08, 0.55, 0.05],
      "brightness": 0.35, "drift": 0.1,
      "stretch": "preserve_attack"
    },
    {
      "name": "drum_kick",
      "oscillator": "sine",
      "pitch": {"from": 170, "to": 45, "curve": "exponential"},
      "root_note": "F2",
      "duration": 0.18,
      "envelope": [0.001, 0.07, 0.2, 0.08],
      "amplitude": 0.8, "drift": 0.05
    },
    {
      "name": "drum_snare",
      "oscillator": "white",
      "pitch": 1200,
      "duration": 0.15,
      "amplitude": 0.5,
      "envelope": [0.001, 0.05, 0.15, 0.09],
      "filters": [{"type": "highpass", "cutoff": 900, "resonance": 0.8},
                  {"type": "bitcrush", "bits": 8}]
    }
  ],
  "patterns": [
    {
      "name": "pattern_boss_a",
      "tempo": 148,
      "steps": 16,
      "step": "1/16",
      "tracks": [
        {"name": "lead",  "instrument": "square_lead", "overrides": {"volume_db": -9},
         "notes": "C5 . D#5 . G5 - . F5 | D#5 . C5 . A#4 - . ."},
        {"name": "bass",  "instrument": "bass_pulse", "overrides": {"volume_db": -10},
         "notes": "C2 . C2 . G#1 . G#1 . | A#1 . A#1 . G1 . G1 ."},
        {"name": "kick",  "instrument": "drum_kick", "overrides": {"volume_db": -6},
         "notes": "x . . . x . . . | x . . x . . x ."},
        {"name": "snare", "instrument": "drum_snare", "overrides": {"volume_db": -10},
         "notes": ". . . . X . . . | . . . . X . . ."}
      ]
    }
  ]
}
```

### Mixing levels — do not skip this

Retro waveforms are square and loud; four tracks hitting the same step sum to
roughly four times one track. Without level control a pattern will clip and RAW
will normalise the whole thing down, which squashes the mix.

Set a `volume_db` override on each track. As a rule of thumb, subtract about
6 dB for every doubling of the number of simultaneous tracks:

| Tracks that can hit together | Typical track `volume_db` |
|---|---|
| 2 | −6 |
| 3 | −8 |
| 4 | −10, with the kick a few dB louder |

Keep instrument `amplitude` around 0.5–0.8 so each one still sounds right when
auditioned alone, and do the balancing at the track level.

---

## 15. Rules

1. **One JSON object. No comments, no trailing commas.**
2. `name` must be a safe identifier: lowercase letters, digits, underscores,
   starting with a letter or underscore. Use game-oriented names —
   `sfx_jump`, `player_laser`, `bgm_boss`, `pattern_intro`.
3. Reference instruments **by name**. RAW converts names to stable internal IDs
   on import, so later renames will not break the pattern.
4. Define an instrument **before** the pattern that uses it, in the same document
   — or rely on one already present in the user's project.
5. `at` values in pitch points are 0.0–1.0, never seconds.
6. Omit `note` for drums. Omit `length` unless you specifically want the sound
   cut short or held.
7. Prefer `from_preset` when the user wants something ordinary; write the recipe
   out in full when they want something specific.
8. Do not invent field names or oscillator names. Anything not listed here is
   ignored with a warning.
9. If the user hasn't chosen sounds, or is bringing their own samples, write
   **slots** and no instruments. A slot-only document is valid and audible.

## 16. What happens on import

RAW is strict about structure and forgiving about values:

- Numbers outside the allowed range are **clamped and reported**, not rejected.
- Unknown fields, unknown oscillators, and unknown filter types are **skipped with
  a warning that lists the valid options**.
- A missing `name` on an entry is an error; that entry is skipped, the rest import.
- **Re-importing an edited document updates the existing assets in place**,
  matched by name. Their internal IDs are kept, so timeline clips and slot
  bindings keep pointing at them. Names are therefore worth keeping stable
  between revisions — change a name and you create a second asset.
- The whole import is a single undo step.

The user sees a report listing every warning. If they paste that report back to
you, use it to correct the document — the messages name the exact path, for
example `sounds[sfx_hit].filters[0]: emphasis has no parameter 'q'`.
