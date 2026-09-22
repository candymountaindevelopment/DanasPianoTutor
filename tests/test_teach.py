"""Piano Tutor: lesson parsing, fingering, rendering, engraving, sheet export."""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest

import numpy as np
from pathlib import Path

from raw.export.sheet import DIVISIONS
from raw.teach.authoring import (
    document_from_lessons,
    expand_hand,
    lesson_to_author,
    load_lesson_file,
    parse_lesson_document,
)
from raw.teach.engrave import engrave
from raw.teach.player import PlayOptions, metronome_events, render_lesson
from raw.teach.score import LEFT, RIGHT, parse_key, spell, spelled_name, white_key_offset
from raw.teach.sheet import lesson_to_musicxml

EXAMPLES = Path(__file__).resolve().parent.parent / "docs" / "examples" / "lessons"
Q = DIVISIONS  # one quarter note

MARY = {
    "format": "raw.author", "version": 1,
    "lessons": [{
        "name": "mary", "title": "Mary", "tempo": 90, "time": "4/4", "key": "C",
        "position": {"right": "C4", "left": "C3"},
        "right": {"notes": "E4(3) D4(2) C4(1) D4(2) | E4 E4 E4:2 | D4 D4 D4:2 | E4 G4(5) G4:2"},
        "left": {"notes": "[C3 E3 G3]:4(5,3,1) | [C3 E3 G3]:4 | [B2 D3 G3]:4(5,2,1) | C3:4"},
    }],
}


def lesson(doc=MARY):
    result = parse_lesson_document(doc)
    assert result.ok, result.errors
    return result.lessons[0], result


class TestTheory(unittest.TestCase):
    def test_keys(self):
        self.assertEqual(parse_key("G"), ("G", 1))
        self.assertEqual(parse_key("F"), ("F", -1))
        self.assertEqual(parse_key("Bb"), ("Bb", -2))
        self.assertEqual(parse_key("Am"), ("Am", 0))
        self.assertEqual(parse_key("E minor"), ("Em", 1))
        self.assertEqual(parse_key("d major"), ("D", 2))
        with self.assertRaises(ValueError):
            parse_key("H")

    def test_spelling_follows_key(self):
        self.assertEqual(spelled_name(66, 1), "F#4")     # in G major
        self.assertEqual(spelled_name(70, -1), "Bb4")    # in F major
        self.assertEqual(spelled_name(70, 0), "A#4")     # C major defaults to sharps
        self.assertEqual(spelled_name(70, 0, -1), "Bb4")  # ...unless the author wrote a flat
        self.assertEqual(spelled_name(61, -2), "Db4")    # flat keys spell chromatics flat
        self.assertEqual(spelled_name(77, 1), "F5")      # a natural beats E# in G major
        self.assertEqual(spell(60, 0), ("C", 0, 4))

    def test_white_key_stepping(self):
        self.assertEqual(white_key_offset(60, 4), 67)    # C4 + 4 white keys = G4
        self.assertEqual(white_key_offset(67, -4), 60)
        self.assertEqual(white_key_offset(61, 1), 62)    # from a black key: next white


class TestParsing(unittest.TestCase):
    def test_notes_lengths_and_fingers(self):
        warnings = []
        notes = expand_hand("C4(1) D4:2 [E4 G4]:1/8(3,5) . . F4:1.5", RIGHT, "1/4", "t", warnings)
        self.assertEqual(warnings, [])
        self.assertEqual([(n.midi, n.start, n.duration, n.finger) for n in notes], [
            (60, 0, Q, 1), (62, Q, 2 * Q, None), (64, 3 * Q, Q // 2, 3), (67, 3 * Q, Q // 2, 5),
            (65, 3 * Q + Q // 2 + 2 * Q, 12, None),
        ])

    def test_hold_extends_previous_and_rest_breaks_it(self):
        notes = expand_hand("C4 - - . -", RIGHT, "1/4", "t", w := [])
        self.assertEqual(notes[0].duration, 3 * Q)
        self.assertTrue(any("nothing to extend" in x for x in w))

    def test_chord_fingers_follow_written_order(self):
        notes = expand_hand("[C3 E3 G3](5,3,1)", LEFT, "1/4", "t", [])
        self.assertEqual({n.midi: n.finger for n in notes}, {48: 5, 52: 3, 55: 1})

    def test_parallel_fingers_line(self):
        les, result = lesson({**MARY, "lessons": [{
            "name": "x", "right": {"notes": "C4 D4 [E4 G4]", "fingers": "1 2 3,5"}}]})
        self.assertEqual([n.finger for n in les.notes], [1, 2, 3, 5])
        self.assertEqual([w for w in result.warnings if "fingers" in w], [])

    def test_bar_length_check(self):
        _, result = lesson({**MARY, "lessons": [{"name": "x", "time": "3/4", "right": "C4 D4 E4 F4 | G4:3"}]})
        self.assertTrue(any("bar 1 has 4 beat" in w for w in result.warnings))

    def test_inferred_fingering_from_position(self):
        les, _ = lesson()
        right = les.hand_notes(RIGHT)
        # Bar 2's unfingered E4s sit under finger 3 of the C position.
        self.assertEqual([n.shown_finger for n in right[4:7]], [3, 3, 3])
        self.assertEqual(les.position_label(RIGHT), "C position — thumb (1) on C4; keys C4 D4 E4 F4 G4")
        self.assertEqual(les.position_label(LEFT), "C position — little finger (5) on C3; keys C3 D3 E3 F3 G3")
        self.assertEqual(les.finger_for(LEFT, 48), 5)
        self.assertEqual(les.finger_for(RIGHT, 67), 5)

    def test_position_moves_with_written_fingers(self):
        les, result = lesson({**MARY, "lessons": [{"name": "x", "right": "G4(1) A4 B4 C5 D5(5)"}]})
        self.assertEqual([n.shown_finger for n in les.notes], [1, 2, 3, 4, 5])
        self.assertFalse(any("outside the hand position" in w for w in result.warnings))

    def test_problems_reported_with_paths(self):
        _, result = lesson({**MARY, "lessons": [{"name": "x", "right": "C4(9) x H4 C4(1) E4(1)"}]})
        joined = "\n".join(result.warnings)
        self.assertIn("finger 9 is outside 1-5", joined)
        self.assertIn("drum trigger", joined)
        self.assertIn("'H4'", joined)

    def test_document_errors(self):
        self.assertFalse(parse_lesson_document("{not json").ok)
        self.assertFalse(parse_lesson_document({"format": "raw.author", "version": 1}).ok)
        self.assertIn("no lessons", parse_lesson_document({"lessons": []}).errors[0])

    def test_document_instrument_as_voice(self):
        doc = {**MARY, "instruments": [{"name": "harp", "oscillator": "saw", "pitch": "C4", "root_note": "C4"}],
               "lessons": [{"name": "x", "voice": "harp", "right": "C4 D4"}]}
        les, result = lesson(doc)
        self.assertEqual(les.voice, "harp")
        self.assertIsNotNone(les.voice_params)
        _, result = lesson({**MARY, "lessons": [{"name": "x", "voice": "nope", "right": "C4"}]})
        self.assertTrue(any("not a built-in voice" in w for w in result.warnings))

    def test_timing_helpers(self):
        les, _ = lesson()
        self.assertEqual(les.measures, 4)
        self.assertEqual(les.length, 4 * 4 * Q)
        self.assertAlmostEqual(les.seconds_per_division(90), 60 / 90 / Q)
        self.assertEqual(les.bar_beat(0), (1, 1))
        self.assertEqual(les.bar_beat(5 * Q), (2, 2))
        self.assertEqual(les.measure_range(2, 3), (4 * Q, 12 * Q))
        self.assertEqual(les.measure_range(0, 99), (0, 16 * Q))

    def test_round_trip(self):
        les, _ = lesson()
        doc = document_from_lessons([les])
        again, result = lesson(doc)
        self.assertEqual([(n.start, n.duration, n.midi, n.hand, n.finger) for n in again.notes],
                         [(n.start, n.duration, n.midi, n.hand, n.finger) for n in les.notes])
        self.assertEqual(lesson_to_author(les)["left"]["notes"].split(" | ")[0], "[C3 E3 G3]:4(5,3,1)")


class TestExamples(unittest.TestCase):
    def test_every_example_parses_cleanly(self):
        files = sorted(EXAMPLES.glob("*.json"))
        self.assertTrue(files)
        for path in files:
            result = load_lesson_file(path)
            self.assertTrue(result.ok, (path.name, result.errors))
            self.assertEqual(result.warnings, [], (path.name, result.warnings))
            for les in result.lessons:
                self.assertTrue(les.notes, les.name)
                for n in les.notes:
                    self.assertIsNotNone(n.shown_finger, (path.name, les.name, n))

    def test_examples_engrave_and_export(self):
        for path in sorted(EXAMPLES.glob("*.json")):
            for les in load_lesson_file(path).lessons:
                layout = engrave(les, 110)
                self.assertEqual(layout.warnings, [], (path.name, layout.warnings))
                xml = lesson_to_musicxml(les)
                self.assertEqual(xml.measures, les.measures)


class TestSpecificationExamples(unittest.TestCase):
    """Every complete example in LESSON_FORMAT.md must parse with no warnings,
    so the file handed to a chatbot cannot drift from the parser."""

    def documents(self):
        text = (EXAMPLES.parent.parent / "LESSON_FORMAT.md").read_text(encoding="utf-8")
        for block in re.findall(r"```json\n(.*?)```", text, re.S):
            if '"format": "raw.author"' in block and "..." not in block:
                yield block

    def test_examples_present_and_clean(self):
        blocks = list(self.documents())
        self.assertGreaterEqual(len(blocks), 2)
        for i, block in enumerate(blocks):
            with self.subTest(example=i):
                result = parse_lesson_document(block)
                self.assertTrue(result.ok, result.errors)
                self.assertEqual(result.warnings, [])

    def test_cheat_sheet_tokens(self):
        for token in ("C4", "C4:2", "C4:3", "C4:1/2.", "C4:4", "C4:1/8", "C4:1.5", ".", ".:2", "G4:6"):
            warnings = []
            expand_hand(token, RIGHT, "1/4", "t", warnings)
            self.assertEqual(warnings, [], token)


class TestPlayer(unittest.TestCase):
    def test_render_length_follows_tempo(self):
        les, _ = lesson()
        slow = render_lesson(les, PlayOptions(tempo=60, count_in_bars=0, tail=0.0))
        fast = render_lesson(les, PlayOptions(tempo=120, count_in_bars=0, tail=0.0))
        self.assertAlmostEqual(slow.section.duration, les.duration_seconds(60), places=3)
        self.assertAlmostEqual(fast.section.duration, slow.section.duration / 2, places=3)
        self.assertEqual(slow.warnings, [])

    def test_count_in_and_loop_range_mapping(self):
        les, _ = lesson()
        r = render_lesson(les, PlayOptions(tempo=90, count_in_bars=1, loop_bars=(2, 3), tail=0.0))
        self.assertAlmostEqual(r.count_in_seconds, 4 * 60 / 90, places=6)
        self.assertEqual((r.start_division, r.end_division), (4 * Q, 12 * Q))
        self.assertAlmostEqual(r.buffer.duration, r.count_in_seconds + r.section.duration, places=3)
        self.assertAlmostEqual(r.division_at(r.count_in_seconds), 4 * Q)
        self.assertAlmostEqual(r.seconds_at(8 * Q), r.count_in_seconds + 4 * Q * les.seconds_per_division(90))

    def test_hands_selection_changes_audio(self):
        les, _ = lesson()
        both = render_lesson(les, PlayOptions(metronome=False, count_in_bars=0))
        right = render_lesson(les, PlayOptions(metronome=False, count_in_bars=0, hands="right"))
        self.assertLess(right.section.rms(), both.section.rms())

    def test_metronome_grid(self):
        les, _ = lesson()
        events = metronome_events(les, 0, les.measure_divisions)
        self.assertEqual(events, [(0, True), (Q, False), (2 * Q, False), (3 * Q, False)])
        with_click = render_lesson(les, PlayOptions(metronome=True, count_in_bars=0, hands="right"))
        without = render_lesson(les, PlayOptions(metronome=False, count_in_bars=0, hands="right"))
        self.assertGreater(with_click.section.rms(), without.section.rms())


class TestEngrave(unittest.TestCase):
    def test_layout_geometry(self):
        les, _ = lesson()
        layout = engrave(les, 120)
        self.assertEqual(len(layout.pages), 1)
        system = layout.systems[0]
        self.assertEqual([m.number for m in system.measures][:1], [1])
        heads = [h for h in system.noteheads if h.hand == RIGHT]
        self.assertEqual(len(heads), 13)
        # E4 sits on the bottom line of the treble staff, C4 one ledger line below it.
        e4 = next(h for h in heads if h.midi == 64)
        c4 = next(h for h in heads if h.midi == 60)
        self.assertAlmostEqual(e4.y, system.treble_top + 4.0)
        self.assertAlmostEqual(c4.y, system.treble_top + 5.0)
        self.assertEqual(len(c4.ledger), 1)
        # Whole-note chords in the left hand have no stems; fingers stack below the bass staff.
        self.assertEqual(len(system.stems), 13)
        left_fingers = [f for f in system.fingers if f.hand == LEFT]
        self.assertTrue(all(f.y > system.bass_top + 4 for f in left_fingers))
        # The cursor moves monotonically through a measure.
        m = system.measures[0]
        xs = [m.x_at(d) for d in range(m.start, m.end, 2)]
        self.assertEqual(xs, sorted(xs))

    def test_ties_and_accidentals(self):
        les, _ = lesson({**MARY, "lessons": [{"name": "x", "key": "G", "right": "C5:6 F#5 F5:1/8 Bb4:1/8"}]})
        layout = engrave(les, 120)
        system = layout.systems[0]
        self.assertEqual(len(system.ties), 1)
        accidentals = [h.accidental for h in system.noteheads if h.accidental]
        self.assertEqual(accidentals, ["♮", "♭"])   # F# is in the key; F natural and Bb are not

    def test_pagination(self):
        les, _ = lesson()
        layout = engrave(les, 40, page_height=60)
        self.assertGreater(len(layout.pages), 1)
        self.assertEqual(sum(len(p.systems) for p in layout.pages), len(layout.systems))


class TestSheet(unittest.TestCase):
    def test_musicxml_has_fingering_staves_and_key(self):
        les, _ = lesson({**MARY, "lessons": [{**MARY["lessons"][0], "key": "G", "composer": "Trad."}]})
        result = lesson_to_musicxml(les)
        xml = result.xml
        self.assertIn("<fifths>1</fifths>", xml)
        self.assertIn('<creator type="composer">Trad.</creator>', xml)
        self.assertIn('<fingering placement="above">3</fingering>', xml)
        self.assertIn('<fingering placement="below">5</fingering>', xml)
        # Right hand always on staff 1, left on staff 2, regardless of pitch.
        notes = re.findall(r"<note>(.*?)</note>", xml)
        g4 = [n for n in notes if "<step>G</step><octave>4</octave>" in n]
        self.assertTrue(g4 and all("<staff>1</staff>" in n for n in g4))
        g3 = [n for n in notes if "<step>G</step><octave>3</octave>" in n]
        self.assertTrue(g3 and all("<staff>2</staff>" in n for n in g3))
        self.assertEqual(result.measures, 4)
        self.assertEqual(result.warnings, [])

    def test_inferred_fingers_optional(self):
        les, _ = lesson()
        self.assertGreater(lesson_to_musicxml(les, include_inferred=True).xml.count("<fingering"),
                           lesson_to_musicxml(les).xml.count("<fingering"))


try:
    from PyQt6.QtWidgets import QApplication

    QT = True
except Exception:  # pragma: no cover
    QT = False


@unittest.skipUnless(QT, "PyQt6 not available")
class TestWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def test_window_loads_plays_and_exports(self):
        from raw.ui.teach.export import write_pdf
        from raw.ui.teach.window import TeachWindow

        w = TeachWindow()
        w.load_path(EXAMPLES / "03_mary_had_a_little_lamb.json")
        self.assertEqual(w.lesson_list.count(), 2)
        self.assertEqual(w.lesson.name, "mary_had_a_little_lamb")
        w.lesson_list.setCurrentRow(1)
        self.assertEqual(w.lesson.name, "mary_with_chords")

        # Tempo slider and spin stay linked.
        w.tempo_slider.setValue(50)
        self.assertEqual(w.tempo_spin.value(), 46)
        self.assertEqual(w.transport.options.tempo, 46.0)
        w.tempo_spin.setValue(92)
        self.assertEqual(w.tempo_slider.value(), 100)

        # Practice range and hands.
        w.from_spin.setValue(3)
        w.to_spin.setValue(4)
        self.assertEqual(w.transport.options.loop_bars, (3, 4))
        w.hands_combo.setCurrentIndex(2)
        self.assertEqual(w.transport.options.hands, "left")
        self.assertEqual(w.staff.canvas.dim_hands, (RIGHT,))

        # Playback runs on the clock even without a device.
        w.transport.play()
        self.assertTrue(w.transport.playing)
        w.transport._tick()
        w.transport.stop()
        self.assertFalse(w.transport.playing)
        self.assertEqual(w.transport.position, 8 * Q)   # back to the start of bar 3

        # Script round trip: edit, apply, lesson list follows.
        doc = json.loads(w.script.text())
        doc["lessons"] = doc["lessons"][:1]
        doc["lessons"][0]["title"] = "Edited"
        w.script.set_text(json.dumps(doc), clean=False)
        self.assertTrue(w.script.dirty)
        result = w.script.apply()
        self.assertTrue(result.ok)
        self.assertEqual(w.lesson_list.count(), 1)
        self.assertEqual(w.lesson.title, "Edited")

        # Splash screen: renders the photo, closes on a click.
        from raw.ui.teach.splash import SPLASH_IMAGE, SplashScreen

        self.assertTrue(SPLASH_IMAGE.exists())
        splash = SplashScreen(timeout_ms=0)
        self.assertIsNotNone(splash._pixmap)
        splash.show_centered_on(w)
        image = splash.grab().toImage()
        self.assertEqual(image.pixelColor(10, image.height() - 10).name(), "#000000")
        self.assertTrue(splash.isVisible())
        splash.mousePressEvent(None)
        self.assertFalse(splash.isVisible())

        with tempfile.TemporaryDirectory() as tmp:
            path, pages = write_pdf(w.lesson, Path(tmp) / "sheet.pdf")
            self.assertTrue(path.exists() and path.stat().st_size > 1000)
            self.assertEqual(pages, 1)
        w.close()


if __name__ == "__main__":
    unittest.main()


class TestWebApi(unittest.TestCase):
    """The JSON façade the browser build calls from Pyodide."""

    def setUp(self):
        from raw.teach import web_api

        self.api = web_api
        self.doc = (EXAMPLES / "03_mary_had_a_little_lamb.json").read_text(encoding="utf-8")

    def test_parse_render_engrave_musicxml(self):
        about = json.loads(self.api.about())
        self.assertIn("version", about)
        parsed = json.loads(self.api.parse(self.doc))
        self.assertTrue(parsed["ok"])
        self.assertEqual(len(parsed["lessons"]), 2)
        les = parsed["lessons"][1]
        self.assertEqual(les["positions"]["L"]["keys"], [48, 50, 52, 53, 55])
        self.assertEqual(les["positions"]["L"]["fingers"], [5, 4, 3, 2, 1])
        self.assertTrue(all({"start", "duration", "midi", "hand", "shown"} <= set(n) for n in les["notes"]))
        meta = json.loads(self.api.render(1, json.dumps({"tempo": 92, "loop_bars": [2, 3], "count_in_bars": 1})))
        samples = self.api.last_samples()
        self.assertEqual(len(samples), meta["frames"] * 4)
        self.assertEqual((meta["start_division"], meta["end_division"]), (4 * Q, 12 * Q))
        layout = json.loads(self.api.engrave_json(1, 110))
        self.assertGreaterEqual(len(layout["pages"][0]["systems"]), 1)
        self.assertIn("noteheads", layout["pages"][0]["systems"][0])
        self.assertIn("<fingering", self.api.musicxml(1, False))
        self.assertGreater(len(self.api.note_preview(1, 60)), 1000)
        self.assertIn('"name": "mary_with_chords"', self.api.to_author(1))

    def test_parse_errors_are_json(self):
        result = json.loads(self.api.parse("{not json"))
        self.assertFalse(result["ok"])
        self.assertTrue(result["errors"])

    def test_core_runs_without_qt(self):
        """Everything the browser packs must import with numpy alone."""
        import subprocess
        import sys

        code = (
            "import sys\n"
            "for m in ('PyQt6', 'sounddevice', 'soundfile', 'scipy'): sys.modules[m] = None\n"
            "from raw.teach import web_api\n"
            "import json; print(json.loads(web_api.about())['name'])\n"
        )
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             cwd=str(EXAMPLES.parents[2]))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("Piano Tutor", out.stdout)

    def test_build_web_packs_only_the_core(self):
        import zipfile
        import tools.build_web as build

        with tempfile.TemporaryDirectory() as tmp:
            build.WEB = Path(tmp)
            out = build.build_core_zip()
            names = zipfile.ZipFile(out).namelist()
        self.assertIn("raw/teach/web_api.py", names)
        self.assertIn("raw/synth/engine.py", names)
        self.assertFalse([n for n in names if n.startswith("raw/ui/") or n.startswith("raw/app/")])


class TestHandSpan(unittest.TestCase):
    """`span`: how many white keys a hand comfortably covers."""

    def test_offsets(self):
        from raw.teach.score import finger_offsets

        self.assertEqual(finger_offsets(5), [0, 1, 2, 3, 4])
        self.assertEqual(finger_offsets(6), [0, 1, 3, 4, 5])
        self.assertEqual(finger_offsets(8), [0, 2, 4, 5, 7])
        self.assertEqual(finger_offsets(99), finger_offsets(8))

    def test_span_spreads_the_resting_keys_and_round_trips(self):
        from raw.teach.authoring import lesson_to_author, parse_lesson_document

        doc = json.dumps({"format": "raw.author", "version": 1, "lessons": [{
            "name": "wide", "title": "Wide", "span": {"right": 8, "left": 5},
            "position": {"right": "C4", "left": "C3"},
            "right": "C4(1) E4 G4 A4 C5(5)", "left": "C3(5) G3(1)"}]})
        result = parse_lesson_document(json.loads(doc))
        self.assertTrue(result.ok, result.errors)
        les = result.lessons[0]
        self.assertEqual(les.span, {"R": 8, "L": 5})
        self.assertEqual(les.position_keys("R"), [60, 64, 67, 69, 72])   # C E G A C
        self.assertEqual([n.shown_finger for n in les.hand_notes("R")], [1, 2, 3, 4, 5])
        self.assertEqual(les.position_keys("L"), [48, 50, 52, 53, 55])
        again = lesson_to_author(les)
        self.assertEqual(again["span"], {"right": 8, "left": 5})
        self.assertNotIn("warning", "".join(result.warnings).lower())

    def test_span_number_and_bad_values(self):
        from raw.teach.authoring import parse_lesson_document

        result = parse_lesson_document(({"format": "raw.author", "version": 1, "lessons": [
            {"name": "a", "title": "A", "span": 6, "right": "C4(1) D4"},
            {"name": "b", "title": "B", "span": {"right": 12, "left": "wide"}, "right": "C4(1)"}]}))
        self.assertEqual(result.lessons[0].span, {"R": 6, "L": 6})
        self.assertEqual(result.lessons[1].span, {"R": 8, "L": 5})
        self.assertTrue(any("span.right" in w and "clamped" in w for w in result.warnings))
        self.assertTrue(any("span.left" in w for w in result.warnings))
        from raw.teach import web_api
        parsed = json.loads(web_api.parse(json.dumps({"format": "raw.author", "version": 1, "lessons": [
            {"name": "a", "title": "A", "span": 6, "right": "C4(1) D4"}]})))
        self.assertEqual(parsed["lessons"][0]["span"], {"R": 6, "L": 6})


class TestPracticeMetronome(unittest.TestCase):
    """The click heard while the microphone is listening must not read as a note.

    `yin_clarity` is the criterion web/listen/pitch.js uses: the cumulative
    mean normalised difference function, and the clarity (1 - d) at its best
    lag within the detector's 40-2000 Hz search range. A reading counts as a
    note when clarity >= 0.6.
    """

    @staticmethod
    def yin_clarity(samples, sample_rate, win=2048, min_hz=40.0, max_hz=2000.0):
        x = np.asarray(samples, dtype=np.float64)
        tau_min = max(2, int(sample_rate / max_hz))
        tau_max = min(int(sample_rate / min_hz), len(x) - win - 1)
        if tau_max <= tau_min:
            return 0.0, None
        d = np.empty(tau_max + 1)
        d[0] = 0.0
        for tau in range(1, tau_max + 1):
            diff = x[:win] - x[tau:tau + win]
            d[tau] = float(diff @ diff)
        cumulative = np.cumsum(d[1:])
        cmnd = np.ones(tau_max + 1)
        nonzero = cumulative > 0
        taus = np.arange(1, tau_max + 1)
        cmnd[1:][nonzero] = d[1:][nonzero] * taus[nonzero] / cumulative[nonzero]
        window = cmnd[tau_min:tau_max + 1]
        best = int(np.argmin(window)) + tau_min
        return float(max(0.0, 1.0 - cmnd[best])), sample_rate / best

    def click_frames(self, unpitched):
        """Analysis frames over a rendered click, as the browser would take them."""
        from raw.teach import voice as voices
        from raw.teach.player import NoteCache

        sr = 44100
        cache = NoteCache()
        buf = cache.render("t", voices.click(False, unpitched), {"volume_db": -12.0}, sr)
        samples = np.asarray(buf.samples, dtype=np.float64).reshape(-1)
        # The analyser holds 4096 samples; pad so the click sits inside a window.
        padded = np.concatenate([np.zeros(4096), samples, np.zeros(8192)])
        frames = []
        for start in range(0, len(padded) - 4096, 1024):
            frames.append(padded[start:start + 4096])
        return frames

    def test_ordinary_click_is_pitched_and_practice_click_is_not(self):
        pitched = [self.yin_clarity(f, 44100) for f in self.click_frames(False)]
        unpitched = [self.yin_clarity(f, 44100) for f in self.click_frames(True)]
        # The sine click is exactly what a detector reports as a note...
        self.assertTrue(any(c >= 0.6 for c, _ in pitched),
                        f"expected the sine click to read as a pitch; best {max(c for c, _ in pitched):.2f}")
        # ...and the practice click is never clear enough to be one.
        worst = max(c for c, _ in unpitched)
        self.assertLess(worst, 0.6, f"practice click reads as a pitch (clarity {worst:.2f})")

    def test_practice_click_is_audible_and_high(self):
        from raw.teach import voice as voices
        from raw.teach.player import NoteCache

        sr = 44100
        cache = NoteCache()
        for accent in (False, True):
            buf = cache.render(f"c{accent}", voices.click(accent, True), {"volume_db": -12.0}, sr)
            x = np.asarray(buf.samples, dtype=np.float64).reshape(-1)
            self.assertGreater(float(np.abs(x).max()), 0.05, "practice click is inaudible")
            spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
            freqs = np.fft.rfftfreq(len(x), 1 / sr)
            centroid = float((spectrum * freqs).sum() / max(1e-9, spectrum.sum()))
            self.assertGreater(centroid, 2000.0, f"practice click energy sits at {centroid:.0f} Hz")
            below = spectrum[freqs < 2000].sum() / max(1e-9, spectrum.sum())
            self.assertLess(below, 0.25, "too much of the practice click is inside the detector range")

    def test_render_uses_the_practice_click_when_asked(self):
        from raw.teach.authoring import load_lesson_file
        from raw.teach.player import NoteCache, PlayOptions, render_lesson

        lesson = load_lesson_file(EXAMPLES / "01_five_finger_warmups.json").lessons[0]
        quiet = PlayOptions(metronome=True, voice_db=-100.0, count_in_bars=0, metronome_unpitched=True)
        loud = PlayOptions(metronome=True, voice_db=-100.0, count_in_bars=0, metronome_unpitched=False)
        a = render_lesson(lesson, quiet, NoteCache())
        b = render_lesson(lesson, loud, NoteCache())
        sa = np.asarray(a.buffer.samples).reshape(-1)
        sb = np.asarray(b.buffer.samples).reshape(-1)
        self.assertEqual(len(sa), len(sb))
        self.assertFalse(np.allclose(sa, sb), "the practice option did not change the click")
