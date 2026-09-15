"""Unified Output finalization path — application-layer invocation of the Schema authority.

Evaluation-ownership model = **Variant A** (roles strictly split): **Schema decides** (the
authority owns the VALID/INVALID rule in ``Schema.validate``); **the application invokes** it here
(resolving/using a pre-bound ACTIVE Schema), obtaining a verdict; **Run persists** the verdict as a
**pure sink** (``Run.record_output``), which neither knows Schema nor decides. An INVALID result is
now **recorded** (Output ``INVALID``), not gated out.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from omemo_content_factory.application.schema_observability import (
    ValidationObserver,
    record_validation,
)
from omemo_content_factory.domain.output import OutputId
from omemo_content_factory.domain.run import Actor, Run
from omemo_content_factory.domain.schema import Schema
from omemo_content_factory.domain.task import TaskId


@dataclass(frozen=True, slots=True)
class SchemaBinding:
    """The authoritative association between an opaque reference and a Schema (ADR-0031).

    The Composition Root resolves this pair from its catalogues. Keeping the opaque handle beside
    the object avoids guessing a naming convention from ``SchemaId`` / ``SchemaVersion``.
    """

    schema_ref: str
    schema: Schema


def resolve_active_schema(schemas: Mapping[str, Schema], schema_ref: str) -> Schema:
    """Resolution layer (local, minimal): pick the Schema registered for ``schema_ref``.

    ``schemas`` is an injected, explicit registry (no global state, PROJECT.md §6). ACTIVE-ness is
    enforced downstream by ``Schema.validate`` (``SchemaNotActiveError``); this layer only maps a
    ref to its Schema.
    """
    return schemas[schema_ref]


def validate_and_record_output(
    run: Run,
    task_id: TaskId,
    *,
    schema_binding: SchemaBinding,
    payload_fields: Mapping[str, str],
    payload: str,
    by: Actor = Actor.CONTENT_DIRECTOR,
    observer: ValidationObserver | None = None,
) -> OutputId:
    """Unified finalization: **invoke** the Schema authority, then **persist** the verdict.

    The application invokes the pure ``schema_binding.schema.validate`` (the Schema authority
    decides), then hands the outcome to the **pure sink** ``Run.record_output``
    (``valid=verdict.is_valid``) to persist. The binding's trusted opaque reference is recorded;
    an executor cannot relabel the Schema that issued the verdict (ADR-0031). Run does not evaluate.
    The Output is **recorded** as VALID or INVALID per the verdict (an INVALID result is recorded,
    not gated out); the recorded Output's id is returned. The owning Task must already be
    ``SUCCEEDED`` (precondition of ``Run.record_output``).

    The optional ``observer`` receives a log-only trace of the decision (VALID/INVALID); it never
    affects the outcome (best-effort dispatch).
    """
    schema = schema_binding.schema
    verdict = schema.validate(payload_fields)
    record_validation(observer, schema, verdict, payload_fields)
    return run.record_output(
        task_id,
        payload=payload,
        schema_ref=schema_binding.schema_ref,
        by=by,
        valid=verdict.is_valid,
    )
