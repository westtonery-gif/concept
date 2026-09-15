"""JSON codec for ``RunSnapshot``: the stored form of a Run's truth (ADR-0024 §3).

A snapshot becomes one JSON-compatible document, ``{"format": 1, "run": {...}}``, and comes back
as an equal snapshot. The snapshot's own type hints drive the codec, so a field added to a snapshot
type is written and read without touching this module. Beyond JSON's own types it knows exactly
four:

- ``Enum``: stored as its value;
- ``Decimal``: stored as a string, so a cost stays exact;
- timezone-aware ``datetime``: stored as ISO 8601 with its offset;
- frozen dataclasses: stored as an object of their fields.

Journal events are the one polymorphic field. Each is written with its class name under ``"type"``
and read back through :data:`EVENT_TYPES`. An event class missing from that registry fails on
save, instead of losing its type on load.

Stored data that is not a well-formed document of a known format raises
:class:`SnapshotFormatError`. A value the domain itself refuses while a snapshot is rebuilt (for
example a negative ``Cost``) raises that domain error, unmasked (ADAPTER_SPEC §3 п.4).
"""

from __future__ import annotations

import dataclasses
import types
import typing
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from omemo_content_factory.domain.analytics import AnalyticsRecordCaptured
from omemo_content_factory.domain.artifact import ArtifactCreated
from omemo_content_factory.domain.evaluation import EvaluationCompleted
from omemo_content_factory.domain.human_review import (
    HumanReviewApproved,
    HumanReviewRejected,
    HumanReviewRequested,
)
from omemo_content_factory.domain.output import OutputValidated
from omemo_content_factory.domain.run import (
    RunCompleted,
    RunCreated,
    RunFailed,
    RunQueued,
    RunSnapshot,
    RunStarted,
)
from omemo_content_factory.domain.task import TaskCompleted, TaskCreated, TaskFailed, TaskStarted

FORMAT_VERSION = 1
"""Version of the stored document layout. A document of another version is refused, not guessed."""

EVENT_TYPES: Mapping[str, type[Any]] = {
    event_type.__name__: event_type
    for event_type in (
        RunCreated,
        RunQueued,
        RunStarted,
        RunCompleted,
        RunFailed,
        TaskCreated,
        TaskStarted,
        TaskCompleted,
        TaskFailed,
        OutputValidated,
        ArtifactCreated,
        HumanReviewRequested,
        HumanReviewApproved,
        HumanReviewRejected,
        EvaluationCompleted,
        AnalyticsRecordCaptured,
    )
}
"""Every event class a Run journal can hold, by the name it is stored under."""

_NONE = type(None)


class SnapshotFormatError(ValueError):
    """Stored data is not a well-formed snapshot document of a known format (ADR-0024 §4)."""


def encode_snapshot(snapshot: RunSnapshot) -> dict[str, Any]:
    """Turn ``snapshot`` into a JSON-compatible document of the current format."""
    return {"format": FORMAT_VERSION, "run": _encode(RunSnapshot, snapshot)}


def decode_snapshot(document: object) -> RunSnapshot:
    """Rebuild the snapshot held by ``document``; ``SnapshotFormatError`` if it is malformed."""
    if not isinstance(document, dict) or set(document) != {"format", "run"}:
        raise SnapshotFormatError("a stored Run must be an object with 'format' and 'run'")
    stored_format = document["format"]
    if type(stored_format) is not int or stored_format != FORMAT_VERSION:
        raise SnapshotFormatError(f"unknown stored format {stored_format!r}")
    try:
        snapshot: RunSnapshot = _decode(RunSnapshot, document["run"])
    except SnapshotFormatError:
        raise
    except (TypeError, ValueError, KeyError, ArithmeticError) as error:
        raise SnapshotFormatError(f"malformed stored Run: {error}") from error
    return snapshot


def _encode(hint: Any, value: Any) -> Any:
    """Encode ``value`` in the stored form of its declared type ``hint``."""
    origin = typing.get_origin(hint)
    if origin is typing.Union or origin is types.UnionType:
        return _encode_union(typing.get_args(hint), value)
    if origin is tuple:
        return [_encode(typing.get_args(hint)[0], item) for item in value]
    if _is_dataclass_type(hint):
        fields = _field_hints(hint)
        return {name: _encode(field, getattr(value, name)) for name, field in fields.items()}
    if isinstance(hint, type) and issubclass(hint, Enum):
        return value.value
    if hint is Decimal:
        return str(value)
    if hint is datetime:
        return value.isoformat()
    if hint is str or hint is int:
        return value
    raise SnapshotFormatError(f"no stored form for {hint!r}")


def _encode_union(options: tuple[Any, ...], value: Any) -> Any:
    """Encode an optional value, or a journal event tagged with its registered class name."""
    if value is None and _NONE in options:
        return None
    present = [option for option in options if option is not _NONE]
    if len(present) == 1:
        return _encode(present[0], value)
    event_type = type(value)
    if EVENT_TYPES.get(event_type.__name__) is not event_type:
        raise SnapshotFormatError(f"{event_type.__name__} is not a registered journal event")
    return {"type": event_type.__name__, **_encode(event_type, value)}


def _decode(hint: Any, raw: Any) -> Any:
    """Rebuild a value of the declared type ``hint`` from its stored form ``raw``."""
    origin = typing.get_origin(hint)
    if origin is typing.Union or origin is types.UnionType:
        return _decode_union(typing.get_args(hint), raw)
    if origin is tuple:
        return tuple(_decode(typing.get_args(hint)[0], item) for item in _expect(list, raw))
    if _is_dataclass_type(hint):
        return _decode_fields(hint, _expect(dict, raw))
    if isinstance(hint, type) and issubclass(hint, Enum):
        return hint(raw)
    if hint is Decimal:
        return Decimal(_expect(str, raw))
    if hint is datetime:
        return datetime.fromisoformat(_expect(str, raw))
    if hint is str or hint is int:
        return _expect(hint, raw)
    raise SnapshotFormatError(f"no stored form for {hint!r}")


def _decode_union(options: tuple[Any, ...], raw: Any) -> Any:
    """Rebuild an optional value, or a journal event from its ``"type"`` tag."""
    if raw is None and _NONE in options:
        return None
    present = [option for option in options if option is not _NONE]
    if len(present) == 1:
        return _decode(present[0], raw)
    data = dict(_expect(dict, raw))
    name = data.pop("type", None)
    event_type = EVENT_TYPES.get(name) if isinstance(name, str) else None
    if event_type is None or not issubclass(event_type, tuple(present)):
        raise SnapshotFormatError(f"unknown journal event type {name!r}")
    return _decode_fields(event_type, data)


def _decode_fields(cls: type[Any], data: dict[str, Any]) -> Any:
    """Construct the dataclass ``cls`` from exactly its fields, each rebuilt from ``data``."""
    fields = _field_hints(cls)
    if set(data) != set(fields):
        raise SnapshotFormatError(f"{cls.__name__} needs {sorted(fields)}, got {sorted(data)}")
    return cls(**{name: _decode(field, data[name]) for name, field in fields.items()})


def _expect(kind: type[Any], raw: Any) -> Any:
    """Return ``raw`` if it is a ``kind`` (a ``bool`` is not an ``int`` here), else refuse it."""
    if not isinstance(raw, kind) or (kind is int and isinstance(raw, bool)):
        raise SnapshotFormatError(f"expected {kind.__name__}, got {raw!r}")
    return raw


def _is_dataclass_type(hint: Any) -> bool:
    return isinstance(hint, type) and dataclasses.is_dataclass(hint)


def _field_hints(cls: type[Any]) -> Mapping[str, Any]:
    """The resolved type of each field of the dataclass ``cls``, in declaration order."""
    hints = typing.get_type_hints(cls)
    return {field.name: hints[field.name] for field in dataclasses.fields(cls)}
