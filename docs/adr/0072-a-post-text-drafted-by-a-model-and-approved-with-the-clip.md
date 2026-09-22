# ADR-0072: A post text drafted by a model, shown on the review page, approved with the clip

- **Status:** Accepted
- **Date:** 2026-09-22
- **Deciders:** Maintainer ("пишет модель, вы правите", 2026-09-22); Lead Architect
- **Amends:** ADR-0023 §7 / ADR-0060 additively (the review package and decision may carry a post
  text); reverses ADR-0058's removal of the draft caption, now that auto-posting (task 31) reads it
- **Serves:** `CLAUDE.md` queue task 30

## Context

Auto-posting through upload-post (task 31) needs a `title` (required by YouTube, used as the caption
elsewhere) and a `description`; upload-post's `tags` apply to YouTube only, so hashtags live in the
description. ADR-0058 dropped the clip's draft caption because nothing read it; task 31 is the
reader. The maintainer wants the model to draft it and to be able to correct it on the review page,
approving text and clip together.

## Decision

### 1. A producer role, `clip_post_writer@v1`

An ordinary producer on the Rin/Leo template: Agent, Prompt `clip-post-writer` in the bundled store,
Schema `clip-post@v1` with `required_fields = ("title", "description")`, no Skills, no Tools. It is
a `TaskExecutor`, built by `build_executor_map`, validated through its `SchemaBinding`, its calls
recorded as Analytics Records (ADR-0029). Input: canonical JSON of the clip's transcript and the
episode's `source_ref` (the file name, which is the one hint of the series' name the core has).

### 2. It runs for a clip after QA passes, before the review is published

`ClipProduction` opens one Task per QA-passed candidate at step **`post-text`**
(`task_input` names the `artifact_ref`), committed before the call and finished with
`start_task` / `finish_task` (ADR-0026 §2). A clip that did not pass QA gets no text — it will never
be posted. A failed or invalid draft does **not** block the review: the page says there is no text,
and task 31 refuses to post a clip without one. Resumption finishes a `RUNNING` Task rather than
opening a second one.

### 3. The review port carries the draft out and the text back — additively

- `PostDraft(title, description)` in `adapters/review_desk.py`: both non-blank; the title at most
  **100 characters** (YouTube's limit, the strictest target).
- `ReviewPackage.post: PostDraft | None = None` — shown to the reviewer.
- `ReviewDecision.post: PostDraft | None = None` — the text **as the reviewer left it** when
  deciding; `None` from a desk that has no place to edit it.

Existing implementers and packages are unchanged (defaults). `NotionReviewDesk` gains two optional
property names, `OMEMO_REVIEW_NOTION_POST_TITLE_PROPERTY` and `…_POST_DESCRIPTION_PROPERTY`
(`rich_text`), set **together or not at all** — one without the other is a configuration error. With
them, `publish` writes the draft into those properties (and into the page body), and
`fetch_decision` reads them back with the decision. The fingerprint covers the draft only when there
is one, so a page published before this ADR still matches its package.

### 4. The approved text is recorded in the Run

When an approval is recorded and the desk returned a text that differs from the draft, the
orchestrator records it as one more `post-text` Task, agent ref `human_reviewer`, succeeded with an
Output in the same `clip-post@v1` shape. **The text to post is the latest `post-text` Output of the
Artifact**: the reviewer's if they edited, otherwise the model's. A reviewer's text over 100
characters in the title is not recorded and the model's draft stands — the page still shows what
was sent.

## Consequences

### Positive

- Task 31 reads one well-defined thing: the latest `post-text` Output of an approved Artifact.
- The human stays the author of record: an edit on the page is what gets posted, and it is traced.

### Negative / Trade-offs

- One more model call per passed clip (short input, short output).
- The review port grows by two optional fields; the Google Docs desk ignores them (no place to edit).
- An edit made **after** the decision is not read — the decision is the moment the text is taken.

## Alternatives considered

- **A text template from configuration.** Cheaper, but the maintainer chose a model-written text.
- **Store the text in the clip Artifact.** An Artifact's content is immutable and is the clip; the
  text is a separate, editable thing with its own author.
- **Read the text from the page at posting time.** Would make posting depend on the review desk and
  leave the Run without a record of what was posted.

## References

- ADR-0008/0014 (Schemas, structured output), ADR-0023 §7, ADR-0026 §2, ADR-0029, ADR-0031,
  ADR-0058, ADR-0060, ADR-0070; upload-post "Upload Video" API (`title`, `description`, `tags`);
  `CLAUDE.md` queue tasks 30, 31
