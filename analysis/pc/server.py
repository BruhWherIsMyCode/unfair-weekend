"""The server program of the PC version: all call logic, no camera.

    python -m analysis.pc.server --host 127.0.0.1 --port 8765 --person feodor --name Feodor \
        --claude off|subscription|api --safety-floor on|off --label live|normal|acted

Per `start_call` from the client: a new CallSession over a RemoteAdapter (the client measures, the server runs the
same stream task modules on the readings), the baselines from the store, run_call with the chosen driver, then
`call_result` to the client, `<runs>/calls/<call_id>.json` on disk, the baseline values appended to the store, and a
compact log line per step in this terminal. One client and one call at a time.

--claude off            the fixed-order driver (default)
--claude api            Claude through the API (ANTHROPIC_API_KEY)
--claude subscription   Claude through the `claude` CLI login (claude-agent-sdk); ANTHROPIC_API_KEY is removed from
                        this process first so the CLI cannot bill a key
--safety-floor on|off   ON (default, keep it for the demo and any real use): two same-side outside readings are
                        published mid-call whatever Claude does. OFF is for experiments only
                        (UW_SAFETY_FLOOR=off also turns it off).

--tts off|elevenlabs    elevenlabs: every line the call can play is spoken once with an ElevenLabs voice at start-up
                        (cached in analysis/runs/tts_cache/<voice_id>/) and the audio is attached to `say`. Needs
                        ELEVENLABS_API_KEY and UW_TTS_VOICE_ID in THIS terminal. Any failure: lines go out as text.

Speech (v8): the API key lives only in this process (env ELEVENLABS_API_KEY). A client asks for a single-use token
(`stt_token_request`), calls ElevenLabs speech-to-text itself, and sends back the text and word timings. Without a key
the answer to a token request is `speech_not_configured`; that call then needs only smile and arms.

STATUS: the websocket serving path, the real Claude paths and every real ElevenLabs request were never run in the
build session.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from analysis import config
from analysis.pc import link
from analysis.pc.family import FamilyStandIn
from analysis.pc.remote_adapter import RemoteAdapter
from analysis.pc.remote_tasks import remote_modules
from analysis.pc.store import BaselineStore
from analysis.session import limits
from analysis.speech import elevenlabs
from analysis.session.adapters import render_family_text
from analysis.session.agent_tools import load_script
from analysis.session.call_record import (LiveLog, build_record, format_log_line, format_numbers,  # noqa: F401
                                          format_speech, write_record)
from analysis.session.call_session import CallSession
from analysis.session.clock import RealClock
from analysis.session.run_call import run_call, safety_floor_from_env

SERVER_VERSION = "pc-1"
LINES_PATH = Path(__file__).resolve().parent / "lines.en.json"


def load_pc_script(lines_path: Optional[Path] = None) -> Dict[str, str]:
    """contract/script.en.json with the PC line texts (lines.en.json) laid over it."""
    script = dict(load_script())
    overrides = json.loads(Path(lines_path or LINES_PATH).read_text("utf-8"))
    script.update({k: v for k, v in overrides.items() if not k.startswith("_") and isinstance(v, str)})
    return script


class PcServer:
    def __init__(self, person: str, name: str, claude: str = "off", safety_floor: bool = True, label: str = "live",
                 data_dir: Optional[Path] = None, runs_dir: Optional[Path] = None, clock: Any = None,
                 out: Callable[[str], Any] = print, client_factory: Optional[Callable[[], Any]] = None,
                 modules_factory: Callable[..., Dict[str, Any]] = remote_modules,
                 script: Optional[Dict[str, str]] = None, line_audio: Optional[Dict[str, str]] = None,
                 http: Optional[Callable[..., Any]] = None, environ: Optional[Mapping[str, str]] = None,
                 inline_blocking: bool = False) -> None:
        """v8 additions: `line_audio` (line id -> base64 PCM, from tts_lines), and for tests `http` (a fake ElevenLabs),
        `environ` (a fake environment holding ELEVENLABS_API_KEY) and `inline_blocking` (mint tokens in the event
        loop thread instead of an executor, so a fake clock stays deterministic)."""
        if claude not in ("off", "api", "subscription"):
            raise ValueError("claude must be off, api or subscription")
        self.line_audio: Dict[str, str] = dict(line_audio or {})
        self.http = http
        self.environ = environ
        self.inline_blocking = inline_blocking
        self.client_caps: Dict[str, Any] = {}
        self._token_inflight = False
        self._tokens_in_call = 0
        self._token_tasks: set = set()
        self._call_speech = False
        self.person, self.name, self.claude, self.label = person, name, claude, label
        self.safety_floor = safety_floor
        self.store = BaselineStore(person, data_dir)
        self.runs_dir = Path(runs_dir) if runs_dir is not None else config.RUNS_DIR / "calls"
        self.clock = clock or RealClock()
        self.out = out
        self.client_factory = client_factory  # tests: returns a fake Claude client
        self.modules_factory = modules_factory
        self.script = script if script is not None else load_pc_script()
        self._busy = False
        self._call_counter = 0
        self.last_status: Optional[Dict[str, Any]] = None
        self.call_summaries: List[Dict[str, Any]] = []  # every finished call, for tests and the log

    # ---- one connection ---------------------------------------------------------------------------------

    async def handle_connection(self, transport: link.Transport) -> None:
        endpoint = link.Endpoint(transport)
        if self._busy:
            try:
                await endpoint.send("error", code="busy", text="another client is connected")
            except link.TransportClosed:
                pass
            await endpoint.close()
            return
        self._busy = True
        self.last_status = None
        adapter: Optional[RemoteAdapter] = None
        call_task: Optional["asyncio.Future"] = None
        greeted = False
        try:
            while True:
                try:
                    message = await endpoint.recv()
                except link.TransportClosed:
                    break
                problem = link.validate_client_message(message)
                if problem is not None:
                    self.out("link: invalid message from client (%s)" % problem)
                    if not await self._reply(endpoint, "error", code="invalid_message", text=problem):
                        break
                    continue
                kind = message["type"]
                if not greeted:
                    if kind != "hello":
                        if not await self._reply(endpoint, "error", code="expected_hello", text="send hello first"):
                            break
                        continue
                    if message["protocol"] not in link.SUPPORTED_PROTOCOLS:
                        await self._reply(endpoint, "error", code="protocol",
                                          text="this server speaks protocol %s" % " or ".join(
                                              str(v) for v in link.SUPPORTED_PROTOCOLS))
                        break
                    greeted = True
                    self.client_caps = dict(message["capabilities"])
                    self.out("client connected: %s (person %s, protocol %d, speech %s)" % (
                        message["client"], message["person_id"], message["protocol"],
                        "yes" if self.client_caps.get("speech") else "no"))
                    if not await self._reply(endpoint, "hello_ok", protocol=message["protocol"],
                                             server_version=SERVER_VERSION):
                        break
                    continue
                if kind == "hello":
                    continue
                if kind == "stt_token_request":
                    self._handle_token_request(endpoint, message)
                    continue
                if kind == "status":
                    self.last_status = message
                if kind == "start_call":
                    if call_task is not None and not call_task.done():
                        if not await self._reply(endpoint, "error", code="call_in_progress",
                                                 text="a call is already running"):
                            break
                        continue
                    adapter = self._new_adapter(endpoint)
                    call_task = asyncio.ensure_future(self._run_one_call(endpoint, adapter))
                    continue
                if adapter is not None and call_task is not None and not call_task.done():
                    await adapter.dispatch(message)
                elif kind == "status":
                    pass  # remembered above; the next call starts from it
        finally:
            if adapter is not None and call_task is not None and not call_task.done():
                adapter.connection_lost()  # the client left mid-call: same as a hang-up
            if call_task is not None:
                await asyncio.gather(call_task, return_exceptions=True)
            if self._token_tasks:
                await asyncio.gather(*list(self._token_tasks), return_exceptions=True)
            self._busy = False
            self.out("client disconnected")
            await endpoint.close()

    async def _reply(self, endpoint: link.Endpoint, type_: str, **fields: Any) -> bool:
        try:
            await endpoint.send(type_, **fields)
            return True
        except link.TransportClosed:
            return False

    # ---- speech: single-use tokens (the API key never leaves this process) ---------------------------------------

    def speech_configured(self) -> bool:
        return elevenlabs.key_configured(self.environ)

    async def _blocking(self, fn: Callable[[], Any]) -> Any:
        if self.inline_blocking:
            return fn()
        return await asyncio.get_running_loop().run_in_executor(None, fn)

    def _handle_token_request(self, endpoint: link.Endpoint, message: Dict[str, Any]) -> None:
        """Answer `stt_token_request` with `stt_token`: one batch_scribe token, at most one in flight and
        limits.STT_TOKENS_PER_CALL per call. Without a key: token null + error speech_not_configured."""
        ref = message["ref"]
        if not self.speech_configured():
            error: Optional[str] = "speech_not_configured"
        elif self._token_inflight:
            error = "token_busy"
        elif self._tokens_in_call >= limits.STT_TOKENS_PER_CALL:
            error = "token_limit"
        else:
            error = None
        if error is not None:
            self.out("speech: token refused (%s)" % error)
            task = asyncio.ensure_future(self._reply(endpoint, "stt_token", ref=ref, token=None, error=error))
        else:
            self._token_inflight = True
            self._tokens_in_call += 1
            task = asyncio.ensure_future(self._mint_and_reply(endpoint, ref, message["purpose"]))
        self._token_tasks.add(task)
        task.add_done_callback(self._token_tasks.discard)

    async def _mint_and_reply(self, endpoint: link.Endpoint, ref: str, purpose: str) -> None:
        token: Optional[str] = None
        error: Optional[str] = None
        try:
            token = await self._blocking(lambda: elevenlabs.mint_token("batch_scribe", self.http, self.environ))
            self.out("speech: token issued for %s (%d of %d this call)" % (purpose, self._tokens_in_call,
                                                                              limits.STT_TOKENS_PER_CALL))
        except elevenlabs.SpeechError as exc:
            error = "token_failed_%s" % (exc.status if exc.status else "network")
            self.out("speech: token request failed (%s)" % error)
        except Exception as exc:
            error = "token_failed"
            self.out("speech: token request failed (%s)" % type(exc).__name__)
        finally:
            self._token_inflight = False
        await self._reply(endpoint, "stt_token", ref=ref, token=token, error=error)

    def _new_adapter(self, endpoint: link.Endpoint) -> RemoteAdapter:
        self._call_counter += 1
        endpoint.call_id = "call_%s_%d" % (time.strftime("%Y%m%d_%H%M%S"), self._call_counter)
        status = self.last_status
        initial: Dict[str, str] = {}
        if status is not None:
            initial = {"camera": status["camera"], "microphone": status["microphone"]}
        family = FamilyStandIn(endpoint.call_id, self.name, self.runs_dir / "family_messages.log", self.out)
        self._tokens_in_call = 0
        self._call_speech = bool(self.client_caps.get("speech")) and self.speech_configured()
        grace = limits.SPEECH_ANSWER_GRACE_S if self._call_speech else 2.0
        return RemoteAdapter(endpoint, self.script, self.name, self.clock, family, initial, self.line_audio,
                             answer_grace_s=grace)

    # ---- one call ---------------------------------------------------------------------------------------

    async def _run_one_call(self, endpoint: link.Endpoint, adapter: RemoteAdapter) -> None:
        call_id = endpoint.call_id or "call_unknown"
        started_wall = time.time()
        # v8: the sentence task is required only when the client can speak AND this server can mint tokens
        speech = self._call_speech
        required = limits.REQUIRED_TASKS_WITH_SPEECH if speech else limits.REQUIRED_TASKS
        modules = self.modules_factory(speech=speech)
        initial = dict(adapter._states)
        session = CallSession(call_id, self.person, self.name, adapter, modules, self.store.load_baselines(),
                              self.clock, label=self.label, initial_resources=initial or None,
                              safety_floor=self.safety_floor, required_tasks=required)
        session.log = LiveLog(lambda e: self._print_entry(e, session))
        adapter.session = session
        session.note("required_tasks", tasks=list(required), speech=speech,
                     client_speech=bool(self.client_caps.get("speech")), server_key=self.speech_configured(),
                     line_audio=len(self.line_audio))
        if self.client_caps.get("speech") and not speech:
            self.out("speech: the client can speak but ELEVENLABS_API_KEY is not set in this terminal; "
                     "answers fall back to typing and the sentence check is skipped")
        self.out("=== call %s started (claude=%s, safety floor %s, label %s)" % (
            call_id, self.claude, "ON" if self.safety_floor else "OFF", self.label))
        mode, backend = ("fixed", "api") if self.claude == "off" else ("claude", self.claude)
        client = self.client_factory() if (self.client_factory is not None and mode == "claude") else None
        if mode == "claude" and backend == "subscription" and client is None:
            from analysis.session.claude_sub_client import ignore_api_key_env

            if ignore_api_key_env():
                self.out("subscription mode: API key variable ignored")
        try:
            summary = await run_call(session, adapter, mode, client, self.script, agent_clock=self.clock,
                                     backend=backend)
        except Exception as exc:  # the call is lost, the server is not
            session.note("call_crashed", error=type(exc).__name__)
            self.out("call crashed: %s" % type(exc).__name__)
            summary = session.build_session_result().to_dict()
        family_text = adapter.family.latest_text or render_family_text(summary, self.name)
        record = build_record(call_id, self.person, self.label, self.claude, self.safety_floor, summary, family_text,
                              session.log, started_wall)
        self._write_record(call_id, record)
        stored = False
        try:
            stored = self.store.append(self.label, session.baseline_update())
        except Exception as exc:
            self.out("baseline store failed: %s" % type(exc).__name__)
        self.out("=== call %s finished: %s (baseline values stored: %s)" % (call_id, summary["status"], stored))
        self.call_summaries.append(summary)
        await self._reply(endpoint, "call_result", summary=summary, family_text=family_text)
        endpoint.call_id = None

    def _print_entry(self, entry: Dict[str, Any], session: Any) -> None:
        line = format_log_line(entry, session)
        if line is not None:
            self.out(line)

    def _write_record(self, call_id: str, record: Dict[str, Any]) -> None:
        write_record(self.runs_dir, call_id, record, self.out)


# ---- command line ------------------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Server of the PC version: call logic, no camera.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--person", default="feodor")
    p.add_argument("--name", default="Feodor", help="the name the avatar says")
    p.add_argument("--claude", choices=("off", "subscription", "api"), default="off")
    p.add_argument("--safety-floor", choices=("on", "off"), default="on",
                   help="keep ON for the demo and any real use; OFF is for experiments only")
    p.add_argument("--label", choices=("live", "normal", "acted"), default="live")
    p.add_argument("--tts", choices=("off", "elevenlabs"), default="off",
                   help="elevenlabs: the avatar's lines are spoken with an ElevenLabs voice (needs ELEVENLABS_API_KEY "
                        "and UW_TTS_VOICE_ID in THIS terminal)")
    return p.parse_args(argv)


def prepare_tts(args: argparse.Namespace, script: Dict[str, str]) -> Optional[Dict[str, str]]:
    """--tts elevenlabs: synthesize every line once (cached). None when the options are not usable."""
    from analysis.pc.tts_lines import prepare_line_audio

    cfg = elevenlabs.load_config()
    if not cfg.voice_explicit:
        print("--tts elevenlabs needs a voice id: set UW_TTS_VOICE_ID (the built-in default is an unverified placeholder).")
        return None
    if not elevenlabs.key_configured():
        print("--tts elevenlabs needs ELEVENLABS_API_KEY in this terminal.")
        return None
    return prepare_line_audio(script, args.name, cfg.voice_id, config.RUNS_DIR / "tts_cache",
                              lambda text: elevenlabs.synthesize(text, cfg.voice_id, cfg.tts_model))


async def serve_forever(args: argparse.Namespace) -> None:
    floor = args.safety_floor == "on" and safety_floor_from_env(True)
    script = load_pc_script()
    audio: Dict[str, str] = {}
    if args.tts == "elevenlabs":
        prepared = prepare_tts(args, script)
        if prepared is None:
            return
        audio = prepared
    server = PcServer(args.person, args.name, args.claude, floor, args.label, script=script, line_audio=audio)
    print("speech: %s" % ("the client may use ElevenLabs speech-to-text (key found)" if server.speech_configured()
                          else "ELEVENLABS_API_KEY not set: answers are typed, no sentence check"))
    if not floor:
        print("!!! SAFETY FLOOR IS OFF: a confirmed change will NOT be published mid-call. Experiments only. !!!")
    ws = await link.serve(server.handle_connection, args.host, args.port)
    print("server listening on ws://%s:%d  (person %s, claude %s). Ctrl+C to stop." % (
        args.host, ws.port, args.person, args.claude))
    try:
        await asyncio.Future()
    finally:
        await ws.close()


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        asyncio.run(serve_forever(args))
    except KeyboardInterrupt:
        print("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
