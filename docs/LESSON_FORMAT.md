# Piano Tutor Lesson Format — writing beginner piano pieces as JSON

You are writing **lessons for entry-level piano students** for Piano Tutor, part
of Retro Audio Workstation (RAW). A lesson is a short piece written for two
hands with finger numbers. From that one description the tutor shows the sheet
music, lights up the keys and fingers as it plays, lets the student change the
speed, adds a metronome, and prints the score.

Produce **one JSON object, nothing else** — no comments, no trailing commas.
The user pastes it into the tutor's **Script** tab and presses Apply, or saves
it as a `.json` file and opens it with **File → Open Lesson**.

---

## 1. Quick card

```json
{
  "format": "raw.author",
  "version": 1,
  "lessons": [
    {
      "name": "hot_cross_buns",
      "title": "Hot Cross Buns",
      "tempo": 84,
      "time": "4/4",
      "key": "C",
      "level": 1,
      "position": {"right": "C4", "left": "C3"},
      "right": {"notes": "E4(3) D4(2) C4(1):2 | E4 D4 C4:2 | C4:1/8 C4:1/8 C4:1/8 C4:1/8 D4:1/8 D4:1/8 D4:1/8 D4:1/8 | E4 D4 C4:2"},
      "left":  {"notes": "C3(5):4 | G3(1):4 | C3:2 G3:2 | C3:4"}
    }
  ]
}
```

A document may hold several lessons (a set of exercises, or a piece in
stages). Everything except `name` and at least one hand has a default.

---

## 2. Lesson fields

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | identifier | **required** | `[a-z_][a-z0-9_]*`; the file/export name |
| `title` | string | name | shown on the score |
| `composer` | string | `""` | shown on the score, e.g. `"Traditional"` |
| `tempo` | number | 80 | BPM, 20–300. **Write the performance tempo**; the student slows it down in the app |
| `time` | string | `"4/4"` | `"3/4"`, `"2/4"`, `"6/8"` … |
| `key` | string | `"C"` | `C G D A E B F# Db Ab Eb Bb F` or minors `Am Em …`; sets the key signature and how notes are spelled |
| `level` | 1–5 | 1 | 1 = first lessons (five-finger position, quarter and half notes) |
| `position` | object | derived | `{"right": "C4", "left": "C3"}` — the **lowest key** of each hand's five-finger span (see §5) |
| `instructions` | string | `""` | what to practise, in one or two sentences |
| `tips` | list of strings | built-in | posture / practice tips shown beside the score |
| `voice` | string | `"piano"` | `piano`, `music_box`, `organ`, `chip`, or the name of an instrument in this document (§7) |
| `step` | note value | `"1/4"` | the length of one token when no length is written |
| `right`, `left` | hand (§3) | — | at least one hand is required |

---

## 3. Writing a hand

```json
"right": {"notes": "E4(3) D4(2) C4(1):2 | E4 D4 C4:2"}
```

or just a string — `"right": "E4 D4 C4:2"` — when there is no fingering.

| Token | Meaning |
|---|---|
| `C4`, `F#4`, `Bb3` | play that note for one step (a quarter note by default) |
| `C4:2` | play it for **2 steps** (a half note); `:4` a whole note; `:0.5` an eighth |
| `C4:1/8`, `C4:1/4.` | explicit note value; a trailing `.` is a dot (`1/4.` = dotted quarter); `:1.5` also works |
| `C4(3)` | play it with **finger 3**. Length and finger may be combined: `C4:2(1)` or `C4(1):2` |
| `[C3 E3 G3]` | a **chord** — all notes together, one token |
| `[C3 E3 G3](5,3,1)` | chord with fingers, **in the order the notes are written** |
| `.` | a rest of one step; `.:2` a two-step rest |
| `-` | hold: extends the previous note (or chord) by one step |
| `\|` | bar line. Optional — but write them: the tutor checks each bar against the time signature and reports mistakes |

Middle C is `C4`. Sharps `#`, flats `b`. Write the note **as the student should
read it**: `Bb4` and `A#4` are the same key but print differently.

Fingers: **1 = thumb … 5 = little finger**, both hands.

### The `fingers` line

If you prefer to keep fingering separate, add a `fingers` string with one entry
per note event (a chord counts as one entry, written `5,3,1`):

```json
"left": {"notes": "[C3 E3 G3]:4 | [B2 D3 G3]:4", "fingers": "5,3,1 5,2,1"}
```

Inline `(n)` and the `fingers` line may be mixed; inline wins.

---

## 4. Rhythm cheat-sheet (with the default `"step": "1/4"`)

| Want | Write |
|---|---|
| quarter note | `C4` |
| half note | `C4:2` |
| dotted half | `C4:3` or `C4:1/2.` |
| whole note | `C4:4` |
| eighth notes | `C4:1/8 D4:1/8` (or set `"step": "1/8"` and write `C4 D4`) |
| dotted quarter + eighth | `C4:1.5 D4:1/8` |
| quarter rest | `.` |
| tie across a bar | just write the long value — `G4:6` — the score draws the tie |

Every bar must add up to the time signature; a wrong bar is reported as
`lessons[name].right: bar 3 has 5 beat(s), the time signature wants 4`.

---

## 5. Hand positions and fingering — read this

Beginners play in a **five-finger position**: each finger rests on its own
white key. The right hand's thumb (1) sits on the lowest key, the left hand's
little finger (5) does. "C position" is right hand 1 on C4 / left hand 5 on C3.

Piano Tutor uses the position to **fill in fingers you do not write**: a note
under the hand is played by whichever finger sits on it. So you only need to
write a finger

* on the **first note** of each hand,
* wherever the **hand moves** or a finger **stretches** outside the position
  (writing `A4(5)` after a C-position passage tells the tutor finger 5 reached
  up to A),
* on every note of a **chord** the first time it appears.

Declare the starting position explicitly with `"position"` so the keyboard can
show it before the piece starts:

```json
"position": {"right": "C4", "left": "C3"}
```

Notes that have no finger *and* fall outside the current position are reported
(`… 3 note(s) have no finger number and are outside the hand position`): add a
finger there. The tutor also warns when one finger is asked to play two
different keys at once.

**Level 1** pieces stay in one position, use quarter, half and whole notes, and
usually give the left hand one note or one chord per bar. Keep melodies inside
C4–G4 for the right hand and C3–G3 for the left.

---

## 6. Structure

```json
{
  "format": "raw.author",
  "version": 1,
  "project":     {"name": "First Pieces"},
  "instruments": [ ... optional, see §7 ... ],
  "lessons":     [ { ...lesson... }, { ...lesson... } ]
}
```

Order lessons from easiest to hardest; the tutor lists them in the order
written.

---

## 7. Voices

The built-in voices are chip-style recipes rendered by RAW's synthesiser:
`piano` (default), `music_box`, `organ`, `chip`. To design your own, add an
`instruments` entry in the RAW authoring format (see `AUTHORING_FORMAT.md`) and
reference it by name — always set `root_note`:

```json
"instruments": [
  {"name": "harpsichord", "oscillator": "saw", "pitch": "C4", "root_note": "C4",
   "duration": 0.5, "amplitude": 0.45, "envelope": [0.002, 0.25, 0.2, 0.08],
   "drift": 0.0, "stretch": "uniform"}
],
"lessons": [{"name": "minuet", "voice": "harpsichord", ...}]
```

Keep instruments **filter-free** (or `drive` only); the tutor renders every
note of the piece.

---

## 8. Worked example — a level-2 piece with chords

```json
{
  "format": "raw.author",
  "version": 1,
  "lessons": [
    {
      "name": "mary_with_chords",
      "title": "Mary Had a Little Lamb (with chords)",
      "composer": "Traditional",
      "tempo": 92,
      "time": "4/4",
      "key": "C",
      "level": 2,
      "position": {"right": "C4", "left": "C3"},
      "instructions": "The same tune, now with a left-hand chord in every bar. C chord: fingers 5-3-1 on C-E-G. G chord: fingers 5-2-1 on B-D-G.",
      "tips": ["Play all three chord notes exactly together.", "Practise the left hand alone until the chord change is smooth."],
      "right": {"notes": "E4(3) D4(2) C4(1) D4(2) | E4 E4 E4:2 | D4 D4 D4:2 | E4 G4(5) G4:2 | E4 D4 C4 D4 | E4 E4 E4 E4 | D4 D4 E4 D4 | C4:4"},
      "left":  {"notes": "[C3 E3 G3]:4(5,3,1) | [C3 E3 G3]:4 | [B2 D3 G3]:4(5,2,1) | [C3 E3 G3]:4(5,3,1) | [C3 E3 G3]:4 | [C3 E3 G3]:4 | [B2 D3 G3]:4(5,2,1) | [C3 E3 G3]:4(5,3,1)"}
    }
  ]
}
```

---

## 9. Rules

1. One JSON object; no comments, no trailing commas.
2. `name` is a lowercase identifier; `title` is what the student sees.
3. Write bar lines and make every bar add up.
4. Finger the first note of each hand, every position change, and every
   chord. Fingers are 1–5.
5. Only use public-domain or original music.
6. Do not invent fields. Unknown fields are ignored with a warning.
7. Keep level-1 pieces within one five-finger position per hand.

## 10. What happens on Apply

The tutor is strict about structure and forgiving about values. Every problem
is reported with a path — `lessons[ode_to_joy].right: bar 4 has 5 beat(s), the
time signature wants 4` — and the piece still loads. If the user pastes the
report back to you, fix exactly what it names.
