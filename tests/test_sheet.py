"""Sheet-music export to MusicXML.

The round-trip tests read the XML back into (start, duration, pitch) triples
and compare with the input — so ties, rests, chords, voices and bar-line splits
are all checked for fidelity, not just for well-formedness.
"""

from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from raw.core.assets import PatternAsset
from raw.core.authoring import parse_document
from raw.core.project import Project
from raw.export.sheet import (
    DIVISIONS,
    NoteEvent,
    build_musicxml,
    chord_to_musicxml,
    decompose,
    measure_divisions,
    musical_to_divisions,
    pattern_to_musicxml,
    write_musicxml,
)
from raw.patterns.notes import note_to_midi

try:
    from PyQt6.QtWidgets import QApplication

    QT = True
except Exception:  # pragma: no cover
    QT = False

STEP_NAMES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def read_back(xml: str) -> list[tuple[int, int, int]]:
    """Reconstruct (start, duration, midi) from MusicXML, merging ties."""
    root = ET.fromstring(xml)
    part = root.find("part")
    m_len = None
    m_start = 0
    open_ties: dict[tuple[int, int], list] = {}   # (voice, midi) -> [start, duration]
    finished: list[tuple[int, int, int]] = []

    for measure in part.findall("measure"):
        attrs = measure.find("attributes")
        if attrs is not None and m_len is None:
            beats = int(attrs.find("time/beats").text)
            beat_type = int(attrs.find("time/beat-type").text)
            m_len = beats * DIVISIONS * 4 // beat_type
        cursor = m_start
        last_note_start = cursor
        for el in measure:
            if el.tag == "backup":
                cursor -= int(el.find("duration").text)
                continue
            if el.tag == "forward":
                cursor += int(el.find("duration").text)
                continue
            if el.tag != "note":
                continue
            dur = int(el.find("duration").text)
            voice = int(el.find("voice").text)
            is_chord = el.find("chord") is not None
            start = last_note_start if is_chord else cursor
            if el.find("rest") is None:
                pitch = el.find("pitch")
                midi = (int(pitch.find("octave").text) + 1) * 12 + STEP_NAMES[pitch.find("step").text]
                alter = pitch.find("alter")
                if alter is not None:
                    midi += int(alter.text)
                ties = {t.get("type") for t in el.findall("tie")}
                key = (voice, midi)
                if "stop" in ties and key in open_ties:
                    open_ties[key][1] += dur
                    if "start" not in ties:
                        s, d = open_ties.pop(key)
                        finished.append((s, d, midi))
                elif "start" in ties:
                    open_ties[key] = [start, dur]
                else:
                    finished.append((start, dur, midi))
            if not is_chord:
                last_note_start = cursor
                cursor += dur
        m_start += m_len
    for (voice, midi), (s, d) in open_ties.items():
        finished.append((s, d, midi))
    return sorted(finished)


def voice_totals(xml: str) -> list[tuple[int, int, int]]:
    """(measure, voice, total duration) for every voice in every measure."""
    root = ET.fromstring(xml)
    out = []
    for measure in root.find("part").findall("measure"):
        totals: dict[int, int] = {}
        for note in measure.findall("note"):
            if note.find("chord") is not None:
                continue
            v = int(note.find("voice").text)
            totals[v] = totals.get(v, 0) + int(note.find("duration").text)
        for v, t in sorted(totals.items()):
            out.append((int(measure.get("number")), v, t))
    return out


def events_of(*triples):
    return [NoteEvent(s, d, m) for s, d, m in triples]


class TestDurations(unittest.TestCase):
    def test_musical_to_divisions(self):
        self.assertEqual(musical_to_divisions("1/4"), 8)
        self.assertEqual(musical_to_divisions("1/8"), 4)
        self.assertEqual(musical_to_divisions("1/16"), 2)
        self.assertEqual(musical_to_divisions("1"), 32)
        self.assertEqual(musical_to_divisions("1/4."), 12)

    def test_triplets_warn(self):
        warnings = []
        musical_to_divisions("1/8t", warnings)
        self.assertTrue(any("triplet" in w for w in warnings))

    def test_decompose_prefers_single_values(self):
        self.assertEqual(decompose(8), [(8, "quarter", 0)])
        self.assertEqual(decompose(12), [(12, "quarter", 1)])
        self.assertEqual(decompose(32), [(32, "whole", 0)])

    def test_decompose_ties_awkward_lengths(self):
        pieces = decompose(5)
        self.assertEqual(sum(p[0] for p in pieces), 5)
        self.assertGreater(len(pieces), 1)

    def test_measure_length(self):
        self.assertEqual(measure_divisions((4, 4)), 32)
        self.assertEqual(measure_divisions((3, 4)), 24)
        self.assertEqual(measure_divisions((6, 8)), 24)


class TestStructure(unittest.TestCase):
    def test_output_is_well_formed_xml_with_a_grand_staff(self):
        result = build_musicxml(events_of((0, 8, 60)), "T", 120)
        root = ET.fromstring(result.xml)
        self.assertEqual(root.tag, "score-partwise")
        attrs = root.find("part/measure/attributes")
        self.assertEqual(attrs.find("staves").text, "2")
        clefs = {c.get("number"): c.find("sign").text for c in attrs.findall("clef")}
        self.assertEqual(clefs, {"1": "G", "2": "F"})
        self.assertEqual(root.find("part-list/score-part/part-name").text, "Piano")

    def test_tempo_and_time_signature_are_written(self):
        result = build_musicxml(events_of((0, 8, 60)), "T", 96, (3, 4))
        root = ET.fromstring(result.xml)
        self.assertEqual(root.find("part/measure/direction/sound").get("tempo"), "96")
        self.assertEqual(root.find("part/measure/attributes/time/beats").text, "3")

    def test_every_voice_fills_every_measure_exactly(self):
        events = events_of((0, 8, 60), (8, 3, 64), (11, 21, 67), (0, 32, 48), (40, 8, 43))
        result = build_musicxml(events, "T", 120)
        for measure, voice, total in voice_totals(result.xml):
            self.assertEqual(total, 32, f"measure {measure} voice {voice} sums to {total}")

    def test_notes_split_across_staves_at_middle_c(self):
        result = build_musicxml(events_of((0, 8, 60), (0, 8, 59)), "T", 120)
        root = ET.fromstring(result.xml)
        staffs = {n.find("pitch/step").text: n.find("staff").text
                  for n in root.iter("note") if n.find("pitch") is not None}
        self.assertEqual(staffs["C"], "1")
        self.assertEqual(staffs["B"], "2")

    def test_silent_staff_still_has_rests(self):
        result = build_musicxml(events_of((0, 8, 72)), "T", 120)
        totals = voice_totals(result.xml)
        self.assertTrue(any(v == 5 for _, v, _ in totals), "bass staff voice should exist")

    def test_sharps_use_alter(self):
        result = build_musicxml(events_of((0, 8, 61)), "T", 120)
        pitch = ET.fromstring(result.xml).find(".//pitch")
        self.assertEqual(pitch.find("step").text, "C")
        self.assertEqual(pitch.find("alter").text, "1")
        self.assertEqual(pitch.find("octave").text, "4")

    def test_title_is_escaped(self):
        result = build_musicxml(events_of((0, 8, 60)), "Rock & Roll <3", 120)
        self.assertEqual(ET.fromstring(result.xml).find("work/work-title").text, "Rock & Roll <3")


class TestRoundTrip(unittest.TestCase):
    def check(self, events, time_signature=(4, 4)):
        result = build_musicxml(events, "T", 120, time_signature)
        got = read_back(result.xml)
        want = sorted((e.start, e.duration, e.midi) for e in events)
        self.assertEqual(got, want)
        for measure, voice, total in voice_totals(result.xml):
            self.assertEqual(total, measure_divisions(time_signature),
                             f"measure {measure} voice {voice}")
        return result

    def test_simple_melody_with_rests(self):
        self.check(events_of((0, 8, 60), (16, 8, 64), (24, 4, 67)))

    def test_chord(self):
        self.check(events_of((0, 16, 60), (0, 16, 64), (0, 16, 67)))

    def test_note_tied_across_a_bar_line(self):
        self.check(events_of((24, 16, 62)))

    def test_awkward_length_is_tied_within_the_bar(self):
        self.check(events_of((0, 5, 60), (5, 7, 62), (12, 20, 64)))

    def test_overlapping_notes_get_separate_voices(self):
        self.check(events_of((0, 32, 60), (8, 8, 64), (16, 8, 67)))

    def test_long_bass_note_under_moving_treble(self):
        self.check(events_of((0, 64, 36), (0, 8, 72), (8, 8, 74), (16, 8, 76), (24, 8, 77),
                             (32, 8, 79), (40, 8, 77), (48, 8, 76), (56, 8, 74)))

    def test_three_four_time(self):
        self.check(events_of((0, 24, 60), (24, 12, 64), (36, 12, 67)), (3, 4))

    def test_chords_with_different_lengths_at_the_same_step(self):
        self.check(events_of((0, 32, 48), (0, 8, 64), (0, 8, 67)))

    def test_five_simultaneous_notes_on_one_staff_drop_one_with_a_warning(self):
        events = events_of(*[(0, 8 + i, 60 + i) for i in range(5)])  # all different lengths
        result = build_musicxml(events, "T", 120)
        self.assertTrue(any("dropped" in w for w in result.warnings))
        self.assertEqual(len(read_back(result.xml)), 4)


class TestPatternExport(unittest.TestCase):
    def setUp(self):
        self.project = Project()
        self.project.settings.tempo = 120.0

    def pattern_from(self, doc):
        result = parse_document(doc, self.project)
        for asset in result.assets:
            self.project.assets.add(asset)
        return next(a for a in result.assets if isinstance(a, PatternAsset))

    def test_notes_string_pattern_exports(self):
        pattern = self.pattern_from({
            "format": "raw.author",
            "instruments": [{"name": "lead", "pitch": "C4", "root_note": "C4"}],
            "patterns": [{"name": "p", "tempo": 100, "steps": 8, "step": "1/8",
                          "tracks": [{"instrument": "lead", "notes": "C4 . E4 - G4 . . C5"}]}],
        })
        result = pattern_to_musicxml(pattern, self.project)
        got = read_back(result.xml)
        # 1/8 step = 4 divisions; E4 is held for two steps
        self.assertEqual(got, sorted([(0, 4, 60), (8, 8, 64), (16, 4, 67), (28, 4, 72)]))
        self.assertEqual(result.measures, 1)
        self.assertEqual(ET.fromstring(result.xml).find("part/measure/direction/sound").get("tempo"), "100")

    def test_drums_are_left_off_with_a_note(self):
        pattern = self.pattern_from({
            "format": "raw.author",
            "instruments": [{"name": "lead", "pitch": "C4", "root_note": "C4"},
                            {"name": "kick", "from_preset": "hit"}],
            "patterns": [{"name": "p", "steps": 4, "step": "1/4", "tracks": [
                {"instrument": "lead", "notes": "C4 . E4 ."},
                {"instrument": "kick", "notes": "x . x ."}]}],
        })
        result = pattern_to_musicxml(pattern, self.project)
        self.assertEqual(len(read_back(result.xml)), 2)
        self.assertTrue(any("unpitched" in w for w in result.warnings))

    def test_swing_is_reported_not_notated(self):
        pattern = self.pattern_from({
            "format": "raw.author",
            "instruments": [{"name": "lead", "pitch": "C4", "root_note": "C4"}],
            "patterns": [{"name": "p", "steps": 4, "step": "1/8", "swing": 0.4,
                          "tracks": [{"instrument": "lead", "notes": "C4 D4 E4 F4"}]}],
        })
        result = pattern_to_musicxml(pattern, self.project)
        self.assertTrue(any("swing" in w for w in result.warnings))
        self.assertEqual(len(read_back(result.xml)), 4)

    def test_the_lofi_example_exports_and_balances(self):
        doc = (Path(__file__).resolve().parents[1] / "docs/examples/lofi.author.json").read_text("utf-8")
        pattern = self.pattern_from(doc)
        result = pattern_to_musicxml(pattern, self.project)
        self.assertEqual(result.measures, 4)
        self.assertGreater(result.note_count, 30)
        m_len = measure_divisions(self.project.settings.time_signature)
        for measure, voice, total in voice_totals(result.xml):
            self.assertEqual(total, m_len, f"measure {measure} voice {voice}")
        back = read_back(result.xml)
        self.assertEqual(len(back), result.note_count)

    def test_written_file_gets_the_musicxml_extension(self):
        pattern = self.pattern_from({
            "format": "raw.author",
            "instruments": [{"name": "l", "pitch": "C4", "root_note": "C4"}],
            "patterns": [{"name": "p", "steps": 2, "step": "1/4",
                          "tracks": [{"instrument": "l", "notes": "C4 ."}]}],
        })
        with tempfile.TemporaryDirectory() as tmp:
            path = write_musicxml(pattern_to_musicxml(pattern, self.project), Path(tmp) / "score")
            self.assertEqual(path.suffix, ".musicxml")
            ET.parse(path)  # parses as XML


class TestChordExport(unittest.TestCase):
    def test_chord_becomes_one_whole_note_bar(self):
        project = Project()
        notes = [note_to_midi(n) for n in ("C4", "E4", "G4", "B4")]
        result = chord_to_musicxml(notes, "C Major 7", project)
        self.assertEqual(result.measures, 1)
        self.assertEqual(read_back(result.xml), sorted((0, 32, int(m)) for m in notes))
        types = {n.find("type").text for n in ET.fromstring(result.xml).iter("note")
                 if n.find("pitch") is not None}
        self.assertEqual(types, {"whole"})

    def test_wide_chord_spans_both_staves(self):
        project = Project()
        result = chord_to_musicxml([36, 48, 64, 67], "Wide", project)
        staffs = {n.find("staff").text for n in ET.fromstring(result.xml).iter("note")
                  if n.find("pitch") is not None}
        self.assertEqual(staffs, {"1", "2"})


@unittest.skipUnless(QT, "PyQt6 not available")
class TestUiHooks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_chord_lab_has_an_export_button_and_console_has_sheet(self):
        from raw.app.controller import Controller
        from raw.ui.chord_lab import ChordLab
        from raw.ui.console_dock import ConsoleDock

        c = Controller()
        lab = ChordLab(c)
        self.assertTrue(hasattr(lab, "sheet_btn"))
        console = ConsoleDock(c)
        self.assertIn("sheet", console._namespace)
        lab.deleteLater()
        console.deleteLater()

    def test_console_sheet_writes_a_file(self):
        from raw.app.controller import Controller
        from raw.ui.console_dock import ConsoleDock

        c = Controller()
        c.import_authored({
            "format": "raw.author",
            "instruments": [{"name": "l", "pitch": "C4", "root_note": "C4"}],
            "patterns": [{"name": "tune", "steps": 4, "step": "1/4",
                          "tracks": [{"instrument": "l", "notes": "C4 E4 G4 C5"}]}],
        })
        console = ConsoleDock(c)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "tune.musicxml"
            console._namespace["sheet"]("tune", str(out))
            self.assertTrue(out.exists())
            self.assertEqual(len(read_back(out.read_text("utf-8"))), 4)
        console.deleteLater()


if __name__ == "__main__":
    unittest.main()
