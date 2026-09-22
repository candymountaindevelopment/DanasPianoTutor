# Design — Danas Tutor, one screen

The artboards the browser app is built from. Open any `.dc.html` in a
browser; the first three are live (the rail, the hand selector and the
Listen toggle work).

| File | Board |
|---|---|
| `Main.dc.html` | **Learn** — read it and play along |
| `Practice.dc.html` | **Practice** — the tuner ribbon under the staff, the score card in the panel |
| `Ear.dc.html` | **Ear** — the pitch trace takes the staff's place |
| `Anatomy.dc.html` | *What folded into what* — the eight consolidations, numbered on a wireframe |
| `Parts.dc.html` | *Parts and palette* — transport, ribbon, hands, ⌘K, type, colour, the five rules |
| `canvas.json` | board positions and the notes that sit beside them |

## The thesis

The tutor and the Ear are not two apps: they are three modes of one screen.
Three columns — a 52 px rail, the stage, a 392 px panel — and **the staff and
the keyboard never move between modes**. Switching mode slides the tuner
ribbon in and changes the right panel; nothing else jumps.

## What folded into what

1. Two toolbars of 23 controls → one bar (mode, ⌘K, Listen, ⋯) plus a
   floating transport of seven, every one of which changes while you play.
2. The lesson list → the engraved title *is* the switcher.
3. A Settings tab competing with the lesson → three modes on the rail, with
   the feature switches at its foot.
4. Four tabs → two modes in the bar; the script editor is Ctrl+E over the
   screen, because writing a lesson is not playing one.
5. A full-height note lane → a 74 px tape on the staff's own x-axis, so bar 2
   sits under bar 2. Off by default.
6. Two hand diagrams in boxes → the hands are drawn on the keys they hold,
   with the finger numbers on the keys.
7. Danas Ear as a second page → a mode. Same keyboard, same clock, one
   microphone. (The standalone page stays at `/listen/`.)
8. A five-row table of key, time, tempo, bars, voice → one line of small caps
   under the title.

## The five rules

1. **One accent.** Mint (`#46D7A1`) always means *now* — the note being
   played, the key being held, the pitch being heard. Nothing decorative is
   ever coloured.
2. **No cards.** Regions are divided by a single 1 px hairline; nothing is a
   box inside a box inside a panel.
3. **Chrome earns its place**: a 52 px bar, a 52 px rail, one floating
   transport. 88 % of the screen is the lesson.
4. A control that **changes while you play** lives on the screen. One that is
   **set once** lives in ⌘K.
5. Information is **drawn on the thing it describes**: fingers on keys,
   tuning under the staff, misses on the notes that were missed.

Type: Instrument Serif (titles and the one big number), Instrument Sans
(everything you read), JetBrains Mono (anything that changes while you play,
so nothing jitters). All three are self-hosted — see
`tools/build_web.py --vendor` — because the production CSP allows fonts from
`'self'` only.

The implementation is `web/` (see docs/TECHNICAL.md §14.6). These boards are
the reference, not the source: where the two differ, the app wins and the
board should be updated.

Note: the boards reference a `support.js` that was not part of the export, so
some interactions may be inert when opened straight from disk.
