"""Claude through the owner's subscription login (the `claude` CLI), via the Claude Agent SDK for Python.

`ClaudeSubscriptionClient.create(system, messages, tools)` has the SAME contract as `ClaudeClient.create`: it returns
a plain dict with "content" blocks ("text" / "tool_use") and "stop_reason". The Agent SDK has no tool-calling
protocol of our own, so the model is asked to answer with exactly one JSON object {"tool": ..., "input": {...}}
and this module turns that into a tool_use block.

One persistent ClaudeSDKClient session per call: connected once before the first request (inside the same
per-request timeout budget), disconnected by `close()` (run_call's finally). Each request sends only the messages
the session has not seen yet, rendered as plain text. No Claude Code tool can run: tools=[], every built-in tool
name is also in disallowed_tools, max_turns=1, setting_sources=[], strict_mcp_config=True, a fresh temp cwd.

Auth is the subscription login of the claude CLI. Construction removes ANTHROPIC_API_KEY from this process's
environment (the CLI would otherwise bill the key); the value is never read into a log, a message or an error.
Errors carry the class name only. The SDK (`claude_agent_sdk`) is imported lazily inside the class; a missing
package raises ClaudeUnavailable. Tests inject `sdk_factory` and never import the SDK.

STATUS: written, NEVER executed against the real SDK or CLI.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from typing import Any, Callable, Dict, List, Optional

from analysis.session import limits
from analysis.session.claude_client import API_KEY_ENV, MODEL_ENV, ClaudeRequestError, ClaudeUnavailable

DEFAULT_SUB_MODEL = "haiku"
EXCERPT_CHARS = 200
BUILTIN_TOOLS = ["Bash", "BashOutput", "KillShell", "KillBash", "Read", "Write", "Edit", "MultiEdit", "NotebookEdit",
                 "Glob", "Grep", "LS", "WebFetch", "WebSearch", "Task", "Agent", "TodoWrite", "ExitPlanMode",
                 "EnterPlanMode", "SlashCommand", "Skill", "TaskStop", "AskUserQuestion", "ToolSearch", "Monitor",
                 "CronCreate", "CronDelete", "CronList", "EnterWorktree", "ExitWorktree", "PushNotification",
                 "RemoteTrigger"]

SdkFactory = Callable[[Dict[str, Any]], Any]


def ignore_api_key_env() -> bool:
    """Remove ANTHROPIC_API_KEY from os.environ. True when it was set. Never returns or logs the value."""
    return os.environ.pop(API_KEY_ENV, None) is not None


def answer_format_section(tools: List[Dict[str, Any]]) -> str:
    """The 'Answer format' text appended to the system prompt: one JSON object, and the tool list."""
    rendered = json.dumps([{"name": t.get("name"), "description": t.get("description"),
                            "input_schema": t.get("input_schema")} for t in tools],
                          separators=(",", ":"), sort_keys=True)
    return ("\n\nAnswer format\n"
            "You have no tools of your own here. Choose exactly one step per turn from the tools below and answer "
            'with exactly one JSON object and nothing else: {"tool": "<name>", "input": {<fields of that tool>}}. '
            "No explanation, no code fence, no other text.\n"
            "Tools (JSON, name + input schema):\n" + rendered + "\n")


def parse_first_json_object(text: str) -> Optional[Dict[str, Any]]:
    """The first JSON object in `text` (code fences and surrounding words tolerated), or None."""
    decoder = json.JSONDecoder()
    index = text.find("{")
    while index != -1:
        try:
            obj, _ = decoder.raw_decode(text, index)
        except ValueError:
            index = text.find("{", index + 1)
            continue
        return obj if isinstance(obj, dict) else None
    return None


def _compact(content: Any) -> str:
    if isinstance(content, str):
        try:
            return json.dumps(json.loads(content), separators=(",", ":"), sort_keys=True)
        except ValueError:
            return content
    return json.dumps(content, separators=(",", ":"), sort_keys=True, default=str)


class ClaudeSubscriptionClient:
    def __init__(self, timeout_s: float = limits.CLAUDE_SUB_TIMEOUT_S, sdk_factory: Optional[SdkFactory] = None,
                 model: Optional[str] = None) -> None:
        if sdk_factory is None:
            try:
                import claude_agent_sdk  # noqa: F401  optional dependency, imported only here
            except ImportError:
                raise ClaudeUnavailable("the claude-agent-sdk package is not installed") from None
            sdk_factory = _real_sdk_factory
        self.api_key_ignored = ignore_api_key_env()
        self.model = model or os.environ.get(MODEL_ENV) or DEFAULT_SUB_MODEL
        self.timeout_s = timeout_s
        self._factory = sdk_factory
        self._sdk: Any = None
        self._cwd: Optional[str] = None
        self._sent = 0  # how many entries of the message list this session has already seen
        self._counter = 0
        self.last_excerpt = ""  # the first 200 characters of the last unparsable answer

    # ---- the one public request ------------------------------------------------------------------------

    async def create(self, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        try:
            text = await asyncio.wait_for(self._round_trip(system, messages, tools), timeout=self.timeout_s)
        except asyncio.TimeoutError:
            raise ClaudeRequestError("timeout") from None
        except ClaudeRequestError:
            raise
        except Exception as exc:  # the message is dropped on purpose: nothing from the request may leak
            raise ClaudeRequestError(type(exc).__name__) from None
        return self._to_response(text)

    async def close(self) -> None:
        sdk, self._sdk = self._sdk, None
        try:
            if sdk is not None:
                await sdk.disconnect()
        except Exception:
            pass
        finally:
            if self._cwd is not None:
                shutil.rmtree(self._cwd, ignore_errors=True)
                self._cwd = None

    # ---- pieces ----------------------------------------------------------------------------------------

    async def _connect(self, system: str, tools: List[Dict[str, Any]]) -> None:
        self._cwd = tempfile.mkdtemp(prefix="uw_sub_")
        options = {"system_prompt": system + answer_format_section(tools), "model": self.model, "tools": [],
                   "disallowed_tools": list(BUILTIN_TOOLS), "max_turns": 1, "setting_sources": [],
                   "strict_mcp_config": True, "cwd": self._cwd}
        sdk = self._factory(options)
        await sdk.connect()
        self._sdk = sdk

    async def _round_trip(self, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> str:
        if self._sdk is None:
            await self._connect(system, tools)
        prompt = self._render_new(messages)
        self._sent = len(messages)
        await self._sdk.query(prompt)
        parts: List[str] = []
        async for message in self._sdk.receive_response():
            kind = type(message).__name__
            if kind == "AssistantMessage":
                for block in getattr(message, "content", None) or []:
                    text = getattr(block, "text", None)
                    if isinstance(text, str):
                        parts.append(text)
            elif kind == "ResultMessage":
                if getattr(message, "is_error", False):
                    raise ClaudeRequestError("ResultMessage")
                result = getattr(message, "result", None)
                if not parts and isinstance(result, str):
                    parts.append(result)
            elif "RateLimit" in kind:
                info = getattr(message, "rate_limit_info", message)
                if getattr(info, "status", None) in ("rejected", "exceeded", "limit_reached"):
                    raise ClaudeRequestError(kind)
        return "".join(parts)

    def _render_new(self, messages: List[Dict[str, Any]]) -> str:
        """Plain text for the user messages this session has not seen. Assistant entries are our own earlier
        answers, already in the session."""
        names: Dict[str, str] = {}
        for m in messages:
            if m.get("role") == "assistant" and isinstance(m.get("content"), list):
                for block in m["content"]:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        names[str(block.get("id"))] = str(block.get("name"))
        lines: List[str] = []
        for m in messages[self._sent:]:
            if m.get("role") != "user":
                continue
            content = m.get("content")
            if isinstance(content, str):
                lines.append(content)
                continue
            for block in content or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    name = names.get(str(block.get("tool_use_id")), "tool")
                    lines.append("Result of %s: %s" % (name, _compact(block.get("content"))))
                elif block.get("type") == "text":
                    lines.append(str(block.get("text")))
        return "\n".join(lines)

    def _to_response(self, text: str) -> Dict[str, Any]:
        obj = parse_first_json_object(text)
        name = obj.get("tool") if obj is not None else None
        if isinstance(name, str) and name:
            args = obj.get("input")
            self._counter += 1
            return {"content": [{"type": "tool_use", "id": "toolu_sub_%d" % self._counter, "name": name,
                                 "input": args if isinstance(args, dict) else {}}], "stop_reason": "tool_use"}
        self.last_excerpt = text[:EXCERPT_CHARS]
        return {"content": [{"type": "text", "text": self.last_excerpt}], "stop_reason": "end_turn"}


def _real_sdk_factory(options: Dict[str, Any]) -> Any:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient  # lazy: only the real path imports the SDK

    return ClaudeSDKClient(ClaudeAgentOptions(**options))
