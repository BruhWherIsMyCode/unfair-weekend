"""The client program of the PC version: camera + measuring + a window. It decides nothing about the call.

    python -m analysis.pc.client --server ws://127.0.0.1:8765 --person feodor [--camera 0] [--mirrored]
                                 [--auto-start] [--tts off|system]

Threads. The MAIN thread owns the camera (frame_source.py), the measuring (client_measure.py), the OpenCV window
(client_window.py) and the state machine (client_state.py). A SECOND thread runs an asyncio loop with the websocket
(link.py). Two thread-safe queues join them: `inbox` (server messages and connection changes, link thread -> main)
and the link thread's send queue (main -> link thread, filled through `LinkThread.send`). The state machine is only
ever touched by the main thread. With --tts system a THIRD thread speaks lines with pyttsx3 (optional import; without
it the client is text-only and says so once).

Keys: SPACE = start a call, q = hang up / quit. Answers are spoken when a microphone and `sounddevice` exist
(--answers speech, the default then; --answers typed forces typing). Speech runs in a FOURTH thread (SpeechThread:
record, ask the server for a single-use token, transcribe with ElevenLabs, send text + word timings, never audio);
line audio from the server plays in a FIFTH thread (AudioPlayer). The API key never reaches this program.

STATUS: written, NEVER run in the build session (it opens the camera, the microphone and a window).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import queue
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from analysis.pc import link
from analysis.pc.client_measure import HINT_PERIOD_S, Measurer, hint_for
from analysis.pc.client_speech import SpeechRunner
from analysis.pc.client_state import ClientState
from analysis.pc.frame_source import CameraWatch, WebcamSource
from analysis.pc.remote_tasks import reading_to_dict
from analysis.session import limits

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


class AudioPlayer:
    """Plays `say` audio (raw PCM 16 kHz) in its own thread with sounddevice (through mic.py). `finished` gets the
    line id when playback ends, also when playing failed (the line then counts as played)."""

    def __init__(self) -> None:
        self.finished: "queue.Queue" = queue.Queue()
        self._lines: "queue.Queue" = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def play(self, line_id: str, pcm: bytes) -> None:
        self._lines.put((line_id, pcm))

    def _run(self) -> None:
        from analysis.pc import mic

        while True:
            line_id, pcm = self._lines.get()
            try:
                mic.play_pcm16k(pcm)
            except Exception as exc:
                print("could not play the line audio (%s)" % type(exc).__name__)
            self.finished.put(line_id)


class SpeechThread(threading.Thread):
    """Runs the speech jobs (record, token, transcribe) so the camera loop never waits for them.

    `level` and `phase` are plain attributes the main thread reads (single writes, safe enough in CPython). A token
    comes back through `deliver_token`, called by the main thread when the server's `stt_token` arrives."""

    def __init__(self, link_thread: LinkThread, language: str) -> None:
        super().__init__(daemon=True)
        from analysis.pc import mic
        from analysis.speech import elevenlabs

        self.link = link_thread
        self.jobs: "queue.Queue" = queue.Queue()
        self.results: "queue.Queue" = queue.Queue()
        self.level = 0.0
        self.phase = ""
        self._waiting: Dict[str, "queue.Queue"] = {}
        self.runner = SpeechRunner(mic.record_until_silence, elevenlabs.transcribe, self._request_token, language,
                                   on_level=self._on_level, on_phase=self._on_phase)

    def _on_level(self, level: float) -> None:
        self.level = level

    def _on_phase(self, phase: str) -> None:
        self.phase = phase

    def _request_token(self, purpose: str, ref: str) -> Tuple[Optional[str], Optional[str]]:
        box: "queue.Queue" = queue.Queue(maxsize=1)
        self._waiting[ref] = box
        self.link.send("stt_token_request", {"purpose": purpose, "ref": ref})
        try:
            return box.get(timeout=limits.STT_TOKEN_WAIT_S)
        except queue.Empty:
            return None, "token_timeout"
        finally:
            self._waiting.pop(ref, None)

    def deliver_token(self, ref: str, token: Optional[str], error: Optional[str]) -> None:
        box = self._waiting.get(ref)
        if box is not None:
            box.put((token, error))

    def run(self) -> None:
        while True:
            job = self.jobs.get()
            if job is None:
                return
            kind = job[0]
            result = self.runner.listen(job[1], job[2]) if kind == "listen" else self.runner.sentence(job[1], job[2])
            self.phase = ""
            self.level = 0.0
            self.results.put(result)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Client of the PC version: camera, measuring, window.")
    p.add_argument("--server", default="ws://127.0.0.1:8765")
    p.add_argument("--person", default="feodor")
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--mirrored", action="store_true", help="the camera picture is mirrored (many front cameras)")
    p.add_argument("--auto-start", action="store_true", help="start a call as soon as the server answers")
    p.add_argument("--tts", choices=("off", "system"), default="off")
    p.add_argument("--answers", choices=("typed", "speech"), default=None,
                   help="speech (default when a microphone and sounddevice exist) or typed answers")
    return p.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    from analysis.pc.client_window import Window  # cv2 window code, imported only when the program runs

    speaker = Speaker() if args.tts == "system" else None
    speech_ok = False
    if args.answers != "typed":
        from analysis.pc import mic

        mic_state, mic_reason = mic.microphone_status()
        speech_ok = mic_state == "available"
        if not speech_ok:
            print("spoken answers are off (%s); answers are typed. Try:  pip install sounddevice" % mic_reason)
    state = ClientState(args.person, tts=bool(speaker is not None and speaker.available), auto_start=args.auto_start,
                        speech=speech_ok, play_audio=speech_ok)
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
    speech = SpeechThread(link_thread, language=os.environ.get("UW_STT_LANGUAGE", "en")) if speech_ok else None
    if speech is not None:
        speech.start()
    player = AudioPlayer() if speech_ok else None
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
                    if payload["type"] == "stt_token" and speech is not None:
                        speech.deliver_token(payload["ref"], payload.get("token"), payload.get("error"))
                    state.on_server_message(payload)
                else:
                    state.on_connection(kind == "connected")

            if speech is not None:  # spoken answers and the sentence: jobs out, results back
                for job in state.take_speech_jobs():
                    speech.jobs.put(job)
                state.set_input_level(speech.level)
                state.speech_phase = speech.phase
                while True:
                    try:
                        result = speech.results.get_nowait()
                    except queue.Empty:
                        break
                    if result["kind"] == "answer":
                        state.speech_answer_done(result)
                    else:
                        state.speech_sentence_done(result)
            if player is not None:
                for line_id, pcm in state.take_audio():
                    player.play(line_id, pcm)
                while True:
                    try:
                        state.line_finished(player.finished.get_nowait())
                    except queue.Empty:
                        break

            due = state.capture_due()
            if due is not None and due.task == "sentence":
                state.reading_done(due, state.keepalive())  # nothing to measure on the camera: tell the server we record
            elif due is not None:
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
        if speech is not None:
            speech.jobs.put(None)
        source.close()
        measurer.close()
        window.close()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
