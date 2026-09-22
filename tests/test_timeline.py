"""Timeline clip editing: commands, copy/paste, and drag-and-drop placement.

These drive the real widgets, without showing a window.
"""

from __future__ import annotations

import unittest

from raw.core.assets import SynthAsset
from raw.core.commands import AddClips, CommandStack, MoveClips, RemoveClips
from raw.core.project import Project
from raw.synth import engine

try:
    from PyQt6.QtWidgets import QApplication

    QT = True
except Exception:  # pragma: no cover
    QT = False


def clip(uid="a", track=0, start=0.0, duration=0.5, cid="c1", overrides=None):
    return {
        "id": cid,
        "asset": uid,
        "track": track,
        "start": start,
        "duration": duration,
        "overrides": overrides or {},
    }


class TestClipCommands(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.p.timeline = {"tracks": [{"name": "a"}, {"name": "b"}], "clips": []}
        self.stack = CommandStack(self.p)

    @property
    def clips(self):
        return self.p.timeline["clips"]

    def test_add_and_undo(self):
        self.stack.push(AddClips([clip(cid="c1"), clip(cid="c2", start=1.0)]))
        self.assertEqual(len(self.clips), 2)
        self.stack.undo()
        self.assertEqual(len(self.clips), 0)
        self.stack.redo()
        self.assertEqual({c["id"] for c in self.clips}, {"c1", "c2"})

    def test_add_does_not_alias_the_input(self):
        source = [clip(cid="c1")]
        self.stack.push(AddClips(source))
        self.clips[0]["start"] = 99.0
        self.assertEqual(source[0]["start"], 0.0)

    def test_remove_restores_position_in_order(self):
        self.stack.push(AddClips([clip(cid=f"c{i}", start=i) for i in range(4)]))
        self.stack.push(RemoveClips(["c1", "c2"]))
        self.assertEqual([c["id"] for c in self.clips], ["c0", "c3"])
        self.stack.undo()
        self.assertEqual([c["id"] for c in self.clips], ["c0", "c1", "c2", "c3"])

    def test_remove_keeps_overrides(self):
        self.stack.push(AddClips([clip(cid="c1", overrides={"pitch_offset": 5})]))
        self.stack.push(RemoveClips(["c1"]))
        self.stack.undo()
        self.assertEqual(self.clips[0]["overrides"], {"pitch_offset": 5})

    def test_move_is_undoable(self):
        self.stack.push(AddClips([clip(cid="c1", start=0.0, track=0)]))
        self.clips[0]["start"] = 2.0
        self.clips[0]["track"] = 1
        self.stack.push(MoveClips([("c1", (0.0, 0), (2.0, 1))]))
        self.assertEqual((self.clips[0]["start"], self.clips[0]["track"]), (2.0, 1))
        self.stack.undo()
        self.assertEqual((self.clips[0]["start"], self.clips[0]["track"]), (0.0, 0))
        self.stack.redo()
        self.assertEqual((self.clips[0]["start"], self.clips[0]["track"]), (2.0, 1))

    def test_move_ignores_clips_that_vanished(self):
        self.stack.push(MoveClips([("gone", (0.0, 0), (1.0, 0))]))
        self.assertEqual(self.clips, [])


@unittest.skipUnless(QT, "PyQt6 not available")
class TestTimelineView(unittest.TestCase):
    app = None

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from raw.app.controller import Controller
        from raw.ui.timeline import TimelineDock

        self.c = Controller()
        for name in ("laser", "coin", "blip"):
            self.c.project.assets.add(SynthAsset(name=f"sfx_{name}", params=engine.preset(name)))
        self.dock = TimelineDock(self.c)
        self.view = self.dock.view
        self.uids = [a.uid for a in self.c.project.assets.sorted()]

    def tearDown(self):
        self.dock.deleteLater()

    def place(self, count=2, start=0.0, track=1):
        return self.view.place_assets(self.uids[:count], start, track)

    def test_place_assets_lays_them_end_to_end(self):
        clips = self.place(count=3, start=0.0, track=1)
        self.assertEqual(len(clips), 3)
        starts = [c["start"] for c in clips]
        self.assertEqual(starts, sorted(starts))
        self.assertTrue(all(c["track"] == 1 for c in clips))

    def test_placed_clips_never_overlap(self):
        """Nearest-snapping used to round the cursor backwards, stacking short
        clips on top of each other."""
        self.view.grid = "1/4"
        clips = sorted(self.place(count=3, start=1.0, track=1), key=lambda c: c["start"])
        for earlier, later in zip(clips, clips[1:]):
            self.assertGreaterEqual(
                later["start"] + 1e-9,
                earlier["start"] + earlier["duration"],
                f"{earlier['id']} overlaps {later['id']}",
            )
        self.assertEqual(len({c["start"] for c in clips}), len(clips))

    def test_snap_up_always_advances(self):
        self.view.grid = "1/4"
        beat = self.view.beat_seconds()
        self.assertAlmostEqual(self.view.snap_up(beat * 1.01), beat * 2, places=6)
        self.assertAlmostEqual(self.view.snap_up(beat), beat, places=6)
        self.view.grid = "off"
        self.assertAlmostEqual(self.view.snap_up(1.234), 1.234, places=6)

    def test_place_is_one_undo_step(self):
        self.place(count=3)
        self.assertEqual(len(self.view.timeline["clips"]), 3)
        self.c.undo()
        self.assertEqual(len(self.view.timeline["clips"]), 0)

    def test_place_ignores_unknown_uids(self):
        self.assertEqual(self.view.place_assets(["nope"], 0.0, 0), [])

    def test_copy_then_paste_preserves_spacing_and_tracks(self):
        self.place(count=2, start=0.0, track=1)
        self.view.select_all_clips()
        self.assertEqual(self.view.copy_clips(), 2)

        original = sorted(c["start"] for c in self.view.timeline["clips"])
        gap = original[1] - original[0]

        self.view.playhead = 4.0
        pasted = self.view.paste_clips()
        self.assertEqual(len(pasted), 2)
        starts = sorted(c["start"] for c in pasted)
        self.assertAlmostEqual(starts[0], self.view.snap(4.0), places=6)
        self.assertAlmostEqual(starts[1] - starts[0], gap, places=6)
        self.assertEqual(len(self.view.timeline["clips"]), 4)

    def test_paste_returns_to_the_track_it_was_copied_from(self):
        self.place(count=2, start=0.0, track=2)
        self.view.select_all_clips()
        self.view.copy_clips()
        self.view.playhead = 6.0
        pasted = self.view.paste_clips()
        self.assertTrue(all(c["track"] == 2 for c in pasted),
                        [c["track"] for c in pasted])

    def test_paste_onto_an_explicit_track_overrides_the_source(self):
        self.place(count=1, start=0.0, track=2)
        self.view.select_all_clips()
        self.view.copy_clips()
        pasted = self.view.paste_clips(at=5.0, track=0)
        self.assertEqual(pasted[0]["track"], 0)

    def test_paste_preserves_relative_track_offsets(self):
        a = self.view.place_assets([self.uids[0]], 0.0, 1)
        b = self.view.place_assets([self.uids[1]], 0.0, 3)
        self.assertTrue(a and b)
        self.view.select_all_clips()
        self.view.copy_clips()
        self.view.playhead = 6.0
        tracks = sorted(c["track"] for c in self.view.paste_clips())
        self.assertEqual(tracks, [1, 3])

    def test_paste_gives_new_ids(self):
        self.place(count=1)
        self.view.select_all_clips()
        self.view.copy_clips()
        self.view.playhead = 3.0
        self.view.paste_clips()
        ids = [c["id"] for c in self.view.timeline["clips"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_paste_copies_overrides_without_aliasing(self):
        clips = self.place(count=1)
        clips[0]["overrides"] = {"pitch_offset": 7}
        self.view.rebuild()
        self.view.select_all_clips()
        self.view.copy_clips()
        self.view.playhead = 2.0
        pasted = self.view.paste_clips()
        self.assertEqual(pasted[0]["overrides"], {"pitch_offset": 7})
        pasted[0]["overrides"]["pitch_offset"] = 0
        self.assertEqual(self.view.timeline["clips"][0]["overrides"], {"pitch_offset": 7})

    def test_paste_with_empty_clipboard_does_nothing(self):
        self.c.clip_clipboard = []
        self.assertEqual(self.view.paste_clips(), [])

    def test_cut_removes_the_originals(self):
        self.place(count=2)
        self.view.select_all_clips()
        self.assertEqual(self.view.cut_clips(), 2)
        self.assertEqual(len(self.view.timeline["clips"]), 0)
        self.view.playhead = 0.0
        self.view.paste_clips()
        self.assertEqual(len(self.view.timeline["clips"]), 2)

    def test_duplicate_places_after_the_selection(self):
        clips = self.place(count=1, start=0.0, track=1)
        end = clips[0]["start"] + clips[0]["duration"]
        self.view.select_all_clips()
        made = self.view.duplicate_clips()
        self.assertEqual(len(made), 1)
        self.assertGreaterEqual(made[0]["start"] + 1e-9, min(end, self.view.snap(end)))
        self.assertEqual(made[0]["track"], 1)

    def test_duplicate_selects_the_new_clips(self):
        self.place(count=1)
        self.view.select_all_clips()
        made = self.view.duplicate_clips()
        self.assertEqual({c["id"] for c in self.view.selected_clips()}, {c["id"] for c in made})

    def test_delete_removes_only_the_selection(self):
        self.place(count=3)
        items = [i for i in self.view.scene().items() if hasattr(i, "clip")]
        items[0].setSelected(True)
        self.assertEqual(self.view.delete_clips(), 1)
        self.assertEqual(len(self.view.timeline["clips"]), 2)
        self.c.undo()
        self.assertEqual(len(self.view.timeline["clips"]), 3)

    def test_operations_with_no_selection_are_safe(self):
        self.place(count=1)
        self.assertEqual(self.view.copy_clips(), 0)
        self.assertEqual(self.view.duplicate_clips(), [])
        self.assertEqual(self.view.delete_clips(), 0)
        self.assertEqual(len(self.view.timeline["clips"]), 1)

    def test_track_at_maps_y_to_a_valid_track(self):
        from raw.ui.timeline import RULER_HEIGHT, TRACK_HEIGHT

        self.assertEqual(self.view.track_at(RULER_HEIGHT + 5), 0)
        self.assertEqual(self.view.track_at(RULER_HEIGHT + TRACK_HEIGHT + 5), 1)
        self.assertEqual(self.view.track_at(10_000), len(self.view.tracks) - 1)
        self.assertEqual(self.view.track_at(-50), 0)

    def test_snap_respects_the_grid(self):
        self.view.grid = "1/4"
        beat = self.view.beat_seconds()
        self.assertAlmostEqual(self.view.snap(beat * 1.1), beat, places=6)
        self.view.grid = "off"
        self.assertAlmostEqual(self.view.snap(1.234), 1.234, places=6)

    def test_mime_round_trip(self):
        from raw.ui.dnd import asset_mime_data, asset_uids_from_mime

        mime = asset_mime_data(self.uids, ["a", "b", "c"])
        self.assertEqual(asset_uids_from_mime(mime), self.uids)

    def test_non_asset_mime_yields_nothing(self):
        from PyQt6.QtCore import QMimeData

        from raw.ui.dnd import asset_uids_from_mime

        plain = QMimeData()
        plain.setText("hello")
        self.assertEqual(asset_uids_from_mime(plain), [])


if __name__ == "__main__":
    unittest.main()
