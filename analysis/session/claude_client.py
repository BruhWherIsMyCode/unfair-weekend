"""The ONLY module that talks to Claude.

`ClaudeClient.create(system, messages, tools)` sends one Messages API request and returns the response as a plain
dict (content blocks of type "text" / "tool_use", and stop_reason). The `anthropic` package is imported lazily
inside the class; the API key comes from the environment variable ANTHROPIC_API_KEY only and is never logged,
never put in an exception message and never stored anywhere else. The model comes from UW_CLAUDE_MODEL
(default claude-haiku-4-5), max_tokens is CLAUDE_MAX_TOKENS, one request may take CLAUDE_TIMEOUT_S.

A missing package or key raises ClaudeUnavailable at construction; the call loop then uses the fixed-order
driver. `FakeClaudeClient` scripts responses for tests and the demo: no network.

STATUS: the real client has been written and NEVER executed.
"""
from __future__ import annotations

import asyncio
import copy
import os
from typing import Any, Callable, Dict, List, Optional, Union

from analysis.session import limits

DEFAULT_MODEL = "claude-haiku-4-5"
API_KEY_ENV = "ANTHROPIC_API_KEY"
MODEL_ENV = "UW_CLAUDE_MODEL"


class ClaudeUnavailable(Exception):
    """The real client cannot be built (package or key missing)."""


class ClaudeRequestError(Exception):
    """A request failed. Carries only the error class name, never request or key text."""


class ClaudeClient:
    def __init__(self, timeout_s: float = limits.CLAUDE_TIMEOUT_S) -> None:
        try:
            import anthropic  # optional dependency, imported only here
        except ImportError:
            raise ClaudeUnavailable("the anthropic package is not installed") from None
        key = os.environ.get(API_KEY_ENV)
        if not key:
            raise ClaudeUnavailable("%s is not set" % API_KEY_ENV)
        self.model = os.environ.get(MODEL_ENV) or DEFAULT_MODEL
        self.timeout_s = timeout_s
        self._client = anthropic.AsyncAnthropic(api_key=key, timeout=timeout_s, max_retries=0)

    async def create(self, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        try:
            response = await asyncio.wait_for(
                self._client.messages.create(
                    model=self.model, max_tokens=limits.CLAUDE_MAX_TOKENS, system=system, messages=messages,
                    tools=tools, tool_choice={"type": "any", "disable_parallel_tool_use": True}),
                timeout=self.timeout_s)
        except asyncio.TimeoutError:
            raise ClaudeRequestError("timeout") from None
        except Exception as exc:  # the message is dropped on purpose: nothing from the request may leak
            raise ClaudeRequestError(type(exc).__name__) from None
        return _to_dict(response)


def _to_dict(response: Any) -> Dict[str, Any]:
    blocks: List[Dict[str, Any]] = []
    for block in getattr(response, "content", None) or []:
        kind = getattr(block, "type", None)
        if kind == "text":
            blocks.append({"type": "text", "text": getattr(block, "text", "")})
        elif kind == "tool_use":
            blocks.append({"type": "tool_use", "id": getattr(block, "id", None), "name": getattr(block, "name", None),
                           "input": dict(getattr(block, "input", None) or {})})
    return {"content": blocks, "stop_reason": getattr(response, "stop_reason", None)}


# ---- the fake: scripted responses, no network ----------------------------------------------------------

class Hang:
    """Script item: the request never answers (until the caller's timeout cancels it)."""


class Delay:
    """Script item: answer with `response` after `seconds` of clock time."""

    def __init__(self, seconds: float, response: Any) -> None:
        self.seconds = seconds
        self.response = response


def tool_use_response(name: str, args: Optional[Dict[str, Any]] = None, tool_use_id: Optional[str] = None,
                      text: Optional[str] = None) -> Dict[str, Any]:
    content: List[Dict[str, Any]] = [] if text is None else [{"type": "text", "text": text}]
    content.append({"type": "tool_use", "id": tool_use_id or "toolu_fake", "name": name, "input": dict(args or {})})
    return {"content": content, "stop_reason": "tool_use"}


def text_response(text: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}


def multi_tool_response(*calls: Any) -> Dict[str, Any]:
    """Several tool_use blocks in one response; each call is (name, args[, tool_use_id])."""
    content = []
    for n, call in enumerate(calls):
        name, args = call[0], call[1]
        content.append({"type": "tool_use", "id": call[2] if len(call) > 2 else "toolu_multi_%d" % n, "name": name,
                        "input": dict(args)})
    return {"content": content, "stop_reason": "tool_use"}


ScriptItem = Union[Dict[str, Any], Exception, type, Hang, Delay]


class FakeClaudeClient:
    """Same `create` as ClaudeClient. `script` is a list of items consumed one per request, or a callable
    taking the message list and returning an item. An item is a response dict, an exception (raised), `Hang()`
    (never answers) or `Delay(seconds, response)` (answers after clock time; needs `clock`).

    Tool-use ids of dict responses are made unique per request when they are the default "toolu_fake".
    Every request is recorded in `requests` (deep copies, so later changes to the history cannot alter them)."""

    def __init__(self, script: Union[List[ScriptItem], Callable[[List[Dict[str, Any]]], ScriptItem], None] = None,
                 clock: Any = None) -> None:
        self.script = script if script is not None else []
        self.clock = clock
        self.requests: List[Dict[str, Any]] = []
        self._n = 0

    async def create(self, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        self._n += 1
        self.requests.append(copy.deepcopy({"system": system, "messages": messages, "tools": tools}))
        if callable(self.script):
            item = self.script(copy.deepcopy(messages))
        else:
            if not self.script:
                raise ClaudeRequestError("script exhausted")
            item = self.script.pop(0)
        if isinstance(item, Delay):
            await self.clock.sleep(item.seconds)
            item = item.response
        if isinstance(item, Hang):
            await self.clock.sleep(10 ** 6)
            raise ClaudeRequestError("timeout")
        if isinstance(item, type) and issubclass(item, BaseException):
            raise item()
        if isinstance(item, BaseException):
            raise item
        response = copy.deepcopy(item)
        content = response.get("content") if isinstance(response, dict) else None
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id") == "toolu_fake":
                block["id"] = "toolu_fake_%d" % self._n
        return response
