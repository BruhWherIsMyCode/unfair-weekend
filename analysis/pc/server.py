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

STATUS: the websocket serving path and the real Claude paths were never run in the build session.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from analysis import config
from analysis.pc import link
from analysis.pc.family import FamilyStandIn
from analysis.pc.remote_adapter import RemoteAdapter
from analysis.pc.remote_tasks import remote_modules
from analysis.pc.store import BaselineStore
from analysis.session import limits
from analysis.session.adapters import render_family_text
from analysis.session.agent_tools import load_script
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


class LiveLog(list):
    """session.log that also prints a compact line for the interesting entries."""

    def __init__(self, show: Callable[[Dict[str, Any]], None]) -> None:
        super().__init__()
        self._show = show

    def append(self, entry: Dict[str, Any]) -> None:  # type: ignore[override]
        super().append(entry)
        try:
            self._show(entry)
        except Exception:  # printing must never break a call
            pass


def format_numbers(reading: Optional[Dict[str, Any]]) -> str:
    if not reading:
        return "no reading"
    low, high = reading.get("usual_low"), reading.get("usual_high")
    usual = "usual %.3f..%.3f" % (low, high) if low is not None and high is not None else "usual still learning"
    outside = {True: "OUTSIDE", False: "inside", None: "-"}[reading.get("outside")]
    return "value %.3f %s %s side=%s" % (reading["value"], usual, outside, reading.get("side"))


def format_log_line(entry: Dict[str, Any], session: Any) -> Optional[str]:
    kind = entry["kind"]
    rest = {k: v for k, v in entry.items() if k not in ("t", "kind")}
    if kind == "attempt_finished":
        last = session.results[-1] if session.results else {}
        return "task %s %s: %s | %s" % (last.get("task"), rest.get("attempt_id"), rest.get("outcome"),
                                      format_numbers(last.get("reading")))
    shown = {"call_driver": "driver", "agent_tool": "claude tool", "agent_fallback": "FALLBACK to fixed order",
             "fallback_step": "fixed step", "agent_refused": "step refused", "change_confirmed": "CHANGE CONFIRMED",
             "published": "family message", "concern_raised": "CONCERN RAISED", "resource_changed": "resource",
             "floor_off_suppressed": "FLOOR OFF - would have published", "task_refused": "task refused",
             "say": "say", "hang_up": "client hung up", "driver_crashed": "driver crashed",
             "cue": "cue"}
    if kind not in shown:
        return None
    return "%6.1fs %-26s %s" % (entry["t"], shown[kind], json.dumps(rest, sort_keys=True) if rest else "")


class PcServer:
    def __init__(self, person: str, name: str, claude: str = "off", safety_floor: bool = True, label: str = "live",
                 data_dir: Optional[Path] = None, runs_dir: Optional[Path] = None, clock: Any = None,
                 out: Callable[[str], Any] = print, client_factory: Optional[Callable[[], Any]] = None,
                 modules_factory: Callable[[], Dict[str, Any]] = remote_modules,
                 script: Optional[Dict[str, str]] = None) -> None:
        if claude not in ("off", "api", "subscription"):
            raise ValueError("claude must be off, api or subscription")
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
                    if message["protocol"] != link.PROTOCOL_VERSION:
                        await self._reply(endpoint, "error", code="protocol",
                                          text="this server speaks protocol %d" % link.PROTOCOL_VERSION)
                        break
                    greeted = True
                    self.out("client connected: %s (person %s)" % (message["client"], message["person_id"]))
                    if not await self._reply(endpoint, "hello_ok", protocol=link.PROTOCOL_VERSION,
                                             server_version=SERVER_VERSION):
                        break
                    continue
                if kind == "hello":
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
            self._busy = False
            self.out("client disconnected")
            await endpoint.close()

    async def _reply(self, endpoint: link.Endpoint, type_: str, **fields: Any) -> bool:
        try:
            await endpoint.send(type_, **fields)
            return True
        except link.TransportClosed:
            return False

    def _new_adapter(self, endpoint: link.Endpoint) -> RemoteAdapter:
        self._call_counter += 1
        endpoint.call_id = "call_%s_%d" % (time.strftime("%Y%m%d_%H%M%S"), self._call_counter)
        status = self.last_status
        initial: Dict[str, str] = {}
        if status is not None:
            initial = {"camera": status["camera"], "microphone": status["microphone"]}
        family = FamilyStandIn(endpoint.call_id, self.name, self.runs_dir / "family_messages.log", self.out)
        return RemoteAdapter(endpoint, self.script, self.name, self.clock, family, initial)

    # ---- one call ---------------------------------------------------------------------------------------

    async def _run_one_call(self, endpoint: link.Endpoint, adapter: RemoteAdapter) -> None:
        call_id = endpoint.call_id or "call_unknown"
        started_wall = time.time()
        modules = self.modules_factory()
        initial = dict(adapter._states)
        session = CallSession(call_id, self.person, self.name, adapter, modules, self.store.load_baselines(),
                              self.clock, label=self.label, initial_resources=initial or None,
                              safety_floor=self.safety_floor)
        session.log = LiveLog(lambda e: self._print_entry(e, session))
        adapter.session = session
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
        record = {"call_id": call_id, "person": self.person, "label": self.label, "claude": self.claude,
                  "safety_floor": self.safety_floor, "summary": summary, "family_text": family_text,
                  "session_log": list(session.log),
                  "timings": {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started_wall)),
                              "duration_s": round(time.time() - started_wall, 1),
                              "session_duration_s": summary.get("duration_s")}}
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
        try:
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            (self.runs_dir / ("%s.json" % call_id)).write_text(json.dumps(record, indent=2, default=str), "utf-8")
        except OSError as exc:
            self.out("could not write the call record: %s" % type(exc).__name__)


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
    return p.parse_args(argv)


async def serve_forever(args: argparse.Namespace) -> None:
    floor = args.safety_floor == "on" and safety_floor_from_env(True)
    server = PcServer(args.person, args.name, args.claude, floor, args.label)
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
