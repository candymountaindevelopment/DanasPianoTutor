"""Project model, serialisation, migration, commands, overrides, and export."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from raw.audio.buffer import AudioBuffer
from raw.audio.io import read_wav, write_wav
from raw.core.assets import AssetRegistry, SynthAsset, UnknownAsset, asset_from_dict
from raw.core.commands import AddAsset, CommandStack, RemoveAsset, RenameAsset, SetAssetField
from raw.core.ids import slugify, unique_name
from raw.core.overrides import explain, musical_to_seconds, resolve, resolve_duration
from raw.core.project import Project
from raw.core.renderer import Renderer
from raw.core.validation import validate
from raw.synth import engine


def demo_project() -> Project:
    p = Project()
    for name in engine.PRESET_NAMES:
        p.assets.add(SynthAsset(name=f"sfx_{name}", params=engine.preset(name)))
    return p


class TestIds(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(slugify("Player Laser!"), "player_laser")
        self.assertEqual(slugify("9lives"), "_9lives")
        self.assertEqual(slugify(""), "asset")

    def test_unique_name(self):
        self.assertEqual(unique_name("sfx", {"sfx"}), "sfx_1")
        self.assertEqual(unique_name("sfx", {"sfx", "sfx_1"}), "sfx_2")


class TestRegistry(unittest.TestCase):
    def test_names_stay_unique(self):
        reg = AssetRegistry()
        a = reg.add(SynthAsset(name="sfx_hit"))
        b = reg.add(SynthAsset(name="sfx_hit"))
        self.assertNotEqual(a.name, b.name)

    def test_rename_preserves_uid(self):
        reg = AssetRegistry()
        a = reg.add(SynthAsset(name="sfx_hit"))
        uid = a.uid
        reg.rename(uid, "enemy_hit")
        self.assertEqual(reg.get(uid).name, "enemy_hit")

    def test_render_key_ignores_cosmetic_fields(self):
        a = SynthAsset(name="one")
        b = SynthAsset(uid=a.uid, name="two", tags=["x"], params=a.params)
        self.assertEqual(a.render_key(), b.render_key())


class TestSerialisation(unittest.TestCase):
    def test_round_trip_is_lossless(self):
        p = demo_project()
        again, notes = Project.from_dict(p.to_dict())
        self.assertEqual(notes, [])
        self.assertEqual(
            json.dumps(p.to_dict()["assets"], sort_keys=True),
            json.dumps(again.to_dict()["assets"], sort_keys=True),
        )

    def test_save_and_load(self):
        p = demo_project()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.raw.json"
            p.save(path)
            loaded, _ = Project.load(path)
            self.assertEqual(len(loaded.assets), len(p.assets))
            self.assertEqual(loaded.path, path)

    def test_unknown_asset_type_is_preserved(self):
        raw = {
            "project_version": 2,
            "assets": {
                "u1": {"uid": "u1", "type": "QuantumAsset", "name": "future", "wobble": 42}
            },
        }
        p, _ = Project.from_dict(raw)
        asset = p.assets.get("u1")
        self.assertIsInstance(asset, UnknownAsset)
        self.assertEqual(p.to_dict()["assets"]["u1"]["wobble"], 42)

    def test_trajectory_survives_round_trip(self):
        from raw.synth.trajectory import Trajectory

        t = Trajectory([(0.0, 900.0), (0.4, 500.0), (1.0, 120.0)], "exponential")
        again = Trajectory.from_dict(t.to_dict())
        self.assertEqual(t.points, again.points)
        self.assertEqual(t.curve, again.curve)


class TestMigration(unittest.TestCase):
    V1 = {
        "project_version": 1,
        "metadata": {"name": "old"},
        "settings": {"sample_rate": 44100, "tempo": 120},
        "assets": {
            "a1": {
                "uid": "a1",
                "type": "SynthAsset",
                "name": "sfx_old",
                "params": {"oscillator": "square", "duration": 0.2},
            }
        },
    }

    def test_migration_runs_and_bumps_version(self):
        p, notes = Project.from_dict(dict(self.V1))
        self.assertTrue(any("v1 -> v2" in n for n in notes))
        self.assertEqual(p.to_dict()["project_version"], 2)

    def test_migration_is_behaviour_preserving(self):
        """brightness 0.5 is the identity point, so v1 renders are unchanged."""
        p, _ = Project.from_dict(dict(self.V1))
        params = p.assets.get("a1").params
        self.assertEqual(params.brightness, 0.5)
        self.assertEqual(params.filters, [])

        bare = engine.SynthParams(oscillator="square", duration=0.2)
        np.testing.assert_allclose(
            engine.render(params, 44100).buffer.samples,
            engine.render(bare, 44100).buffer.samples,
        )

    def test_future_version_is_not_downgraded(self):
        p, notes = Project.from_dict({"project_version": 99, "assets": {}})
        self.assertTrue(any("newer build" in n for n in notes))


class TestCommands(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.stack = CommandStack(self.p)

    def test_add_and_undo(self):
        asset = SynthAsset(name="sfx_a")
        self.stack.push(AddAsset(asset))
        self.assertEqual(len(self.p.assets), 1)
        self.stack.undo()
        self.assertEqual(len(self.p.assets), 0)
        self.stack.redo()
        self.assertEqual(len(self.p.assets), 1)

    def test_remove_restores_full_asset(self):
        asset = SynthAsset(name="sfx_a", params=engine.preset("laser"))
        self.stack.push(AddAsset(asset))
        uid, before = asset.uid, asset.to_dict()
        self.stack.push(RemoveAsset(uid))
        self.assertIsNone(self.p.assets.get(uid))
        self.stack.undo()
        self.assertEqual(self.p.assets.get(uid).to_dict(), before)

    def test_rename_undo(self):
        asset = SynthAsset(name="sfx_a")
        self.stack.push(AddAsset(asset))
        self.stack.push(RenameAsset(asset.uid, "sfx_b"))
        self.assertEqual(self.p.assets.get(asset.uid).name, "sfx_b")
        self.stack.undo()
        self.assertEqual(self.p.assets.get(asset.uid).name, "sfx_a")

    def test_set_field_undo(self):
        asset = SynthAsset(name="sfx_a", params=engine.preset("blip"))
        self.stack.push(AddAsset(asset))
        new = asset.params.copy()
        new.duration = 1.5
        self.stack.push(SetAssetField(asset.uid, "params", new))
        self.assertEqual(self.p.assets.get(asset.uid).params.duration, 1.5)
        self.stack.undo()
        self.assertEqual(self.p.assets.get(asset.uid).params.duration, 0.08)

    def test_dirty_tracking(self):
        self.assertFalse(self.stack.dirty)
        self.stack.push(AddAsset(SynthAsset(name="a")))
        self.assertTrue(self.stack.dirty)
        self.stack.mark_clean()
        self.assertFalse(self.stack.dirty)
        self.stack.undo()
        self.assertTrue(self.stack.dirty)

    def test_redo_is_cleared_by_a_new_command(self):
        self.stack.push(AddAsset(SynthAsset(name="a")))
        self.stack.undo()
        self.assertTrue(self.stack.can_redo)
        self.stack.push(AddAsset(SynthAsset(name="b")))
        self.assertFalse(self.stack.can_redo)


class TestOverrides(unittest.TestCase):
    def test_worked_example_from_the_spec(self):
        values = resolve(
            {"volume_db": 0.0, "brightness": 0.60},
            [
                ("instrument", {}),
                ("pattern", {"volume_db": -3.0}),
                ("event", {"pitch_offset": 2, "volume_db": -2.0}),
                ("clip", {"volume_db": 1.0}),
            ],
        )
        self.assertAlmostEqual(values["volume_db"], -4.0)
        self.assertAlmostEqual(values["brightness"], 0.60)
        self.assertEqual(values["pitch_offset"], 2)

    def test_set_truncates_accumulation(self):
        values = resolve(
            {"volume_db": 0.0},
            [("pattern", {"volume_db": -6.0}),
             ("event", {"volume_db": {"mode": "set", "value": -1.0}}),
             ("clip", {"volume_db": -2.0})],
        )
        self.assertAlmostEqual(values["volume_db"], -3.0)

    def test_multiplicative_default_for_spectral_center(self):
        values = resolve({"spectral_center": 1.0}, [("event", {"spectral_center": 1.5})])
        self.assertAlmostEqual(values["spectral_center"], 1.5)

    def test_clamping_applies_after_the_chain(self):
        values = resolve({"pan": 0.0}, [("a", {"pan": 0.8}), ("b", {"pan": 0.8})])
        self.assertAlmostEqual(values["pan"], 1.0)

    def test_empty_overrides_resolve_to_base(self):
        base = {"volume_db": -2.0, "brightness": 0.3}
        self.assertEqual(resolve(base, [("event", {})]), base)

    def test_explain_matches_resolve(self):
        layers = [("pattern", {"volume_db": -3.0}), ("event", {"volume_db": -2.0})]
        trace = explain({"volume_db": 0.0}, layers, "volume_db")
        self.assertAlmostEqual(trace[-1][3], resolve({"volume_db": 0.0}, layers)["volume_db"])

    def test_musical_durations(self):
        self.assertAlmostEqual(musical_to_seconds("1/8", 120), 0.25)
        self.assertAlmostEqual(musical_to_seconds("1/4", 120), 0.5)
        self.assertAlmostEqual(musical_to_seconds("1/8.", 120), 0.375)
        self.assertAlmostEqual(musical_to_seconds("1/8t", 120), 1.0 / 6.0)

    def test_duration_forms(self):
        self.assertAlmostEqual(resolve_duration(0.4, 0.2, 120), 0.4)
        self.assertAlmostEqual(resolve_duration({"ratio": 2.5}, 0.2, 120), 0.5)
        self.assertAlmostEqual(resolve_duration({"musical": "1/8"}, 0.2, 120), 0.25)
        self.assertAlmostEqual(resolve_duration(None, 0.2, 120), 0.2)


class TestRenderer(unittest.TestCase):
    def test_cache_hits_on_repeat(self):
        p = demo_project()
        r = Renderer()
        asset = p.assets.by_name("sfx_laser")
        r.render_asset(asset, p)
        r.render_asset(asset, p)
        self.assertEqual(r.cache.hits, 1)
        self.assertEqual(r.cache.misses, 1)

    def test_post_process_override_does_not_re_render(self):
        p = demo_project()
        r = Renderer()
        asset = p.assets.by_name("sfx_laser")
        plain = r.render_asset(asset, p)
        quiet = r.render_asset(asset, p, {"volume_db": -6.0})
        self.assertEqual(r.cache.misses, 1)  # served from the same base render
        np.testing.assert_allclose(quiet.samples, plain.samples * 10 ** (-6.0 / 20.0))

    def test_structural_override_renders_separately(self):
        p = demo_project()
        r = Renderer()
        asset = p.assets.by_name("sfx_laser")
        r.render_asset(asset, p)
        r.render_asset(asset, p, {"duration": 0.6})
        self.assertEqual(r.cache.misses, 2)

    def test_rename_does_not_invalidate_cache(self):
        p = demo_project()
        r = Renderer()
        asset = p.assets.by_name("sfx_laser")
        r.render_asset(asset, p)
        p.assets.rename(asset.uid, "pew")
        r.render_asset(asset, p)
        self.assertEqual(r.cache.hits, 1)


class TestAudioIO(unittest.TestCase):
    def test_wav_round_trip_16_bit(self):
        x = np.sin(np.linspace(0, 40 * np.pi, 4410)) * 0.5
        buf = AudioBuffer(x, 44100)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.wav"
            write_wav(buf, path, 16)
            back = read_wav(path)
        self.assertEqual(back.num_frames, buf.num_frames)
        self.assertEqual(back.sample_rate, 44100)
        np.testing.assert_allclose(back.samples, buf.samples, atol=1e-4)

    def test_wav_round_trip_24_bit(self):
        x = np.linspace(-0.9, 0.9, 2048)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.wav"
            write_wav(AudioBuffer(x, 22050), path, 24)
            back = read_wav(path)
        np.testing.assert_allclose(back.samples[:, 0], x, atol=1e-6)

    def test_stereo_round_trip(self):
        x = np.stack([np.linspace(-0.5, 0.5, 1000), np.linspace(0.5, -0.5, 1000)], axis=1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.wav"
            write_wav(AudioBuffer(x, 44100), path, 16)
            back = read_wav(path)
        self.assertEqual(back.channels, 2)
        np.testing.assert_allclose(back.samples, x, atol=1e-4)


class TestCompressedAudioIO(unittest.TestCase):
    """MP3/OGG/FLAC must work without FFmpeg when soundfile is installed."""

    def setUp(self):
        from raw.audio import io as audio_io

        self.io = audio_io
        if audio_io._soundfile() is None and audio_io.ffmpeg_path() is None:
            self.skipTest("no compressed-audio backend installed")

    def tone(self, seconds=0.5, freq=440.0, sr=44100):
        t = np.arange(int(seconds * sr)) / sr
        return AudioBuffer(0.4 * np.sin(2 * np.pi * freq * t), sr)

    def test_round_trip_for_each_format(self):
        from raw.audio.io import read_audio, write_audio

        source = self.tone()
        for suffix in (".mp3", ".ogg", ".flac"):
            if suffix not in self.io.writable_extensions():
                continue
            with self.subTest(format=suffix), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / f"t{suffix}"
                write_audio(source, path)
                self.assertTrue(path.exists() and path.stat().st_size > 0)
                back = read_audio(path)
                self.assertEqual(back.sample_rate, source.sample_rate)
                # Lossy codecs pad and colour, so compare loudness not samples.
                self.assertGreater(back.duration, source.duration * 0.8)
                self.assertAlmostEqual(back.rms(), source.rms(), delta=0.05)

    def test_import_filter_lists_mp3_when_supported(self):
        if ".mp3" not in self.io.readable_extensions():
            self.skipTest("no mp3 decoder")
        self.assertIn("*.mp3", self.io.import_filter())

    def test_backend_summary_mentions_wav_always(self):
        self.assertIn("wav", self.io.backend_summary())

    def test_missing_file_raises_clearly(self):
        from raw.audio.io import read_audio

        with self.assertRaises(FileNotFoundError):
            read_audio(Path(tempfile.gettempdir()) / "definitely_not_here_9182.mp3")

    def test_unreadable_data_reports_every_backend_it_tried(self):
        from raw.audio.io import read_audio

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.mp3"
            path.write_bytes(b"this is not audio")
            with self.assertRaises(RuntimeError) as ctx:
                read_audio(path)
            self.assertIn("broken.mp3", str(ctx.exception))


class TestBuffer(unittest.TestCase):
    def test_pan_is_equal_power(self):
        b = AudioBuffer(np.ones(100), 44100).panned(0.0)
        self.assertEqual(b.channels, 2)
        self.assertAlmostEqual(b.samples[0, 0] ** 2 + b.samples[0, 1] ** 2, 1.0, places=9)

    def test_normalize(self):
        b = AudioBuffer(np.linspace(0, 0.2, 100), 44100).normalized(0.98)
        self.assertAlmostEqual(b.peak(), 0.98, places=6)

    def test_mix_offsets(self):
        a = AudioBuffer(np.ones(10), 100)
        mixed = AudioBuffer.mix([(a, 0), (a, 5)], 100)
        self.assertEqual(mixed.num_frames, 15)
        self.assertAlmostEqual(mixed.samples[7, 0], 2.0)

    def test_peaks_reduction_fits_columns(self):
        b = AudioBuffer(np.random.default_rng(0).normal(0, 0.3, 100000), 44100)
        lo, hi = b.peaks(300)
        self.assertEqual(len(lo), 300)
        self.assertTrue(np.all(lo <= hi))


class TestExport(unittest.TestCase):
    def test_game_pack_writes_manifest(self):
        p = demo_project()
        with tempfile.TemporaryDirectory() as tmp:
            from raw.export import export_game_pack

            manifest = export_game_pack(p, tmp)
            root = Path(tmp)
            self.assertTrue((root / "manifest.json").exists())
            self.assertEqual(len(manifest["assets"]), len(engine.PRESET_NAMES))
            for entry in manifest["assets"].values():
                self.assertTrue((root / entry["file"]).exists())

    def test_constants_generation(self):
        p = demo_project()
        with tempfile.TemporaryDirectory() as tmp:
            from raw.export.game_pack import export_constants

            path = export_constants(p, Path(tmp) / "c.py", "python")
            text = path.read_text()
        self.assertIn('SFX_LASER = "sfx_laser"', text)


class TestValidation(unittest.TestCase):
    def test_clean_project_has_no_issues(self):
        self.assertEqual(validate(demo_project()), [])

    def test_detects_bad_pitch(self):
        p = demo_project()
        from raw.synth.trajectory import Trajectory

        p.assets.by_name("sfx_laser").params.pitch = Trajectory.constant(30000.0)
        self.assertTrue(any("Nyquist" in i.message for i in validate(p)))

    def test_detects_broken_structure(self):
        from raw.synth.timestructure import Region, TimeStructure

        p = demo_project()
        p.assets.by_name("sfx_coin").params.time_structure = TimeStructure(
            0.16, [Region("a", "attack", 0.0, 0.4), Region("b", "body", 0.6, 1.0)]
        )
        self.assertTrue(any("tile" in i.message for i in validate(p)))


if __name__ == "__main__":
    unittest.main()
