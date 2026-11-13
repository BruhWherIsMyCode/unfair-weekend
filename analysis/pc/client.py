"""The client program of the PC version: camera + measuring + a window. It decides nothing about the call.

    python -m analysis.pc.client --server ws://127.0.0.1:8765 --person feodor [--camera 0] [--mirrored]
                                 [--auto-start] [--tts off|system]

Threads. The MAIN thread owns the camera (frame_source.py), the measuring (client_measure.py), the OpenCV window
(client_window.py) and the state machine (client_state.py). A SECOND thread runs an asyncio loop with the websocket
(link.py). Two thread-safe queues join them: `inbox` (server messages and connection changes, link thread -> main)
and the link thread's send queue (main -> link thread, filled through `LinkThread.send`). The state machine is only
ever touched by the main thread. With --tts system a THIRD thread speaks lines with pyttsx3 (optional import; without
it the client is text-only and says so once).

Keys: SPACE = start a call, q = hang up / quit. Answers are typed until the speech build.

STATUS: written, NEVER run in the build session (it opens the camera and a window).
"""
from __future__ import annotations

import argparse
import asyncio
import queue
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from analysis.pc import link
from analysis.pc.client_measure import HINT_PERIOD_S, Measurer, hint_for
from analysis.pc.client_state import ClientState
from analysis.pc.frame_source import CameraWatch, WebcamSource
from analysis.pc.remote_tasks import reading_to_dict

RECONNECT_S = 2.0


class LinkThread(threading.Thread):
    """The websocket in its own asyncio loop. Never touches the state machine."""

    def __init__(self, url: str, inbox: "queue.Queue") -> None:
        super().__init__(daemon=True)
        self.url = url
        self.inbox = inbox
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._send_q: Optional["asyncio.Queue"] = None
        self._stop_flag = threading.Event()

    def send(self, type_: str, fields: Dict[str, Any]) -> None:
        loop, q = self._loop, self._send_q
        if loop is not None and q is not None:
            loop.call_soon_threadsafe(q.put_nowait, (type_, fields))

    def stop(self) -> None:
        self._stop_flag.set()

    def run(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._send_q = asyncio.Queue()
        while not self._stop_flag.is_set():
            try:
                transport = await link.connect(self.url)
            except Exception:
                self.inbox.put(("disconnected", None))
                await asyncio.sleep(RECONNECT_S)
                continue
            endpoint = link.Endpoint(transport)
            self.inbox.put(("connected", None))
            sender = asyncio.ensure_future(self._pump(endpoint))
            try:
                await self._receive(endpoint)
            finally:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
                await endpoint.close()
            self.inbox.put(("disconnected", None))
            await asyncio.sleep(RECONNECT_S)

    async def _receive(self, endpoint: link.Endpoint) -> None:
        while not self._stop_flag.is_set():
            try:
                message = await endpoint.recv()
            except link.TransportClosed:
                return
            problem = link.validate_server_message(message)
            if problem is not None:
                print("link: invalid message from the server (%s)" % problem)
                continue
            if message.get("call_id"):
                endpoint.call_id = message["call_id"]
            self.inbox.put(("message", message))
            if message["type"] == "call_result":
                endpoint.call_id = None

    async def _pump(self, endpoint: link.Endpoint) -> None:
        assert self._send_q is not None
        while True:
            type_, fields = await self._send_q.get()
            try:
                await endpoint.send(type_, **fields)
            except link.TransportClosed:
                return


class Speaker:
    """pyttsx3 in its own thread. `available` is False when the package is missing (text-only mode)."""

    def __init__(self) -> None:
        self.available = False
        self.finished: "queue.Queue" = queue.Queue()
        self._lines: "queue.Queue" = queue.Queue()
        try:
            import pyttsx3  # optional

            self._pyttsx3 = pyttsx3
            self.available = True
        except ImportError:
            print("pyttsx3 is not installed: text-only mode (lines are shown, not spoken)")
            return
        threading.Thread(target=self._run, daemon=True).start()

    def speak(self, line_id: str, text: str) -> None:
        self._lines.put((line_id, text))

    def _run(self) -> None:
        engine = self._pyttsx3.init()
        while True:
            line_id, text = self._lines.get()
            try:
                engine.say(text)
                engine.runAndWait()
            except Exception:
                pass
            self.finished.put(line_id)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Client of the PC version: camera, measuring, window.")
    p.add_argument("--server", default="ws://127.0.0.1:8765")
    p.add_argument("--person", default="feodor")
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--mirrored", action="store_true", help="the camera picture is mirrored (many front cameras)")
    p.add_argument("--auto-start", action="store_true", help="start a call as soon as the server answers")
    p.add_argument("--tts", choices=("off", "system"), default="off")
    return p.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    from analysis.pc.client_window import Window  # cv2 window code, imported only when the program runs

    speaker = Speaker() if args.tts == "system" else None
    state = ClientState(args.person, tts=bool(speaker is not None and speaker.available), auto_start=args.auto_start)
    source = WebcamSource(args.camera)
    if not source.opened:
        print("Could not open camera %d. Try --camera 1, or close the program that uses it." % args.camera)
        return 1
    measurer = Measurer(args.mirrored)
    watch = CameraWatch()
    window = Window()
    inbox: "queue.Queue" = queue.Queue()
    link_thread = LinkThread(args.server, inbox)
    link_thread.start()
    last_hint_at = 0.0
    last_frame: Any = None
    try:
        while not state.quit and not window.closed():
            now = time.monotonic()
            ok, frame, t_ms = source.read()
            if ok:
                last_frame = frame
            cam_state, cam_reason = watch.observe(now, ok, frame)
            state.set_camera(cam_state, cam_reason)

            while True:
                try:
                    kind, payload = inbox.get_nowait()
                except queue.Empty:
                    break
                if kind == "message":
                    state.on_server_message(payload)
                else:
                    state.on_connection(kind == "connected")

            due = state.capture_due()
            if due is not None:
                reading = None
                if not (due.task == "arms" and due.expected_side is None):  # settle / swap: nothing to measure
                    reading = measurer.measure(due.task, frame if ok else None, t_ms, due.expected_side)
                state.reading_done(due, reading_to_dict(due.task, reading))
                state.set_local_hint(hint_for(due.task, reading, due.expected_side, due.phase))
            elif state.capture is None and ok and now - last_hint_at >= HINT_PERIOD_S:
                last_hint_at = now
                state.set_local_hint(hint_for("smile", measurer.measure("smile", frame, t_ms, None), None))

            if speaker is not None and state.tts:
                for line_id, text in state.take_speech():
                    speaker.speak(line_id, text)
                while True:
                    try:
                        state.line_finished(speaker.finished.get_nowait())
                    except queue.Empty:
                        break

            state.tick()
            for type_, fields in state.pop_outgoing():
                link_thread.send(type_, fields)

            view = state.view()
            window.draw(frame if ok else last_frame, view)
            key = window.poll_key(view.answer_prompt is not None)
            if key == "start":
                state.request_start()
            elif key == "quit":
                state.request_hang_up_or_quit()
            elif key == "enter":
                state.key_enter()
            elif key == "escape":
                state.key_escape()
            elif key == "backspace":
                state.key_backspace()
            elif key is not None:
                state.key_char(key)
            for type_, fields in state.pop_outgoing():
                link_thread.send(type_, fields)
    except FileNotFoundError:
        print("Model file missing. Run:  python -m analysis.get_models")
        return 1
    finally:
        link_thread.stop()
        source.close()
        measurer.close()
        window.close()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
