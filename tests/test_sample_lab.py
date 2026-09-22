"""Non-destructive sample slicing, and the splice-bar UI."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from raw.audio.buffer import AudioBuffer
from raw.audio.io import write_wav
from raw.core.assets import AudioAsset
from raw.core.project import Project
from raw.core.renderer import Renderer

try:
    from PyQt6.QtWidgets import QApplication

    QT = True
except Exception:  # pragma: no cover
    QT = False

SR = 44100


def stepped_source(seconds=2.0):
    """A file whose amplitude tells you which second you are in: 0.2 / 0.5 / 0.9."""
    n = int(seconds * SR)
    x = np.zeros(n)
    t = np.arange(n) / SR
    tone = np.sin(2 * np.pi * 440 * t)
    x[: n // 2] = tone[: n // 2] * 0.2
    x[n // 2:] = tone[n // 2:] * 0.9
    return AudioBuffer(x, SR)


class TestSliceModel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "src.wav"
        write_wav(stepped_source(2.0), self.path, 16)
        self.project = Project()
        self.project.path = Path(self.tmp.name) / "p.raw.json"
        self.asset = self.project.assets.add(
            AudioAsset(name="src", source_path="src.wav", duration=2.0, sample_rate=SR)
        )
        self.renderer = Renderer()

    def tearDown(self):
        self.tmp.cleanup()

    def test_untrimmed_asset_renders_the_whole_file(self):
        buf = self.renderer.render_asset(self.asset, self.project)
        self.assertAlmostEqual(buf.duration, 2.0, places=3)
        self.assertFalse(self.asset.is_slice)

    def test_slice_renders_only_its_bounds(self):
        slice_asset = self.project.assets.add(
            AudioAsset(name="quiet", source_path="src.wav",
                       trim_start=0.0, trim_length=0.5, duration=0.5)
        )
        buf = self.renderer.render_asset(slice_asset, self.project)
        self.assertAlmostEqual(buf.duration, 0.5, places=3)
        self.assertLess(buf.peak(), 0.35)  # the quiet half

    def test_slice_start_selects_a_different_part(self):
        loud = self.project.assets.add(
            AudioAsset(name="loud", source_path="src.wav",
                       trim_start=1.2, trim_length=0.5, duration=0.5)
        )
        buf = self.renderer.render_asset(loud, self.project)
        self.assertAlmostEqual(buf.duration, 0.5, places=3)
        self.assertGreater(buf.peak(), 0.7)  # the loud half

    def test_two_slices_of_one_file_are_independent(self):
        a = self.project.assets.add(
            AudioAsset(name="a", source_path="src.wav", trim_start=0.1, trim_length=0.3))
        b = self.project.assets.add(
            AudioAsset(name="b", source_path="src.wav", trim_start=1.4, trim_length=0.3))
        pa = self.renderer.render_asset(a, self.project).peak()
        pb = self.renderer.render_asset(b, self.project).peak()
        self.assertLess(pa, pb)

    def test_no_audio_file_is_copied(self):
        self.project.assets.add(
            AudioAsset(name="a", source_path="src.wav", trim_start=0.1, trim_length=0.3))
        wavs = list(Path(self.tmp.name).glob("*.wav"))
        self.assertEqual(len(wavs), 1, "slicing must not write new audio files")

    def test_out_of_range_bounds_warn_and_fall_back(self):
        bad = self.project.assets.add(
            AudioAsset(name="bad", source_path="src.wav", trim_start=9.0, trim_length=1.0))
        buf = self.renderer.render_asset(bad, self.project)
        self.assertTrue(any("outside the source" in w for w in self.renderer.last_warnings))
        self.assertGreater(buf.duration, 0.0)

    def test_slice_survives_the_project_round_trip(self):
        self.project.assets.add(
            AudioAsset(name="a", source_path="src.wav", trim_start=0.25, trim_length=0.5))
        again, _ = Project.from_dict(self.project.to_dict())
        restored = again.assets.by_name("a")
        self.assertAlmostEqual(restored.trim_start, 0.25)
        self.assertAlmostEqual(restored.trim_length, 0.5)
        self.assertTrue(restored.is_slice)

    def test_summary_says_slice(self):
        a = AudioAsset(name="a", source_path="s.wav", trim_start=0.1, trim_length=0.2, duration=0.2)
        self.assertIn("slice", a.summary())
        self.assertIn("sample", AudioAsset(name="b", duration=1.0).summary())


class TestSourceDecodeCache(unittest.TestCase):
    """Slices share one source file; decoding it per slice is the difference
    between instant and unusable on a long sample."""

    def setUp(self):
        from raw.audio import io as audio_io

        self.io = audio_io
        self.io.clear_source_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "src.wav"
        write_wav(stepped_source(1.0), self.path, 16)

    def tearDown(self):
        self.io.clear_source_cache()
        self.tmp.cleanup()

    def test_second_read_is_served_from_the_cache(self):
        first = self.io.read_audio_cached(self.path)
        second = self.io.read_audio_cached(self.path)
        self.assertIs(first, second)

    def test_editing_the_file_invalidates_it(self):
        first = self.io.read_audio_cached(self.path)
        write_wav(stepped_source(1.5), self.path, 16)
        second = self.io.read_audio_cached(self.path)
        self.assertIsNot(first, second)
        self.assertAlmostEqual(second.duration, 1.5, places=2)

    def test_clearing_drops_everything(self):
        self.io.read_audio_cached(self.path)
        self.io.clear_source_cache()
        self.assertIn("0 file(s)", self.io.source_cache_stats())

    def test_many_slices_decode_the_source_once(self):
        project = Project()
        project.path = Path(self.tmp.name) / "p.raw.json"
        renderer = Renderer()
        decodes = []
        real = self.io.read_audio

        def counting(path):
            decodes.append(str(path))
            return real(path)

        self.io.read_audio = counting
        try:
            for i in range(6):
                asset = project.assets.add(
                    AudioAsset(name=f"s{i}", source_path="src.wav",
                               trim_start=i * 0.1, trim_length=0.1))
                renderer.render_asset(asset, project)
        finally:
            self.io.read_audio = real
        self.assertEqual(len(decodes), 1, f"decoded {len(decodes)} times")

    def test_a_missing_file_does_not_poison_the_cache(self):
        with self.assertRaises(FileNotFoundError):
            self.io.read_audio_cached(Path(self.tmp.name) / "nope.wav")
        self.assertIn("0 file(s)", self.io.source_cache_stats())


@unittest.skipUnless(QT, "PyQt6 not available")
class TestSpliceBars(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from raw.app.controller import Controller
        from raw.ui.sample_lab import SampleLab

        self.tmp = tempfile.TemporaryDirectory()
        self.c = Controller()
        self.c.project.path = Path(self.tmp.name) / "p.raw.json"
        write_wav(stepped_source(2.0), Path(self.tmp.name) / "src.wav", 16)
        self.asset = self.c.project.assets.add(
            AudioAsset(name="src", source_path="src.wav", duration=2.0, sample_rate=SR))
        self.lab = SampleLab(self.c)
        self.c.select(self.asset.uid)

    def tearDown(self):
        self.lab.deleteLater()
        self.tmp.cleanup()

    def test_selecting_a_sample_creates_one_full_length_bar(self):
        self.assertEqual(len(self.lab.bars), 1)
        bar = self.lab.bars[0]
        self.assertAlmostEqual(bar.start, 0.0, places=3)
        self.assertAlmostEqual(bar.length, 2.0, places=2)

    def test_selecting_a_synth_asset_disables_the_lab(self):
        from raw.core.assets import SynthAsset
        from raw.synth import engine

        synth = self.c.project.assets.add(SynthAsset(name="s", params=engine.preset("blip")))
        self.c.select(synth.uid)
        self.assertEqual(self.lab.bars, [])
        self.assertFalse(self.lab.add_btn.isEnabled())

    def test_sliders_and_spinboxes_stay_in_sync(self):
        bar = self.lab.bars[0]
        bar.start_slider.setValue(500)
        self.assertAlmostEqual(bar.start, 0.5, places=3)
        bar.start_spin.setValue(0.125)
        self.assertEqual(bar.start_slider.value(), 125)

    def test_millisecond_precision(self):
        bar = self.lab.bars[0]
        bar.start_spin.setValue(0.001)
        self.assertAlmostEqual(bar.start, 0.001, places=4)
        self.assertEqual(bar.start_slider.value(), 1)

    def test_length_cannot_run_past_the_end(self):
        bar = self.lab.bars[0]
        bar.start_spin.setValue(1.8)
        self.assertLessEqual(bar.end, 2.0 + 1e-6)
        self.assertLessEqual(bar.length, 0.2 + 1e-6)

    def test_adding_bars_tiles_them_after_the_previous_one(self):
        first = self.lab.bars[0]
        first.set_bounds(0.0, 0.4)
        second = self.lab.add_bar()
        self.assertAlmostEqual(second.start, 0.4, places=3)
        self.assertEqual(len(self.lab.bars), 2)

    def test_bars_get_distinct_colours(self):
        self.lab.split_evenly(4)
        colours = [b.color for b in self.lab.bars]
        self.assertEqual(len(set(colours)), 4)

    def test_split_evenly_tiles_the_whole_sample(self):
        self.lab.split_evenly(4)
        self.assertEqual(len(self.lab.bars), 4)
        self.assertAlmostEqual(self.lab.bars[0].start, 0.0, places=3)
        self.assertAlmostEqual(self.lab.bars[-1].end, 2.0, places=2)
        for a, b in zip(self.lab.bars, self.lab.bars[1:]):
            self.assertAlmostEqual(a.end, b.start, places=3)

    def test_removing_a_bar_reindexes_the_rest(self):
        self.lab.split_evenly(3)
        self.lab.remove_bar(self.lab.bars[0])
        self.assertEqual([b.index for b in self.lab.bars], [0, 1])

    def test_store_creates_a_slice_asset_pointing_at_the_same_file(self):
        self.lab.split_evenly(2)
        bar = self.lab.bars[1]
        bar.name.setText("second_half")
        self.lab.store_bar(bar)
        stored = self.c.project.assets.by_name("second_half")
        self.assertIsInstance(stored, AudioAsset)
        self.assertEqual(stored.source_path, self.asset.source_path)
        self.assertAlmostEqual(stored.trim_start, 1.0, places=2)
        self.assertAlmostEqual(stored.trim_length, 1.0, places=2)

    def test_store_is_undoable(self):
        before = len(self.c.project.assets)
        self.lab.store_bar(self.lab.bars[0])
        self.assertEqual(len(self.c.project.assets), before + 1)
        self.c.undo()
        self.assertEqual(len(self.c.project.assets), before)

    def test_store_all_is_one_undo_step(self):
        self.lab.split_evenly(4)
        before = len(self.c.project.assets)
        self.lab.store_all()
        self.assertEqual(len(self.c.project.assets), before + 4)
        self.c.undo()
        self.assertEqual(len(self.c.project.assets), before)

    def test_slicing_a_slice_composes_bounds_against_the_real_file(self):
        self.lab.split_evenly(2)
        self.lab.bars[1].name.setText("half")
        self.lab.store_bar(self.lab.bars[1])
        half = self.c.project.assets.by_name("half")

        self.c.select(half.uid)
        self.assertAlmostEqual(self.lab.source.duration, 1.0, places=2)
        self.lab.split_evenly(2)
        self.lab.bars[1].name.setText("quarter")
        self.lab.store_bar(self.lab.bars[1])

        quarter = self.c.project.assets.by_name("quarter")
        self.assertAlmostEqual(quarter.trim_start, 1.5, places=2)
        self.assertAlmostEqual(quarter.trim_length, 0.5, places=2)

    def test_stored_slice_renders_at_its_length(self):
        self.lab.split_evenly(4)
        self.lab.store_bar(self.lab.bars[2])
        stored = [a for a in self.c.project.assets if a.name != "src"][0]
        buf = self.c.render(stored, preview=False)
        self.assertAlmostEqual(buf.duration, 0.5, places=2)

    def test_reselecting_the_same_sample_keeps_the_bars(self):
        """Selection is re-emitted after every undo; that must not wipe slice work."""
        self.lab.split_evenly(4)
        self.lab.bars[0].set_bounds(0.05, 0.2)
        self.c.selectionChanged.emit(self.asset.uid)
        self.assertEqual(len(self.lab.bars), 4)
        self.assertAlmostEqual(self.lab.bars[0].start, 0.05, places=3)

    def test_undo_does_not_reset_the_bars(self):
        self.lab.split_evenly(3)
        self.lab.store_bar(self.lab.bars[0])
        self.c.undo()
        self.assertEqual(len(self.lab.bars), 3)

    def test_switching_samples_rebuilds_the_bars(self):
        other = self.c.project.assets.add(
            AudioAsset(name="other", source_path="src.wav", duration=2.0, sample_rate=SR))
        self.lab.split_evenly(4)
        self.c.select(other.uid)
        self.assertEqual(len(self.lab.bars), 1)

    def test_cleared_bars_leave_no_widgets_behind(self):
        from raw.ui.sample_lab import SpliceBar

        self.lab.split_evenly(4)
        self.assertEqual(len(self.lab.findChildren(SpliceBar)), 4)
        self.lab._clear_bars()
        self.assertEqual(self.lab.bars, [])
        still_parented = [b for b in self.lab.findChildren(SpliceBar) if b.parent() is not None]
        self.assertEqual(still_parented, [])

    # ---------------------------------------------------------------- looping

    def test_play_button_starts_a_loop(self):
        played = []
        self.c.playbackStarted.connect(lambda origin, dur: played.append((origin, dur)))
        bar = self.lab.bars[0]
        bar.play_btn.setChecked(True)
        self.assertTrue(bar.is_playing)
        self.assertEqual(bar.play_btn.text(), "■")
        self.assertEqual(played[0][0], "sample_lab")
        self.assertTrue(self.c._play_loop)

    def test_the_same_button_stops_it(self):
        bar = self.lab.bars[0]
        bar.play_btn.setChecked(True)
        bar.play_btn.setChecked(False)
        self.assertFalse(bar.is_playing)
        self.assertEqual(bar.play_btn.text(), "▶")
        self.assertFalse(self.c.is_playing)

    def test_looping_position_wraps_instead_of_ending(self):
        from raw.audio.buffer import AudioBuffer

        positions = []
        self.c.playbackPosition.connect(lambda origin, pos: positions.append(pos))
        self.c.play(AudioBuffer.silence(0.05, SR), loop=True, origin="sample_lab")
        self.c._play_started_at -= 0.17  # pretend three loops have elapsed
        self.c._tick_playback()
        self.assertTrue(self.c.is_playing, "a loop must not end on its own")
        self.assertLess(positions[-1], 0.05)

    def test_only_one_bar_loops_at_a_time(self):
        self.lab.split_evenly(3)
        self.lab.bars[0].play_btn.setChecked(True)
        self.lab.bars[2].play_btn.setChecked(True)
        self.assertFalse(self.lab.bars[0].is_playing)
        self.assertTrue(self.lab.bars[2].is_playing)

    def test_whole_sample_button_toggles_too(self):
        self.lab.play_source_btn.setChecked(True)
        self.assertIn("Stop", self.lab.play_source_btn.text())
        self.lab.play_source_btn.setChecked(False)
        self.assertIn("Loop", self.lab.play_source_btn.text())
        self.assertFalse(self.c.is_playing)

    def test_starting_the_whole_sample_clears_a_bar_loop(self):
        self.lab.bars[0].play_btn.setChecked(True)
        self.lab.play_source_btn.setChecked(True)
        self.assertFalse(self.lab.bars[0].is_playing)

    def test_external_playback_clears_the_loop_buttons(self):
        from raw.audio.buffer import AudioBuffer

        bar = self.lab.bars[0]
        bar.play_btn.setChecked(True)
        self.c.play(AudioBuffer.silence(0.1, SR), origin="preview")
        self.assertFalse(bar.is_playing)

    def test_stop_clears_the_loop_buttons(self):
        bar = self.lab.bars[0]
        bar.play_btn.setChecked(True)
        self.c.stop()
        self.assertFalse(bar.is_playing)

    def test_an_empty_slice_does_not_latch_the_button(self):
        bar = self.lab.bars[0]
        bar.set_bounds(0.0, 0.001)
        bar.length_spin.setValue(0.001)
        bar.start_spin.setValue(self.lab.source.duration)
        bar.play_btn.setChecked(True)
        # either it played something valid, or the button reset itself
        if not bar.is_playing:
            self.assertFalse(self.c.is_playing)

    def test_switching_samples_stops_playback(self):
        other = self.c.project.assets.add(
            AudioAsset(name="other2", source_path="src.wav", duration=2.0, sample_rate=SR))
        self.lab.bars[0].play_btn.setChecked(True)
        self.c.select(other.uid)
        self.assertFalse(self.c.is_playing)

    def test_waveform_regions_track_the_bars(self):
        self.lab.split_evenly(3)
        self.assertEqual(len(self.lab.wave._regions), 3)
        self.lab.remove_bar(self.lab.bars[0])
        self.assertEqual(len(self.lab.wave._regions), 2)


if __name__ == "__main__":
    unittest.main()
