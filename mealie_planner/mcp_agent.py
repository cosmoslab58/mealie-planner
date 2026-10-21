import os
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Load env variables relative to project root
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(base_dir, '.env'))

import logging

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mealie_planner.ai_client import AIClient
from mealie_planner.config import (
    ACTIVE_CHAT_MODEL,
    CHATBOT_GUIDELINES_PROMPT,
    CHATBOT_SYSTEM_PROMPT_TEMPLATE,
    FAMILY_DIETARY_RULES_PROMPT,
    FAMILY_NAMES,
    TIMEZONE,
)

logger = logging.getLogger(__name__)

def get_system_prompt(week_start_str=None, week_end_str=None):
    tz = ZoneInfo(TIMEZONE)
    now_str = datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S (%A)')
    if week_start_str and week_end_str:
        now_str += f"\nThe user is currently viewing/managing the week from {week_start_str} to {week_end_str}."
    
    return CHATBOT_SYSTEM_PROMPT_TEMPLATE.format(
        FAMILY_DIETARY_RULES_PROMPT=FAMILY_DIETARY_RULES_PROMPT,
        TIMEZONE=TIMEZONE,
        FAMILY_NAMES=FAMILY_NAMES,
        now_str=now_str,
        CHATBOT_GUIDELINES_PROMPT=CHATBOT_GUIDELINES_PROMPT
    )

def get_server_params():
    """Build the stdio parameters used to launch the MCP tool server subprocess."""
    mcp_src_dir = os.path.join(base_dir, "mealie-mcp-server", "src")
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": f"{mcp_src_dir}:{base_dir}",
        "MEALIE_BASE_URL": os.getenv("MEALIE_API_URL", "http://mealie:9000"),
        "MEALIE_API_KEY": os.getenv("MEALIE_TOKEN") or "",
    })
    return StdioServerParameters(
        command="python3",
        args=[os.path.join(base_dir, "mealie_planner", "mcp_server.py")],
        env=env
    )


def build_claude_tools(tools_resp):
    """Convert an MCP list_tools response into Anthropic tool definitions.

    MCP input schemas are already JSON Schema, which is what the Messages API takes."""
    if not tools_resp.tools:
        return []
    tools = []
    for tool in tools_resp.tools:
        schema = tool.inputSchema or {}
        if schema.get("type") != "object":
            schema = {"type": "object", "properties": {}}
        tools.append({
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": schema,
        })
    return tools


def _history_to_messages(history):
    """Stored history uses {"role", "content"} text turns; older rows say "model"."""
    messages = []
    for h in history:
        role = "assistant" if h.get("role") in ("model", "assistant") else "user"
        text = h.get("content") or ""
        if not text:
            continue
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] += "\n\n" + text
        else:
            messages.append({"role": role, "content": text})
    # The conversation must open with a user turn.
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages


def _tool_result_text(tool_result):
    if not tool_result.content:
        return ""
    return "\n".join(c.text for c in tool_result.content if getattr(c, "text", None) is not None)


async def run_chat_turn(session, claude_tools, history, user_message, model_name=None,
                        week_start_str=None, week_end_str=None):
    """Run one user turn of the tool-calling chat loop against an active MCP session.

    Returns: (reply_text, updated_history, plan_changed)
    """
    plan_changed = False
    if model_name is None:
        model_name = ACTIVE_CHAT_MODEL

    messages = _history_to_messages(history)
    messages.append({"role": "user", "content": user_message})

    system_prompt = get_system_prompt(week_start_str, week_end_str)
    ai = AIClient()

    max_turns = 20
    for _turn in range(max_turns):
        response = ai.generate(
            messages,
            system=system_prompt,
            tools=claude_tools or None,
            model=model_name,
        )

        tool_uses = [b for b in response.content if b.type == "tool_use"]

        if response.stop_reason != "tool_use" or not tool_uses:
            reply_text = "".join(b.text for b in response.content if b.type == "text")

            new_history = list(history)
            new_history.append({"role": "user", "content": user_message})
            new_history.append({"role": "assistant", "content": reply_text})
            return reply_text, new_history, plan_changed

        # Keep the full assistant content (thinking + tool_use blocks) in context.
        messages.append({"role": "assistant", "content": response.content})

        # Execute every tool call, then return all results in ONE user message.
        tool_results = []
        for call in tool_uses:
            func_name = call.name
            func_args = call.input or {}
            try:
                logger.info(f"[Agent] Executing tool: {func_name} with args: {func_args}")
                tool_result = await session.call_tool(func_name, func_args)
                res_text = _tool_result_text(tool_result)
                is_error = bool(getattr(tool_result, "isError", False))
                if not is_error and func_name.startswith(("create_", "update_", "delete_", "add_", "remove_")):
                    plan_changed = True
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": res_text or "(no output)",
                    "is_error": is_error,
                })
            except Exception as ex:
                logger.error(f"[Agent] Error running tool {func_name}: {ex}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": f"Error: {ex}",
                    "is_error": True,
                })

        messages.append({"role": "user", "content": tool_results})

    return "I apologize, but I hit a loop limit trying to answer your request. Please try again.", history, plan_changed


async def run_mcp_chat(history, user_message, model_name=None, week_start_str=None, week_end_str=None):
    """One-shot variant: spawns a fresh MCP session for a single chat turn.

    The web app uses the persistent session in chat_session.py instead; this
    remains for scripts and ad-hoc use.
    """
    async with stdio_client(get_server_params()) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            claude_tools = build_claude_tools(await session.list_tools())
            return await run_chat_turn(
                session, claude_tools, history, user_message,
                model_name=model_name, week_start_str=week_start_str, week_end_str=week_end_str,
            )
