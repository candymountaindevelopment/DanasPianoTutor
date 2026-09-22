"""Delay, reverb, noise suppression and the sustainer."""

from __future__ import annotations

import unittest

import numpy as np

from raw.synth.filters import (
    FILTER_PARAMS,
    FILTER_TYPES,
    FilterNode,
    apply_stack,
    denoise,
    echo,
    param_range,
    reverb,
    sustain,
)

SR = 44100


def impulse(n=SR // 2):
    x = np.zeros(n)
    x[100] = 1.0
    return x


def tone(seconds=0.5, freq=440.0, amp=0.5):
    t = np.arange(int(seconds * SR)) / SR
    return amp * np.sin(2 * np.pi * freq * t)


def decaying_tone(seconds=1.0, freq=440.0):
    t = np.arange(int(seconds * SR)) / SR
    return np.sin(2 * np.pi * freq * t) * np.exp(-4.0 * t)


class TestRegistration(unittest.TestCase):
    def test_new_types_are_registered_with_params_and_ranges(self):
        for kind in ("delay", "reverb", "denoise", "sustain"):
            with self.subTest(kind=kind):
                self.assertIn(kind, FILTER_TYPES)
                self.assertTrue(FILTER_PARAMS[kind])
                for name in FILTER_PARAMS[kind]:
                    lo, hi = param_range(kind, name)
                    self.assertLess(lo, hi)
                    self.assertGreaterEqual(FILTER_PARAMS[kind][name], lo)
                    self.assertLessEqual(FILTER_PARAMS[kind][name], hi)

    def test_ranges_fix_the_ambiguous_amount_parameter(self):
        self.assertEqual(param_range("emphasis", "amount"), (-24.0, 24.0))
        self.assertEqual(param_range("denoise", "amount"), (0.0, 1.0))
        self.assertEqual(param_range("sustain", "amount"), (0.0, 1.0))


class TestDelay(unittest.TestCase):
    def test_echo_places_taps_at_the_delay_time(self):
        out = echo(impulse(), SR, time=0.1, feedback=0.0, mix=1.0)
        first = 100 + int(0.1 * SR)
        self.assertAlmostEqual(out[100], 1.0, places=6)
        self.assertGreater(abs(out[first]), 0.5)

    def test_feedback_creates_decaying_repeats(self):
        out = echo(impulse(), SR, time=0.1, feedback=0.5, mix=1.0)
        d = int(0.1 * SR)
        taps = [abs(out[100 + d * k]) for k in range(1, 4)]
        self.assertTrue(all(a > b for a, b in zip(taps, taps[1:])), taps)

    def test_delay_lengthens_the_signal(self):
        x = tone(0.2)
        out = echo(x, SR, time=0.2, feedback=0.5, mix=0.5)
        self.assertGreater(out.size, x.size)

    def test_zero_mix_is_a_no_op(self):
        x = tone(0.2)
        np.testing.assert_array_equal(echo(x, SR, 0.2, 0.5, 0.0), x)

    def test_feedback_is_clamped_so_it_cannot_run_away(self):
        out = echo(impulse(), SR, time=0.05, feedback=5.0, mix=1.0)
        self.assertTrue(np.all(np.isfinite(out)))
        self.assertLess(np.max(np.abs(out)), 50.0)


class TestReverb(unittest.TestCase):
    def test_reverb_adds_a_tail(self):
        x = tone(0.2)
        out = reverb(x, SR, size=1.0, damping=0.3, mix=0.5, predelay=0.0)
        self.assertGreater(out.size, x.size)
        self.assertGreater(np.abs(out[x.size:]).max(), 1e-4)

    def test_bigger_size_gives_a_longer_tail(self):
        x = impulse(SR // 10)
        short = reverb(x, SR, 0.3, 0.3, 0.5, 0.0)
        long = reverb(x, SR, 2.0, 0.3, 0.5, 0.0)
        self.assertGreater(long.size, short.size)

    def test_damping_removes_high_frequencies(self):
        x = impulse(SR // 10)
        bright = reverb(x, SR, 1.0, 0.0, 1.0, 0.0)
        dark = reverb(x, SR, 1.0, 1.0, 1.0, 0.0)

        def high_energy(sig):
            spec = np.abs(np.fft.rfft(sig))
            freqs = np.fft.rfftfreq(sig.size, 1.0 / SR)
            return spec[freqs > 6000].sum() / max(spec.sum(), 1e-9)

        self.assertLess(high_energy(dark), high_energy(bright))

    def test_predelay_delays_the_onset_of_the_tail(self):
        """Predelay separates the reverb from the direct sound, so the gap to
        look at is right after the impulse — not after the whole signal."""
        pre = 0.2
        x = impulse(SR // 10)
        out = reverb(x, SR, 1.0, 0.3, 1.0, pre)
        quiet = out[300 : 100 + int(pre * SR) - 50]
        self.assertLess(np.abs(quiet).max(), 1e-9)
        after = out[100 + int(pre * SR) : 100 + int(pre * SR) + 500]
        self.assertGreater(np.abs(after).max(), 1e-6)

    def test_reverb_is_deterministic(self):
        x = tone(0.2)
        a = reverb(x, SR, 1.0, 0.4, 0.4, 0.01)
        b = reverb(x, SR, 1.0, 0.4, 0.4, 0.01)
        np.testing.assert_array_equal(a, b)

    def test_zero_mix_is_a_no_op(self):
        x = tone(0.2)
        np.testing.assert_array_equal(reverb(x, SR, 1.0, 0.3, 0.0, 0.0), x)


class TestDenoise(unittest.TestCase):
    def signal_with_hiss(self, noise_level=0.05):
        clean = decaying_tone(1.0)
        rng = np.random.default_rng(3)
        return clean, clean + rng.normal(0.0, noise_level, clean.size)

    def test_denoise_lowers_the_noise_floor(self):
        clean, noisy = self.signal_with_hiss()
        cleaned = denoise(noisy, SR, amount=1.0, floor=0.02)
        tail = slice(int(0.85 * SR), None)
        self.assertLess(np.std(cleaned[tail]), np.std(noisy[tail]))

    def test_denoise_keeps_the_signal(self):
        clean, noisy = self.signal_with_hiss()
        cleaned = denoise(noisy, SR, amount=0.8, floor=0.05)
        head = slice(0, int(0.1 * SR))
        self.assertGreater(np.std(cleaned[head]), 0.3 * np.std(clean[head]))

    def test_denoise_preserves_length(self):
        _, noisy = self.signal_with_hiss()
        self.assertEqual(denoise(noisy, SR, 0.7, 0.08).size, noisy.size)

    def test_zero_amount_is_a_no_op(self):
        _, noisy = self.signal_with_hiss()
        np.testing.assert_array_equal(denoise(noisy, SR, 0.0, 0.08), noisy)

    def test_short_input_is_returned_untouched(self):
        x = tone(0.01)
        np.testing.assert_array_equal(denoise(x, SR, 1.0, 0.05), x)

    def test_output_is_finite(self):
        _, noisy = self.signal_with_hiss(0.2)
        self.assertTrue(np.all(np.isfinite(denoise(noisy, SR, 1.0, 0.0))))


class TestSustain(unittest.TestCase):
    def test_sustain_raises_the_decayed_tail(self):
        x = decaying_tone(1.0)
        out = sustain(x, SR, amount=0.9, attack=0.005, release=0.4)

        def rms(sig, a, b):
            return np.sqrt(np.mean(sig[int(a * SR):int(b * SR)] ** 2))

        before = rms(x, 0.7, 0.95) / max(rms(x, 0.0, 0.05), 1e-9)
        after = rms(out, 0.7, 0.95) / max(rms(out, 0.0, 0.05), 1e-9)
        self.assertGreater(after, before * 1.5)

    def test_sustain_preserves_length(self):
        x = decaying_tone(0.5)
        self.assertEqual(sustain(x, SR, 0.5, 0.01, 0.2).size, x.size)

    def test_zero_amount_is_a_no_op(self):
        x = decaying_tone(0.3)
        np.testing.assert_array_equal(sustain(x, SR, 0.0, 0.01, 0.2), x)

    def test_output_is_finite_and_bounded(self):
        x = decaying_tone(0.5)
        out = sustain(x, SR, 1.0, 0.001, 3.0)
        self.assertTrue(np.all(np.isfinite(out)))
        self.assertLess(np.abs(out).max(), 10.0)

    def test_sustain_does_not_bury_the_signal(self):
        """Makeup gain was divided instead of multiplied: a 0.70 peak came out
        at 0.003."""
        x = decaying_tone(1.0)
        for amount in (0.3, 0.6, 0.9, 1.0):
            with self.subTest(amount=amount):
                out = sustain(x, SR, amount, 0.005, 0.3)
                self.assertGreater(np.abs(out).max(), 0.25 * np.abs(x).max())

    def test_denoise_removes_most_of_the_hiss(self):
        clean = decaying_tone(1.0)
        rng = np.random.default_rng(11)
        noisy = clean + rng.normal(0.0, 0.05, clean.size)
        cleaned = denoise(noisy, SR, amount=1.0, floor=0.03)
        tail = slice(int(0.9 * SR), None)
        self.assertLess(np.std(cleaned[tail]), 0.5 * np.std(noisy[tail]))


class TestStackIntegration(unittest.TestCase):
    def test_stack_survives_a_lengthening_node_mid_chain(self):
        x = tone(0.2)
        u = np.linspace(0.0, 1.0, x.size)
        nodes = [
            FilterNode("delay", {"time": 0.1, "feedback": 0.4, "mix": 0.5}, True, "d"),
            FilterNode("lowpass", {"cutoff": 3000.0, "resonance": 0.7}, True, "lp"),
            FilterNode("reverb", {"size": 0.5, "damping": 0.5, "mix": 0.3}, True, "rv"),
        ]
        out, warnings = apply_stack(x, nodes, SR, u)
        self.assertEqual(warnings, [])
        self.assertGreater(out.size, x.size)
        self.assertTrue(np.all(np.isfinite(out)))

    def test_every_new_type_runs_through_the_stack(self):
        x = decaying_tone(0.4)
        u = np.linspace(0.0, 1.0, x.size)
        for kind in ("delay", "reverb", "denoise", "sustain"):
            with self.subTest(kind=kind):
                out, warnings = apply_stack(x, [FilterNode(kind, {}, True, "n")], SR, u)
                self.assertEqual(warnings, [])
                self.assertTrue(np.all(np.isfinite(out)))
                self.assertGreater(out.size, 0)

    def test_disabled_nodes_are_skipped(self):
        x = tone(0.2)
        u = np.linspace(0.0, 1.0, x.size)
        out, _ = apply_stack(x, [FilterNode("reverb", {}, False, "r")], SR, u)
        np.testing.assert_array_equal(out, x)


class TestSampleIntegration(unittest.TestCase):
    def test_effects_reach_an_audio_asset(self):
        import tempfile
        from pathlib import Path

        from raw.audio.buffer import AudioBuffer
        from raw.audio.io import write_wav
        from raw.core.assets import AudioAsset
        from raw.core.project import Project
        from raw.core.renderer import Renderer

        with tempfile.TemporaryDirectory() as tmp:
            write_wav(AudioBuffer(decaying_tone(0.5), SR), Path(tmp) / "s.wav", 16)
            project = Project()
            project.path = Path(tmp) / "p.raw.json"
            asset = project.assets.add(
                AudioAsset(name="s", source_path="s.wav", duration=0.5, sample_rate=SR))
            renderer = Renderer()
            dry = renderer.render_asset(asset, project, use_cache=False)

            asset.filters = [FilterNode("reverb", {"size": 1.0, "mix": 0.5}, True, "r")]
            wet = renderer.render_asset(asset, project, use_cache=False)
            self.assertGreater(wet.duration, dry.duration)
            self.assertTrue(asset.has_effects)


if __name__ == "__main__":
    unittest.main()
