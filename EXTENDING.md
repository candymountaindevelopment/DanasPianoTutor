# Extending

Every extension point below is a registry. Adding to one requires no changes to
serialisation, the GUI, or the tests.

## Add an oscillator

`raw/synth/oscillator.py`. Oscillators are pure functions of accumulated phase —
they never see time, which is what makes stretching exact.

```python
@oscillator("fm_bell")
def _fm_bell(phase, duty, rng):
    return np.sin(phase + 3.0 * np.sin(phase * 2.01))
```

It appears in the synth dock's Waveform list immediately.

## Add a filter type

`raw/synth/filters.py`. Add the name to `FILTER_TYPES`, its parameters and
defaults to `FILTER_PARAMS`, and a branch in `apply_filter`. The filter dialog
builds its editor from `FILTER_PARAMS`, so no UI work is needed.

Any parameter may be a `Trajectory` instead of a number; trajectory-valued
parameters are re-evaluated every 128 samples so they sweep with the sound.

## Add an asset type

`raw/core/assets.py`:

```python
@asset_type("GraphAsset")
@dataclass
class GraphAsset(Asset):
    nodes: list = field(default_factory=list)

    def to_dict(self): ...
    @staticmethod
    def from_dict(d): ...
    def summary(self): return f"graph · {len(self.nodes)} nodes"
```

Then teach `Renderer._render_base` how to turn it into audio, and add a label
in `raw/ui/asset_manager.py::TYPE_LABELS`.

Types this build does not recognise load as `UnknownAsset` and are written back
verbatim on save, so a project from a newer build survives a round trip.

## Add an overridable parameter

`raw/core/overrides.py`, add a `ParamSpec` to `PARAM_SPECS`:

```python
ParamSpec("vibrato_depth", "add", 0.0, 12.0, "number", "Vibrato", "st")
```

Pick `default_mode` by how a user would phrase the edit — "up 3 semitones" is
`add`, "one and a half times brighter" is `mul`, "exactly 0.4 seconds" is `set`.
Then honour the key in `engine.render`. If the parameter can be applied to an
already-rendered buffer, add it to `POST_PROCESS_PARAMS` so it skips re-rendering.

## Add a dock

Build a `QWidget` taking `controller` as its first argument, then register it in
`raw/ui/main_window.py::MainWindow.__init__` with `self._dock(...)`. Connect to
the controller's signals rather than reaching into other widgets:

```
projectChanged   the whole project was replaced
assetsChanged    membership or names changed
assetModified    one asset changed, carries its uid
selectionChanged carries the selected uid, or ""
statusMessage    text for the status bar
dirtyChanged     unsaved-changes state
```

## Add a project migration

`raw/core/migrations.py`. Append `(from_version, fn)` to `MIGRATIONS` and bump
`PROJECT_VERSION` in `raw/__init__.py`.

A migration must be **behaviour preserving**: a project loaded through it must
render identically to before. `tests/test_core.py::TestMigration` asserts this
for v1→v2 by comparing rendered samples, and a new migration should get the same
treatment. Note how `brightness: 0.5` was chosen as the identity point of the
Brightness mapping precisely so that v1 projects are unaffected.

## Add a field to the authoring format

Three places, in this order:

1. `raw/core/authoring.py` — add the key to `SOUND_KEYS` (or `PATTERN_KEYS` /
   `TRACK_KEYS` / `EVENT_KEYS`) and handle it in the matching parser. Keys not in
   those sets are reported as unknown, which is how a chatbot learns it guessed.
2. `docs/AUTHORING_FORMAT.md` — document it in the field table, with its range and
   default. This file is what gets pasted into a chatbot; if it is not in here, no
   model will ever produce it.
3. `docs/raw_author_schema.json` — add it to the schema so machine validation
   agrees with the parser.

Then teach `sound_to_author` / `pattern_to_author` to emit it, so export round
trips.

`tests/test_authoring.py::TestSpecificationExamples` parses every complete JSON
example inside the specification and fails on any warning. That is deliberate: it
stops the brief from drifting away from the parser.

### Parser conventions

- **Strict about structure, forgiving about values.** Out-of-range numbers are
  clamped and reported; they do not abort the import. A document that is 95%
  right should still produce 95% of its assets.
- **Warnings name the valid options.** `unknown filter type 'reverb'; skipped
  (valid: lowpass, highpass, ...)` is worth far more than `invalid filter`,
  because the user pastes it back to the chatbot and gets a fix.
- **Names in, UUIDs out.** The authoring format references assets by name;
  `parse_document` resolves them to UUIDs so hot-swap safety holds once the data
  is inside the project.

## Sound slots

A pattern track may reference a **slot** (`kick`, `lead`) instead of an asset.
Bindings live on `Project.slots`; a pattern can override one locally through
`pattern.defaults["slots"]`.

Resolution happens in exactly one place — `raw/core/slots.py::resolve_track_source`.
Never resolve a track to an asset at a call site, or the two reference styles
will drift apart. A track carrying a direct `instrument` uid is simply a slot
that is already bound, and keeps working untouched.

Unbound slots render an audible placeholder blip, pitched from a hash of the
slot name. That is deliberate: a silent placeholder would make it impossible to
audition a groove before choosing sounds, which is the entire point of slots.

### Anything that renders must declare its dependencies

`Asset.dependencies(project)` returns the UIDs an asset needs in order to
render, and the render cache folds those assets' `render_key()`s into its own
key. Without this, editing an instrument leaves cached pattern renders stale —
the pattern's own dict has not changed, so the cache cannot tell.

**If you add an asset type that references other assets, override
`dependencies()`.** Cycles are safe: `dependency_keys` walks with a seen-set and
excludes the root, whose key is already in the payload.

## Add an export profile

`raw/export/game_pack.py::EXPORT_PROFILES`. Profiles control format, rate, bit
depth, channels, normalisation, and directory layout.

## Add a console command

`raw/ui/console_dock.py::_build_namespace`. Route every mutation through a
`Command` so console edits stay undoable:

```python
def transpose(name, semitones):
    asset = _resolve(name)
    params = asset.params.copy()
    params.pitch = params.pitch.scaled(2 ** (semitones / 12))
    c.modify_asset(asset.uid, SetAssetField(asset.uid, "params", params, "Transpose"))
```

## Rules worth keeping

1. **Rendering must not mutate its inputs.** The render cache keys on the
   parameter hash; a side effect during render changes the hash and the cache
   can never hit. `SynthParams.structure()` returns a copy for this reason.
2. **Never resample to change length.** Remap time and re-integrate phase.
3. **Absolute vs normalised modulators.** A modulator that models the hardware
   (drift, jitter, bitcrush) stays in absolute time. One that models the note
   (vibrato, filter sweeps) stretches with it.
4. **Preserve unknown data.** Unrecognised asset types, override keys that no
   longer apply after a hot-swap — keep them and let the validator report them.
   Silently dropping user data is worse than a warning.
