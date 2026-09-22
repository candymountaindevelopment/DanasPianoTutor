"""Chord tables, chord rendering, baking to a sample, and the Chord Lab dock."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from raw.core.assets import AudioAsset
from raw.core.bake import BakeError, bake_buffer, bake_path
from raw.core.project import Project
from raw.core.renderer import Renderer
from raw.patterns.chords import (
    CHORDS,
    chord_name,
    chord_notes,
    describe,
    params_root_midi,
    render_chord,
)
from raw.patterns.notes import note_to_midi
from raw.synth import engine

SR = 44100

try:
    from PyQt6.QtWidgets import QApplication

    QT = True
except Exception:  # pragma: no cover
    QT = False


def tone_params(duration=0.4):
    from raw.synth.drift import Drift
    from raw.synth.envelope import ADSR
    from raw.synth.trajectory import Trajectory

    return engine.SynthParams(
        oscillator="sine", duration=duration,
        pitch=Trajectory.constant(261.6255653005986), root_note="C4",
        envelope=ADSR(0.002, 0.0, 1.0, 0.01), drift=Drift(0.0), brightness=0.5,
    )


def peaks_in_spectrum(buffer, count=3):
    x = buffer.samples[:, 0]
    n = min(1 << 16, x.size)
    spec = np.abs(np.fft.rfft(x[:n] * np.hanning(n)))
    freqs = np.fft.rfftfreq(n, 1.0 / buffer.sample_rate)
    order = np.argsort(spec)[::-1]
    found, seen = [], []
    for i in order:
        f = freqs[i]
        if f < 40 or any(abs(f - s) < 12 for s in seen):
            continue
        seen.append(f)
        found.append(f)
        if len(found) >= count:
            break
    return sorted(found)


class TestChordTables(unittest.TestCase):
    def test_major_and_minor_intervals(self):
        self.assertEqual(chord_notes(60, "Major"), [60, 64, 67])
        self.assertEqual(chord_notes(60, "Minor"), [60, 63, 67])
        self.assertEqual(chord_notes(60, "Minor 7"), [60, 63, 67, 70])

    def test_inversion_lifts_the_lowest_note(self):
        self.assertEqual(chord_notes(60, "Major", 1), [64, 67, 72])
        self.assertEqual(chord_notes(60, "Major", 2), [67, 72, 76])

    def test_inversion_wraps_rather_than_running_away(self):
        self.assertEqual(chord_notes(60, "Major", 3), [60, 64, 67])

    def test_unknown_quality_falls_back_to_major(self):
        self.assertEqual(chord_notes(60, "Wobble"), [60, 64, 67])

    def test_every_quality_produces_sorted_unique_notes(self):
        for quality in CHORDS:
            with self.subTest(quality=quality):
                notes = chord_notes(60, quality)
                self.assertEqual(notes, sorted(notes))
                self.assertEqual(len(notes), len(set(notes)))

    def test_names_and_description(self):
        self.assertEqual(chord_name(60, "Major"), "C Major")
        self.assertEqual(chord_name(61, "Minor 7"), "C# Minor 7")
        self.assertEqual(describe([60, 64, 67]), "C4 E4 G4")
        self.assertIn("nothing", describe([]))


class TestChordRendering(unittest.TestCase):
    def test_root_is_read_from_the_recipe(self):
        self.assertAlmostEqual(params_root_midi(tone_params()), note_to_midi("C4"), places=3)

    def test_a_major_chord_contains_its_three_pitches(self):
        buffer, warnings = render_chord(tone_params(), chord_notes(60, "Major"), SR)
        self.assertFalse([w for w in warnings if "limited" not in w], warnings)
        found = peaks_in_spectrum(buffer, 3)
        for expected in (261.63, 329.63, 392.0):
            self.assertTrue(any(abs(f - expected) < 6 for f in found),
                            f"{expected} Hz missing from {found}")

    def test_more_notes_do_not_clip(self):
        """In-phase voices sum coherently, so sqrt(n) alone is not enough."""
        for quality in ("Single note", "Major", "Major 7", "Major 9"):
            with self.subTest(quality=quality):
                buffer, _ = render_chord(tone_params(), chord_notes(60, quality), SR)
                self.assertLessEqual(buffer.peak(), 1.0)
                self.assertGreater(buffer.peak(), 0.2, "and not crushed to nothing")

    def test_limiting_is_reported_when_it_engages(self):
        _, warnings = render_chord(tone_params(), chord_notes(60, "Major 9"), SR)
        self.assertTrue(any("limited" in w for w in warnings))

    def test_strum_lengthens_and_staggers(self):
        block, _ = render_chord(tone_params(), chord_notes(60, "Major 7"), SR, strum=0.0)
        strummed, _ = render_chord(tone_params(), chord_notes(60, "Major 7"), SR, strum=0.05)
        self.assertGreater(strummed.duration, block.duration)

    def test_duration_override_is_honoured(self):
        buffer, _ = render_chord(tone_params(), [60, 64], SR, duration=0.75)
        self.assertAlmostEqual(buffer.duration, 0.75, places=2)

    def test_empty_selection_is_handled(self):
        buffer, warnings = render_chord(tone_params(), [], SR)
        self.assertTrue(warnings)
        self.assertGreater(buffer.duration, 0.0)

    def test_transposition_is_exact(self):
        low, _ = render_chord(tone_params(), [60], SR)
        high, _ = render_chord(tone_params(), [72], SR)
        self.assertAlmostEqual(peaks_in_spectrum(high, 1)[0] / peaks_in_spectrum(low, 1)[0],
                               2.0, delta=0.05)


class TestBaking(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Project()
        self.project.path = Path(self.tmp.name) / "p.raw.json"
        self.buffer, _ = render_chord(tone_params(), chord_notes(60, "Minor 7"), SR)

    def tearDown(self):
        self.tmp.cleanup()

    def test_bake_writes_a_wav_inside_the_project(self):
        asset = bake_buffer(self.project, self.buffer, "chord_c_minor7")
        path = self.project.resolve_asset_path(asset.source_path)
        self.assertTrue(path.exists())
        self.assertTrue(path.is_relative_to(self.project.root))

    def test_the_stored_path_is_relative_so_the_project_can_move(self):
        asset = bake_buffer(self.project, self.buffer, "chord")
        self.assertFalse(Path(asset.source_path).is_absolute())
        self.assertIn("assets/generated", asset.source_path)

    def test_baked_asset_renders_back(self):
        asset = bake_buffer(self.project, self.buffer, "chord")
        self.project.assets.add(asset)
        out = Renderer().render_asset(asset, self.project)
        self.assertAlmostEqual(out.duration, self.buffer.duration, places=2)
        self.assertGreater(out.peak(), 0.05)

    def test_baked_asset_is_sliceable_and_processable(self):
        asset = bake_buffer(self.project, self.buffer, "chord")
        self.project.assets.add(asset)
        asset.trim_start = 0.05
        asset.trim_length = 0.2
        asset.reverse = True
        out = Renderer().render_asset(asset, self.project, use_cache=False)
        self.assertAlmostEqual(out.duration, 0.2, places=2)
        self.assertTrue(asset.is_slice and asset.has_effects)

    def test_names_do_not_collide(self):
        first = bake_buffer(self.project, self.buffer, "chord")
        second = bake_buffer(self.project, self.buffer, "chord")
        self.assertNotEqual(first.source_path, second.source_path)

    def test_recipe_is_kept_in_metadata(self):
        asset = bake_buffer(self.project, self.buffer, "chord",
                            meta={"generator": "chord_lab", "notes": [60, 63, 67, 70]})
        self.assertEqual(asset.meta["notes"], [60, 63, 67, 70])
        again, _ = Project.from_dict(
            {"project_version": 2, "assets": {asset.uid: asset.to_dict()}})
        self.assertEqual(again.assets.get(asset.uid).meta["generator"], "chord_lab")

    def test_unsaved_project_refuses_with_an_actionable_message(self):
        unsaved = Project()
        with self.assertRaises(BakeError) as ctx:
            bake_path(unsaved, "chord")
        self.assertIn("Save the project first", str(ctx.exception))


@unittest.skipUnless(QT, "PyQt6 not available")
class TestChordLabDock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from raw.app.controller import Controller
        from raw.ui.chord_lab import ChordLab

        self.tmp = tempfile.TemporaryDirectory()
        self.c = Controller()
        self.c.project.path = Path(self.tmp.name) / "p.raw.json"
        self.lab = ChordLab(self.c)

    def tearDown(self):
        self.c.stop()
        self.lab.deleteLater()
        self.tmp.cleanup()

    def test_picker_selects_keys_on_the_keyboard(self):
        self.lab.root.setCurrentText("C")
        self.lab.octave.setValue(4)
        self.lab.quality.setCurrentText("Major")
        self.assertEqual(self.lab.keys.selected, [60, 64, 67])
        self.assertEqual(self.lab.notes_label.text(), "C4 E4 G4")

    def test_clicking_a_key_toggles_it(self):
        self.lab.quality.setCurrentText("Major")
        self.lab.keys.set_selected([60, 64, 67])
        current = set(self.lab.keys.selected)
        self.lab.keys.set_selected(current | {71})
        self.assertIn(71, self.lab.keys.selected)

    def test_keyboard_range_grows_for_wide_chords(self):
        self.lab.octave.setValue(7)
        self.lab.quality.setCurrentText("Major 9")
        for note in self.lab.keys.selected:
            self.assertTrue(self.lab.keys.contains(note))

    def test_clear_empties_the_selection(self):
        self.lab.quality.setCurrentText("Major")
        self.lab.keys_clear()
        self.assertEqual(self.lab.keys.selected, [])
        self.assertIsNone(self.lab._render())

    def test_render_produces_audio(self):
        self.lab.quality.setCurrentText("Minor 7")
        buffer = self.lab._render()
        self.assertIsNotNone(buffer)
        self.assertGreater(buffer.peak(), 0.02)

    def test_bake_adds_a_sample_asset_and_selects_it(self):
        self.lab.quality.setCurrentText("Major")
        self.lab.name.setText("my_chord")
        self.lab.bake()
        asset = self.c.project.assets.by_name("my_chord")
        self.assertIsInstance(asset, AudioAsset)
        self.assertEqual(self.c.selected_uid, asset.uid)
        self.assertTrue(self.c.project.resolve_asset_path(asset.source_path).exists())

    def test_bake_uses_the_current_settings_not_the_last_preview(self):
        """The preview is debounced; Bake must not write the previous chord."""
        self.lab.quality.setCurrentText("Major")
        self.lab.duration.setValue(0.3)
        self.lab._render()  # prime the cache at 0.3s

        self.lab.duration.setValue(1.1)  # changed, debounce still pending
        self.lab.name.setText("fresh_chord")
        self.lab.bake()

        asset = self.c.project.assets.by_name("fresh_chord")
        self.assertGreater(asset.duration, 0.9, "baked the stale 0.3s render")

    def test_bake_matches_the_selected_notes(self):
        self.lab.octave.setValue(4)
        self.lab.quality.setCurrentText("Major")
        self.lab._render()
        self.lab.octave.setValue(2)  # an octave lower, debounce pending
        self.lab.name.setText("low_chord")
        self.lab.bake()
        self.assertEqual(
            self.c.project.assets.by_name("low_chord").meta["notes"],
            self.lab.keys.selected,
        )

    def test_bake_is_undoable(self):
        self.lab.quality.setCurrentText("Major")
        before = len(self.c.project.assets)
        self.lab.bake()
        self.assertEqual(len(self.c.project.assets), before + 1)
        self.c.undo()
        self.assertEqual(len(self.c.project.assets), before)

    def test_baked_chord_opens_in_the_sample_tabs(self):
        from raw.ui.sample_fx import SampleFx
        from raw.ui.sample_lab import SampleLab

        fx = SampleFx(self.c)
        lab = SampleLab(self.c)
        self.lab.quality.setCurrentText("Major 7")
        self.lab.name.setText("baked_chord")
        self.lab.bake()
        self.assertTrue(fx.isEnabled(), "Sample FX should accept a baked chord")
        self.assertEqual(len(lab.bars), 1, "Sample Lab should load it for slicing")
        fx.deleteLater()
        lab.deleteLater()

    def test_project_synth_assets_appear_as_voices(self):
        from raw.core.assets import SynthAsset

        self.c.project.assets.add(SynthAsset(name="my_lead", params=engine.preset("laser")))
        self.lab._reload_sources()
        self.assertGreaterEqual(self.lab.source.findText("my_lead"), 0)

    def test_loop_toggle_tracks_playback(self):
        self.lab.quality.setCurrentText("Major")
        self.lab.play_btn.setChecked(True)
        self.assertTrue(self.c.is_playing)
        self.lab.play_btn.setChecked(False)
        self.assertFalse(self.c.is_playing)


if __name__ == "__main__":
    unittest.main()
