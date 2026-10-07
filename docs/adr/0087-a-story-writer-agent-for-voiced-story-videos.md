# ADR-0087: A story-writer Agent for voiced story videos — flat fields, a line grammar, a decoder

- **Status:** Accepted
- **Date:** 2026-10-07
- **Deciders:** Maintainer ("го", 2026-10-07, after the reference analysis and the proposal below);
  Lead Architect
- **Builds on:** ADR-0085/0086 (the speech port and its vendors), ADR-0080 (a prompt-writing Agent
  in the generation department), ADR-0034 (a model's answer decoded by one pure function)
- **Serves:** `CLAUDE.md` queue task 23 (new subtask 23.13)

## Context

The format the maintainer wants to make — short voiced stories with cartoon characters, for
American accounts — is **dialogue first**. Ten reference videos were analysed on 2026-10-06/07 and
three of them came with view counts (9.7 M, 5 M, 1 M, the maintainer's figures, unverified). What
those three share and the other seven do not is a **skeleton**: a visible mark or gift on the
hero's body, an opening that is already a conflict, someone using the gift while the hero suffers,
escalation in steps, a turn, and an ending that is a hook or justice. Nothing in the factory
writes such a thing; ADR-0080's writers produce *prompts for images and video*, not the story.

Two facts shaped the design. A structured Output here is **flat strings** (ADR-0014; ADR-0039 noted
that a native array becomes a Python repr), so a list must live in a string. And a model that is
asked for 130–300 spoken words writes 399 (observed on the first live call): **limits that are
arithmetic must be checked by arithmetic**, not asked for twice.

## Decision

### 1. One role, `story_writer@v1`, on the Rin/Leo template

Agent + Prompt `story-writer` (bundled store, ADR-0030) + Schema `story-script@v1`; no Skills, no
Tools. Input is free text — an idea and any wishes. Output fields, all strings:

| field | content |
|---|---|
| `title` | post title, English, ≤ 90 characters |
| `premise` | one sentence: the gift and who uses it |
| `characters` | 2–5 lines `key \| look \| voice` — `look` is for the artist, `voice` for the voice step |
| `dialogue` | 14–40 lines `key \| line`, with optional audio tags `[laughs]` |
| `next_part` | one sentence: what the next part reveals |

The Prompt is written in Russian like every Prompt here and produces **English** — the accounts
are American. It carries the skeleton above, the grammar, the tag rules (a tag is never spoken, so
meaning must not depend on it) and what is forbidden.

### 2. A pure decoder is the only judge of the grammar and the limits

`application/story_script.py` `decode_story_script(fields) -> StoryScript`, in the shape of
`decode_verdict` (ADR-0034): every violation raises `StoryScriptError`, nothing is guessed. It
checks the grammar; unique, well-formed keys; a cast of 2–5; speakers declared and every character
speaking; 14–40 lines; the opening line ≤ 14 words; a line ≤ 35 words; **110–240 spoken words in
total** (audio tags not counted); tags balanced, ≤ 3 words and never the whole line; title ≤ 90.
It reports `word_count` and `estimated_seconds` (2.1 words a second of finished track — measured: 259 words came out as 125 s). Whether a story is *good* is
not its question.

On a refusal the caller may send the decoder's own message back for **one** repair round. The demo
does; the department's workflow step will.

### 3. What the writer is told to avoid — the maintainer's call, made with the evidence

The strongest reference (9.7 M) is about parents draining a child's life for money; the second is a
mother harvesting a daughter's happiness. Both are **exploitation of a child**, which platforms
treat strictly and which limits advertising on the content — the monetisation the format exists
for. So the Prompt keeps the skeleton and moves the gift to an adult, a pet or an object, forbids
violence, abuse, kidnapping, sex and self-harm, forbids putting children in danger or showing them
exploited, and allows only original characters (no franchises, real people or brands — one
reference is built on someone else's cartoon). This is a deliberate departure from copying the best
performer, and is recorded as one.

### 4. The speech step gets the tags the writer emits

`dialogue` lines carry ElevenLabs-style tags (`[laughs]`). `eleven_v3` acts on them; a voice that
cannot would read them aloud, so `KokoroSpeechSynthesizer` drops them before speaking (it is not
the voice in use — see Consequences — but the contract should not depend on which one is).

## Consequences

### Positive

- The story is produced by the factory from one sentence, and the next steps (voices, assembly,
  captions) already exist or are queued.
- The decoder turns "the model ignored the limit" from a surprise into a measured, repairable
  error; the first live call showed exactly that and the repair round fixed it.
- No new dependency, no new port: a producer, a pure function and a Prompt.

### Negative / Trade-offs

- **The skeleton rests on three videos** with figures from the maintainer, selected for success. It
  is a hypothesis, and the Prompt says nothing a different sample could not overturn.
- A **second model call** when the first answer breaks a limit; the live run cost $0.018 in total.
- Quality is unjudged: nothing yet reads a finished script for sense, humour or repetition. That is
  a reviewer's job (the human gate) until a QA role exists.
- The maintainer dropped the local Kokoro voice on 2026-10-07 ("от Kokoro отказываемся"): the voice
  is ElevenLabs only, with the access question ADR-0086 recorded. The Kokoro adapter stays in the
  tree, unused and not selected unless its variables are set.

## Deferred

1. **Picking an ElevenLabs voice id per character** from `voice` — a small mapping step, once a key
   and a voice list exist.
2. **A story QA role** (sense, humour, repetition, the forbidden list) reusing `qa-verdict@v1`.
3. **From script to pictures:** per-line shots and the existing scene/prompt writers (ADR-0080/0082).
4. **Series:** `next_part` is produced but nothing yet writes part two from it.
5. **Whether the skeleton holds** against more videos with figures.

## References

- `generation-refs/NOTES.md` — the ten references and what the three with figures share
- ADR-0034 (the verdict decoder), ADR-0014 (flat structured output), ADR-0030 (the Prompt store),
  ADR-0072 (a small text-writing role), ADR-0086 (the speech vendors)
