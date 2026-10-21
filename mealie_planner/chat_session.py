"""Persistent MCP chat session.

Instead of spawning a fresh MCP server subprocess for every chat message
(interpreter start + session init + list_tools each turn), a single session is
kept alive on a dedicated background event loop and reused across turns. If a
turn fails, the session is restarted once and the turn retried.
"""
import asyncio
import logging
import threading
from contextlib import AsyncExitStack

logger = logging.getLogger(__name__)

CHAT_TURN_TIMEOUT_SECONDS = 300


class _ChatSessionManager:
    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="mcp-chat-loop")
        self._thread.start()
        self._stack = None
        self._session = None
        self._tools = None
        # Serialize turns: the MCP session handles one conversation at a time.
        self._turn_lock = threading.Lock()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, coro, timeout):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def run_chat(self, history, user_message, week_start_str=None, week_end_str=None):
        with self._turn_lock:
            try:
                return self._submit(
                    self._chat(history, user_message, week_start_str, week_end_str),
                    CHAT_TURN_TIMEOUT_SECONDS,
                )
            except Exception as e:
                logger.warning("Chat turn failed (%s); restarting MCP session and retrying once.", e)
                try:
                    self._submit(self._close(), 30)
                except Exception as close_err:
                    logger.error("Error closing MCP session: %s", close_err)
                return self._submit(
                    self._chat(history, user_message, week_start_str, week_end_str),
                    CHAT_TURN_TIMEOUT_SECONDS,
                )

    async def _ensure_session(self):
        if self._session is not None:
            return
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        from .mcp_agent import build_claude_tools, get_server_params

        self._stack = AsyncExitStack()
        try:
            read_stream, write_stream = await self._stack.enter_async_context(
                stdio_client(get_server_params())
            )
            self._session = await self._stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await self._session.initialize()
            self._tools = build_claude_tools(await self._session.list_tools())
            logger.info("Persistent MCP chat session started (%d tools).", len(self._tools))
        except BaseException:
            await self._close()
            raise

    async def _chat(self, history, user_message, week_start_str, week_end_str):
        from .mcp_agent import run_chat_turn

        await self._ensure_session()
        return await run_chat_turn(
            self._session, self._tools, history, user_message,
            week_start_str=week_start_str, week_end_str=week_end_str,
        )

    async def _close(self):
        stack, self._stack = self._stack, None
        self._session = None
        self._tools = None
        if stack is not None:
            try:
                await stack.aclose()
            except Exception as e:
                logger.warning("Error while closing MCP session stack: %s", e)


_manager = None
_manager_lock = threading.Lock()


def run_chat(history, user_message, week_start_str=None, week_end_str=None):
    """Run one chat turn on the shared persistent MCP session.

    Returns (reply_text, updated_history, plan_changed).
    """
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = _ChatSessionManager()
    return _manager.run_chat(
        history, user_message,
        week_start_str=week_start_str, week_end_str=week_end_str,
    )
