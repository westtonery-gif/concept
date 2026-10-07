# ADR-0089: A storyboard-writer Agent — and the picture prompt of a shot is built in code

- **Status:** Accepted
- **Date:** 2026-10-07
- **Deciders:** Maintainer ("окей делаем", 2026-10-07); Lead Architect
- **Builds on:** ADR-0087 (the story script), ADR-0088 (the frame generator and the character
  sheets), ADR-0034/0087 (a model's answer decoded by one pure function)
- **Serves:** `CLAUDE.md` queue task 23 (23.14)

## Context

A story script is dialogue and a cast. To draw it, something has to say what each picture shows:
which lines it covers, who is in it, where, from what angle, what moves. ADR-0080/0082's writers
write prompts for the *photo-to-video* pipeline and know nothing of a voiced story. The character
sheets of ADR-0088 are drawn from words; the shots after them must be drawn from words **and those
sheets**, so a character stays the same character across thirty pictures.

The first live storyboards taught four things, each now a rule.

1. **Counting is not asked of the model.** Told "10–24 shots" the writer returned thirty, one per
   line, twice — which is what the genre does (the references cut every 2–4 seconds). The bound was
   mine and arbitrary; the decoder now fixes only a floor (10) and the ceiling the lines impose.
2. **Faults arrive together.** A decoder that names the first fault sends the writer round again to
   trip on the next one; the repair round is paid for. It now reports every fault in one message.
3. **A character the picture names is drawn, listed or not.** Told "gramps_hal" alone, the writer
   described Dale in the picture too; with no reference he came out an ordinary man, not the
   gull-headed one. Rejecting this twice failed the run; the writer's habit is harmless once the
   code **adds** such a character (and a speaker) to the cast, so their sheet is passed as a
   reference. Only a cast over three is a fault.
4. **Order in a prompt is not neutral.** With the setting in front, every picture came out a wide
   view with small characters. The shot goes first.

## Decision

### 1. One role, `storyboard_writer@v1`, on the Rin/Leo template

Agent + Prompt `storyboard-writer` (bundled store, ADR-0030) + Schema `storyboard@v1`; no Skills, no
Tools. Input is built by the pure `storyboard_input(script)`: the cast (`key | look`) and the
dialogue with line numbers. Output, two flat strings:

| field | content |
|---|---|
| `world` | 30–130 words: the place, light and constant details every picture shares |
| `shots` | one line per shot, `lines | cast | picture | motion` |

`lines` is `4` or `4-5` (at most three lines); `cast` is up to three character keys, or `-` for an
insert shot; `picture` is 20–90 words (the Prompt asks for 35–80, the decoder accepts from 20 so one
word short is not a second paid round); `motion` is 4–40 words of what moves during the shot. English
output, Russian Prompt, like the story writer.

### 2. A pure decoder judges grammar and arithmetic

`application/storyboard.py` `decode_storyboard(fields, script) -> Storyboard`, in the shape of
`decode_story_script`: shots cover every line **once and in order**; each shot at most three lines;
at least ten shots; cast keys belong to the story and are not repeated; sizes are in range. It
reports every fault at once (up to ten) and **adds** to a shot's cast the characters its picture
names and the speakers of its lines (when it has a cast); an insert shot with no one named stays an
insert. Whether the pictures will be good is for the eye.

### 3. The prompt of a picture is built in code, not written by the model

`shot_prompt(storyboard, shot, script)` returns, in this order: the shot's picture, a framing
sentence, the cast's looks (repeated in words *and* passed as reference pictures — a reference is a
hint, a sentence is an instruction), the setting, and the style. The style and the setting live in
code and in one field, so they cannot drift between shots; the writer never repeats them.

## Consequences

### Positive

- A story becomes a list of ready-to-draw prompts and reference sets for ~$0.05 of model time. The
  first one cost $0.0456 for thirty shots.
- Consistency is a property of the pipeline (sheets as references, looks in words, one style), not
  of the writer's attention.
- Thirty shots at $0.03 are $0.90 of pictures, so a mistake in the storyboard is cheap to redo
  *before* any video is bought.

### Negative / Trade-offs

- **The writer's habit is corrected by code**, which is quiet: a character it did not mean to show
  can be drawn because the picture mentioned them in passing ("glances at Dale"). The cap of three
  and the reading of the result are what stand between that and a crowded frame.
- **Framing is only advised.** "Medium shot" came back as a medium-wide view with the characters
  smallish; moving the shot first helped a little, not completely. Close-ups for dialogue are the
  first thing to look at in the full set.
- A character's mark that must read (a number on a hat) is drawn by a model that draws numbers
  poorly: "94" arrived correct once and as "9·" another time. A fix, if it matters, is a
  post-edit, not a prompt.
- Everything is paid per shot; a thirty-shot story is thirty calls and thirty failure points.

## Deferred

1. **The full set of thirty pictures** and a decision on the framing, after looking at them.
2. **Seedance clips** from each picture with its `motion`, then assembly with the voice track and
   word-by-word captions — the department's remaining pieces.
3. **A QA role for the storyboard / the pictures** (the faces, the numbers, the hands).
4. **Shot length** from the spoken lines' real durations (the dialogue mixer already has them).

## References

- ADR-0087 (script), ADR-0088 (frames), ADR-0034 (decoder shape), ADR-0070/0083 (what QA blocks)
- `generation-tests/frames/…/storyboard.json` — the first storyboard, thirty shots with prompts
