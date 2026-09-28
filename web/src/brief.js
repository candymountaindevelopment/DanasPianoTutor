/* The scripting library, packed into one message for a chatbot.
 *
 * Writing a lesson is writing JSON, which is the kind of work a chatbot does
 * well — once it has been told the format. That format already exists as
 * LESSON_FORMAT.md and ships with the app, so what was missing was not another
 * document but a way to hand the whole of it over in one paste: the reference
 * verbatim, the facts only this copy knows (its build, the voices it has, the
 * lesson open at the moment), and a line where the person says what they want
 * written.
 */

/* A long piece as an example teaches nothing a short one does not, and every
 * character of it is paid for in the chatbot's context. */
const MAX_EXAMPLE = 6000;

function factLines({ about, lesson, span }) {
  const lines = [];
  const stamp = [about.version ? "v" + about.version : null,
    about.build ? "build " + about.build : null,
    about.built || null].filter(Boolean).join(" · ");
  lines.push(`- ${about.name || "Danas Piano Tutor"}${stamp ? " " + stamp : ""}`
    + (about.engine ? `, RAW engine ${about.engine}` : ""));
  const voices = about.voices && about.voices.length ? about.voices : ["piano", "music_box", "organ", "chip"];
  lines.push(`- Voices you may name in \`voice\`: ${voices.join(", ")}`);
  const [lo, hi] = about.span_range || [5, 8];
  lines.push(`- Hand span: ${lo} to ${hi} white keys`
    + (span ? `; this student is set to ${span}` : "; each lesson sets its own (5 unless it says otherwise)"));
  if (lesson) {
    const time = Array.isArray(lesson.time) ? lesson.time.join("/") : lesson.time;
    lines.push(`- Open at the moment: "${lesson.title}" — level ${lesson.level}, key ${lesson.key},`
      + ` ${time}, ${lesson.measures} bars at ${lesson.tempo} BPM`);
  }
  return lines;
}

export function chatbotBrief({ reference, about = {}, lesson = null, script = "", span = null }) {
  const out = [];
  const push = (...lines) => out.push(...lines, "");

  push(`# Write a piano lesson for ${about.name || "Danas Piano Tutor"}`);
  push(
    "You are writing a lesson for a beginner piano student. A lesson is one JSON",
    "document, and the reference further down is the whole of what this app",
    "understands — nothing outside it will work.");

  push("## How your answer is used");
  push(
    "The person copies your JSON into the tutor's **Script** tab and presses Apply,",
    "so put one JSON document in a fenced block and nothing else inside it: no",
    "comments, no trailing commas, no prose between the braces. Explain yourself",
    "outside the block as much as you like.",
    "",
    "From that document the tutor engraves the sheet music, lights up each key and",
    "finger number as it plays, glides a drawn hand along the keyboard so every",
    "finger lands on its own key, plays at any tempo with a metronome and a",
    "count-in, and — in Practice mode — listens through the microphone and scores",
    "what the student played against what you wrote. So the fingering matters as",
    "much as the notes: what one hand cannot reach in one position, a beginner",
    "cannot play.",
    "",
    "The app is strict about structure and forgiving about values. If it reports a",
    "problem, the person will paste the report back; it names the lesson, the hand",
    "and the bar, and you should fix exactly what it names.");

  push("## This copy of the app");
  push(...factLines({ about, lesson, span }));

  const example = (script || "").trim();
  if (example && example.length <= MAX_EXAMPLE) {
    push("## A document this app has accepted");
    push("```json", example, "```");
  }

  push("## The reference");
  push(reference.trim());

  push("## What to write");
  push(
    "> Replace this line with the lesson you want: the tune or the exercise, which",
    "> hands, how many bars, the key, the level (1 = first lessons, five-finger",
    "> position, quarter and half notes), and what it should teach. If the line is",
    "> still here when you read this, ask what is wanted before writing anything.");

  return out.join("\n").replace(/\n{3,}/g, "\n\n").trim() + "\n";
}
