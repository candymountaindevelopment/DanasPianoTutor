"""Import merge, and the transport (BPM control + playback progress)."""

from __future__ import annotations

import unittest

from raw.core.assets import InstrumentAsset, PatternAsset, SynthAsset
from raw.core.authoring import parse_document
from raw.core.commands import CommandStack, ReplaceAsset
from raw.core.project import Project
from raw.core.renderer import Renderer
from raw.synth import engine

try:
    from PyQt6.QtWidgets import QApplication

    QT = True
except Exception:  # pragma: no cover
    QT = False

DOC_V1 = {
    "format": "raw.author",
    "instruments": [{"name": "lead", "oscillator": "sine", "pitch": "A4",
                     "root_note": "A4", "duration": 0.3, "amplitude": 0.8}],
    "patterns": [{"name": "p", "tempo": 120, "steps": 4, "step": "1/8",
                  "tracks": [{"instrument": "lead", "notes": "A4 . A4 ."}]}],
}

DOC_V2 = {
    "format": "raw.author",
    "instruments": [{"name": "lead", "oscillator": "square", "pitch": "A4",
                     "root_note": "A4", "duration": 0.3, "amplitude": 0.3}],
    "patterns": [{"name": "p", "tempo": 90, "steps": 4, "step": "1/8",
                  "tracks": [{"instrument": "lead", "notes": "A4 . C5 ."}]}],
}


def apply(project, result):
    for asset in result.assets:
        project.assets.add(asset)
    for asset in result.updated:
        project.assets.replace(asset.uid, asset)


class TestImportMerge(unittest.TestCase):
    def setUp(self):
        self.project = Project()
        apply(self.project, parse_document(DOC_V1, self.project, merge=True))

    def test_first_import_is_all_new(self):
        project = Project()
        result = parse_document(DOC_V1, project, merge=True)
        self.assertEqual(len(result.assets), 2)
        self.assertEqual(result.updated, [])

    def test_reimport_updates_instead_of_duplicating(self):
        result = parse_document(DOC_V2, self.project, merge=True)
        self.assertEqual(result.assets, [])
        self.assertEqual(len(result.updated), 2)
        apply(self.project, result)
        self.assertEqual(sorted(a.name for a in self.project.assets), ["lead", "p"])

    def test_uuids_survive_so_references_keep_working(self):
        lead_uid = self.project.assets.by_name("lead").uid
        pattern_uid = self.project.assets.by_name("p").uid

        result = parse_document(DOC_V2, self.project, merge=True)
        apply(self.project, result)

        self.assertEqual(self.project.assets.by_name("lead").uid, lead_uid)
        self.assertEqual(self.project.assets.by_name("p").uid, pattern_uid)
        # and the pattern still points at the same instrument
        self.assertEqual(self.project.assets.by_name("p").tracks[0]["instrument"], lead_uid)

    def test_a_timeline_clip_still_resolves_after_reimport(self):
        pattern = self.project.assets.by_name("p")
        self.project.timeline = {"tracks": [{"name": "t"}],
                                 "clips": [{"id": "c1", "asset": pattern.uid, "track": 0,
                                            "start": 0.0, "duration": 1.0, "overrides": {}}]}
        apply(self.project, parse_document(DOC_V2, self.project, merge=True))
        clip = self.project.timeline["clips"][0]
        self.assertIsNotNone(self.project.assets.get(clip["asset"]))

    def test_a_slot_binding_still_resolves_after_reimport(self):
        from raw.core.slots import Slot

        lead = self.project.assets.by_name("lead")
        self.project.slots["lead"] = Slot("lead", lead.uid)
        apply(self.project, parse_document(DOC_V2, self.project, merge=True))
        self.assertIsNotNone(self.project.assets.get(self.project.slots["lead"].asset))

    def test_the_content_actually_changes(self):
        apply(self.project, parse_document(DOC_V2, self.project, merge=True))
        lead = self.project.assets.by_name("lead")
        self.assertEqual(lead.params.oscillator, "square")
        self.assertAlmostEqual(lead.params.amplitude, 0.3)
        self.assertAlmostEqual(self.project.assets.by_name("p").tempo, 90.0)

    def test_merge_off_still_duplicates(self):
        result = parse_document(DOC_V2, self.project, merge=False)
        self.assertEqual(len(result.assets), 2)
        self.assertEqual(result.updated, [])
        self.assertTrue(any("already exists" in w for w in result.warnings))

    def test_type_change_is_not_merged(self):
        doc = {"format": "raw.author", "sounds": [{"name": "lead", "from_preset": "blip"}]}
        result = parse_document(doc, self.project, merge=True)
        self.assertEqual(result.updated, [])
        self.assertEqual(len(result.assets), 1)
        self.assertTrue(any("already exists as a" in w for w in result.warnings))

    def test_duplicate_names_inside_one_document_are_reported(self):
        doc = {"format": "raw.author",
               "sounds": [{"name": "a", "from_preset": "blip"},
                          {"name": "a", "from_preset": "coin"}]}
        result = parse_document(doc, Project(), merge=True)
        self.assertTrue(any("defined twice" in w for w in result.warnings))

    def test_replace_command_is_undoable(self):
        stack = CommandStack(self.project)
        lead = self.project.assets.by_name("lead")
        before = lead.params.oscillator

        result = parse_document(DOC_V2, self.project, merge=True)
        replacement = next(a for a in result.updated if a.name == "lead")
        stack.push(ReplaceAsset(replacement.uid, replacement))
        self.assertEqual(self.project.assets.get(lead.uid).params.oscillator, "square")

        stack.undo()
        self.assertEqual(self.project.assets.get(lead.uid).params.oscillator, before)

    def test_render_reflects_the_updated_instrument(self):
        renderer = Renderer()
        pattern = self.project.assets.by_name("p")
        loud = renderer.render_asset(pattern, self.project).peak()
        apply(self.project, parse_document(DOC_V2, self.project, merge=True))
        quiet = renderer.render_asset(self.project.assets.by_name("p"), self.project).peak()
        self.assertLess(quiet, loud)


@unittest.skipUnless(QT, "PyQt6 not available")
class TestTransport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from raw.app.controller import Controller
        from raw.ui.timeline import TimelineDock

        self.c = Controller()
        apply(self.c.project, parse_document(DOC_V1, self.c.project, merge=True))
        self.dock = TimelineDock(self.c)

    def tearDown(self):
        self.c.stop()
        self.dock.deleteLater()

    # ------------------------------------------------------------------ bpm

    def test_bpm_edits_the_project_when_nothing_is_selected(self):
        self.c.select("")
        self.assertEqual(self.dock.bpm_target.text(), "project")
        self.dock.bpm.setValue(96.0)
        self.assertAlmostEqual(self.c.project.settings.tempo, 96.0)

    def test_bpm_edits_the_selected_pattern(self):
        pattern = self.c.project.assets.by_name("p")
        self.c.select(pattern.uid)
        self.assertEqual(self.dock.bpm_target.text(), "p")
        self.dock.bpm.setValue(68.0)
        self.assertAlmostEqual(self.c.project.assets.by_name("p").tempo, 68.0)

    def test_bpm_change_is_undoable(self):
        pattern = self.c.project.assets.by_name("p")
        self.c.select(pattern.uid)
        self.dock.bpm.setValue(68.0)
        self.c.undo()
        self.assertAlmostEqual(self.c.project.assets.by_name("p").tempo, 120.0)

    def test_bpm_control_follows_the_selection(self):
        pattern = self.c.project.assets.by_name("p")
        self.c.select(pattern.uid)
        self.assertAlmostEqual(self.dock.bpm.value(), 120.0)
        self.c.select("")
        self.assertAlmostEqual(self.dock.bpm.value(), self.c.project.settings.tempo)

    def test_changing_tempo_changes_the_rendered_length(self):
        pattern = self.c.project.assets.by_name("p")
        self.c.select(pattern.uid)
        fast = self.c.render(pattern, preview=False).duration
        self.dock.bpm.setValue(60.0)
        slow = self.c.render(self.c.project.assets.by_name("p"), preview=False).duration
        self.assertGreater(slow, fast * 1.5)

    # -------------------------------------------------------------- progress

    def test_playback_emits_progress_for_its_origin(self):
        from raw.audio.buffer import AudioBuffer

        seen = []
        self.c.playbackPosition.connect(lambda origin, pos: seen.append((origin, pos)))
        self.c.play(AudioBuffer.silence(0.5, 44100), origin="timeline")
        self.assertTrue(self.c.is_playing)
        self.assertEqual(seen[0][0], "timeline")
        self.assertAlmostEqual(seen[0][1], 0.0, places=6)

    def test_stop_ends_progress(self):
        from raw.audio.buffer import AudioBuffer

        stopped = []
        self.c.playbackStopped.connect(stopped.append)
        self.c.play(AudioBuffer.silence(5.0, 44100), origin="timeline")
        self.c.stop()
        self.assertFalse(self.c.is_playing)
        self.assertEqual(stopped, ["timeline"])

    def test_offset_is_added_to_reported_position(self):
        from raw.audio.buffer import AudioBuffer

        seen = []
        self.c.playbackPosition.connect(lambda origin, pos: seen.append(pos))
        self.c.play(AudioBuffer.silence(1.0, 44100), origin="timeline", offset=2.5)
        self.assertAlmostEqual(seen[0], 2.5, places=6)

    def test_timeline_playhead_follows_only_timeline_playback(self):
        self.dock.view.playhead = 1.0
        self.c.playbackPosition.emit("preview", 7.0)
        self.assertAlmostEqual(self.dock.view.playhead, 1.0)
        self.c.playbackPosition.emit("timeline", 7.0)
        self.assertAlmostEqual(self.dock.view.playhead, 7.0)

    def test_playhead_returns_to_where_playback_started(self):
        from raw.core.assets import SynthAsset

        self.c.project.assets.add(SynthAsset(name="s", params=engine.preset("blip")))
        self.c.select(self.c.project.assets.by_name("s").uid)
        self.dock.add_clip()

        self.dock.view.playhead = 0.0
        self.dock.play_timeline()
        self.c.playbackPosition.emit("timeline", 0.4)
        self.assertAlmostEqual(self.dock.view.playhead, 0.4, places=3)

        self.c.stop()
        self.assertAlmostEqual(self.dock.view.playhead, 0.0, places=3)

    def test_playhead_returns_to_a_non_zero_start(self):
        from raw.core.assets import SynthAsset

        self.c.project.assets.add(SynthAsset(name="s", params=engine.preset("explosion")))
        self.c.select(self.c.project.assets.by_name("s").uid)
        self.dock.add_clip()

        self.dock.view.playhead = 0.25
        self.dock.play_timeline()
        self.c.playbackPosition.emit("timeline", 0.6)
        self.c.stop()
        self.assertAlmostEqual(self.dock.view.playhead, 0.25, places=3)

    def test_preview_playback_leaves_the_playhead_alone(self):
        from raw.audio.buffer import AudioBuffer

        self.dock.view.playhead = 1.5
        self.c.play(AudioBuffer.silence(0.2, 44100), origin="preview")
        self.c.stop()
        self.assertAlmostEqual(self.dock.view.playhead, 1.5, places=3)

    def test_play_button_label_resets_on_stop(self):
        from raw.audio.buffer import AudioBuffer

        self.c.play(AudioBuffer.silence(0.2, 44100), origin="timeline")
        self.c.stop()
        self.assertIn("Play", self.dock.play_btn.text())

    def test_clock_formatting(self):
        from raw.ui.timeline import TimelineDock

        self.assertEqual(TimelineDock._clock(0.0), "0:00.0")
        self.assertEqual(TimelineDock._clock(9.25), "0:09.2")
        self.assertEqual(TimelineDock._clock(75.0), "1:15.0")

    def test_playing_an_empty_timeline_is_harmless(self):
        self.assertIsNone(self.dock.render_timeline(play=True))
        self.assertFalse(self.c.is_playing)


if __name__ == "__main__":
    unittest.main()
