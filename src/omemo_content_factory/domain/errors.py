"""Shared root of every domain-rule violation (ADR-0017).

``DomainError`` is the single common base of the per-aggregate error hierarchies (Run, Task,
Output, Artifact, Human Review, Schema, Workflow). It carries **no behaviour, no attributes and
no API** — it only lets a caller tell "a domain rule was violated" apart from a technical failure
(``LLMError``, ``CompositionError``, ``ProviderModelSelectionError`` stay outside it) with one
``except DomainError``.

Each aggregate keeps its own base (e.g. ``RunDomainError``) and its concrete errors unchanged;
only their common ancestor is new. This module imports nothing, so every domain module may depend
on it without creating an import cycle.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for all domain-rule violations, across every aggregate (ADR-0017)."""
