"""Agent definitions — concrete role assets (Agent + Prompt + Schema) owned by the factory.

Each module here holds the **static definition** of one production role: its :class:`Agent`
descriptor, its versioned :class:`Prompt` artifact (PROJECT.md §15) and the :class:`Schema` its
Output conforms to (§18; ADR-0010/0011/0008). These are the catalogues the Composition Root
consumes (``build_content_director(agents, prompts, client, schemas)``) — data, not execution.

Layout is intentionally **flat and named after the agent** (one module per role): no content-type
taxonomy, no runtime catalogue machinery, no Content Type entity (all deferred,
ARCHITECTURE_FREEZE §3). Adding a role = adding a module of existing types (PROJECT.md §4 п.11),
never changing the core.
"""
