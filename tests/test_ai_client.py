"""AIClient + chat agent tests against a fake Anthropic client (no network)."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mealie_planner import ai_client as ai_mod
from mealie_planner.ai_client import AIClient, build_output_schema, effort_from_thinking_budget
from mealie_planner.exceptions import AIResponseError
from mealie_planner.models import (
    MealExclusions,
    ParsedIngredientList,
    RecipeNutritionImputation,
    StandardizedIngredients,
    WeeklyMealPlanResponse,
)


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_use_block(id_, name, input_):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def message(content, stop_reason="end_turn"):
    return SimpleNamespace(id="msg_test", content=content, stop_reason=stop_reason, stop_details=None)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    AIClient._instance = None
    c = AIClient()
    c.client = MagicMock()
    # Sonnet calls go through with_options(...).beta.messages; point both at the
    # same mock so every test can read one call log.
    c.client.with_options.return_value = c.client
    c.client.beta.messages = c.client.messages
    yield c
    AIClient._instance = None


def sent_kwargs(c):
    return c.client.messages.create.call_args.kwargs


# --- call(): request shape ---------------------------------------------------

def test_plain_text_call(client):
    client.client.messages.create.return_value = message([text_block("  Black Bean Tacos  ")])
    out = client.call("simplify", expect_json=False, temperature=0.4)
    assert out == "  Black Bean Tacos  "
    kw = sent_kwargs(client)
    assert kw["model"] == "claude-haiku-5-5"
    assert kw["messages"] == [{"role": "user", "content": "simplify"}]
    assert kw["output_config"] == {"effort": "low"}
    # Haiku 5.5 rejects non-default sampling params, and these are never sent.
    for banned in ("temperature", "top_p", "top_k", "thinking", "fallbacks", "system"):
        assert banned not in kw


def test_expect_json_without_schema_strips_fences(client):
    client.client.messages.create.return_value = message([text_block('```json\n[{"id": "a"}]\n```')])
    out = client.call("swap ideas", expect_json=True)
    assert json.loads(out) == [{"id": "a"}]
    kw = sent_kwargs(client)
    assert "JSON" in kw["system"]
    assert "format" not in kw["output_config"]


def test_object_schema_passes_through_unwrapped(client):
    payload = {"days": [{"date": "2026-10-20", "meals": {"breakfast": "Oats", "lunch": "x", "dinner": "y"}}]}
    client.client.messages.create.return_value = message([text_block(json.dumps(payload))])
    out = client.call("plan", response_schema=WeeklyMealPlanResponse, temperature=0.7)
    assert WeeklyMealPlanResponse.model_validate_json(out).days[0].meals.breakfast == "Oats"
    fmt = sent_kwargs(client)["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["type"] == "object"
    assert fmt["schema"]["additionalProperties"] is False


def test_array_root_schema_is_wrapped_and_unwrapped(client):
    client.client.messages.create.return_value = message(
        [text_block('{"result": [{"raw": "2 lb beef", "core_ingredient": "beef", "has_meat": true}]}')]
    )
    out = client.call("parse", response_schema=ParsedIngredientList)
    items = ParsedIngredientList.model_validate_json(out).root
    assert items[0].core_ingredient == "beef"
    schema = sent_kwargs(client)["output_config"]["format"]["schema"]
    assert schema["type"] == "object"
    assert schema["required"] == ["result"]
    assert schema["properties"]["result"]["type"] == "array"
    assert "$defs" in schema  # hoisted to the root so the $ref still resolves


def test_thinking_budget_maps_to_effort(client):
    client.client.messages.create.return_value = message([text_block("ok")])
    client.call("x", expect_json=False, thinking_budget=8192)
    assert sent_kwargs(client)["output_config"]["effort"] == "high"
    client.call("x", expect_json=False, effort="medium")
    assert sent_kwargs(client)["output_config"]["effort"] == "medium"


@pytest.mark.parametrize("budget,effort", [(None, "low"), (0, "low"), (1024, "medium"), (4096, "medium"), (24576, "high")])
def test_effort_from_thinking_budget(budget, effort):
    assert effort_from_thinking_budget(budget) == effort


def test_refusal_and_truncation_raise(client):
    client.client.messages.create.return_value = message([], stop_reason="refusal")
    with pytest.raises(AIResponseError):
        client.call("x")
    client.client.messages.create.return_value = message([text_block('{"days": [')], stop_reason="max_tokens")
    with pytest.raises(AIResponseError):
        client.call("x", response_schema=WeeklyMealPlanResponse)


# --- schema conversion -------------------------------------------------------

def test_enum_keyed_dict_becomes_optional_properties():
    schema, wrapped = build_output_schema(MealExclusions)
    assert not wrapped
    assert set(schema["properties"]) == {
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
    }
    assert schema["additionalProperties"] is False
    assert "required" not in schema
    assert schema["properties"]["Monday"]["items"]["enum"] == ["breakfast", "lunch", "dinner"]
    # The reply shape is unchanged: a sparse {day: [meals]} object.
    MealExclusions.model_validate_json('{"Monday": ["dinner"]}')


@pytest.mark.parametrize("model", [ParsedIngredientList, StandardizedIngredients, RecipeNutritionImputation,
                                   WeeklyMealPlanResponse, MealExclusions])
def test_every_app_schema_converts(model):
    schema, _ = build_output_schema(model)
    assert schema["type"] == "object"
    json.dumps(schema)


# --- chat agent tool loop ----------------------------------------------------

class FakeSession:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=f"{name} ok")], isError=False)


def test_build_claude_tools():
    from mealie_planner.mcp_agent import build_claude_tools

    tools_resp = SimpleNamespace(tools=[
        SimpleNamespace(name="get_recipes", description="List recipes",
                        inputSchema={"type": "object", "properties": {"page": {"type": "integer"}}}),
        SimpleNamespace(name="ping", description=None, inputSchema=None),
    ])
    tools = build_claude_tools(tools_resp)
    assert tools[0] == {"name": "get_recipes", "description": "List recipes",
                        "input_schema": {"type": "object", "properties": {"page": {"type": "integer"}}}}
    assert tools[1]["input_schema"] == {"type": "object", "properties": {}}


def test_chat_turn_runs_tools_and_returns_reply(client, monkeypatch):
    from mealie_planner import mcp_agent

    monkeypatch.setattr(mcp_agent, "get_system_prompt", lambda *a, **k: "SYSTEM")
    first = message([
        SimpleNamespace(type="thinking", thinking="", signature="sig"),
        tool_use_block("tu_1", "get_mealplans", {"start_date": "2026-10-20"}),
        tool_use_block("tu_2", "create_mealplan", {"date": "2026-10-21"}),
    ], stop_reason="tool_use")
    second = message([text_block("Done, tacos on Wednesday.")])
    client.client.messages.create.side_effect = [first, second]

    session = FakeSession()
    history = [{"role": "user", "content": "hi"}, {"role": "model", "content": "hello"}]
    reply, new_history, changed = asyncio.run(
        mcp_agent.run_chat_turn(session, [{"name": "t"}], history, "add tacos")
    )

    assert reply == "Done, tacos on Wednesday."
    assert changed is True
    assert [c[0] for c in session.calls] == ["get_mealplans", "create_mealplan"]
    assert new_history[-2:] == [{"role": "user", "content": "add tacos"},
                                {"role": "assistant", "content": "Done, tacos on Wednesday."}]

    calls = client.client.messages.create.call_args_list
    first_kw = calls[0].kwargs
    assert first_kw["system"] == "SYSTEM"
    assert first_kw["output_config"] == {"effort": "medium"}
    assert first_kw["tools"] == [{"name": "t"}]
    # Legacy "model" role is mapped to "assistant".
    assert [m["role"] for m in first_kw["messages"][:2]] == ["user", "assistant"]

    msgs = calls[1].kwargs["messages"]
    assert msgs[-2]["role"] == "assistant" and msgs[-2]["content"] is first.content
    results = msgs[-1]["content"]
    assert msgs[-1]["role"] == "user"
    assert [r["tool_use_id"] for r in results] == ["tu_1", "tu_2"]  # both in ONE user message
    assert all(r["type"] == "tool_result" and not r["is_error"] for r in results)


def test_chat_tool_exception_becomes_error_result(client, monkeypatch):
    from mealie_planner import mcp_agent

    monkeypatch.setattr(mcp_agent, "get_system_prompt", lambda *a, **k: "SYSTEM")

    class BoomSession:
        async def call_tool(self, name, args):
            raise RuntimeError("mealie down")

    client.client.messages.create.side_effect = [
        message([tool_use_block("tu_1", "delete_mealplan", {"id": "x"})], stop_reason="tool_use"),
        message([text_block("Sorry, that failed.")]),
    ]
    reply, _, changed = asyncio.run(mcp_agent.run_chat_turn(BoomSession(), [], [], "remove it"))
    assert reply == "Sorry, that failed."
    assert changed is False
    result = client.client.messages.create.call_args_list[1].kwargs["messages"][-1]["content"][0]
    assert result["is_error"] is True and "mealie down" in result["content"]


def test_module_has_no_gemini_leftovers():
    assert not hasattr(ai_mod, "requests")



def test_sonnet_calls_use_the_beta_endpoint_with_default_fallbacks(client):
    client.client.messages.create.return_value = message([text_block("ok")])
    client.call("hi", expect_json=False)
    assert "fallbacks" not in sent_kwargs(client) and sent_kwargs(client)["model"] == "claude-haiku-5-5"
    client.call("hi", expect_json=False, model="claude-sonnet-5-5", effort="medium")
    assert sent_kwargs(client)["fallbacks"] == "default"
    assert sent_kwargs(client)["betas"] == ["server-side-fallback-2026-07-01"]
