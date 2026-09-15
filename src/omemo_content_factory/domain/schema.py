"""Schema domain model — a versioned contract at an agent boundary.

A standalone aggregate root of the definitions catalogue (DOMAIN_MODEL.md §2.8, §9.5): it has
independent identity and versioning, is **referenced** by other entities (Output via
``schema_ref``) but **owned by none**, and exists outside and before any Run. It is created via
its own factory ``Schema.create`` (ADR-0008 §2) — not through Run — and is **immutable except for
its own status lifecycle**.

This module depends on **nothing** in ``run``/``task``/``output`` (one-directional: ``run`` may
reference Schema, never the reverse), so there is no import cycle.

Scope note (ADR-0008): the **minimal faithful subset** of the documented Schema. The contract is a
set of required field names; validation is the deterministic, dependency-free system-side check
(PROJECT.md §9). Richer typing, optional fields, migration rules and a multi-version container are
deferred; only stdlib is used.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from omemo_content_factory.domain.errors import DomainError

SchemaId: TypeAlias = str
"""Opaque, stable identifier of the logical contract (ADR-0008 §3); shared across its versions."""


# --- Value Objects -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SchemaVersion:
    """A version of a Schema (DOMAIN_MODEL.md §11): a Value Object, compared by value ("v3 == v3").

    Has no identity and no lifecycle of its own; a new revision is a new value.
    """

    value: int


@dataclass(frozen=True, slots=True)
class SchemaValidation:
    """The verdict of validating a result against a Schema (ADR-0008 §6).

    ``is_valid`` is the bivalent outcome; ``missing_fields`` lists the required fields that were
    absent or empty. It is a normal result, never an error.
    """

    is_valid: bool
    missing_fields: tuple[str, ...]


class SchemaStatus(Enum):
    """The three lifecycle states of a Schema version (DOMAIN_MODEL.md §2.8; ADR-0008 §4).

    A version is created ``DRAFT``; allowed edges are ``DRAFT -> ACTIVE -> DEPRECATED``.
    Validation is admissible only against an ``ACTIVE`` version.
    """

    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


# --- Read-only snapshot ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SchemaView:
    """Immutable, read-only snapshot of a Schema version (ADR-0008 §4). Point-in-time."""

    schema_id: SchemaId
    version: SchemaVersion
    description: str
    required_fields: tuple[str, ...]
    status: SchemaStatus


# --- Domain errors -----------------------------------------------------------------------
# Schema domain-rule violations (ADR-0008 §11). Rooted at the shared ``DomainError`` (ADR-0017).


class SchemaDomainError(DomainError):
    """Base class for all Schema domain-rule violations."""


class InvalidSchemaTransitionError(SchemaDomainError):
    """A requested Schema status transition is not allowed (ADR-0008 §4)."""


class SchemaNotActiveError(SchemaDomainError):
    """Validation was requested against a version that is not ``ACTIVE`` (ADR-0008 §6)."""


class ImmutableSchemaAttributeError(SchemaDomainError):
    """An attempt was made to change an immutable attribute of a Schema (everything but status)."""


# --- Allowed transitions -----------------------------------------------------------------

_ALLOWED_SCHEMA_TRANSITIONS: dict[SchemaStatus, set[SchemaStatus]] = {
    SchemaStatus.DRAFT: {SchemaStatus.ACTIVE},
    SchemaStatus.ACTIVE: {SchemaStatus.DEPRECATED},
    SchemaStatus.DEPRECATED: set(),
}

# Immutable public attributes AND their private backing fields: everything except ``status``.
_IMMUTABLE_SCHEMA_ATTRIBUTES = frozenset(
    {
        "schema_id",
        "version",
        "description",
        "required_fields",
        "_schema_id",
        "_version",
        "_description",
        "_required_fields",
    }
)


# --- Schema aggregate root ---------------------------------------------------------------


class Schema:
    """The Schema aggregate root — a versioned boundary contract (ADR-0008 §2).

    Created via the ``create`` factory and mutated (status only) via ``transition``. ``__slots__``
    + a guarded ``__setattr__`` make every field but ``status`` write-once.
    """

    __slots__ = (
        "_description",
        "_required_fields",
        "_schema_id",
        "_status",
        "_version",
    )

    _schema_id: SchemaId
    _version: SchemaVersion
    _description: str
    _required_fields: tuple[str, ...]
    _status: SchemaStatus

    def __init__(
        self,
        schema_id: SchemaId,
        version: SchemaVersion,
        description: str,
        required_fields: tuple[str, ...],
    ) -> None:
        # Immutable fields are written exactly once, here, bypassing the guard below.
        object.__setattr__(self, "_schema_id", schema_id)
        object.__setattr__(self, "_version", version)
        object.__setattr__(self, "_description", description)
        object.__setattr__(self, "_required_fields", required_fields)
        # The status lifecycle is the only mutable state; it flows through the guarded setter.
        self._status = SchemaStatus.DRAFT

    @classmethod
    def create(
        cls,
        *,
        schema_id: SchemaId,
        version: SchemaVersion,
        description: str,
        required_fields: Iterable[str],
    ) -> Schema:
        """Create a new Schema version in ``DRAFT`` (ADR-0008 §2, §4)."""
        return cls(schema_id, version, description, tuple(required_fields))

    def __setattr__(self, name: str, value: object) -> None:
        """Reject reassignment of every attribute except ``status`` (ADR-0008 §3)."""
        if name in _IMMUTABLE_SCHEMA_ATTRIBUTES:
            raise ImmutableSchemaAttributeError(f"'{name}' is immutable after creation")
        super().__setattr__(name, value)

    @property
    def view(self) -> SchemaView:
        """A read-only snapshot of the current state (ADR-0008 §4)."""
        return SchemaView(
            schema_id=self._schema_id,
            version=self._version,
            description=self._description,
            required_fields=self._required_fields,
            status=self._status,
        )

    def transition(self, to: SchemaStatus) -> None:
        """Apply a guarded status transition ``DRAFT -> ACTIVE -> DEPRECATED`` (ADR-0008 §4).

        Any edge outside the allowed table raises ``InvalidSchemaTransitionError`` and leaves the
        status unchanged.
        """
        if to not in _ALLOWED_SCHEMA_TRANSITIONS[self._status]:
            raise InvalidSchemaTransitionError(f"transition {self._status} -> {to} is not allowed")
        self._status = to

    def validate(self, payload_fields: Mapping[str, str]) -> SchemaValidation:
        """Validate a structured result against this contract (ADR-0008 §6; SCHEMA_SPEC §4).

        Admissible only against an ``ACTIVE`` version (else ``SchemaNotActiveError``). The result
        is valid iff every required field is present and non-empty; otherwise invalid. Deterministic
        and free of external dependencies; the verdict is a normal outcome, not an error.
        """
        if self._status is not SchemaStatus.ACTIVE:
            raise SchemaNotActiveError(f"validation requires an ACTIVE schema, not {self._status}")
        missing = tuple(
            name for name in self._required_fields if payload_fields.get(name, "") == ""
        )
        return SchemaValidation(is_valid=not missing, missing_fields=missing)
