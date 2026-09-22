"""The authoring format: parsing, error tolerance, pattern rendering, and a
guarantee that every example in the specification actually imports.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import numpy as np

from raw.core.authoring import (
    document_from_project,
    expand_notes,
    parse_document,
    sound_to_author,
)
from raw.core.assets import InstrumentAsset, PatternAsset, SynthAsset
from raw.core.project import Project
from raw.core.renderer import Renderer
from raw.patterns.notes import midi_to_note, note_to_hz, note_to_midi
from raw.patterns.renderer import instrument_root_midi, render_pattern
from raw.synth import engine

DOCS = Path(__file__).resolve().parents[1] / "docs"
SR = 44100


def dominant_freq(buffer, start_frac=0.15, win=4096) -> float:
    n = buffer.num_frames
    win = min(win, n)
    start = min(max(0, int(start_frac * n)), max(0, n - win))
    seg = buffer.samples[start : start + win, 0] * np.hanning(win)
    spec = np.abs(np.fft.rfft(seg))
    spec[:3] = 0.0
    k = int(np.argmax(spec))
    return k * buffer.sample_rate / win


class TestNotes(unittest.TestCase):
    def test_reference_pitches(self):
        self.assertEqual(note_to_midi("C4"), 60)
        self.assertEqual(note_to_midi("A4"), 69)
        self.assertAlmostEqual(note_to_hz("A4"), 440.0)
        self.assertAlmostEqual(note_to_hz("A5"), 880.0)

    def test_accidentals(self):
        self.assertEqual(note_to_midi("A#3"), note_to_midi("Bb3"))
        self.assertEqual(note_to_midi("C#4"), 61)

    def test_round_trip_names(self):
        for name in ("C4", "F#3", "A5", "D#2"):
            self.assertEqual(midi_to_note(note_to_midi(name)), name)

    def test_rejects_nonsense(self):
        from raw.patterns.notes import NoteError

        with self.assertRaises(NoteError):
            note_to_midi("H9")


class TestCompactNotes(unittest.TestCase):
    def parse(self, text):
        warnings: list[str] = []
        return expand_notes(text, "t", warnings), warnings

    def test_notes_and_rests(self):
        events, warnings = self.parse("C4 . E4 . G4")
        self.assertEqual([e["step"] for e in events], [0, 2, 4])
        self.assertEqual([e["note"] for e in events], ["C4", "E4", "G4"])
        self.assertEqual(warnings, [])

    def test_bar_separators_are_ignored(self):
        events, _ = self.parse("C4 . | E4 .")
        self.assertEqual([e["step"] for e in events], [0, 2])

    def test_hold_extends_previous(self):
        events, _ = self.parse("C4 - - . E4")
        self.assertEqual(events[0]["length"], 3)
        self.assertEqual(events[1]["step"], 4)

    def test_trigger_and_accent_carry_no_note(self):
        events, _ = self.parse("x . X .")
        self.assertNotIn("note", events[0])
        self.assertEqual(events[1]["velocity"], 1.0)

    def test_explicit_length_suffix(self):
        events, _ = self.parse("C4:1/8 . D4:4")
        self.assertEqual(events[0]["length"], "1/8")
        self.assertEqual(events[1]["length"], 4.0)

    def test_bad_token_warns_but_keeps_timing(self):
        events, warnings = self.parse("C4 zz E4")
        self.assertEqual([e["step"] for e in events], [0, 2])
        self.assertTrue(any("zz" in w for w in warnings))

    def test_orphan_hold_warns(self):
        _, warnings = self.parse("- C4")
        self.assertTrue(any("nothing to extend" in w for w in warnings))


class TestParsing(unittest.TestCase):
    def test_minimal_document(self):
        result = parse_document({"format": "raw.author", "version": 1,
                                 "sounds": [{"name": "sfx_jump", "from_preset": "jump"}]})
        self.assertTrue(result.ok)
        self.assertEqual(len(result.assets), 1)
        self.assertIsInstance(result.assets[0], SynthAsset)
        self.assertEqual(result.assets[0].name, "sfx_jump")

    def test_accepts_a_json_string(self):
        result = parse_document('{"format":"raw.author","sounds":[{"name":"a"}]}')
        self.assertTrue(result.ok)

    def test_invalid_json_is_an_error_not_a_crash(self):
        result = parse_document('{"format": "raw.author",}')
        self.assertFalse(result.ok)
        self.assertTrue(any("valid JSON" in e for e in result.errors))

    def test_missing_name_is_an_error_but_others_still_import(self):
        result = parse_document({"format": "raw.author",
                                 "sounds": [{"oscillator": "sine"}, {"name": "ok_one"}]})
        self.assertEqual(len(result.assets), 1)
        self.assertTrue(any("missing required field 'name'" in e for e in result.errors))

    def test_note_names_as_pitch(self):
        result = parse_document({"format": "raw.author",
                                 "sounds": [{"name": "a", "pitch": "A4"}]})
        self.assertAlmostEqual(result.assets[0].params.pitch.value_at(0.0), 440.0, places=3)

    def test_pitch_ramp_by_note(self):
        result = parse_document({"format": "raw.author", "sounds": [
            {"name": "a", "pitch": {"from": "A5", "to": "A3"}}]})
        traj = result.assets[0].params.pitch
        self.assertAlmostEqual(traj.value_at(0.0), 880.0, places=2)
        self.assertAlmostEqual(traj.value_at(1.0), 220.0, places=2)

    def test_multi_point_pitch(self):
        result = parse_document({"format": "raw.author", "sounds": [
            {"name": "a", "pitch": {"curve": "step", "points": [
                {"at": 0.0, "note": "B5"}, {"at": 0.5, "note": "E6"}]}}]})
        traj = result.assets[0].params.pitch
        self.assertEqual(traj.curve, "step")
        self.assertEqual(len(traj.points), 2)

    def test_envelope_array_form(self):
        result = parse_document({"format": "raw.author", "sounds": [
            {"name": "a", "envelope": [0.01, 0.02, 0.5, 0.03]}]})
        env = result.assets[0].params.envelope
        self.assertAlmostEqual(env.attack, 0.01)
        self.assertAlmostEqual(env.sustain, 0.5)

    def test_unknown_oscillator_warns_and_keeps_default(self):
        result = parse_document({"format": "raw.author", "sounds": [
            {"name": "a", "oscillator": "supersaw"}]})
        self.assertEqual(result.assets[0].params.oscillator, "square")
        self.assertTrue(any("supersaw" in w and "valid" in w for w in result.warnings))

    def test_unknown_filter_parameter_names_the_valid_ones(self):
        result = parse_document({"format": "raw.author", "sounds": [
            {"name": "a", "filters": [{"type": "emphasis", "q": 3}]}]})
        self.assertTrue(any("has no parameter 'q'" in w for w in result.warnings))
        self.assertTrue(any("center" in w for w in result.warnings))

    def test_unknown_filter_type_is_skipped(self):
        result = parse_document({"format": "raw.author", "sounds": [
            {"name": "a", "filters": [{"type": "phaser"}]}]})
        self.assertEqual(result.assets[0].params.filters, [])
        self.assertTrue(any("phaser" in w for w in result.warnings))

    def test_time_effects_are_accepted(self):
        result = parse_document({"format": "raw.author", "sounds": [{
            "name": "a",
            "filters": [
                {"type": "delay", "time": 0.2, "feedback": 0.4, "mix": 0.3},
                {"type": "reverb", "size": 1.5, "mix": 0.4},
            ]}]})
        self.assertEqual(result.warnings, [])
        self.assertEqual([f.type for f in result.assets[0].params.filters],
                         ["delay", "reverb"])

    def test_out_of_range_is_clamped_and_reported(self):
        result = parse_document({"format": "raw.author", "sounds": [
            {"name": "a", "brightness": 5.0, "duration": 999}]})
        p = result.assets[0].params
        self.assertEqual(p.brightness, 1.0)
        self.assertEqual(p.duration, 60.0)
        self.assertEqual(len([w for w in result.warnings if "clamped" in w]), 2)

    def test_unknown_field_is_reported(self):
        result = parse_document({"format": "raw.author", "sounds": [
            {"name": "a", "reverb_amount": 0.5}]})
        self.assertTrue(any("reverb_amount" in w for w in result.warnings))

    def test_wrong_format_header_still_parses_with_a_warning(self):
        result = parse_document({"sounds": [{"name": "a"}]})
        self.assertTrue(result.ok)
        self.assertTrue(any("format" in w for w in result.warnings))

    def test_relative_region_lengths_are_normalised(self):
        result = parse_document({"format": "raw.author", "sounds": [{
            "name": "a", "duration": 0.4, "regions": [
                {"role": "attack", "length": 1, "elasticity": 0},
                {"role": "body", "length": 3, "elasticity": 1.0}]}]})
        ts = result.assets[0].params.time_structure
        self.assertEqual(result.assets[0].params.stretch_mode, "custom")
        self.assertEqual(ts.issues(), [])
        self.assertAlmostEqual(ts.regions[0].end, 0.25, places=6)

    def test_project_settings_are_collected(self):
        result = parse_document({"format": "raw.author", "project": {"tempo": 145},
                                 "sounds": [{"name": "a"}]})
        self.assertEqual(result.settings["tempo"], 145)

    def test_empty_document_is_an_error(self):
        self.assertFalse(parse_document({"format": "raw.author"}).ok)


class TestPatternParsing(unittest.TestCase):
    DOC = {
        "format": "raw.author",
        "instruments": [{"name": "lead", "pitch": "C4", "root_note": "C4"}],
        "patterns": [{
            "name": "pat_a", "tempo": 120, "steps": 8, "step": "1/8",
            "tracks": [{"name": "melody", "instrument": "lead", "notes": "C4 . E4 . G4 . . ."}],
        }],
    }

    def test_instrument_reference_resolves_to_a_uid(self):
        result = parse_document(dict(self.DOC))
        instrument = next(a for a in result.assets if isinstance(a, InstrumentAsset))
        pattern = next(a for a in result.assets if isinstance(a, PatternAsset))
        self.assertEqual(pattern.tracks[0]["instrument"], instrument.uid)

    def test_resolves_against_an_existing_project_asset(self):
        project = Project()
        project.assets.add(InstrumentAsset(name="lead", params=engine.preset("blip")))
        doc = {"format": "raw.author", "patterns": [dict(self.DOC["patterns"][0])]}
        result = parse_document(doc, project)
        pattern = result.assets[0]
        self.assertEqual(pattern.tracks[0]["instrument"], project.assets.by_name("lead").uid)

    def test_unknown_instrument_warns_but_keeps_the_track(self):
        doc = {"format": "raw.author", "patterns": [{
            "name": "p", "tracks": [{"instrument": "ghost", "notes": "C4"}]}]}
        result = parse_document(doc)
        self.assertTrue(any("ghost" in w for w in result.warnings))
        self.assertEqual(len(result.assets[0].tracks), 1)

    def test_events_beyond_the_pattern_length_are_reported(self):
        doc = {"format": "raw.author", "instruments": [{"name": "l"}], "patterns": [{
            "name": "p", "steps": 4,
            "tracks": [{"instrument": "l", "notes": "C4 . . . . . C5 ."}]}]}
        result = parse_document(doc)
        self.assertTrue(any("fall past step" in w for w in result.warnings))

    def test_name_collision_is_reported(self):
        project = Project()
        project.assets.add(SynthAsset(name="sfx_a"))
        result = parse_document(
            {"format": "raw.author", "sounds": [{"name": "sfx_a"}]}, project
        )
        self.assertTrue(any("already exists" in w for w in result.warnings))


class TestPatternRendering(unittest.TestCase):
    def build(self, doc):
        project = Project()
        result = parse_document(doc, project)
        for asset in result.assets:
            project.assets.add(asset)
        return project, result

    def test_pattern_renders_audible_audio(self):
        doc = {
            "format": "raw.author",
            "instruments": [{"name": "lead", "oscillator": "square", "pitch": "C4",
                             "root_note": "C4", "duration": 0.2}],
            "patterns": [{"name": "p", "tempo": 120, "steps": 8, "step": "1/8",
                          "tracks": [{"instrument": "lead", "notes": "C4 . E4 . G4 . . ."}]}],
        }
        project, _ = self.build(doc)
        pattern = project.assets.by_name("p")
        buf, warnings = render_pattern(pattern, project, Renderer())
        self.assertEqual(warnings, [])
        self.assertGreater(buf.peak(), 0.05)
        # 8 steps of an eighth note at 120 BPM = 2.0 s
        self.assertAlmostEqual(buf.duration, 2.0, places=2)

    def test_a_note_transposes_rather_than_replaces_the_curve(self):
        doc = {
            "format": "raw.author",
            "instruments": [{"name": "lead", "oscillator": "sine", "pitch": "C4",
                             "root_note": "C4", "duration": 0.4,
                             "envelope": [0.001, 0.0, 1.0, 0.001], "drift": 0}],
            "patterns": [
                {"name": "low", "tempo": 120, "steps": 2, "step": "1/4",
                 "tracks": [{"instrument": "lead", "notes": "C4 ."}]},
                {"name": "high", "tempo": 120, "steps": 2, "step": "1/4",
                 "tracks": [{"instrument": "lead", "notes": "C5 ."}]},
            ],
        }
        project, _ = self.build(doc)
        renderer = Renderer()
        low, _ = render_pattern(project.assets.by_name("low"), project, renderer)
        high, _ = render_pattern(project.assets.by_name("high"), project, renderer)
        self.assertAlmostEqual(dominant_freq(high) / dominant_freq(low), 2.0, delta=0.06)

    def test_a_swept_instrument_keeps_its_sweep_when_transposed(self):
        """The whole point of transposing instead of replacing."""
        doc = {"format": "raw.author", "instruments": [
            {"name": "zap", "oscillator": "square", "root_note": "A4",
             "pitch": {"from": "A5", "to": "A3"}, "duration": 0.3}]}
        project, result = self.build(doc)
        params = project.assets.by_name("zap").params
        self.assertAlmostEqual(params.pitch.value_at(0.0) / params.pitch.value_at(1.0), 4.0, places=3)
        self.assertAlmostEqual(instrument_root_midi(project.assets.by_name("zap")), 69.0, places=3)

    def test_drums_without_a_note_play_at_their_own_pitch(self):
        doc = {
            "format": "raw.author",
            "instruments": [{"name": "kick", "oscillator": "sine",
                             "pitch": {"from": 170, "to": 45}, "duration": 0.18}],
            "patterns": [{"name": "beat", "tempo": 120, "steps": 4, "step": "1/4",
                          "tracks": [{"instrument": "kick", "notes": "x . x ."}]}],
        }
        project, _ = self.build(doc)
        pattern = project.assets.by_name("beat")
        for track in pattern.tracks:
            for event in track["events"]:
                self.assertNotIn("note", event)
        buf, warnings = render_pattern(pattern, project, Renderer())
        self.assertEqual(warnings, [])
        self.assertGreater(buf.peak(), 0.05)

    def test_velocity_lowers_the_level(self):
        doc = {
            "format": "raw.author",
            "instruments": [{"name": "i", "oscillator": "sine", "pitch": "A4",
                             "root_note": "A4", "duration": 0.2, "drift": 0}],
            "patterns": [
                {"name": "loud", "tempo": 120, "steps": 2, "step": "1/4",
                 "tracks": [{"instrument": "i", "events": [{"step": 0, "note": "A4", "velocity": 1.0}]}]},
                {"name": "soft", "tempo": 120, "steps": 2, "step": "1/4",
                 "tracks": [{"instrument": "i", "events": [{"step": 0, "note": "A4", "velocity": 0.5}]}]},
            ],
        }
        project, _ = self.build(doc)
        renderer = Renderer()
        loud, _ = render_pattern(project.assets.by_name("loud"), project, renderer)
        soft, _ = render_pattern(project.assets.by_name("soft"), project, renderer)
        self.assertAlmostEqual(soft.peak() / loud.peak(), 0.5, delta=0.02)

    def test_pattern_level_override_transposes_every_event(self):
        doc = {
            "format": "raw.author",
            "instruments": [{"name": "i", "oscillator": "sine", "pitch": "A4",
                             "root_note": "A4", "duration": 0.3,
                             "envelope": [0.001, 0.0, 1.0, 0.001], "drift": 0}],
            "patterns": [{"name": "p", "tempo": 120, "steps": 2, "step": "1/4",
                          "tracks": [{"instrument": "i", "notes": "A4 ."}]}],
        }
        project, _ = self.build(doc)
        pattern = project.assets.by_name("p")
        renderer = Renderer()
        plain, _ = render_pattern(pattern, project, renderer)
        up, _ = render_pattern(pattern, project, renderer, overrides={"pitch_offset": 12})
        self.assertAlmostEqual(dominant_freq(up) / dominant_freq(plain), 2.0, delta=0.06)

    def test_pattern_override_survives_the_renderer_entry_point(self):
        """A pattern reached through Renderer.render_asset must honour overrides."""
        doc = {
            "format": "raw.author",
            "instruments": [{"name": "i", "oscillator": "sine", "pitch": "A4",
                             "root_note": "A4", "duration": 0.3,
                             "envelope": [0.001, 0.0, 1.0, 0.001], "drift": 0}],
            "patterns": [{"name": "p", "tempo": 120, "steps": 2, "step": "1/4",
                          "tracks": [{"instrument": "i", "notes": "A4 ."}]}],
        }
        project, _ = self.build(doc)
        renderer = Renderer()
        pattern = project.assets.by_name("p")
        plain = renderer.render_asset(pattern, project)
        up = renderer.render_asset(pattern, project, {"pitch_offset": 12})
        self.assertAlmostEqual(dominant_freq(up) / dominant_freq(plain), 2.0, delta=0.06)

    def test_pattern_renders_through_the_normal_asset_path(self):
        doc = {
            "format": "raw.author",
            "instruments": [{"name": "i", "oscillator": "square", "pitch": "A4", "duration": 0.1}],
            "patterns": [{"name": "p", "steps": 4, "step": "1/8",
                          "tracks": [{"instrument": "i", "notes": "A4 . A4 ."}]}],
        }
        project, _ = self.build(doc)
        buf = Renderer().render_asset(project.assets.by_name("p"), project)
        self.assertGreater(buf.peak(), 0.05)


class TestExportRoundTrip(unittest.TestCase):
    def test_project_exports_and_reimports(self):
        project = Project()
        for name in engine.PRESET_NAMES:
            project.assets.add(SynthAsset(name=f"sfx_{name}", params=engine.preset(name)))
        doc = document_from_project(project)
        self.assertEqual(doc["format"], "raw.author")

        result = parse_document(doc)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(len(result.assets), len(engine.PRESET_NAMES))

        original = project.assets.by_name("sfx_laser").params
        again = next(a for a in result.assets if a.name == "sfx_laser").params
        self.assertEqual(again.oscillator, original.oscillator)
        self.assertAlmostEqual(again.duration, original.duration, places=4)
        self.assertAlmostEqual(again.pitch.value_at(0.0), original.pitch.value_at(0.0), places=2)
        self.assertAlmostEqual(again.pitch.value_at(1.0), original.pitch.value_at(1.0), places=2)
        self.assertEqual(again.stretch_mode, original.stretch_mode)

    def test_exported_sound_declares_a_root_note(self):
        asset = SynthAsset(name="a", params=engine.preset("blip"))
        self.assertIn("root_note", sound_to_author(asset))


class TestSpecificationExamples(unittest.TestCase):
    """Every complete example in AUTHORING_FORMAT.md must import cleanly.

    This keeps the file we hand to a chatbot from drifting away from the code.
    """

    def documents(self):
        text = (DOCS / "AUTHORING_FORMAT.md").read_text(encoding="utf-8")
        for block in re.findall(r"```json\n(.*?)```", text, re.S):
            if '"format": "raw.author"' in block and "..." not in block:
                yield block

    def test_specification_has_examples(self):
        self.assertGreaterEqual(len(list(self.documents())), 3)

    def test_every_example_parses_without_errors_or_warnings(self):
        for i, block in enumerate(self.documents()):
            with self.subTest(example=i):
                result = parse_document(block)
                self.assertTrue(result.ok, f"example {i}: {result.errors}")
                self.assertEqual(result.warnings, [], f"example {i} produced warnings")

    def test_schema_is_valid_json(self):
        schema = json.loads((DOCS / "raw_author_schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["title"], "RAW Authoring Format")

    def test_bundled_example_file_renders(self):
        path = DOCS / "examples" / "boss_fight.author.json"
        project = Project()
        result = parse_document(path.read_text(encoding="utf-8"), project)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.warnings, [])
        for asset in result.assets:
            project.assets.add(asset)
        buf, warnings = render_pattern(project.assets.by_name("pattern_boss_a"), project, Renderer())
        self.assertEqual(warnings, [])
        self.assertGreater(buf.peak(), 0.1)


if __name__ == "__main__":
    unittest.main()
