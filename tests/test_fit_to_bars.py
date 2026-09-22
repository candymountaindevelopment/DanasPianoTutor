"""Fitting a sample to a bar count, and the stretch maths behind it."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from raw.audio.buffer import AudioBuffer
from raw.audio.io import write_wav
from raw.core.assets import AudioAsset
from raw.core.project import Project
from raw.core.renderer import Renderer, bar_seconds, bars_for, implied_tempo

SR = 44100
NATURAL = 2.220  # deliberately not a round number of bars

try:
    from PyQt6.QtWidgets import QApplication

    QT = True
except Exception:  # pragma: no cover
    QT = False


def dominant_freq(buffer, win=8192) -> float:
    n = min(win, buffer.num_frames)
    seg = buffer.samples[:n, 0] * np.hanning(n)
    spec = np.abs(np.fft.rfft(seg))
    spec[:3] = 0.0
    return float(np.argmax(spec)) * buffer.sample_rate / n


class TestBarMaths(unittest.TestCase):
    def setUp(self):
        self.project = Project()
        self.project.settings.tempo = 120.0
        self.project.settings.time_signature = (4, 4)

    def test_bar_seconds(self):
        self.assertAlmostEqual(bar_seconds(self.project), 2.0, places=9)
        self.project.settings.tempo = 90.0
        self.assertAlmostEqual(bar_seconds(self.project), 8 / 3, places=9)

    def test_bar_seconds_follows_the_time_signature(self):
        self.project.settings.time_signature = (3, 4)
        self.assertAlmostEqual(bar_seconds(self.project), 1.5, places=9)

    def test_bars_for(self):
        self.assertAlmostEqual(bars_for(2.22, self.project), 1.11, places=9)

    def test_implied_tempo(self):
        self.assertAlmostEqual(implied_tempo(2.22, 1, self.project), 108.108, places=2)
        self.assertAlmostEqual(implied_tempo(2.0, 1, self.project), 120.0, places=6)


class TestStretchAccuracy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        n = int(NATURAL * SR)
        t = np.arange(n) / SR
        write_wav(AudioBuffer(0.6 * np.sin(2 * np.pi * 440 * t), SR),
                  Path(self.tmp.name) / "loop.wav", 16)
        self.project = Project()
        self.project.path = Path(self.tmp.name) / "p.raw.json"
        self.project.settings.tempo = 120.0
        self.asset = self.project.assets.add(
            AudioAsset(name="loop", source_path="loop.wav", duration=NATURAL, sample_rate=SR))
        self.renderer = Renderer()

    def tearDown(self):
        self.tmp.cleanup()

    def render(self, overrides=None):
        return self.renderer.render_asset(self.asset, self.project, overrides, use_cache=False)

    def test_tape_stretch_hits_the_requested_length_exactly(self):
        """This inverted the ratio: 2.220s asked for 2.000s and produced 2.464s."""
        self.asset.stretch_method = "tape"
        out = self.render({"duration": 2.0})
        self.assertEqual(out.num_frames, round(2.0 * SR), "must be sample-exact")
        self.assertAlmostEqual(out.duration, 2.0, places=9)

    def test_fit_to_a_bar_is_sample_exact(self):
        for method in ("tape", "wsola"):
            with self.subTest(method=method):
                self.asset.stretch_method = method
                self.asset.fit_bars = 1.0
                out = self.render()
                self.assertEqual(out.num_frames, round(bar_seconds(self.project) * SR))

    def test_tape_stretch_lengthens_correctly_too(self):
        self.asset.stretch_method = "tape"
        self.assertAlmostEqual(self.render({"duration": 4.0}).duration, 4.0, places=3)

    def test_tape_shifts_pitch_by_the_stretch_ratio(self):
        self.asset.stretch_method = "tape"
        plain = dominant_freq(self.render())
        shorter = dominant_freq(self.render({"duration": NATURAL / 2}))
        self.assertAlmostEqual(shorter / plain, 2.0, delta=0.05)

    def test_wsola_preserves_pitch(self):
        self.asset.stretch_method = "wsola"
        plain = dominant_freq(self.render())
        stretched = dominant_freq(self.render({"duration": 2.0}))
        self.assertAlmostEqual(stretched / plain, 1.0, delta=0.05)
        self.assertAlmostEqual(self.render({"duration": 2.0}).duration, 2.0, places=3)

    def test_every_method_lands_on_the_target_length(self):
        for method in ("tape", "wsola"):
            with self.subTest(method=method):
                self.asset.stretch_method = method
                self.assertAlmostEqual(self.render({"duration": 2.0}).duration, 2.0, places=3)


class TestFitToBars(TestStretchAccuracy):
    def test_fit_bars_produces_exactly_one_bar(self):
        self.asset.fit_bars = 1.0
        self.assertAlmostEqual(self.render().duration, bar_seconds(self.project), places=3)

    def test_fit_bars_handles_fractions_and_multiples(self):
        for bars in (0.5, 2.0, 4.0):
            with self.subTest(bars=bars):
                self.asset.fit_bars = bars
                self.assertAlmostEqual(
                    self.render().duration, bars * bar_seconds(self.project), places=3)

    def test_changing_tempo_refits_the_loop(self):
        self.asset.fit_bars = 1.0
        self.assertAlmostEqual(self.render().duration, 2.0, places=3)
        self.project.settings.tempo = 90.0
        self.assertAlmostEqual(self.render().duration, 8 / 3, places=3)

    def test_an_explicit_duration_override_wins_over_fit_bars(self):
        self.asset.fit_bars = 4.0
        self.assertAlmostEqual(self.render({"duration": 1.0}).duration, 1.0, places=3)

    def test_fit_bars_composes_with_trim(self):
        self.asset.trim_start = 0.2
        self.asset.trim_length = 1.0
        self.asset.fit_bars = 1.0
        self.assertAlmostEqual(self.render().duration, 2.0, places=3)

    def test_fit_bars_survives_the_round_trip(self):
        self.asset.fit_bars = 2.5
        again, _ = Project.from_dict(self.project.to_dict())
        self.assertAlmostEqual(again.assets.by_name("loop").fit_bars, 2.5)

    def test_no_fit_leaves_the_natural_length(self):
        self.asset.fit_bars = None
        self.assertAlmostEqual(self.render().duration, NATURAL, places=3)


@unittest.skipUnless(QT, "PyQt6 not available")
class TestFitUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from raw.app.controller import Controller
        from raw.ui.sample_fx import SampleFx
        from raw.ui.timeline import TimelineDock

        self.tmp = tempfile.TemporaryDirectory()
        n = int(NATURAL * SR)
        t = np.arange(n) / SR
        write_wav(AudioBuffer(0.6 * np.sin(2 * np.pi * 440 * t), SR),
                  Path(self.tmp.name) / "loop.wav", 16)
        self.c = Controller()
        self.c.project.path = Path(self.tmp.name) / "p.raw.json"
        self.c.project.settings.tempo = 120.0
        self.asset = self.c.project.assets.add(
            AudioAsset(name="loop", source_path="loop.wav", duration=NATURAL, sample_rate=SR))
        self.fx = SampleFx(self.c)
        self.dock = TimelineDock(self.c)
        self.c.select(self.asset.uid)

    def tearDown(self):
        self.fx.deleteLater()
        self.dock.deleteLater()
        self.tmp.cleanup()

    def test_nearest_picks_a_sensible_bar_count(self):
        self.fx._fit_nearest()
        self.assertAlmostEqual(self.fx.fit_bars.value(), 1.0, places=3)
        self.assertTrue(self.fx.fit_enabled.isChecked())

    # --------------------------------------------------------------- autofit

    def test_autofit_down_compresses_to_the_bar_below(self):
        """2.220s is 1.11 bars, so shorter means exactly 1 bar."""
        self.fx._fit_step(-1)
        self.assertAlmostEqual(self.fx.fit_bars.value(), 1.0, places=6)
        self.assertTrue(self.fx.fit_enabled.isChecked())
        self.fx._push()
        self.assertAlmostEqual(self.c.render(self.asset, preview=False).duration, 2.0, places=3)

    def test_autofit_up_stretches_to_the_bar_above(self):
        self.fx._fit_step(1)
        self.assertAlmostEqual(self.fx.fit_bars.value(), 2.0, places=6)
        self.fx._push()
        self.assertAlmostEqual(self.c.render(self.asset, preview=False).duration, 4.0, places=3)

    def test_autofit_steps_again_from_the_current_fit(self):
        self.fx._fit_step(1)
        self.fx._fit_step(1)
        self.assertAlmostEqual(self.fx.fit_bars.value(), 3.0, places=6)
        self.fx._fit_step(-1)
        self.assertAlmostEqual(self.fx.fit_bars.value(), 2.0, places=6)

    def test_autofit_moves_even_from_an_exact_bar_count(self):
        """The buttons must never look dead on a loop that is already exact."""
        self.fx.fit_enabled.setChecked(True)
        self.fx.fit_bars.setValue(2.0)
        self.fx._fit_step(-1)
        self.assertAlmostEqual(self.fx.fit_bars.value(), 1.0, places=6)
        self.fx._fit_step(1)
        self.fx._fit_step(1)
        self.assertAlmostEqual(self.fx.fit_bars.value(), 3.0, places=6)

    def test_autofit_down_uses_fractions_below_one_bar(self):
        self.fx.fit_enabled.setChecked(True)
        self.fx.fit_bars.setValue(1.0)
        self.fx._fit_step(-1)
        self.assertAlmostEqual(self.fx.fit_bars.value(), 0.5, places=6)
        self.fx._fit_step(-1)
        self.assertAlmostEqual(self.fx.fit_bars.value(), 0.25, places=6)

    def test_autofit_clamps_at_the_ends_of_the_ladder(self):
        from raw.ui.sample_fx import BAR_LADDER

        self.fx.fit_enabled.setChecked(True)
        self.fx.fit_bars.setValue(BAR_LADDER[0])
        self.fx._fit_step(-1)
        self.assertAlmostEqual(self.fx.fit_bars.value(), BAR_LADDER[0], places=6)
        self.fx.fit_bars.setValue(BAR_LADDER[-1])
        self.fx._fit_step(1)
        self.assertAlmostEqual(self.fx.fit_bars.value(), BAR_LADDER[-1], places=6)

    def test_autofit_respects_the_tempo(self):
        self.c.project.settings.tempo = 90.0
        self.fx._fit_step(-1)
        self.fx._push()
        # 2.220s is 0.8325 bars at 90 BPM, so shorter is half a bar
        self.assertAlmostEqual(self.fx.fit_bars.value(), 0.5, places=6)
        self.assertAlmostEqual(
            self.c.render(self.asset, preview=False).duration, (8 / 3) * 0.5, places=3)

    def test_autofit_is_undoable(self):
        self.fx._fit_step(1)
        self.fx._push()
        self.assertAlmostEqual(self.c.project.assets.by_name("loop").fit_bars, 2.0)
        self.c.undo()
        self.assertIsNone(self.c.project.assets.by_name("loop").fit_bars)

    def test_enabling_fit_reaches_the_asset(self):
        self.fx.fit_enabled.setChecked(True)
        self.fx.fit_bars.setValue(2.0)
        self.fx._push()
        self.assertAlmostEqual(self.c.project.assets.by_name("loop").fit_bars, 2.0)
        self.assertAlmostEqual(self.c.render(self.asset, preview=False).duration, 4.0, places=2)

    def test_disabling_fit_clears_it(self):
        self.fx.fit_enabled.setChecked(True)
        self.fx._push()
        self.fx.fit_enabled.setChecked(False)
        self.fx._push()
        self.assertIsNone(self.c.project.assets.by_name("loop").fit_bars)

    def test_timing_readout_mentions_bars_and_tempo(self):
        self.fx._refresh_timing()
        text = self.fx.timing_info.text()
        self.assertIn("bar =", text)
        self.assertIn("BPM", text)

    def test_stretch_method_is_editable_and_undoable(self):
        self.fx.method.setCurrentText("wsola")
        self.fx._push()
        self.assertEqual(self.c.project.assets.by_name("loop").stretch_method, "wsola")
        self.c.undo()
        self.assertEqual(self.c.project.assets.by_name("loop").stretch_method, "tape")

    def test_clip_width_follows_the_fitted_length(self):
        self.dock.add_clip()
        clip = self.c.project.timeline["clips"][0]
        self.assertAlmostEqual(clip["duration"], NATURAL, places=2)

        self.fx.fit_enabled.setChecked(True)
        self.fx.fit_bars.setValue(1.0)
        self.fx._push()
        self.dock.view.rebuild()
        self.assertAlmostEqual(self.c.project.timeline["clips"][0]["duration"], 2.0, places=2)

    def test_clip_width_follows_a_duration_override(self):
        self.dock.add_clip()
        clip = self.c.project.timeline["clips"][0]
        clip["overrides"] = {"duration": {"musical": "2"}}
        self.dock.view.rebuild()
        self.assertAlmostEqual(clip["duration"], 4.0, places=2)


if __name__ == "__main__":
    unittest.main()
