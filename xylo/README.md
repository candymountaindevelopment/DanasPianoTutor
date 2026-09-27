# Xylophone

Play a note — into the microphone, on the computer keys, or by clicking —
and watch the bar ring.

**One file.** `index.html` carries its own styles, its own pitch detector and
its own little mallet synthesiser. It imports nothing, fetches nothing, and
knows nothing about the tutor. Copy it anywhere and it still works.

## Running it

Double-click `index.html`. If your browser refuses a microphone on a
`file://` address (Chrome usually does), serve the folder instead — any
static server will do:

```bash
cd xylo
python -m http.server 8788 --bind 127.0.0.1
```

It is also served beside the tutor at `/xylo/` by `tools/serve_web.py` and in
the published build.

## What it does

- **Play** runs a tune. The notes come in from the right along a lane under
  the instrument, and a ball leaps from note to note, landing on each one at
  the moment it sounds — so the jump is the count-in for the note. Play along
  and the notes you get right turn green.
- **Listen** opens the microphone and strikes whichever bar is sounding. It
  waits for two consecutive frames of the same note before striking, so a
  wobble in the voice does not set off a row of bars.
- **Click a bar** to hear it — a mallet tone, built from a fundamental and two
  inharmonic partials, decaying in about a second.
- **The computer keys** `a w s e d f t g y h u j k o l p` play the bars from
  the bottom up.
Four tunes are built in — *Twinkle, Twinkle*, *Mary Had a Little Lamb*,
*Ode to Joy* and a scale up and down — at three speeds. They are written as
steps above the instrument's lowest bar, so moving **From** transposes them.

## The controls

| | |
|---|---|
| **Tune / Speed** | what the ball bounces along, and how fast it comes |
| **From / Octaves** | where the instrument starts and how much of it there is; the tune follows |
| **Shape** | *Auto* picks by the shape of the screen; *Upright* is chromatic, with the accidentals overlapping the naturals as on a real xylophone; *Ladder* is the toy — white notes, longest bar at the top |
| **Sensitivity** | how loud a sound has to be before it counts, from −70 dB (twitchy) to −30 dB (only deliberate notes) |
| **Sound** | whether a struck bar — by hand or by the tune — makes a sound as well as a flash |

## On a phone

It is built for one. Portrait gets the ladder, which is the shape of the
screen; landscape gets the upright instrument with the chrome trimmed to a
single row. The controls become a strip that scrolls sideways with Listen and
Demo at its head, the targets are 40 px, the bars take multi-touch, and a
small screen starts with one octave. Rotating refits the drawing. The catch
is the microphone: a browser will only open one on a page served over
`https://` or from `localhost`, so on a phone this wants the deployed copy at
`/xylo/` rather than a file.

## How the ball keeps time

The beat is read from the audio context's own clock, not from a timer: at
every frame `beat = (currentTime - t0) × bpm / 60`. Notes are handed to the
audio scheduler a quarter of a second early, so they sound exactly on the
beat, and the bar is struck when that moment actually arrives. The lane draws
each note at `nowX + (beat_note - beat_now) × pixels-per-beat`, and the ball's
arc runs between the positions of the note it left and the note it is going
to. Because those are the same numbers, the landing cannot drift away from
the sound.

Both ends of the arc scroll leftwards at the same rate, so interpolating them
evenly would leave the ball hanging over the now line. It leaves fast and
arrives slowly instead — `1 - (1 - t)⁴` — which makes it leap forward onto
the note coming in and ride it down; the ease still ends at exactly 1, so the
landing is still the note's own moment. A page that is not on screen is not
animated, so the tune waits for it rather than running on in silence.

## How it hears

The microphone is analysed in 4096-sample windows with **YIN** (de Cheveigné
& Kawahara, 2002): a cumulative-mean normalised difference function, the
first dip under 0.15, then parabolic interpolation. A reading counts when its
clarity is at least 0.6 and the window is above the sensitivity gate. One
extra rule earns its place: a window that is quiet where YIN looks but loud
at its end — a strike caught by its tail — is thrown away, because digital
silence looks perfectly periodic and would otherwise be reported as a
confident low note.

Range is 60–2200 Hz, which covers B1 to roughly C#7: a singing voice, a
whistle, a piano, a guitar. Chords are not separated — the detector reports
one pitch at a time.
