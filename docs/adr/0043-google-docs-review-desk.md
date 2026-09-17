# ADR-0043: Google Docs Adapter — a real `ReviewDesk` over Google Drive, a service account and a decision marker

- **Status:** Accepted
- **Date:** 2026-09-17
- **Deciders:** Lead Architect / Domain Architect; the two product decisions (§2, §4) were taken by
  the maintainer when asked

## Context

ROADMAP Stage 10 asks for "a full implementation of the Google Docs Adapter": a candidate is
published with its context and the human decision is read back. The session queue calls this task
14.1. The following are already in place:

- `ADR-0023` §7 fixes the contract: `ReviewDesk.publish(ReviewPackage) -> str` and
  `fetch_decision(review_id) -> ReviewDecision | None`. `ADAPTER_SPEC.md` §3 gives the shared rules
  (lives in `infrastructure/`, own technical error, idempotent writes, never touches a Run,
  credentials from the environment).
- `ADR-0025` built `InMemoryReviewDesk`, the reference where the contract is silent: another
  package under a published `review_id` is refused, an unpublished `review_id` is an error.
- `ADR-0040` is the pattern the Notion adapter set: stdlib `urllib`, a local HTTP server in the
  tests, fail closed on configuration, no wiring in the same step.

The queue flagged two decisions that Notion did not have to make, and asked for them not to be
guessed: **how the factory authenticates** to Google, and **how a decision is read from a Doc**,
which has no "Approve" button. `ARCHITECTURE.md` §3.11/§12/§13 and `ADAPTER_SPEC.md` leave both
open. It also left the library choice (official client vs. raw REST) to this task.

## Decision

### 1. One API: Google Drive v3, over stdlib `urllib`
The desk needs three calls, all of the Drive API v3:

| Call | Used for |
|---|---|
| `GET /drive/v3/files?q=…` | find the Doc of a `review_id` in the configured folder |
| `POST /upload/drive/v3/files?uploadType=multipart` | create the Doc: metadata with `mimeType: application/vnd.google-apps.document` plus a `text/plain` body, which Drive converts into a Google Doc in the same call |
| `GET /drive/v3/files/{id}/export?mimeType=text/plain` | read the Doc's text back |

The Docs API is not needed: conversion on upload writes the content and a plain-text export reads
it. `google-api-python-client` was rejected for the reasons `ADR-0040` §1 rejected `notion-client`
(a large untyped dependency tree, `httplib2`, a discovery layer, for three requests), and because
the tests could then no longer run the adapter's real HTTP code against a local server.

Shared drives are supported (`supportsAllDrives=true`, `includeItemsFromAllDrives=true`,
`corpora=allDrives`).

### 2. Authentication: a service account (maintainer's decision)
The factory authenticates as a Google Cloud **service account**, with the OAuth 2.0 JWT-bearer
grant: a JWT `{iss: client_email, scope, aud: token_uri, iat, exp: iat + 3600}` signed with RS256
is exchanged at the key's `token_uri` for an access token. The token is cached until 60 s before
it expires and is dropped after any `401`, so the next call signs in again. Scope:
`https://www.googleapis.com/auth/drive` — `drive.file` would not see a folder the reviewer created
and shared.

A user OAuth refresh token was the alternative. It was rejected: it needs a one-off interactive
sign-in script, and it expires or is revoked (after 7 days while the OAuth app is in testing),
which would stop reviews silently. A service account runs headless and does not expire.

The operator shares a folder with the service account's e-mail as an editor. That folder should be
on a **shared drive**: a service account has no Drive storage quota of its own, so creating a Doc
in a folder of a personal "My Drive" can be refused by Google. This is operator setup, documented
in the README.

**The one new dependency: `cryptography`.** The standard library cannot sign RS256. `cryptography`
is fully typed (`mypy --strict` needs no ignores), ships wheels for every supported platform and is
the library `google-auth` itself uses when present. `google-auth` was rejected: it would add
`cachetools`, `pyasn1`, `pyasn1-modules` and `rsa`; its transports need `requests` or `urllib3`;
and all it would do here is sign one JWT. The token request itself stays on `urllib`.

### 3. How a package maps to a Doc
| Contract | Google Drive |
|---|---|
| "published" | a non-trashed file in the configured folder whose `appProperties.omemo_review` is the SHA-256 (hex) of the `review_id` |
| "the same package" | its `appProperties.omemo_package` is the SHA-256 of the package's canonical JSON (every `ReviewPackage` and `ArtifactView` field) |
| location returned by `publish` | `https://docs.google.com/document/d/{file id}/edit` |
| Doc title | `Ревью <review_id> (<kind>, v<version>)` |

Hashes are stored instead of the raw ids so that no id is ever interpolated into a Drive query
(no escaping to get wrong) and the 124-byte `appProperties` limit never applies.

`publish`: a Doc with the same review hash and the same package hash → its location, no write
(idempotent). The same review hash with another package hash, or with none → `ReviewDeskError`, the
first Doc stays (as in `ADR-0025`). No Doc → one is created.

### 4. The decision: a marker line at the top of the Doc (maintainer's decision)
The Doc starts with a decision block, then a separator line, then the review material:

```
Как принять решение: после «РЕШЕНИЕ:» впишите одно слово — одобрено, отклонено или доработать. …
РЕШЕНИЕ:
ПРИЧИНА:
======== МАТЕРИАЛЫ РЕВЬЮ ========
Run: <run_id>
Ревью: <review_id>
Артефакт: <artifact_id> — <kind>, версия <version>
Заменяет версию: <supersedes_ref>            (only for a later version)

БРИФ
<brief>

ЗАМЕЧАНИЯ QA
- <flag>                                     (or «— нет»)

КАНДИДАТ
<content>
```

`fetch_decision` exports the Doc as text and reads **only the block above the first separator
line**, so nothing in the brief or the candidate can pass for a decision.

| Value after `РЕШЕНИЕ:` (trimmed, case-insensitive, a final `.`/`!` ignored) | Result |
|---|---|
| empty | `None` — still pending |
| `одобрено` / `approved` | `APPROVED` |
| `отклонено` / `rejected` | `REJECTED` |
| `доработать` / `changes_requested` | `CHANGES_REQUESTED` |
| anything else | `None` and a `WARNING` naming the review and the unrecognised value |

The reason is the text after `ПРИЧИНА:` plus every following line of the block, joined by line
breaks and trimmed; empty → `None`. It is never required: the domain does not require one
(`Run.submit_review`), and a desk only carries the decision.

An unrecognised value is **pending, not an error**: it is a reviewer's typo, not a technical fault,
and raising would make every later check of that review fail until the Doc is fixed. The warning
keeps it visible. A **damaged template** is an error (`ReviewDeskError`): no separator line; no
`РЕШЕНИЕ:` or no `ПРИЧИНА:` line above it; either one more than once; `ПРИЧИНА:` before
`РЕШЕНИЕ:`. Reading a damaged block as "pending" would park a Run forever behind silence.

The desk does not freeze the first decision it reads: if the reviewer later edits the line, the
next fetch returns the new value. The Run is what makes a decision final — `submit_review` accepts
exactly one (`ADR-0007` §5), and the wiring (task 14.3) stops asking once it has.

The alternatives were a comment command (`/approve`, Drive Comments API) and a smart-chip
dropdown. A comment is easy to miss and needs another API; a dropdown chip cannot be created or
reliably read through the public APIs.

### 5. Decisions where the contract is silent
The rule of `ADR-0025` §3 / `ADR-0040` §3 applies: a technical or configuration fault is loud.
`ReviewDeskError` when:

- `fetch_decision` finds no Doc (never published, or trashed);
- more than one Doc carries the review hash (two concurrent publishers — not prevented, surfaced);
- the decision block is damaged (§4);
- the token endpoint or Drive answers non-2xx, the connection fails or times out;
- a JSON response is not an object or not the expected shape; a file id is not
  `[A-Za-z0-9_-]+`; the export is not UTF-8.

Error messages name the call and the HTTP status, never a token, the key or a response body.

### 6. Configuration from the environment, fail closed
`google_docs_settings_from_env(environ) -> GoogleDocsDeskSettings` reads two required variables; a
missing or blank one raises `ReviewDeskError` naming every missing variable:

| Variable | Meaning |
|---|---|
| `OMEMO_GOOGLE_SERVICE_ACCOUNT_FILE` | path to the service account's JSON key |
| `OMEMO_GOOGLE_REVIEW_FOLDER_ID` | the Drive folder where review Docs are created |

The key file is read at once: unreadable, not a JSON object, `type` other than `service_account`,
a blank `client_email` / `private_key` / `token_uri`, or a private key that is not an RSA PEM key →
`ReviewDeskError` (the path may be named, key material never). The private key is kept out of
`repr`. A path, not inline JSON, keeps a multi-line secret out of the environment. The desk's
constructor also takes `api_url` (default `https://www.googleapis.com`, overridden only by tests),
`timeout` (default 10 s) and `clock` (default `time.time`, for token expiry in tests).

### 7. Not wired yet
No entrypoint builds a `GoogleDocsReviewDesk`, and the Composition Root has no `build_review_desk`.
Publishing on `WAITING_HUMAN` is task 14.2; applying a fetched decision is 14.3.

## Deferred
- **Retry/back-off on `429`/`5xx`:** surfaced as `ReviewDeskError`, not retried (as `ADR-0040`).
- **Rich formatting** (headings, bold) — would need the Docs API `batchUpdate`; plain text is
  enough for a decision.
- **Updating a published Doc**: a new Artifact version is a new review (`ADR-0019`), so a new Doc.
- **Preventing duplicate Docs under concurrent publishers** (a lock or a deterministic file id).
- **Domain-wide delegation** (acting as a Workspace user) for a folder in "My Drive".

## Consequences

### Positive
- Stage 10's adapter exists; nothing Google-specific leaves `infrastructure/`.
- Headless authentication that does not expire; reviewers need nothing but the Doc.
- The tests run the real signing, token exchange, multipart upload and export over a real socket.
- A damaged Doc or a broken setup is refused loudly instead of reading as "pending".

### Negative / Trade-offs
- A second runtime dependency, `cryptography` (with `cffi`).
- A hand-maintained HTTP client: a change in Drive's JSON shape is caught only by shape checks.
- The decision is free text: a typo leaves the review pending until someone reads the warning.
- The folder must be shared with the service account, preferably on a shared drive.

## Alternatives considered
- **`google-api-python-client` / `google-auth`:** §1, §2.
- **OAuth 2.0 user refresh token:** §2.
- **Comment command or dropdown chip for the decision:** §4.
- **Docs API `documents.create` + `batchUpdate`:** two APIs and more calls for the same text.
- **Raw ids in `appProperties`:** query escaping and a 124-byte limit (§3).
- **An unrecognised decision raises:** §4.

## References
- `ROADMAP.md`: Stage 10
- `ARCHITECTURE.md`: §3.11, §12, §13
- `ADR-0007`, `ADR-0019`, `ADR-0023`, `ADR-0025`, `ADR-0040`
- `ADAPTER_SPEC.md` §3, §6; `ADAPTER_ACCEPTANCE.md` §10
