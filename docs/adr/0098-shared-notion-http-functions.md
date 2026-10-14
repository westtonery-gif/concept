# ADR-0098: Shared HTTP functions for the three Notion adapters

- **Status:** Accepted
- **Date:** 2026-10-09
- **Implements:** ADR-0061

## Context

`NotionBriefBoard`, `NotionEpisodeBoard` and `NotionReviewDesk` now exist. They duplicate
authentication, API version headers, JSON decoding, HTTP error handling and reference quoting.
ADR-0061's requirement for three written consumers is met.

## Decision

Move the identical transport and text/quoting helpers into `infrastructure/_notion.py`.
Use functions; no base class, client state or new dependency is needed. Each adapter keeps
its settings, public API, property validation and application rules.

The transport receives the adapter's existing exception type. Error messages and exception
chaining stay unchanged. Only the two boards opt into treating HTTP 404 as a missing page;
the review desk continues to raise `ReviewDeskError`. The existing exported API URL and
version constants remain available from all three adapter modules.

## Verification

Use the existing tests for all three adapters, which exercise real HTTP against local servers,
including headers, JSON failures, HTTP errors, pagination, writes and review decisions.
Run the repository quality gate. No new transport behavior is introduced.
