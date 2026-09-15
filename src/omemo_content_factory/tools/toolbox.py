"""The ``Toolbox`` — the Tools one agent is granted, and its model's only way to reach a Tool.

ADR-0022 §4. The Composition Root builds one Toolbox per agent from the agent's grant
(``Agent.tool_refs``) and the available Tool instances. The grant is checked there, so a
misconfigured grant fails before anything runs. At reasoning time the Toolbox:

- exposes only the granted Tools' descriptors (what the model is shown);
- answers every ``ToolCall`` with a ``ToolResult``. A call to a Tool outside the grant, or with
  arguments that break the declared parameters, is ``REFUSED`` and the Tool never runs (fail
  closed); a ``ToolExecutionError`` becomes ``FAILED``. Model output therefore never raises out of
  a reasoning step and never reaches past the grant.

The Toolbox takes refs, not an ``Agent``: the tools layer stays blind to agents.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType

from omemo_content_factory.domain.tool import (
    ToolDescriptor,
    ToolGrantError,
    ToolId,
    ToolParameterType,
    ToolRef,
)
from omemo_content_factory.tools.contract import (
    Tool,
    ToolCall,
    ToolExecutionError,
    ToolResult,
    ToolValue,
)


class Toolbox:
    """The granted Tools of one agent, keyed by the model-facing name (``tool_id``)."""

    __slots__ = ("_tools",)

    def __init__(self, *, grants: Iterable[ToolRef], available: Iterable[Tool]) -> None:
        if isinstance(grants, str):
            raise ToolGrantError("grants must be a collection of Tool refs, not a single str")
        by_ref = _index_by_ref(available)
        granted: dict[ToolId, Tool] = {}
        for ref in grants:
            tool = by_ref.get(ref)
            if tool is None:
                raise ToolGrantError(f"granted Tool '{ref}' is not among the available Tools")
            tool_id = tool.descriptor.tool_id
            if tool_id in granted:
                raise ToolGrantError(
                    f"Tool '{tool_id}' is granted more than once; grant one version per Tool"
                )
            granted[tool_id] = tool
        self._tools: Mapping[ToolId, Tool] = MappingProxyType(granted)

    @property
    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        """The granted Tools' descriptors, in grant order — all the model is ever shown."""
        return tuple(tool.descriptor for tool in self._tools.values())

    def invoke(self, call: ToolCall) -> ToolResult:
        """Answer one model call; the Tool runs only if granted and the call is well-formed."""
        tool = self._tools.get(call.name) if isinstance(call.name, str) else None
        if tool is None:
            granted = ", ".join(self._tools) or "none"
            return ToolResult.refused(
                f"Tool {call.name!r} is not granted to this agent (granted: {granted})"
            )
        checked = _check_arguments(tool.descriptor, call.arguments)
        if isinstance(checked, str):
            return ToolResult.refused(f"Tool '{call.name}' refused the call: {checked}")
        try:
            data = tool.invoke(MappingProxyType(checked))
        except ToolExecutionError as exc:
            return ToolResult.failed(f"Tool '{call.name}' failed: {exc}")
        return ToolResult.ok(data)


def _index_by_ref(available: Iterable[Tool]) -> dict[ToolRef, Tool]:
    by_ref: dict[ToolRef, Tool] = {}
    for tool in available:
        ref = tool.descriptor.ref
        if ref in by_ref:
            raise ToolGrantError(f"two available Tools share the ref '{ref}'")
        by_ref[ref] = tool
    return by_ref


def _check_arguments(descriptor: ToolDescriptor, raw: object) -> dict[str, ToolValue] | str:
    """The call's arguments checked against the declared parameters, or why they are refused."""
    if not isinstance(raw, Mapping):
        return "arguments must be an object of named values"
    declared = {parameter.name for parameter in descriptor.parameters}
    unknown = sorted(str(name) for name in raw if name not in declared)
    if unknown:
        return f"unknown argument(s): {', '.join(unknown)}"
    checked: dict[str, ToolValue] = {}
    for parameter in descriptor.parameters:
        if parameter.name not in raw:
            if parameter.required:
                return f"missing required argument '{parameter.name}'"
            continue
        value = raw[parameter.name]
        if not _is_kind(value, parameter.kind):
            return (
                f"argument '{parameter.name}' must be a {parameter.kind.value}, "
                f"got {type(value).__name__}"
            )
        checked[parameter.name] = value
    return checked


def _is_kind(value: object, kind: ToolParameterType) -> bool:
    if kind is ToolParameterType.BOOLEAN:
        return isinstance(value, bool)
    if kind is ToolParameterType.INTEGER:
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, str)
