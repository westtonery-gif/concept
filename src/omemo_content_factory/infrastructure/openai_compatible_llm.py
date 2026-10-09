"""An ``LLMClient`` for any provider that speaks the OpenAI dialect (ADR-0094).

xAI (Grok), OpenAI, OpenRouter, Groq, Mistral, DeepSeek, Together, Ollama, LM Studio, vLLM … differ
in their base URL and key far more than in their wire format. This one adapter implements the
unchanged :class:`~omemo_content_factory.infrastructure.llm.LLMClient` port over two dialects —
``chat`` (``/chat/completions``) and ``responses`` (``/responses``) — with the same behaviour as the
Anthropic adapter: structured output through a private ``emit_fields`` function (ADR-0014), the
bounded Tool loop through the scoped ``Toolbox`` (ADR-0028), one measured and exactly priced
:class:`LLMCallMetrics` per completed turn (ADR-0029) and an :class:`LLMError` that keeps the
metrics of turns already paid for.

Plain stdlib ``urllib`` — no new dependency, like every other vendor adapter here. The API key is
handed to the constructor, sent as a bearer token and **never** printed: not in ``repr``, not in an
error. Nothing here judges the answer; an unusable one is empty fields for ``Schema.validate``
(ADR-0014 §2, Variant B).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol

from omemo_content_factory.domain.tool import ToolDescriptor
from omemo_content_factory.infrastructure.llm import (
    LLMCallMetrics,
    LLMCompletion,
    LLMError,
    TokenPricing,
)
from omemo_content_factory.tools.contract import ToolCall, ToolResult
from omemo_content_factory.tools.toolbox import Toolbox

__all__ = [
    "EFFORT_LEVELS",
    "OpenAICompatibleLLMClient",
    "Structured",
    "Wire",
]

EFFORT_LEVELS = ("low", "medium", "high")

_EMIT = "emit_fields"
_DEFAULT_MAX_TOOL_CALLS = 8
_TIMEOUT_SECONDS = 300.0
"""A model that reasons for a long time is not a hung connection; five minutes is."""
_RETRY_DELAYS = (1.0, 3.0)
_RETRIABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_JSON_SUFFIX = (
    "\n\nAnswer with a single JSON object and nothing else. Its keys are exactly: {names}. "
    "Every value is a string."
)


class Wire(Enum):
    """The two request/response dialects (ADR-0094 §2)."""

    CHAT = "chat"
    RESPONSES = "responses"


class Structured(Enum):
    """How structure is obtained from the model (ADR-0094 §3)."""

    TOOL = "tool"
    TOOL_REQUIRED = "tool-required"
    JSON = "json"


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class _Call:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class _Turn:
    """One provider answer, reduced to what the loop needs."""

    model: str
    text: str
    calls: tuple[_Call, ...]
    input_tokens: int
    output_tokens: int
    echo: tuple[dict[str, Any], ...]
    """Items to append to the history so the next request continues this answer."""


class _Dialect(Protocol):
    path: str

    def initial(self, user: str) -> list[dict[str, Any]]: ...

    def body(
        self,
        *,
        model: str,
        system: str,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        choice: str,
        max_tokens: int,
        token_param: str,
        effort: str | None,
        json_mode: bool,
    ) -> dict[str, Any]: ...

    def parse(self, raw: dict[str, Any]) -> _Turn: ...

    def results(self, answered: Sequence[tuple[_Call, ToolResult]]) -> list[dict[str, Any]]: ...


def _usage(raw: dict[str, Any]) -> dict[str, Any]:
    usage = raw.get("usage")
    return usage if isinstance(usage, dict) else {}


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


class _ChatDialect:
    """``POST /chat/completions`` — the lingua franca."""

    path = "/chat/completions"

    def initial(self, user: str) -> list[dict[str, Any]]:
        return [{"role": "user", "content": user}]

    def body(
        self,
        *,
        model: str,
        system: str,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        choice: str,
        max_tokens: int,
        token_param: str,
        effort: str | None,
        json_mode: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, *history],
            token_param: max_tokens,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["parameters"],
                    },
                }
                for t in tools
            ]
            if choice == "forced":
                body["tool_choice"] = {"type": "function", "function": {"name": _EMIT}}
            else:
                body["tool_choice"] = choice  # "required" | "auto"
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if effort:
            body["reasoning_effort"] = effort
        return body

    def parse(self, raw: dict[str, Any]) -> _Turn:
        choices = raw.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise LLMError("the provider's answer has no choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise LLMError("the provider's answer has no message")
        calls: list[_Call] = []
        for item in message.get("tool_calls") or []:
            function = item.get("function") if isinstance(item, dict) else None
            if isinstance(function, dict) and isinstance(function.get("name"), str):
                arguments = function.get("arguments")
                calls.append(
                    _Call(
                        id=str(item.get("id", "")),
                        name=function["name"],
                        arguments=arguments
                        if isinstance(arguments, str)
                        else json.dumps(arguments),
                    )
                )
        content = message.get("content")
        usage = _usage(raw)
        return _Turn(
            model=str(raw.get("model") or ""),
            text=content if isinstance(content, str) else "",
            calls=tuple(calls),
            input_tokens=_int(usage.get("prompt_tokens")),
            output_tokens=_int(usage.get("completion_tokens")),
            echo=(message,),
        )

    def results(self, answered: Sequence[tuple[_Call, ToolResult]]) -> list[dict[str, Any]]:
        return [
            {"role": "tool", "tool_call_id": call.id, "content": _result_text(result)}
            for call, result in answered
        ]


class _ResponsesDialect:
    """``POST /responses`` — resent statelessly, so no server-side conversation state is needed."""

    path = "/responses"

    def initial(self, user: str) -> list[dict[str, Any]]:
        return [{"role": "user", "content": user}]

    def body(
        self,
        *,
        model: str,
        system: str,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        choice: str,
        max_tokens: int,
        token_param: str,
        effort: str | None,
        json_mode: bool,
    ) -> dict[str, Any]:
        del token_param  # this dialect has one name for it
        body: dict[str, Any] = {
            "model": model,
            "instructions": system,
            "input": history,
            "max_output_tokens": max_tokens,
            "store": False,
        }
        if tools:
            body["tools"] = [{"type": "function", **t} for t in tools]
            body["tool_choice"] = (
                {"type": "function", "name": _EMIT} if choice == "forced" else choice
            )
        if json_mode:
            body["text"] = {"format": {"type": "json_object"}}
        if effort:
            body["reasoning"] = {"effort": effort}
        return body

    def parse(self, raw: dict[str, Any]) -> _Turn:
        output = raw.get("output")
        if not isinstance(output, list):
            raise LLMError("the provider's answer has no output")
        calls: list[_Call] = []
        text_parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "function_call" and isinstance(item.get("name"), str):
                arguments = item.get("arguments")
                calls.append(
                    _Call(
                        id=str(item.get("call_id") or item.get("id") or ""),
                        name=item["name"],
                        arguments=arguments
                        if isinstance(arguments, str)
                        else json.dumps(arguments),
                    )
                )
            elif item.get("type") == "message":
                for part in item.get("content") or []:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        text_parts.append(part["text"])
        usage = _usage(raw)
        return _Turn(
            model=str(raw.get("model") or ""),
            text="".join(text_parts),
            calls=tuple(calls),
            input_tokens=_int(usage.get("input_tokens")),
            output_tokens=_int(usage.get("output_tokens")),
            echo=tuple(item for item in output if isinstance(item, dict)),
        )

    def results(self, answered: Sequence[tuple[_Call, ToolResult]]) -> list[dict[str, Any]]:
        return [
            {"type": "function_call_output", "call_id": call.id, "output": _result_text(result)}
            for call, result in answered
        ]


_DIALECTS: dict[Wire, _Dialect] = {Wire.CHAT: _ChatDialect(), Wire.RESPONSES: _ResponsesDialect()}


@dataclass(frozen=True, slots=True)
class OpenAICompatibleLLMClient:
    """An :class:`LLMClient` for any OpenAI-dialect endpoint (ADR-0094)."""

    model: str
    pricing: TokenPricing
    max_tokens: int
    base_url: str
    provider: str = "openai-compatible"
    api_key: str | None = field(default=None, repr=False)
    wire: Wire = Wire.CHAT
    structured: Structured = Structured.TOOL
    effort: str | None = None
    token_param: str = "max_tokens"
    max_tool_calls: int = _DEFAULT_MAX_TOOL_CALLS
    timeout: float = _TIMEOUT_SECONDS
    clock: Callable[[], datetime] = field(default=_utc_now, repr=False, compare=False)
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("a model name must not be blank")
        if not self.provider.strip():
            raise ValueError("a provider label must not be blank")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("a base URL starts with http:// or https://")
        if isinstance(self.max_tokens, bool) or not isinstance(self.max_tokens, int):
            raise ValueError(f"max_tokens must be an int >= 1, got {self.max_tokens!r}")
        if self.max_tokens < 1:
            raise ValueError(f"max_tokens must be an int >= 1, got {self.max_tokens!r}")
        if (
            isinstance(self.max_tool_calls, bool)
            or not isinstance(self.max_tool_calls, int)
            or self.max_tool_calls < 1
        ):
            raise ValueError("max_tool_calls must be an int >= 1")
        if self.effort is not None and self.effort not in EFFORT_LEVELS:
            raise ValueError(f"effort is one of {', '.join(EFFORT_LEVELS)}")
        if not self.token_param.strip():
            raise ValueError("token_param must not be blank")

    # --- the port ------------------------------------------------------------------------

    def complete(
        self, *, system: str, user: str, fields: Sequence[str], toolbox: Toolbox
    ) -> LLMCompletion:
        dialect = _DIALECTS[self.wire]
        descriptors = toolbox.descriptors
        if any(d.tool_id == _EMIT for d in descriptors):
            raise LLMError(f"Tool name '{_EMIT}' is reserved for structured output")
        json_mode = self.structured is Structured.JSON
        tools = [_function(d) for d in descriptors]
        if not json_mode:
            tools.append(_finalizer(fields))
        else:
            system = system + _JSON_SUFFIX.format(names=", ".join(fields))
        history = dialect.initial(user)
        metrics: list[LLMCallMetrics] = []
        try:
            return self._converse(dialect, system, history, tools, toolbox, json_mode, metrics)
        except LLMError as exc:
            if exc.metrics or not metrics:
                raise
            raise LLMError(str(exc), metrics=tuple(metrics)) from exc

    def _converse(
        self,
        dialect: _Dialect,
        system: str,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        toolbox: Toolbox,
        json_mode: bool,
        metrics: list[LLMCallMetrics],
    ) -> LLMCompletion:
        """The bounded loop of ADR-0028: ask, run the Tools the model asked for, ask again."""
        has_tools = bool(toolbox.descriptors)
        used = 0
        while True:
            choice = self._choice(has_tools=has_tools, json_mode=json_mode)
            turn, measurement = self._turn(dialect, system, history, tools, choice, json_mode)
            metrics.append(measurement)
            done = self._final(turn, has_tools=has_tools, json_mode=json_mode, metrics=metrics)
            if done is not None:
                return done
            operational = [call for call in turn.calls if call.name != _EMIT]
            if used + len(operational) > self.max_tool_calls:
                raise LLMError(f"Tool call budget exhausted ({self.max_tool_calls} per task step)")
            answered = [
                (call, toolbox.invoke(ToolCall(call.name, _arguments(call))))
                for call in operational
            ]
            used += len(answered)
            history.extend(turn.echo)
            history.extend(dialect.results(answered))

    @staticmethod
    def _final(
        turn: _Turn, *, has_tools: bool, json_mode: bool, metrics: list[LLMCallMetrics]
    ) -> LLMCompletion | None:
        """The completion if this turn ends the conversation, ``None`` if Tools must be run."""
        if json_mode and not turn.calls:
            return LLMCompletion(_json_fields(turn.text), tuple(metrics))
        finals = [call for call in turn.calls if call.name == _EMIT]
        operational = [call for call in turn.calls if call.name != _EMIT]
        if finals:
            if operational or len(finals) != 1:
                raise LLMError("provider mixed the final structured output with other Tool calls")
            return LLMCompletion(_call_fields(finals[0]), tuple(metrics))
        if operational:
            return None
        if has_tools:
            raise LLMError("provider returned no Tool call in a required tool-use turn")
        # a forced single call that came back empty: Variant B, the Schema judges
        return LLMCompletion({}, tuple(metrics))

    # --- one provider turn ---------------------------------------------------------------

    def _choice(self, *, has_tools: bool, json_mode: bool) -> str:
        if json_mode:
            return "auto"
        if has_tools or self.structured is Structured.TOOL_REQUIRED:
            return "required"
        return "forced"

    def _turn(
        self,
        dialect: _Dialect,
        system: str,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        choice: str,
        json_mode: bool,
    ) -> tuple[_Turn, LLMCallMetrics]:
        body = dialect.body(
            model=self.model,
            system=system,
            history=history,
            tools=tools,
            choice=choice,
            max_tokens=self.max_tokens,
            token_param=self.token_param,
            effort=self.effort,
            json_mode=json_mode,
        )
        started_at = self.clock()
        raw = self._post(dialect.path, body)
        finished_at = self.clock()
        turn = dialect.parse(raw)
        measurement = LLMCallMetrics(
            provider=self.provider,
            model=turn.model or self.model,
            input_tokens=turn.input_tokens,
            output_tokens=turn.output_tokens,
            cost_amount=self.pricing.cost(
                input_tokens=turn.input_tokens, output_tokens=turn.output_tokens
            ),
            cost_currency=self.pricing.currency,
            started_at=started_at,
            finished_at=finished_at,
        )
        return turn, measurement

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """One POST with the bounded retry of ADR-0094 §8; every failure is an ``LLMError``."""
        url = self.base_url.rstrip("/") + path
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        for attempt in range(len(_RETRY_DELAYS) + 1):
            request = urllib.request.Request(url, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
            except urllib.error.HTTPError as error:
                if error.code in _RETRIABLE_STATUS and attempt < len(_RETRY_DELAYS):
                    self.sleep(_RETRY_DELAYS[attempt])
                    continue
                raise LLMError(
                    f"{self.provider} answered HTTP {error.code}: {_reason(error)}"
                ) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
                if attempt < len(_RETRY_DELAYS):
                    self.sleep(_RETRY_DELAYS[attempt])
                    continue
                raise LLMError(f"{self.provider} could not be reached: {error}") from None
            try:
                parsed = json.loads(raw)
            except ValueError:
                raise LLMError(f"{self.provider} answered something that is not JSON") from None
            if not isinstance(parsed, dict):
                raise LLMError(f"{self.provider} answered a JSON value that is not an object")
            return parsed
        raise LLMError(f"{self.provider} could not be reached")  # pragma: no cover


# --- helpers ---------------------------------------------------------------------------------


def _reason(error: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(error.read())
    except (ValueError, OSError):
        return str(error.reason)
    detail = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(detail, dict):
        detail = detail.get("message")
    return str(detail if detail else error.reason)[:300]


def _parameters(descriptor: ToolDescriptor) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for parameter in descriptor.parameters:
        properties[parameter.name] = {
            "type": parameter.kind.value,
            "description": parameter.description,
        }
        if parameter.required:
            required.append(parameter.name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _function(descriptor: ToolDescriptor) -> dict[str, Any]:
    return {
        "name": descriptor.tool_id,
        "description": descriptor.description,
        "parameters": _parameters(descriptor),
    }


def _finalizer(fields: Sequence[str]) -> dict[str, Any]:
    """The adapter-private finalizer; never part of an Agent grant (ADR-0028 §2)."""
    return {
        "name": _EMIT,
        "description": "Return a value for each requested field.",
        "parameters": {
            "type": "object",
            "properties": {name: {"type": "string"} for name in fields},
            "required": list(fields),
        },
    }


def _result_text(result: ToolResult) -> str:
    return json.dumps(
        {"status": result.status.value, "data": dict(result.data), "error": result.error},
        ensure_ascii=False,
        sort_keys=True,
    )


def _arguments(call: _Call) -> dict[str, object]:
    try:
        parsed = json.loads(call.arguments) if call.arguments.strip() else {}
    except ValueError:
        raise LLMError(f"provider sent unparseable arguments for Tool '{call.name}'") from None
    if not isinstance(parsed, dict):
        raise LLMError(f"provider sent non-object arguments for Tool '{call.name}'")
    return parsed


def _call_fields(call: _Call) -> dict[str, str]:
    """The final call's arguments as ``name -> value``; unusable means empty (Variant B)."""
    try:
        parsed = json.loads(call.arguments)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(name): _text(value) for name, value in parsed.items()}


def _json_fields(text: str) -> dict[str, str]:
    """The ``json`` mode answer; tolerant of a fenced block, otherwise empty (Variant B)."""
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body.removeprefix("json").strip()
    try:
        parsed = json.loads(body)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(name): _text(value) for name, value in parsed.items()}


def _text(value: object) -> str:
    """Fields are strings (the port is uniformly structured); a nested value keeps its JSON form."""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
