"""Anthropic Claude client used by every AI feature in the app.

One model everywhere (``AI_MODEL``, default ``claude-haiku-5-5``). Two entry points:

* ``call()``: single-prompt text or JSON completion (plan generation, parsing,
  nutrition imputation, email copy, categorisation, ...).
* ``generate()``: multi-turn Messages API call with tools, used by the MCP chat agent.

Credentials come from ``ANTHROPIC_API_KEY`` (read by the SDK itself).
"""
import copy
import json
import logging
import re
from typing import Any, Optional

import anthropic

from .exceptions import AIResponseError, ConfigurationError

logger = logging.getLogger(__name__)

# Non-streaming requests: generous enough for a week plan or a merged shopping list.
DEFAULT_MAX_TOKENS = 16000
REQUEST_TIMEOUT_SECONDS = 180
# Sonnet at medium effort over a full recipe catalogue can take minutes.
LONG_TIMEOUT_SECONDS = 600

# Models that accept server-side fallbacks="default" (Claude API only); Haiku has none.
FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_RETRIES = 3

_JSON_ONLY_INSTRUCTION = (
    "Respond with a single valid JSON value only: no prose, no explanations, no markdown code fences."
)

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)


def effort_from_thinking_budget(thinking_budget: Optional[int]) -> str:
    """Map a legacy Gemini ``thinkingBudget`` (tokens; 0 = thinking off) to a Claude effort level.

    Claude Haiku 5.5 always thinks adaptively and rejects fixed token budgets, so
    ``output_config.effort`` is the only depth control.
    """
    if not thinking_budget or thinking_budget <= 0:
        return "low"
    if thinking_budget <= 4096:
        return "medium"
    return "high"


def _strip_code_fences(text: str) -> str:
    match = _FENCE_RE.match(text)
    return match.group(1).strip() if match else text.strip()


def _schema_dict(response_schema) -> dict:
    if hasattr(response_schema, "model_json_schema"):
        return response_schema.model_json_schema()
    if isinstance(response_schema, dict):
        return copy.deepcopy(response_schema)
    raise TypeError(f"Unsupported response_schema: {response_schema!r}")


def _expand_enum_keyed_dicts(node):
    """Turn ``Dict[Literal[...], X]`` schemas into objects with explicit optional properties.

    Pydantic emits these as ``propertyNames: {enum}`` + ``additionalProperties: X``,
    which structured outputs cannot express (it forces ``additionalProperties: false``).
    Listing each allowed key as an optional property keeps the same JSON shape.
    """
    if isinstance(node, list):
        return [_expand_enum_keyed_dicts(n) for n in node]
    if not isinstance(node, dict):
        return node
    node = {k: _expand_enum_keyed_dicts(v) for k, v in node.items()}
    names = node.get("propertyNames")
    extra = node.get("additionalProperties")
    if (
        node.get("type") == "object"
        and isinstance(names, dict)
        and isinstance(names.get("enum"), list)
        and isinstance(extra, dict)
    ):
        node.pop("propertyNames")
        node.pop("additionalProperties")
        props = node.setdefault("properties", {})
        for key in names["enum"]:
            props.setdefault(key, copy.deepcopy(extra))
    return node


def build_output_schema(response_schema) -> tuple[dict, bool]:
    """Build a structured-outputs JSON schema from a Pydantic model or dict.

    Returns ``(schema, wrapped)``. Non-object roots (e.g. ``RootModel[List[...]]``)
    are wrapped as ``{"result": <root>}`` so the request always has an object root;
    ``wrapped`` tells the caller to unwrap the response.
    """
    schema = _expand_enum_keyed_dicts(_schema_dict(response_schema))
    wrapped = schema.get("type") != "object"
    if wrapped:
        defs = schema.pop("$defs", None)
        schema = {
            "type": "object",
            "properties": {"result": schema},
            "required": ["result"],
        }
        if defs is not None:
            schema["$defs"] = defs
    return anthropic.transform_schema(schema), wrapped


def _response_text(message) -> str:
    return "".join(block.text for block in message.content if block.type == "text")


def _check_stop_reason(message) -> None:
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        raise AIResponseError(f"Claude declined the request (refusal, category={category}).")
    if message.stop_reason == "max_tokens":
        raise AIResponseError("Claude response was truncated at max_tokens.")


class AIClient:
    """Process-wide Claude client (singleton, like the Mealie client)."""

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, api_key: Optional[str] = None, model_name: Optional[str] = None):
        if self._initialized and api_key is None and model_name is None:
            return

        from .config import AI_MODEL, CHAT_EFFORT, CORE_EFFORT

        self.model_name = model_name or AI_MODEL
        self.core_effort = CORE_EFFORT
        self.chat_effort = CHAT_EFFORT
        try:
            # api_key=None lets the SDK resolve ANTHROPIC_API_KEY (or another
            # configured credential source) from the environment.
            self.client = anthropic.Anthropic(
                api_key=api_key,
                timeout=REQUEST_TIMEOUT_SECONDS,
                max_retries=MAX_RETRIES,
            )
        except anthropic.AnthropicError as e:
            raise ConfigurationError(f"Anthropic client could not be configured (set ANTHROPIC_API_KEY): {e}") from e
        self._initialized = True

    def _create(self, kwargs: dict[str, Any]):
        """messages.create, or the beta endpoint with fallbacks="default" for models that
        support it: a safety-classifier decline is then rerun on another model in-request."""
        if kwargs["model"] in FALLBACK_MODELS:
            client = self.client.with_options(timeout=LONG_TIMEOUT_SECONDS)
            return client.beta.messages.create(**kwargs, betas=[FALLBACK_BETA], fallbacks="default")
        return self.client.messages.create(**kwargs)

    def call(
        self,
        prompt: str,
        expect_json: bool = True,
        temperature: Optional[float] = None,
        thinking_budget: Optional[int] = None,
        response_schema=None,
        effort: Optional[str] = None,
        model: Optional[str] = None,
    ) -> str:
        """Send one prompt and return the response text.

        * ``response_schema`` (Pydantic model or JSON schema dict): the reply is
          constrained by structured outputs and returned as a JSON string that
          validates against the original schema.
        * ``expect_json`` without a schema: the model is told to answer with bare
          JSON; code fences are stripped defensively.
        * ``temperature`` is accepted for call-site compatibility but not sent:
          Claude Haiku 5.5 rejects non-default sampling parameters.
        * ``thinking_budget`` (legacy Gemini knob) maps onto ``effort`` when
          ``effort`` is not given; with neither, ``CORE_EFFORT`` applies.
        """
        if temperature is not None:
            logger.debug("temperature=%s ignored (not supported by %s)", temperature, self.model_name)
        if effort is None:
            effort = effort_from_thinking_budget(thinking_budget) if thinking_budget is not None else self.core_effort

        output_config: dict[str, Any] = {"effort": effort}
        wrapped = False
        system = None
        if response_schema is not None:
            schema, wrapped = build_output_schema(response_schema)
            output_config["format"] = {"type": "json_schema", "schema": schema}
        elif expect_json:
            system = _JSON_ONLY_INSTRUCTION

        kwargs: dict[str, Any] = {
            "model": model or self.model_name,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": output_config,
        }
        if system:
            kwargs["system"] = system

        logger.debug("AI prompt: %s", prompt[:500] + "..." if len(prompt) > 500 else prompt)
        try:
            message = self._create(kwargs)
        except anthropic.APIError as e:
            logger.error("Claude API call failed: %s", e)
            raise
        logger.debug("AI response id: %s (stop_reason=%s)", message.id, message.stop_reason)
        _check_stop_reason(message)

        text = _response_text(message)
        if response_schema is not None:
            if wrapped:
                return json.dumps(json.loads(text)["result"])
            return text
        if expect_json:
            return _strip_code_fences(text)
        return text

    def generate(
        self,
        messages: list,
        system: Optional[str] = None,
        tools: Optional[list] = None,
        model: Optional[str] = None,
        effort: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        """Multi-turn Messages API call (tool use allowed). Returns the SDK ``Message``.

        ``messages`` and ``tools`` are in Anthropic format; the caller owns the
        tool loop (see ``mcp_agent.run_chat_turn``).
        """
        kwargs: dict[str, Any] = {
            "model": model or self.model_name,
            "max_tokens": max_tokens,
            "messages": messages,
            "output_config": {"effort": effort or self.chat_effort},
            # The tool loop resends the same tools + system + growing history on
            # every iteration; auto-caching the last block makes those re-reads cheap.
            "cache_control": {"type": "ephemeral"},
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools
        message = self._create(kwargs)
        if message.stop_reason == "refusal":
            _check_stop_reason(message)
        return message
