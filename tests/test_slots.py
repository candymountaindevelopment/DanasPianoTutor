"""Dependency-aware caching and sound slots.

The stale-cache test is the important one: without it, "assign the sound later"
silently appears to do nothing, because the pattern's own dict does not change
when the instrument it points at does.
"""

from __future__ import annotations

import unittest

import numpy as np

from raw.core.authoring import document_from_project, parse_document
from raw.core.commands import CommandStack, RemoveSlot, SetSlot
from raw.core.project import Project
from raw.core.renderer import Renderer, dependency_keys
from raw.core.slots import (
    Slot,
    placeholder_midi,
    placeholder_params,
    resolve_track_source,
    unbound_slots,
)
from raw.core.validation import validate
from raw.patterns.renderer import render_pattern

DOC = {
    "format": "raw.author",
    "instruments": [
        {"name": "lead", "oscillator": "sine", "pitch": "A4", "root_note": "A4",
         "duration": 0.3, "amplitude": 0.8, "envelope": [0.001, 0, 1, 0.001], "drift": 0}
    ],
    "patterns": [
        {"name": "p", "tempo": 120, "steps": 2, "step": "1/4",
         "tracks": [{"instrument": "lead", "notes": "A4 ."}]}
    ],
}

SLOT_DOC = {
    "format": "raw.author",
    "slots": {"kick": None, "snare": None, "lead": None},
    "patterns": [
        {"name": "beat", "tempo": 120, "steps": 4, "step": "1/8",
         "tracks": [
             {"name": "kick", "slot": "kick", "notes": "x . x ."},
             {"name": "snare", "slot": "snare", "notes": ". . x ."},
         ]}
    ],
}


def build(doc):
    project = Project()
    result = parse_document(doc, project)
    for asset in result.assets:
        project.assets.add(asset)
    for name, entry in result.slots.items():
        project.slots[name] = Slot(name, entry.get("asset"), entry.get("overrides", {}))
    return project, result


class TestDependencyAwareCache(unittest.TestCase):
    def test_editing_an_instrument_invalidates_the_pattern_render(self):
        project, _ = build(DOC)
        renderer = Renderer()
        pattern = project.assets.by_name("p")
        lead = project.assets.by_name("lead")

        self.assertAlmostEqual(renderer.render_asset(pattern, project).peak(), 0.8, delta=0.02)
        lead.params.amplitude = 0.2
        self.assertAlmostEqual(renderer.render_asset(pattern, project).peak(), 0.2, delta=0.02)

    def test_dependency_keys_lists_referenced_assets(self):
        project, _ = build(DOC)
        pattern = project.assets.by_name("p")
        lead = project.assets.by_name("lead")
        deps = dependency_keys(pattern, project)
        self.assertIn(lead.uid, deps)
        self.assertEqual(deps[lead.uid], lead.render_key())

    def test_dependency_keys_are_empty_for_a_plain_sound(self):
        project, _ = build(DOC)
        self.assertEqual(dependency_keys(project.assets.by_name("lead"), project), {})

    def test_renaming_an_instrument_still_hits_the_cache(self):
        project, _ = build(DOC)
        renderer = Renderer()
        pattern = project.assets.by_name("p")
        renderer.render_asset(pattern, project)
        project.assets.rename(project.assets.by_name("lead").uid, "renamed")
        renderer.render_asset(pattern, project)
        self.assertEqual(renderer.cache.hits, 1)

    def test_self_reference_terminates_and_excludes_the_root(self):
        """A slot can bind a pattern back to itself. The root is left out of
        deps because its own render_key is already in the cache payload."""
        project, _ = build(SLOT_DOC)
        beat = project.assets.by_name("beat")
        project.slots["kick"] = Slot("kick", beat.uid)
        self.assertEqual(dependency_keys(beat, project), {})

    def test_two_patterns_referencing_each_other_terminate(self):
        project, _ = build(SLOT_DOC)
        a = project.assets.by_name("beat")
        b = parse_document(
            {"format": "raw.author",
             "patterns": [{"name": "beat_b", "tracks": [{"slot": "other", "notes": "x ."}]}]}
        ).assets[0]
        project.assets.add(b)
        project.slots["kick"] = Slot("kick", b.uid)
        project.slots["other"] = Slot("other", a.uid)
        self.assertIn(b.uid, dependency_keys(a, project))
        self.assertIn(a.uid, dependency_keys(b, project))

    def test_a_pattern_bound_as_an_instrument_is_refused_not_recursed(self):
        project, _ = build(SLOT_DOC)
        beat = project.assets.by_name("beat")
        project.slots["kick"] = Slot("kick", beat.uid)
        buf, warnings = render_pattern(beat, project, Renderer())
        self.assertTrue(any("not playable" in w for w in warnings))
        self.assertGreater(buf.duration, 0.0)


class TestSlotModel(unittest.TestCase):
    def test_round_trip_through_the_project_file(self):
        project, _ = build(SLOT_DOC)
        project.slots["kick"].overrides = {"volume_db": -3}
        again, _ = Project.from_dict(project.to_dict())
        self.assertEqual(sorted(again.slots), ["kick", "lead", "snare"])
        self.assertEqual(again.slots["kick"].overrides, {"volume_db": -3})

    def test_a_direct_instrument_reference_still_works(self):
        project, _ = build(DOC)
        pattern = project.assets.by_name("p")
        source = resolve_track_source(pattern.tracks[0], pattern, project)
        self.assertIsNotNone(source.asset)
        self.assertIsNone(source.slot)
        self.assertFalse(source.is_placeholder)

    def test_unbound_slot_resolves_to_a_placeholder(self):
        project, _ = build(SLOT_DOC)
        beat = project.assets.by_name("beat")
        source = resolve_track_source(beat.tracks[0], beat, project)
        self.assertIsNone(source.asset)
        self.assertTrue(source.is_placeholder)
        self.assertIn("unbound", source.label)

    def test_binding_a_slot_changes_what_a_track_resolves_to(self):
        project, _ = build(SLOT_DOC)
        from raw.core.assets import SynthAsset
        from raw.synth import engine

        hit = project.assets.add(SynthAsset(name="sfx_hit", params=engine.preset("hit")))
        project.slots["kick"].asset = hit.uid
        beat = project.assets.by_name("beat")
        source = resolve_track_source(beat.tracks[0], beat, project)
        self.assertIs(source.asset, hit)
        self.assertFalse(source.is_placeholder)

    def test_pattern_level_binding_beats_the_project_binding(self):
        project, _ = build(SLOT_DOC)
        from raw.core.assets import SynthAsset
        from raw.synth import engine

        a = project.assets.add(SynthAsset(name="global_kick", params=engine.preset("hit")))
        b = project.assets.add(SynthAsset(name="local_kick", params=engine.preset("blip")))
        project.slots["kick"].asset = a.uid
        beat = project.assets.by_name("beat")
        beat.defaults["slots"] = {"kick": {"asset": b.uid}}
        self.assertIs(resolve_track_source(beat.tracks[0], beat, project).asset, b)

    def test_unbound_slots_reported_per_pattern(self):
        project, _ = build(SLOT_DOC)
        beat = project.assets.by_name("beat")
        self.assertEqual(sorted(unbound_slots(beat, project)), ["kick", "snare"])

    def test_slot_bound_to_a_deleted_asset_counts_as_unbound(self):
        project, _ = build(SLOT_DOC)
        project.slots["kick"].asset = "does-not-exist"
        beat = project.assets.by_name("beat")
        self.assertIn("kick", unbound_slots(beat, project))


class TestSlotCommands(unittest.TestCase):
    def setUp(self):
        self.project, _ = build(SLOT_DOC)
        self.stack = CommandStack(self.project)

    def test_bind_and_undo(self):
        from raw.core.assets import SynthAsset
        from raw.synth import engine

        hit = self.project.assets.add(SynthAsset(name="sfx_hit", params=engine.preset("hit")))
        self.stack.push(SetSlot("kick", hit.uid))
        self.assertEqual(self.project.slots["kick"].asset, hit.uid)
        self.stack.undo()
        self.assertIsNone(self.project.slots["kick"].asset)
        self.stack.redo()
        self.assertEqual(self.project.slots["kick"].asset, hit.uid)

    def test_binding_a_new_slot_then_undo_removes_it(self):
        self.stack.push(SetSlot("hat", None))
        self.assertIn("hat", self.project.slots)
        self.stack.undo()
        self.assertNotIn("hat", self.project.slots)

    def test_remove_slot_is_undoable(self):
        self.project.slots["kick"].overrides = {"volume_db": -4}
        self.stack.push(RemoveSlot("kick"))
        self.assertNotIn("kick", self.project.slots)
        self.stack.undo()
        self.assertEqual(self.project.slots["kick"].overrides, {"volume_db": -4})


class TestPlaceholders(unittest.TestCase):
    def test_pattern_with_no_bindings_still_makes_sound(self):
        project, _ = build(SLOT_DOC)
        beat = project.assets.by_name("beat")
        buf, warnings = render_pattern(beat, project, Renderer())
        self.assertGreater(buf.peak(), 0.05, "an unbound pattern must still be audible")
        self.assertTrue(any("not bound" in w for w in warnings))

    def test_placeholder_pitch_is_stable_and_distinct(self):
        self.assertEqual(placeholder_midi("kick"), placeholder_midi("kick"))
        pitches = {placeholder_midi(n) for n in ("kick", "snare", "hat", "lead", "bass")}
        self.assertGreaterEqual(len(pitches), 3, "roles should be distinguishable by ear")

    def test_placeholder_is_short_and_quiet(self):
        params = placeholder_params("kick")
        self.assertLess(params.duration, 0.2)
        self.assertLess(params.amplitude, 0.6)

    def test_binding_changes_the_rendered_audio(self):
        project, _ = build(SLOT_DOC)
        from raw.core.assets import SynthAsset
        from raw.synth import engine

        renderer = Renderer()
        beat = project.assets.by_name("beat")
        before = render_pattern(beat, project, renderer)[0]

        hit = project.assets.add(SynthAsset(name="sfx_hit", params=engine.preset("explosion")))
        project.slots["kick"].asset = hit.uid
        after = render_pattern(beat, project, renderer)[0]

        self.assertFalse(np.allclose(before.samples[: after.num_frames],
                                     after.samples[: before.num_frames]))

    def test_slot_overrides_reach_the_render(self):
        project, _ = build(SLOT_DOC)
        renderer = Renderer()
        beat = project.assets.by_name("beat")
        loud = render_pattern(beat, project, renderer)[0].peak()
        for name in ("kick", "snare"):
            project.slots[name].overrides = {"volume_db": -12}
        quiet = render_pattern(beat, project, renderer)[0].peak()
        self.assertLess(quiet, loud * 0.5)

    def test_placeholder_render_is_cached(self):
        project, _ = build(SLOT_DOC)
        renderer = Renderer()
        renderer.render_placeholder("kick", project)
        renderer.render_placeholder("kick", project)
        self.assertEqual(renderer.cache.hits, 1)


class TestSlotAuthoring(unittest.TestCase):
    def test_slots_declared_and_left_unbound(self):
        result = parse_document(SLOT_DOC)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(sorted(result.slots), ["kick", "lead", "snare"])
        self.assertTrue(all(e["asset"] is None for e in result.slots.values()))

    def test_slots_bound_to_sounds_in_the_same_document(self):
        doc = {
            "format": "raw.author",
            "sounds": [{"name": "sfx_hit", "from_preset": "hit"}],
            "slots": {"kick": "sfx_hit"},
        }
        result = parse_document(doc)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.slots["kick"]["asset"], result.assets[0].uid)

    def test_slot_bound_to_an_unknown_sound_warns_but_survives(self):
        result = parse_document({"format": "raw.author", "slots": {"kick": "ghost"}})
        self.assertTrue(result.ok)
        self.assertIsNone(result.slots["kick"]["asset"])
        self.assertTrue(any("ghost" in w for w in result.warnings))

    def test_slot_overrides_parse(self):
        doc = {"format": "raw.author",
               "slots": {"kick": {"sound": None, "overrides": {"volume_db": -6}}}}
        result = parse_document(doc)
        self.assertEqual(result.slots["kick"]["overrides"], {"volume_db": -6})

    def test_track_slot_survives_export_round_trip(self):
        project, _ = build(SLOT_DOC)
        doc = document_from_project(project)
        self.assertIn("slots", doc)
        self.assertEqual(doc["patterns"][0]["tracks"][0]["slot"], "kick")
        again = parse_document(doc)
        self.assertTrue(again.ok, again.errors)
        self.assertEqual(again.assets[0].tracks[0]["slot"], "kick")

    def test_slot_and_instrument_together_warns(self):
        doc = {
            "format": "raw.author",
            "sounds": [{"name": "s", "from_preset": "blip"}],
            "patterns": [{"name": "p", "tracks": [
                {"slot": "kick", "instrument": "s", "notes": "x ."}]}],
        }
        result = parse_document(doc)
        self.assertTrue(any("slot wins" in w for w in result.warnings))
        self.assertEqual(result.assets[-1].tracks[0].get("slot"), "kick")


class TestSlotValidation(unittest.TestCase):
    def test_unbound_slots_are_a_warning_not_an_error(self):
        project, _ = build(SLOT_DOC)
        issues = validate(project)
        unbound = [i for i in issues if "not bound" in i.message]
        self.assertEqual(len(unbound), 2)
        self.assertTrue(all(i.level == "warning" for i in unbound))

    def test_slot_pointing_at_a_deleted_asset_is_an_error(self):
        project, _ = build(SLOT_DOC)
        project.slots["kick"].asset = "gone"
        self.assertTrue(any(i.level == "error" and "no longer exists" in i.message
                            for i in validate(project)))


if __name__ == "__main__":
    unittest.main()
