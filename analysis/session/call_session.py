"""CallSession: the coordinator of one call (backend/ARCHITECTURE_PLAN.md sections 3-6).

One task attempt runs at a time. `run_task` creates a new attempt, asks the adapter to play the instruction
and start capture, runs the task module on the attempt's samples, and resolves exactly once: with the
module's result, or with an interrupted result when a rule stops the attempt first.

Events (resource changes, observations, capture acknowledgments, hang-up) are handled by plain synchronous
methods that update state at once and wake the coordinator loop. Resource loss and hang-up therefore always
take effect before a not-yet-committed module result is processed; routine observations only update the
latest snapshot (coalesced). Every decision is appended to `self.log`.

Pure Python + asyncio. No mediapipe, no cv2, no network.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Sequence

from analysis.session import limits, rules
from analysis.session.attempt import (ACCEPTED, ACTIVE, CANCELLED, COMPLETED, FAILED, INTERRUPTED, STOPPING,
                                      Attempt)
from analysis.session.clock import RealClock
from analysis.session.models import (BaselineInfo, Quality, ResourceEvent, SessionResult, SummaryAttempt,
                                     SummarySign, TaskContext, TaskInterrupt, TaskObservation, TaskResult)

_END = object()  # end-of-input marker inside an attempt's sample queue
_NO_BASELINE = {"snapshot_id": "baseline_none", "ready": False, "sessions": 0, "needed": limits.BASELINE_NEEDED_DEFAULT}


class _Runtime:
    """The asyncio pieces of the active attempt."""

    def __init__(self, attempt: Attempt) -> None:
        self.attempt = attempt
        self.queue: "asyncio.Queue" = asyncio.Queue(maxsize=limits.SAMPLE_QUEUE_MAX)
        self.cancel_event = asyncio.Event()  # the cancellation signal given to the module
        self.cancel_ack = asyncio.Event()  # the client acknowledged command.cancel (or the call ended)
        self.driver: Optional["asyncio.Future"] = None
        self.pump: Optional["asyncio.Future"] = None
        self.module_task: Optional["asyncio.Future"] = None
        self.module_finished = False
        self.module_result: Any = None
        self.module_error: Optional[str] = None


class CallSession:
    def __init__(self, call_id: str, person_id: str, person_name: str, adapter: Any, modules: Dict[str, Any],
                 baselines: Optional[Dict[str, Dict[str, Any]]] = None, clock: Any = None, label: str = "live",
                 required_tasks: Sequence[str] = limits.TASKS,
                 initial_resources: Optional[Dict[str, str]] = None) -> None:
        self.call_id = call_id
        self.person_id = person_id
        self.person_name = person_name
        self.adapter = adapter
        self.modules = modules
        self.baselines = baselines or {}
        self.clock = clock or RealClock()
        self.label = label
        self.required_tasks = tuple(required_tasks)
        self.started_at = self.clock.now()

        self.log: List[Dict[str, Any]] = []
        self.attempts: Dict[str, Attempt] = {}
        self.task_attempts: Dict[str, List[Attempt]] = {}
        self.records: List[rules.FinalRecord] = []
        self.results: List[Dict[str, Any]] = []
        self.answers: Dict[str, str] = {"medicine": "unclear", "feeling": "", "unusual": ""}

        self.resources: Dict[str, rules.ResourceInfo] = {r: rules.ResourceInfo() for r in ("camera", "microphone")}
        for res, state in (initial_resources or {}).items():
            self.resources[res] = rules.ResourceInfo(state, None if state == "available" else "unknown", None)
        self._resource_seq: Dict[str, int] = {}
        self._resource_events_seen: set = set()
        self._resource_back = {r: asyncio.Event() for r in self.resources}
        for res, info in self.resources.items():
            if info.state == "available":
                self._resource_back[res].set()
        self._recovery_open: set = set()

        self.hung_up = False
        self.finished = False
        self._final: Optional[Dict[str, Any]] = None
        self.message_id: Optional[str] = None
        self.published: List[Dict[str, Any]] = []
        self._change_published = False

        self._attempt_counter = 0
        self._refusal_counter = 0
        self._active: Optional[Attempt] = None
        self._rt: Optional[_Runtime] = None
        self._busy_worker: Optional["asyncio.Future"] = None
        self._wake = asyncio.Event()
        self._background: set = set()

    # ---- log ----------------------------------------------------------------------------------

    def _log(self, kind: str, **fields: Any) -> None:
        entry = {"t": round(self.clock.now() - self.started_at, 3), "kind": kind}
        entry.update(fields)
        self.log.append(entry)

    # ---- small helpers --------------------------------------------------------------------------

    def _baseline_dict(self, task: str) -> Dict[str, Any]:
        merged = dict(_NO_BASELINE)
        merged.update(self.baselines.get(task) or {})
        return merged

    def _baseline_info(self, task: str) -> BaselineInfo:
        b = self._baseline_dict(task)
        return BaselineInfo(str(b["snapshot_id"]), int(b.get("sessions") or 0),
                            int(b.get("needed") or limits.BASELINE_NEEDED_DEFAULT), bool(b.get("ready")))

    def _records_for(self, task: str) -> List[rules.FinalRecord]:
        return [r for r in self.records if r.task == task]

    def _spawn(self, coro: Any) -> None:
        fut = asyncio.ensure_future(coro)
        self._background.add(fut)
        fut.add_done_callback(self._background.discard)

    def record_answer(self, question_id: str, value: str) -> None:
        if question_id == "medicine":
            self.answers["medicine"] = value if value in ("yes", "no", "unclear") else "unclear"
        elif question_id in ("feeling", "unusual"):
            self.answers[question_id] = str(value)[:2000]
        self._log("answer_recorded", question=question_id)

    # ---- events from the client / call service (synchronous: state first, then wake) -----------------

    def handle_resource_event(self, event: Any) -> str:
        try:
            ev = event if isinstance(event, ResourceEvent) else ResourceEvent.from_dict(event)
            ev.validate()
        except (ValueError, KeyError, TypeError) as exc:
            self._log("resource_event_rejected", error=str(exc))
            return "invalid"
        if ev.event_id in self._resource_events_seen:
            self._log("resource_event_duplicate", event_id=ev.event_id)
            return "duplicate"
        self._resource_events_seen.add(ev.event_id)
        last = self._resource_seq.get(ev.resource)
        if last is not None and ev.sequence <= last:
            self._log("resource_event_stale", event_id=ev.event_id, sequence=ev.sequence, newest=last)
            return "stale"
        self._resource_seq[ev.resource] = ev.sequence
        self.resources[ev.resource] = rules.ResourceInfo(ev.state, ev.reason, ev.event_id)
        self._log("resource_changed", resource=ev.resource, state=ev.state, reason=ev.reason, event_id=ev.event_id)
        if ev.state == "available":
            self._resource_back[ev.resource].set()
            return "applied"
        self._resource_back[ev.resource].clear()

        attempt, rt = self._active, self._rt
        if attempt is None:
            last_attempt = self._last_finished()
            if last_attempt is not None and last_attempt.state == COMPLETED:
                self._log("race", winner="result", detail="loss came after the result was committed; it affects later work only",
                          attempt_id=last_attempt.attempt_id)
            return "applied"
        decision = rules.resource_loss_decision(attempt.task, ev.resource, self.resources[ev.resource])
        if decision is None:
            self._log("resource_unrelated", resource=ev.resource, attempt_id=attempt.attempt_id)
            return "applied"
        if not attempt.is_active:
            self._log("resource_loss_ignored", attempt_id=attempt.attempt_id, state=attempt.state)
            return "applied"
        if rt is not None and rt.module_finished and rt.module_result is not None:
            self._log("race", winner="resource_loss", detail="loss arrived before the usable result was committed",
                      attempt_id=attempt.attempt_id)
        self._request_stop(attempt, "interrupted", decision.code, decision.source_event_id, decision.recoverable,
                           decision.detail)
        return "applied"

    def handle_capture_started(self, attempt_id: str) -> str:
        attempt = self.attempts.get(attempt_id)
        if attempt is None or not attempt.is_active or attempt.capture_command_at is None:
            self._log("capture_started_ignored", attempt_id=attempt_id)
            return "ignored"
        attempt.mark_capture_started(self.clock.now())
        self._log("capture_started", attempt_id=attempt_id)
        self._wake.set()
        return "accepted"

    def handle_cancel_ack(self, attempt_id: str) -> None:
        rt = self._rt
        if rt is not None and rt.attempt.attempt_id == attempt_id:
            rt.cancel_ack.set()
            self._log("cancel_acknowledged", attempt_id=attempt_id)

    def handle_command_failed(self, attempt_id: str, error_code: str) -> None:
        """The client reported that the capture command failed (command.failed)."""
        attempt = self.attempts.get(attempt_id)
        if attempt is None or not attempt.is_active:
            self._log("command_failed_ignored", attempt_id=attempt_id)
            return
        if error_code in ("camera_unavailable", "microphone_unavailable"):
            code, recoverable = error_code, False
        else:
            code, recoverable = "capture_failed", True
        self._request_stop(attempt, "interrupted", code, None, recoverable, "client command.failed: %s" % error_code)

    def hang_up(self) -> None:
        """The client left. Cancel everything; no further adapter work, no new attempts."""
        if self.hung_up:
            return
        self.hung_up = True
        self._log("hang_up")
        attempt, rt = self._active, self._rt
        if attempt is not None and attempt.is_active:
            attempt.begin_stop(self.clock.now(), "cancelled", "call_ended")
        if rt is not None:
            rt.cancel_event.set()
            rt.cancel_ack.set()
        self._wake.set()

    def _on_observation(self, obs: Any) -> str:
        """emit_observation: called by the task module. Applies the observation to the latest snapshot."""
        try:
            if not isinstance(obs, TaskObservation):
                obs = TaskObservation.from_dict(obs)
            obs.validate()
        except (ValueError, KeyError, TypeError) as exc:
            self._log("observation_rejected", error=str(exc))
            return "invalid"
        attempt = self.attempts.get(obs.attempt_id)
        if attempt is None or obs.call_id != self.call_id:
            self._log("observation_ignored", reason="obsolete", attempt_id=obs.attempt_id)
            return "obsolete"
        status = attempt.accept_observation(obs, self.clock.now())
        if status != ACCEPTED:
            self._log("observation_ignored", reason=status, attempt_id=obs.attempt_id, sequence=obs.sequence)
            return status
        if not self.hung_up:
            self._spawn(self._send_progress(attempt, obs))
        self._wake.set()
        return status

    async def _send_progress(self, attempt: Attempt, obs: TaskObservation) -> None:
        hint = None
        if obs.signal == "framing_unusable":
            hint = {"smile": "look_at_camera", "arms": "show_both_arms"}.get(attempt.task)
        event = {"event_id": "progress_%s_%d" % (attempt.attempt_id, obs.sequence), "call_id": self.call_id,
                 "attempt_id": attempt.attempt_id, "type": "task.progress", "elapsed_ms": obs.elapsed_ms,
                 "phase": obs.phase, "input_quality": obs.quality.status, "hint": hint}
        send = getattr(self.adapter, "send_progress", None)
        if send is None or self.hung_up or not attempt.is_active:
            return
        try:
            await send(event)
        except Exception as exc:  # a missing progress hint never stops the task
            self._log("progress_failed", error=type(exc).__name__)

    # ---- running a task -----------------------------------------------------------------------------

    def _refusal_reason(self, task: str) -> Optional[str]:
        if self.hung_up:
            return "call_ended"
        if task not in self.modules or task not in limits.TASKS:
            return "unknown_task"
        if self._active is not None:
            return "attempt_in_progress"
        if self.clock.now() - self.started_at >= limits.CALL_MAX_S:
            return "call_deadline"
        budget = rules.attempt_budget(self._records_for(task))
        if budget is not None:
            return budget
        resource = limits.TASK_RESOURCE[task]
        if self.resources[resource].state != "available":
            return "%s_unavailable" % resource  # an unknown or not-restored resource blocks a new attempt
        if self._busy_worker is not None and not self._busy_worker.done():
            return "analyzer_busy"
        return None

    def _refuse(self, task: str, reason: str) -> Dict[str, Any]:
        self._refusal_counter += 1
        task_name = task if task in limits.TASKS else "smile"
        outcome = "cancelled" if reason == "call_ended" else "failed"
        result = TaskResult(task_name, "refused_%d" % self._refusal_counter, outcome,
                            Quality("unusable", [reason]), "not_observed", self._baseline_info(task_name))
        self._log("task_refused", task=task, reason=reason)
        return result.to_dict()

    def _new_attempt(self, task: str) -> Attempt:
        self._attempt_counter += 1
        prior = self.task_attempts.setdefault(task, [])
        line = ("task_%s" if not prior else "repeat_%s") % task
        attempt = Attempt("A%d" % self._attempt_counter, self.call_id, task, self.clock.now(), line, len(prior) + 1)
        prior.append(attempt)
        self.attempts[attempt.attempt_id] = attempt
        return attempt

    async def run_task(self, task: str) -> Dict[str, Any]:
        """Run one attempt of `task`. Returns the TaskResult dict; resolves exactly once."""
        reason = self._refusal_reason(task)
        if reason is not None:
            return self._refuse(task, reason)
        attempt = self._new_attempt(task)
        rt = _Runtime(attempt)
        self._active, self._rt = attempt, rt
        self._log("attempt_created", attempt_id=attempt.attempt_id, task=task, line=attempt.line_id)
        self._wake.set()
        ticker = asyncio.ensure_future(self._ticker())
        rt.driver = asyncio.ensure_future(self._drive(attempt, rt))
        try:
            result = await self._supervise(attempt, rt)
        finally:
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)
            await self._stop_workers(rt)
            self._active, self._rt = None, None
        return result

    async def _ticker(self) -> None:
        while True:
            await self.clock.sleep(limits.WATCHDOG_TICK_S)
            self._wake.set()

    async def _drive(self, attempt: Attempt, rt: _Runtime) -> None:
        """Play the instruction, start capture, then start the sample pump and the task module."""
        try:
            self._log("say", line=attempt.line_id)
            await self.adapter.say(attempt.line_id)
            if not attempt.is_active:
                return
            definition = self.modules[attempt.task].definition()
            attempt.capture_command_at = self.clock.now()
            self._log("capture_commanded", attempt_id=attempt.attempt_id)
            await self.adapter.start_capture(attempt.attempt_id, attempt.task, definition["capture"])
            if not attempt.is_active:
                return
            rt.pump = asyncio.ensure_future(self._pump(attempt, rt, self.adapter.samples(attempt.attempt_id)))
            rt.module_task = asyncio.ensure_future(self._run_module(attempt, rt))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log("drive_failed", attempt_id=attempt.attempt_id, error=type(exc).__name__)
            self._request_stop(attempt, "interrupted", "capture_failed", None, True, "playback or capture start failed")

    async def _pump(self, attempt: Attempt, rt: _Runtime, source: Any) -> None:
        """Move the attempt's samples from the adapter to the module's bounded queue."""
        try:
            async for sample in source:
                self._on_sample(attempt, rt, sample)
            if attempt.close_input(self.clock.now()):
                self._log("input_closed", attempt_id=attempt.attempt_id, samples=attempt.samples_received)
                await rt.queue.put(_END)
                self._wake.set()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log("pump_failed", attempt_id=attempt.attempt_id, error=type(exc).__name__)
            self._request_stop(attempt, "interrupted", "capture_failed", None, True, "sample source failed")

    def _on_sample(self, attempt: Attempt, rt: _Runtime, sample: Dict[str, Any]) -> None:
        try:
            sequence = int(sample["sequence"])
            captured_ms = int(sample["captured_ms"])
        except (KeyError, TypeError, ValueError):
            self._log("sample_rejected", attempt_id=attempt.attempt_id, reason="malformed")
            return
        status = attempt.accept_sample(sequence, captured_ms, sample.get("data", sample.get("data_base64")),
                                       self.clock.now())
        if status != ACCEPTED:
            self._log("sample_ignored", attempt_id=attempt.attempt_id, sequence=sequence, reason=status)
            return
        try:
            rt.queue.put_nowait(sample)
        except asyncio.QueueFull:
            self._log("sample_dropped", attempt_id=attempt.attempt_id, sequence=sequence, reason="queue_full")
        self._wake.set()

    async def _run_module(self, attempt: Attempt, rt: _Runtime) -> None:
        module = self.modules[attempt.task]
        context = TaskContext(self.call_id, attempt.attempt_id, attempt.task, self._baseline_dict(attempt.task),
                              rt.cancel_event)

        async def samples() -> Any:
            while True:
                item = await rt.queue.get()
                if item is _END:
                    return
                yield item

        try:
            result = await module.run(context, samples(), self._on_observation)
            if attempt.state in ACTIVE:
                rt.module_result = result
            else:
                self._log("late_result_ignored", attempt_id=attempt.attempt_id, state=attempt.state,
                          winner=attempt.stop_reason or attempt.state,
                          detail="the attempt was stopped before this result was committed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if attempt.state in ACTIVE:
                rt.module_error = type(exc).__name__
            self._log("module_error", attempt_id=attempt.attempt_id, error=type(exc).__name__)
        finally:
            rt.module_finished = True
            self._wake.set()

    async def _supervise(self, attempt: Attempt, rt: _Runtime) -> Dict[str, Any]:
        while True:
            await self._wake.wait()
            self._wake.clear()
            result = await self._step(attempt, rt)
            if result is not None:
                return result

    async def _step(self, attempt: Attempt, rt: _Runtime) -> Optional[Dict[str, Any]]:
        if attempt.state == STOPPING:
            return await self._finish_stop(attempt, rt)
        if not attempt.is_active:
            return attempt.result
        now = self.clock.now()
        if now - self.started_at >= limits.CALL_MAX_S:
            self._request_stop(attempt, "cancelled", None, None, False, "timeout")
            return await self._finish_stop(attempt, rt)
        if rt.module_finished:
            return await self._commit_module(attempt, rt)
        decision = rules.decide_interrupt(self._view(attempt, now), self.resources)
        if decision is not None:
            self._request_stop(attempt, "interrupted", decision.code, decision.source_event_id,
                               decision.recoverable, decision.detail)
            return await self._finish_stop(attempt, rt)
        return None

    def _view(self, attempt: Attempt, now: float) -> rules.WatchView:
        return rules.WatchView(
            state=attempt.state, task=attempt.task, now=now, created_at=attempt.created_at,
            capture_command_at=attempt.capture_command_at, capture_started_at=attempt.capture_started_at,
            first_sample_at=attempt.first_sample_at, last_sample_at=attempt.last_sample_at,
            samples_received=attempt.samples_received, last_analyzer_at=attempt.last_analyzer_at,
            framing_bad_since=attempt.framing_bad_since, framing_bad_event_id=attempt.framing_bad_event_id,
            unperformable_event_id=attempt.unperformable_event_id, input_closed=attempt.input_closed,
            window_s=limits.TASK_WINDOW_S[attempt.task])

    # ---- stopping and finishing an attempt ------------------------------------------------------

    def _request_stop(self, attempt: Attempt, kind: str, code: Optional[str], source_event_id: Optional[str],
                      recoverable: bool, why: str) -> bool:
        """Atomically mark the attempt stopping (synchronous, so no result can slip in between)."""
        interrupt = None
        if kind == "interrupted":
            interrupt = TaskInterrupt("interrupt_%s" % attempt.attempt_id, self.call_id, attempt.attempt_id,
                                      code or "capture_failed", source_event_id, recoverable)
        if not attempt.begin_stop(self.clock.now(), kind, why, interrupt):
            return False
        self._log("attempt_stopping", attempt_id=attempt.attempt_id, stop_kind=kind, code=code, why=why,
                  source_event_id=source_event_id)
        if interrupt is not None and code in ("camera_unavailable", "microphone_unavailable") and recoverable:
            self._recovery_open.add(code.split("_")[0])
        if self._rt is not None and self._rt.attempt is attempt:
            self._rt.cancel_event.set()
        self._wake.set()
        return True

    async def _wait_event(self, event: "asyncio.Event", timeout: float) -> bool:
        if event.is_set():
            return True
        waiter = asyncio.ensure_future(event.wait())
        sleeper = asyncio.ensure_future(self.clock.sleep(timeout))
        try:
            await asyncio.wait({waiter, sleeper}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (waiter, sleeper):
                t.cancel()
            await asyncio.gather(waiter, sleeper, return_exceptions=True)
        return event.is_set()

    async def _wait_task(self, task: "asyncio.Future", timeout: float) -> None:
        sleeper = asyncio.ensure_future(self.clock.sleep(timeout))
        try:
            await asyncio.wait({task, sleeper}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            sleeper.cancel()
            await asyncio.gather(sleeper, return_exceptions=True)

    async def _stop_workers(self, rt: _Runtime) -> None:
        """Cancel the driver, pump and module. A module that will not stop keeps the analyzer slot busy."""
        rt.cancel_event.set()
        helpers = [t for t in (rt.driver, rt.pump) if t is not None]
        for t in helpers:
            if not t.done():
                t.cancel()
        module = rt.module_task
        if module is not None and not module.done():
            module.cancel()
        if helpers:
            await asyncio.gather(*helpers, return_exceptions=True)
        if module is not None and not module.done():
            await self._wait_task(module, limits.WORKER_STOP_WAIT_S)
            if not module.done():
                self._busy_worker = module
                self._log("worker_not_stopped", attempt_id=rt.attempt.attempt_id)

    async def _finish_stop(self, attempt: Attempt, rt: _Runtime) -> Dict[str, Any]:
        if attempt.result is not None:
            return attempt.result
        if attempt.capture_command_at is not None and not self.hung_up:
            reason = "timeout" if attempt.stop_reason == "timeout" else "task_interrupted"
            try:
                await self.adapter.cancel_capture(attempt.attempt_id, reason)
            except Exception as exc:
                self._log("cancel_capture_failed", attempt_id=attempt.attempt_id, error=type(exc).__name__)
            if not await self._wait_event(rt.cancel_ack, limits.CANCEL_ACK_S):
                self._log("cancel_ack_timeout", attempt_id=attempt.attempt_id,
                          detail="no acknowledgment in %.1f s; attempt invalidated locally, further samples rejected"
                          % limits.CANCEL_ACK_S)
        await self._stop_workers(rt)

        kind = attempt.stop_kind
        baseline = self._baseline_info(attempt.task)
        if kind == "interrupted":
            code = attempt.stop_interrupt.code
            result = TaskResult(attempt.task, attempt.attempt_id, "interrupted", Quality("unusable", [code]),
                                "uncertain", baseline, None, attempt.stop_interrupt)
            terminal = INTERRUPTED
        elif kind == "failed":
            result = TaskResult(attempt.task, attempt.attempt_id, "failed",
                                Quality("unusable", [attempt.stop_reason or "failed"]), "uncertain", baseline)
            terminal = FAILED
        else:
            reason = "timeout" if attempt.stop_reason == "timeout" else "call_ended"
            result = TaskResult(attempt.task, attempt.attempt_id, "cancelled", Quality("unusable", [reason]),
                                "uncertain", baseline)
            terminal = CANCELLED
        return await self._conclude(attempt, result, terminal)

    async def _commit_module(self, attempt: Attempt, rt: _Runtime) -> Dict[str, Any]:
        """The module finished before anything stopped the attempt: validate and commit its one result."""
        now = self.clock.now()
        baseline = self._baseline_info(attempt.task)
        if rt.module_error is not None:
            attempt.begin_stop(now, "failed", "analysis_error")
            result = TaskResult(attempt.task, attempt.attempt_id, "failed", Quality("unusable", ["analysis_error"]),
                                "uncertain", baseline)
            return await self._conclude(attempt, result, FAILED)
        try:
            raw = rt.module_result
            result = raw if isinstance(raw, TaskResult) else TaskResult.from_dict(raw)
            result.validate()
            if result.attempt_id != attempt.attempt_id or result.task != attempt.task:
                raise ValueError("result belongs to another attempt or task")
            if result.outcome == "interrupted":
                raise ValueError("a task module cannot report interrupted; only the session interrupts")
        except (ValueError, KeyError, TypeError) as exc:
            self._log("invalid_module_result", attempt_id=attempt.attempt_id, error=str(exc))
            attempt.begin_stop(now, "failed", "invalid_result")
            result = TaskResult(attempt.task, attempt.attempt_id, "failed", Quality("unusable", ["invalid_result"]),
                                "uncertain", baseline)
            return await self._conclude(attempt, result, FAILED)
        if result.outcome in ("completed", "could_not_perform"):
            terminal = COMPLETED
        elif result.outcome == "failed":
            attempt.begin_stop(now, "failed", "module_failed")
            terminal = FAILED
        else:
            attempt.begin_stop(now, "cancelled", "module_cancelled")
            terminal = CANCELLED
        self._log("result_committed", attempt_id=attempt.attempt_id, outcome=result.outcome,
                  detail="no loss event had invalidated the window; a later loss affects later work only")
        return await self._conclude(attempt, result, terminal)

    async def _conclude(self, attempt: Attempt, result: TaskResult, terminal: str) -> Dict[str, Any]:
        """Finalize exactly once, remember the result, then publish a confirmed change before returning."""
        d = result.to_dict()
        if not attempt.finalize(d, terminal, self.clock.now()):
            return attempt.result or d
        usable = (result.outcome == "completed" and result.quality.status == "usable"
                  and result.reading is not None)
        self.records.append(rules.FinalRecord(
            attempt.task, attempt.attempt_id, result.outcome, usable,
            result.reading.to_dict() if result.reading is not None else None,
            result.baseline.snapshot_id, result.baseline.ready))
        self.results.append(d)
        self._log("attempt_finished", attempt_id=attempt.attempt_id, outcome=result.outcome, state=terminal,
                  interruption=result.interruption.code if result.interruption else None)
        if usable and not self._change_published and self._confirmed_any():
            self._log("change_confirmed", attempt_id=attempt.attempt_id, task=attempt.task)
            if await self._publish("mid_call"):
                self._change_published = True
        return d

    def _last_finished(self) -> Optional[Attempt]:
        finished = [a for a in self.attempts.values() if a.is_terminal]
        return finished[-1] if finished else None

    # ---- recovery -------------------------------------------------------------------------------

    async def recover_resource(self, resource: str) -> str:
        """Play the fixed recovery line and wait at most RECOVERY_WAIT_S. Returns 'restored' or 'unavailable'.

        One wait per interruption: without an open interruption for this resource nothing is played.
        """
        if self.hung_up or resource not in self.resources:
            return "unavailable"
        if self.resources[resource].state == "available":
            self._recovery_open.discard(resource)
            self._log("recovery", resource=resource, result="already_available")
            return "restored"
        if resource not in self._recovery_open:
            self._log("recovery", resource=resource, result="no_open_interruption")
            return "unavailable"
        self._recovery_open.discard(resource)
        try:
            await self.adapter.say("%s_unavailable" % resource)
        except Exception as exc:
            self._log("recovery_say_failed", error=type(exc).__name__)
        restored = await self._wait_event(self._resource_back[resource], limits.RECOVERY_WAIT_S)
        self._log("recovery", resource=resource, result="restored" if restored else "unavailable")
        return "restored" if restored else "unavailable"

    # ---- summary and finish -----------------------------------------------------------------------

    def _evaluations(self) -> Dict[str, rules.SignEvaluation]:
        return {task: rules.evaluate_sign(self._records_for(task)) for task in limits.TASKS}

    def _confirmed_any(self) -> bool:
        return any(e.confirmed for e in self._evaluations().values())

    def build_session_result(self) -> SessionResult:
        evals = self._evaluations()
        signs = []
        for task in limits.TASKS:
            e = evals[task]
            if not e.attempts:
                continue
            signs.append(SummarySign(
                limits.TASK_SIGN[task], e.side if e.confirmed else None,
                [SummaryAttempt(a["value"], a["usual_low"], a["usual_high"], a["outside"]) for a in e.attempts],
                e.confirmed))
        status = rules.session_status(evals, self.required_tasks)
        used = [t for t in limits.TASKS if evals[t].attempts] or list(self.required_tasks) or ["smile"]
        baselines = [self._baseline_info(t) for t in used]
        return SessionResult(self.person_id, self.label, int(self.clock.now() - self.started_at), dict(self.answers),
                             signs, status, min(b.sessions for b in baselines), max(b.needed for b in baselines))

    def baseline_update(self) -> Dict[str, float]:
        """metric_id -> value that may enter the baseline after this call (nothing if acted or a change was confirmed)."""
        return rules.baseline_values_to_store(self.label, self.records, self._confirmed_any())

    async def _publish(self, phase: str) -> bool:
        summary = self.build_session_result().to_dict()
        try:
            res = await self.adapter.publish(summary, phase, self.message_id)
        except Exception as exc:
            self._log("publish_failed", phase=phase, error=type(exc).__name__)
            return False
        mid = res.get("message_id") if isinstance(res, dict) else None
        if self.message_id is None:
            self.message_id = mid
        elif mid != self.message_id:
            self._log("message_id_changed", expected=self.message_id, got=mid)
        self.published.append({"phase": phase, "message_id": mid, "status": summary["status"]})
        self._log("published", phase=phase, message_id=mid, status=summary["status"])
        return True

    async def finish(self) -> Dict[str, Any]:
        """Say goodbye if the client is still there, publish the final summary, return the SessionResult dict."""
        if self._final is not None:
            return self._final
        if self._active is not None:
            raise RuntimeError("finish() while a task is running")
        if not self.hung_up:
            try:
                await self.adapter.say("bye")
            except Exception as exc:
                self._log("bye_failed", error=type(exc).__name__)
        await self._publish("final")
        self.finished = True
        self._final = self.build_session_result().to_dict()
        self._log("call_finished", status=self._final["status"])
        return self._final
