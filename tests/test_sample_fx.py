"""Non-destructive sample effects: reverse, fades, level and EQ."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from raw.audio.buffer import AudioBuffer
from raw.audio.io import write_wav
from raw.core.assets import AudioAsset
from raw.core.project import Project
from raw.core.renderer import Renderer, apply_sample_effects
from raw.synth.filters import FilterNode

SR = 44100

try:
    from PyQt6.QtWidgets import QApplication

    QT = True
except Exception:  # pragma: no cover
    QT = False


def ramp_source(seconds=1.0):
    """Quiet start, loud end — so reversal is obvious from the envelope."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    return AudioBuffer(np.sin(2 * np.pi * 440 * t) * np.linspace(0.05, 0.9, n), SR)


class TestEffectChain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        write_wav(ramp_source(1.0), Path(self.tmp.name) / "src.wav", 16)
        self.project = Project()
        self.project.path = Path(self.tmp.name) / "p.raw.json"
        self.asset = self.project.assets.add(
            AudioAsset(name="src", source_path="src.wav", duration=1.0, sample_rate=SR))
        self.renderer = Renderer()

    def tearDown(self):
        self.tmp.cleanup()

    def render(self):
        return self.renderer.render_asset(self.asset, self.project, use_cache=False)

    def test_a_fresh_sample_has_no_effects(self):
        self.assertFalse(self.asset.has_effects)

    def test_reverse_flips_the_envelope(self):
        plain = self.render()
        head, tail = plain.slice_seconds(0, 0.2).rms(), plain.slice_seconds(0.8, 1.0).rms()
        self.assertLess(head, tail)

        self.asset.reverse = True
        flipped = self.render()
        self.assertGreater(flipped.slice_seconds(0, 0.2).rms(), flipped.slice_seconds(0.8, 1.0).rms())
        self.assertAlmostEqual(flipped.duration, plain.duration, places=3)

    def test_reverse_is_its_own_inverse(self):
        plain = self.render()
        self.asset.reverse = True
        once = self.render()
        self.assertFalse(np.allclose(plain.samples, once.samples))
        np.testing.assert_allclose(plain.samples, once.reversed().samples, atol=1e-12)

    def test_fades_shape_the_ends(self):
        self.asset.fade_in = 0.2
        self.asset.fade_out = 0.2
        faded = self.render()
        self.assertLess(abs(faded.samples[0, 0]), 1e-6)
        self.assertLess(abs(faded.samples[-1, 0]), 1e-3)

    def test_gain_scales_the_output(self):
        before = self.render().peak()
        self.asset.gain_db = -6.0
        after = self.render().peak()
        self.assertAlmostEqual(after / before, 10 ** (-6.0 / 20.0), places=3)

    def test_normalize_hits_the_target(self):
        self.asset.normalize = 0.5
        self.assertAlmostEqual(self.render().peak(), 0.5, places=3)

    def test_normalize_runs_after_gain(self):
        """Otherwise gain would silently undo the normalisation."""
        self.asset.gain_db = -20.0
        self.asset.normalize = 0.8
        self.assertAlmostEqual(self.render().peak(), 0.8, places=3)

    def test_normalize_runs_after_the_filters(self):
        """The number on the control has to be the peak you actually get."""
        self.asset.filters = [
            FilterNode("bandpass", {"center": 1400.0, "width": 1.2}, True, "f1"),
            FilterNode("bitcrush", {"bits": 7.0}, True, "f2"),
        ]
        self.asset.normalize = 0.9
        self.assertAlmostEqual(self.render().peak(), 0.9, places=2)

    def test_filters_change_the_spectrum_not_the_length(self):
        plain = self.render()
        self.asset.filters = [FilterNode("lowpass", {"cutoff": 200.0, "resonance": 0.7}, True, "f")]
        filtered = self.render()
        self.assertAlmostEqual(filtered.duration, plain.duration, places=4)
        self.assertLess(filtered.rms(), plain.rms())

    def test_effects_compose_with_slicing(self):
        self.asset.trim_start = 0.5
        self.asset.trim_length = 0.25
        self.asset.reverse = True
        self.asset.gain_db = -3.0
        out = self.render()
        self.assertAlmostEqual(out.duration, 0.25, places=3)

    def test_stereo_is_filtered_per_channel(self):
        stereo = AudioBuffer(np.stack([ramp_source().samples[:, 0]] * 2, axis=1), SR)
        write_wav(stereo, Path(self.tmp.name) / "st.wav", 16)
        asset = self.project.assets.add(
            AudioAsset(name="st", source_path="st.wav", channels=2, duration=1.0))
        asset.filters = [FilterNode("lowpass", {"cutoff": 300.0, "resonance": 0.7}, True, "f")]
        out = self.renderer.render_asset(asset, self.project, use_cache=False)
        self.assertEqual(out.channels, 2)
        np.testing.assert_allclose(out.samples[:, 0], out.samples[:, 1], atol=1e-12)

    def test_the_source_file_is_never_written(self):
        self.asset.reverse = True
        self.asset.gain_db = 6.0
        self.asset.filters = [FilterNode("bitcrush", {"bits": 5.0}, True, "f")]
        before = (Path(self.tmp.name) / "src.wav").read_bytes()
        self.render()
        self.assertEqual((Path(self.tmp.name) / "src.wav").read_bytes(), before)

    def test_effects_survive_the_project_round_trip(self):
        self.asset.reverse = True
        self.asset.fade_in = 0.05
        self.asset.gain_db = -2.5
        self.asset.normalize = 0.9
        self.asset.filters = [FilterNode("emphasis", {"center": 900.0, "width": 1.0, "amount": 5.0},
                                         True, "f1")]
        again, _ = Project.from_dict(self.project.to_dict())
        restored = again.assets.by_name("src")
        self.assertTrue(restored.reverse)
        self.assertAlmostEqual(restored.fade_in, 0.05)
        self.assertAlmostEqual(restored.gain_db, -2.5)
        self.assertAlmostEqual(restored.normalize, 0.9)
        self.assertEqual(len(restored.filters), 1)
        self.assertEqual(restored.filters[0].type, "emphasis")
        self.assertTrue(restored.has_effects)

    def test_effects_invalidate_the_render_cache(self):
        first = self.renderer.render_asset(self.asset, self.project).peak()
        self.asset.gain_db = -12.0
        second = self.renderer.render_asset(self.asset, self.project).peak()
        self.assertLess(second, first * 0.5)

    def test_empty_buffer_is_handled(self):
        empty = AudioBuffer.silence(0.0, SR)
        out, warnings = apply_sample_effects(self.asset, empty)
        self.assertEqual(out.num_frames, 0)
        self.assertEqual(warnings, [])


@unittest.skipUnless(QT, "PyQt6 not available")
class TestSampleFxDock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from raw.app.controller import Controller
        from raw.ui.sample_fx import SampleFx

        self.tmp = tempfile.TemporaryDirectory()
        self.c = Controller()
        self.c.project.path = Path(self.tmp.name) / "p.raw.json"
        write_wav(ramp_source(1.0), Path(self.tmp.name) / "src.wav", 16)
        self.asset = self.c.project.assets.add(
            AudioAsset(name="src", source_path="src.wav", duration=1.0, sample_rate=SR))
        self.fx = SampleFx(self.c)
        self.c.select(self.asset.uid)

    def tearDown(self):
        self.fx.deleteLater()
        self.tmp.cleanup()

    def test_selecting_a_sample_enables_the_dock(self):
        self.assertTrue(self.fx.isEnabled())
        self.assertIn("src", self.fx.header.text())

    def test_selecting_a_synth_asset_disables_it(self):
        from raw.core.assets import SynthAsset
        from raw.synth import engine

        s = self.c.project.assets.add(SynthAsset(name="s", params=engine.preset("blip")))
        self.c.select(s.uid)
        self.assertFalse(self.fx.isEnabled())

    def test_reverse_checkbox_reaches_the_asset(self):
        self.fx.reverse.setChecked(True)
        self.fx._push()
        self.assertTrue(self.c.project.assets.by_name("src").reverse)

    def test_changes_are_undoable(self):
        self.fx.gain.setValue(-9.0)
        self.fx._push()
        self.assertAlmostEqual(self.c.project.assets.by_name("src").gain_db, -9.0)
        self.c.undo()
        self.assertAlmostEqual(self.c.project.assets.by_name("src").gain_db, 0.0)

    def test_normalize_target_is_only_used_when_enabled(self):
        self.fx.norm_target.setValue(0.6)
        self.fx.normalize.setChecked(False)
        self.fx._push()
        self.assertIsNone(self.c.project.assets.by_name("src").normalize)
        self.fx.normalize.setChecked(True)
        self.fx._push()
        self.assertAlmostEqual(self.c.project.assets.by_name("src").normalize, 0.6)

    def test_preset_populates_the_filter_list(self):
        self.fx.preset.setCurrentIndex(self.fx.preset.findData("Radio"))
        self.assertEqual(len(self.fx._filters), 2)
        self.assertEqual(self.fx.filters.count(), 2)
        # not on the asset until it is committed through a command
        self.assertEqual(self.asset.filters, [])
        self.fx._push()
        self.assertEqual(len(self.c.project.assets.by_name("src").filters), 2)

    def test_filter_edits_are_undoable(self):
        self.fx.preset.setCurrentIndex(self.fx.preset.findData("Warm"))
        self.fx._push()
        self.assertEqual(len(self.c.project.assets.by_name("src").filters), 2)
        self.c.undo()
        self.assertEqual(self.c.project.assets.by_name("src").filters, [])

    def test_reset_clears_everything(self):
        self.fx.reverse.setChecked(True)
        self.fx.gain.setValue(-6.0)
        self.fx.preset.setCurrentIndex(self.fx.preset.findData("Warm"))
        self.fx._reset()
        self.fx._push()
        asset = self.c.project.assets.by_name("src")
        self.assertFalse(asset.has_effects)

    def test_bypass_renders_the_raw_sample(self):
        self.fx.gain.setValue(-24.0)
        self.fx._push()
        processed = self.c.render(self.asset, preview=False).peak()
        dry = self.fx._dry().peak()
        self.assertGreater(dry, processed * 4)

    def test_filter_reorder(self):
        self.fx.preset.setCurrentIndex(self.fx.preset.findData("Radio"))
        first = self.fx._filters[0].type
        self.fx.filters.setCurrentRow(0)
        self.fx._move_filter(1)
        self.assertEqual(self.fx._filters[1].type, first)

    def test_store_as_new_asset_copies_the_settings(self):
        self.fx.reverse.setChecked(True)
        self.fx.gain.setValue(-3.0)
        self.fx._push()
        payload = self.asset.to_dict()
        payload.pop("uid", None)
        copy_asset = AudioAsset.from_dict(payload)
        self.assertTrue(copy_asset.reverse)
        self.assertAlmostEqual(copy_asset.gain_db, -3.0)
        self.assertNotEqual(copy_asset.uid, self.asset.uid)


if __name__ == "__main__":
    unittest.main()
