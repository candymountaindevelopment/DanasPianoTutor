"""Time map, stretch behaviour, and the pitch-identity guarantee.

test_pitch_identity_* is the load-bearing test of the whole product. If it
fails, a resample has crept into the stretch path and the core claim is false.
"""

from __future__ import annotations

import unittest

import numpy as np

from raw.synth import engine
from raw.synth.envelope import ADSR
from raw.synth.timestructure import Region, TimeStructure
from raw.synth.trajectory import Trajectory

SR = 44100


def rising_crossings(buffer) -> int:
    """Count rising zero crossings — one per oscillator cycle."""
    x = buffer.samples[:, 0]
    x = x[np.abs(x) > 1e-4]
    if x.size < 2:
        return 0
    neg = x[:-1] < 0
    pos = x[1:] >= 0
    return int(np.count_nonzero(neg & pos))


def dominant_freq(buffer, start_frac=0.25, win=4096) -> float:
    """Peak frequency with parabolic interpolation.

    Without the interpolation the estimate is quantised to the FFT bin width,
    which is ~47 cents at 147 Hz with a 4096-point window — enough to fail a
    tolerance the engine actually meets.
    """
    n = buffer.num_frames
    win = min(win, n)
    start = min(max(0, int(start_frac * n)), n - win)
    seg = buffer.samples[start : start + win, 0] * np.hanning(win)
    spec = np.abs(np.fft.rfft(seg))
    spec[:3] = 0.0
    k = int(np.argmax(spec))
    delta = 0.0
    if 0 < k < len(spec) - 1:
        y0, y1, y2 = spec[k - 1], spec[k], spec[k + 1]
        denom = y0 - 2 * y1 + y2
        if abs(denom) > 1e-12:
            delta = float(np.clip(0.5 * (y0 - y2) / denom, -0.5, 0.5))
    return (k + delta) * buffer.sample_rate / win


def cents(a: float, b: float) -> float:
    return 1200.0 * np.log2(b / a)


class TestTimeMap(unittest.TestCase):
    def structure(self) -> TimeStructure:
        return TimeStructure(
            0.16,
            [
                Region("a", "attack", 0.0, 0.05, 0.0),
                Region("t", "transient", 0.05, 0.15, 0.0),
                Region("b", "body", 0.15, 0.60, 1.0),
                Region("l", "tail", 0.60, 1.0, 0.35),
            ],
        )

    def test_map_is_strictly_increasing(self):
        tmap, _ = self.structure().build_map(0.5)
        t = np.linspace(0, tmap.output_duration, 5000)
        nominal = tmap.to_nominal(t)
        self.assertTrue(np.all(np.diff(nominal) >= -1e-12))

    def test_endpoints(self):
        tmap, _ = self.structure().build_map(0.5)
        self.assertAlmostEqual(float(tmap.to_nominal(0.0)[0]), 0.0, places=9)
        self.assertAlmostEqual(
            float(tmap.to_nominal(tmap.output_duration)[0]), 0.16, places=9
        )

    def test_target_duration_is_met(self):
        for target in (0.05, 0.16, 0.4, 1.0, 3.0):
            tmap, _ = self.structure().build_map(target)
            self.assertAlmostEqual(tmap.output_duration, target, places=6)

    def test_solve_is_idempotent(self):
        ts = self.structure()
        first, _ = ts.solve(0.4)
        second, _ = ts.solve(0.4)
        np.testing.assert_allclose(first, second, rtol=0, atol=0)

    def test_rigid_regions_do_not_stretch(self):
        factors, _ = self.structure().solve(0.8)
        self.assertAlmostEqual(factors[0], 1.0, places=9)
        self.assertAlmostEqual(factors[1], 1.0, places=9)
        self.assertGreater(factors[2], 1.0)

    def test_elasticity_weighting(self):
        factors, _ = self.structure().solve(0.4)
        # tail elasticity is 0.35 of the body's, so its excess must be too
        self.assertAlmostEqual((factors[3] - 1.0) / (factors[2] - 1.0), 0.35, places=6)

    def test_infeasible_target_warns_and_still_delivers_length(self):
        ts = self.structure()
        factors, warnings = ts.solve(0.01)
        self.assertTrue(warnings)
        self.assertAlmostEqual(float(np.sum(factors * ts.nominal_durations())), 0.01, places=6)

    def test_tiling_invariants(self):
        self.assertEqual(self.structure().issues(), [])
        broken = TimeStructure(0.2, [Region("a", "attack", 0.0, 0.4), Region("b", "body", 0.5, 1.0)])
        self.assertTrue(broken.issues())
        broken.normalize()
        self.assertEqual(broken.issues(), [])

    def test_sustain_mode_holds_then_advances(self):
        ts = TimeStructure(0.1, [Region("b", "body", 0.0, 1.0, 1.0, mode="sustain")])
        tmap, _ = ts.build_map(0.3)
        # first 0.2s held at the entry value, then traverses at rate 1
        self.assertAlmostEqual(float(tmap.to_nominal(0.1)[0]), 0.0, places=6)
        self.assertAlmostEqual(float(tmap.to_nominal(0.25)[0]), 0.05, places=6)


class TestPitchIdentity(unittest.TestCase):
    def test_pitch_identity_constant_tone(self):
        """A constant tone must render at the same frequency at any length."""
        p = engine.SynthParams(
            oscillator="sine",
            duration=0.25,
            pitch=Trajectory.constant(440.0),
            envelope=ADSR(0.001, 0.0, 1.0, 0.001),
            drift=engine.Drift(0.0),
            brightness=0.5,
            stretch_mode="uniform",
        )
        short = engine.render(p, SR, overrides={"duration": 0.25}).buffer
        long = engine.render(p, SR, overrides={"duration": 1.0}).buffer
        self.assertLess(abs(cents(dominant_freq(short), dominant_freq(long))), 1.0)

    def test_pitch_identity_sweep_cycle_count(self):
        """Under a uniform stretch by s, total cycles scale by exactly s.

        This holds only if the generator revisits the same frequency values on
        a slower schedule. A resample would keep the cycle count constant.
        """
        p = engine.SynthParams(
            oscillator="square",
            duration=0.20,
            pitch=Trajectory.ramp(900.0, 120.0, "exponential"),
            envelope=ADSR(0.001, 0.0, 1.0, 0.001),
            drift=engine.Drift(0.0),
            brightness=0.5,
            stretch_mode="uniform",
        )
        base = engine.render(p, SR, overrides={"duration": 0.20}).buffer
        n_base = rising_crossings(base)
        for s in (2.0, 3.0, 5.0):
            stretched = engine.render(p, SR, overrides={"duration": 0.20 * s}).buffer
            ratio = rising_crossings(stretched) / n_base
            self.assertAlmostEqual(ratio, s, delta=0.02 * s)

    def test_resampling_would_have_shifted_pitch(self):
        """The contrast case: naive resampling drops pitch by 12*log2(s)."""
        p = engine.SynthParams(
            oscillator="sine",
            duration=0.25,
            pitch=Trajectory.constant(440.0),
            envelope=ADSR(0.001, 0.0, 1.0, 0.001),
            drift=engine.Drift(0.0),
        )
        buf = engine.render(p, SR).buffer
        from raw.audio.buffer import AudioBuffer

        # Stretching to 3x by resampling: 3x the frames, played back at the
        # original rate. This is exactly what the time map avoids doing.
        resampled = AudioBuffer(buf.resampled(SR * 3).samples, SR)
        self.assertAlmostEqual(
            cents(dominant_freq(buf), dominant_freq(resampled)), -1200 * np.log2(3), delta=25
        )

    def test_filters_do_not_move_pitch(self):
        """Filtering runs downstream of the phase integral, so it cannot."""
        from raw.synth.filters import FilterNode

        p = engine.SynthParams(
            oscillator="sine",
            duration=0.3,
            pitch=Trajectory.constant(440.0),
            envelope=ADSR(0.001, 0.0, 1.0, 0.001),
            drift=engine.Drift(0.0),
        )
        plain = engine.render(p, SR).buffer
        p.filters = [FilterNode("emphasis", {"center": 2500.0, "width": 0.6, "amount": 12.0}, True, "f")]
        p.brightness = 0.9
        filtered = engine.render(p, SR).buffer
        self.assertLess(abs(cents(dominant_freq(plain), dominant_freq(filtered))), 1.0)


class TestRegionPreservation(unittest.TestCase):
    def params(self) -> engine.SynthParams:
        return engine.preset("coin")

    def test_preserve_impact_keeps_onset_samples(self):
        p = self.params()
        ts = p.structure("preserve_impact")
        rigid = sum(r.extent for r in ts.regions if r.elasticity == 0.0) * p.duration

        base = engine.render(p, SR, overrides={"duration": p.duration}).buffer
        long = engine.render(p, SR, overrides={"duration": p.duration * 3}).buffer
        n = int(rigid * SR) - 2
        np.testing.assert_allclose(base.samples[:n, 0], long.samples[:n, 0], atol=1e-9)

    def test_uniform_scales_everything(self):
        p = self.params()
        factors, _ = p.structure("uniform").solve(p.duration * 2.5)
        np.testing.assert_allclose(factors, np.full(len(factors), 2.5), rtol=1e-9)

    def test_stretch_mode_presets_differ(self):
        p = self.params()
        uniform, _ = p.structure("uniform").solve(0.5)
        impact, _ = p.structure("preserve_impact").solve(0.5)
        self.assertFalse(np.allclose(uniform, impact))


class TestSustainedPresets(unittest.TestCase):
    """`pad` and `drone` are background beds, not one-shots."""

    def rms(self, buf, a, b):
        n = buf.num_frames
        return float(np.sqrt(np.mean(buf.samples[int(a * n):int(b * n), 0] ** 2)))

    def test_they_are_long_and_sustained(self):
        for name in ("pad", "drone"):
            with self.subTest(name=name):
                p = engine.preset(name)
                self.assertGreaterEqual(p.duration, 3.0)
                self.assertGreaterEqual(p.envelope.sustain, 0.8)
                buf = engine.render(p, SR).buffer
                self.assertGreater(self.rms(buf, 0.4, 0.6), 0.02, "middle should be singing")

    def test_drone_starts_and_ends_at_a_similar_level_so_it_loops(self):
        buf = engine.render(engine.preset("drone"), SR).buffer
        head, tail = self.rms(buf, 0.02, 0.10), self.rms(buf, 0.90, 0.98)
        self.assertAlmostEqual(head / max(tail, 1e-9), 1.0, delta=0.45)

    def test_pad_swells_rather_than_starting_flat_out(self):
        buf = engine.render(engine.preset("pad"), SR).buffer
        self.assertLess(self.rms(buf, 0.0, 0.05), self.rms(buf, 0.35, 0.5))

    def test_rendered_length_matches_the_request(self):
        """No lengthening effects in a preset, or stretching gets confusing."""
        for name in ("pad", "drone"):
            with self.subTest(name=name):
                buf = engine.render(engine.preset(name), SR, overrides={"duration": 2.0}).buffer
                self.assertAlmostEqual(buf.duration, 2.0, places=3)

    def test_uniform_stretch_keeps_the_whole_shape(self):
        p = engine.preset("pad")
        self.assertEqual(p.stretch_mode, "uniform")
        factors, _ = p.structure("uniform").solve(p.duration * 3)
        np.testing.assert_allclose(factors, np.full(len(factors), 3.0), rtol=1e-9)

    def test_they_have_a_root_note_so_they_can_be_played_as_chords(self):
        for name in ("pad", "drone"):
            with self.subTest(name=name):
                self.assertIsNotNone(engine.preset(name).root_note)


class TestDeterminism(unittest.TestCase):
    def test_same_seed_same_output(self):
        p = engine.preset("hit")
        a = engine.render(p, SR).buffer
        b = engine.render(p, SR).buffer
        np.testing.assert_array_equal(a.samples, b.samples)

    def test_seed_offset_changes_realization(self):
        p = engine.preset("hit")
        a = engine.render(p, SR).buffer
        b = engine.render(p, SR, overrides={"seed_offset": 1}).buffer
        self.assertFalse(np.array_equal(a.samples, b.samples))
        self.assertAlmostEqual(a.duration, b.duration, places=9)

    def test_render_does_not_mutate_params(self):
        p = engine.preset("laser")
        before = p.to_dict()
        engine.render(p, SR, overrides={"duration": 0.9, "stretch_mode": "uniform"})
        self.assertEqual(before, p.to_dict())

    def test_output_is_finite_and_bounded(self):
        for name in engine.PRESET_NAMES:
            for duration in (0.02, None, 2.0):
                ov = {} if duration is None else {"duration": duration}
                buf = engine.render(engine.preset(name), SR, overrides=ov).buffer
                self.assertTrue(np.all(np.isfinite(buf.samples)), name)
                self.assertLess(buf.peak(), 4.0, name)


if __name__ == "__main__":
    unittest.main()
