"""Connection-state races, timeouts and cleanup without provider requests."""

import asyncio
from dataclasses import FrozenInstanceError

import pytest

from checkin_backend.calls import (
    CallConflict,
    CallNotFound,
    CallService,
    CallState,
    EndReason,
)


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


def test_answer_keeps_identity_and_repeated_answer_does_not_extend_deadline():
    async def scenario():
        clock = Clock()
        service = CallService(clock=clock, call_timeout_s=60)
        try:
            ringing = service.start("p_demo")
            clock.now += 5
            connected = service.answer(ringing.call_id)
            assert connected.call_id == ringing.call_id
            assert connected.state == CallState.CONNECTED
            clock.now += 10
            assert service.answer(ringing.call_id) == connected
            # A snapshot cannot be used to alter the service's connection state.
            assert ringing.state == CallState.RINGING
            with pytest.raises(FrozenInstanceError):
                connected.state = CallState.RINGING
            clock.now = connected.answered_at + 60
            assert service.get(connected.call_id).reason == EndReason.DEADLINE
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_only_one_active_call_per_person_and_new_call_after_end():
    async def scenario():
        service = CallService()
        try:
            first = service.start("p_demo")
            with pytest.raises(CallConflict):
                service.start("p_demo")
            other = service.start("p_other")
            service.answer(first.call_id)
            with pytest.raises(CallConflict):
                service.start("p_demo")
            service.end(first.call_id)
            fresh = service.start("p_demo")
            assert fresh.call_id != first.call_id
            assert service.get(other.call_id).state == CallState.RINGING
            # Late cleanup of an old call cannot release the fresh call's slot.
            service.end(first.call_id)
            with pytest.raises(CallConflict):
                service.start("p_demo")
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_decline_is_no_answer_idempotent_and_cannot_be_answered_later():
    async def scenario():
        service = CallService()
        try:
            call = service.start("p_demo")
            ended = service.decline(call.call_id)
            assert ended.state == CallState.NO_ANSWER
            assert ended.reason == EndReason.DECLINED
            assert ended.answered_at is None
            assert service.decline(call.call_id) == ended
            assert await service.wait_ended(call.call_id) == ended
            with pytest.raises(CallConflict):
                service.answer(call.call_id)
            assert service.start("p_demo").call_id != call.call_id
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_answer_at_ring_deadline_loses_even_if_timer_has_not_run():
    async def scenario():
        clock = Clock()
        service = CallService(clock=clock)
        try:
            call = service.start("p_demo")
            clock.now = call.ring_deadline
            with pytest.raises(CallConflict):
                service.answer(call.call_id)
            ended = await service.wait_ended(call.call_id)
            assert ended.state == CallState.NO_ANSWER
            assert ended.reason == EndReason.RING_TIMEOUT
            assert ended.answered_at is None
            assert service.start("p_demo").call_id != call.call_id
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_expired_ring_releases_person_before_timer_gets_event_loop_time():
    async def scenario():
        clock = Clock()
        service = CallService(clock=clock)
        try:
            old = service.start("p_demo")
            clock.now = old.ring_deadline
            fresh = service.start("p_demo")
            assert fresh.call_id != old.call_id
            assert (await service.wait_ended(old.call_id)).reason == EndReason.RING_TIMEOUT
            assert service.get(fresh.call_id).state == CallState.RINGING
        finally:
            await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("answer", [False, True])
def test_timer_resolves_terminal_future_without_further_service_calls(answer):
    async def scenario():
        service = CallService(ring_timeout_s=0.02, call_timeout_s=0.02)
        try:
            call = service.start("p_demo")
            if answer:
                service.answer(call.call_id)
            ended = await asyncio.wait_for(service.wait_ended(call.call_id), timeout=2)
            assert ended.state == (CallState.ENDED if answer else CallState.NO_ANSWER)
            assert ended.reason == (EndReason.DEADLINE if answer else EndReason.RING_TIMEOUT)
            with pytest.raises(CallConflict):
                service.answer(call.call_id)
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_old_ring_timer_cannot_end_answered_call():
    async def scenario():
        clock = Clock()
        service = CallService(clock=clock, call_timeout_s=60)
        try:
            call = service.start("p_demo")
            # Start the ring timer before replacing it on answer.
            await asyncio.sleep(0)
            clock.now = call.ring_deadline - 1
            service.answer(call.call_id)
            clock.now = call.ring_deadline + 1
            await asyncio.sleep(0)
            assert service.get(call.call_id).state == CallState.CONNECTED
            with pytest.raises(CallConflict):
                service.decline(call.call_id)
            assert service.end(call.call_id, EndReason.COMPLETED).reason == EndReason.COMPLETED
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_cancelling_one_waiter_does_not_cancel_terminal_result_for_other_waiters():
    async def scenario():
        service = CallService()
        try:
            call = service.start("p_demo")
            abandoned = asyncio.create_task(service.wait_ended(call.call_id))
            remaining = asyncio.create_task(service.wait_ended(call.call_id))
            await asyncio.sleep(0)
            abandoned.cancel()
            with pytest.raises(asyncio.CancelledError):
                await abandoned
            ended = service.end(call.call_id)
            assert await remaining == ended
            assert await service.wait_ended(call.call_id) == ended
            assert service.end(call.call_id, EndReason.CONNECTION_LOST) == ended
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_shutdown_resolves_all_waiters_and_joins_all_deadline_tasks():
    async def scenario():
        service = CallService()
        ringing = service.start("p_demo")
        connected = service.start("p_other")
        service.answer(connected.call_id)
        waiters = [asyncio.create_task(service.wait_ended(c.call_id)) for c in (ringing, connected)]
        await asyncio.sleep(0)
        await service.aclose()
        assert service.closed
        ended = await asyncio.gather(*waiters)
        assert all(call.state == CallState.ENDED and call.reason == EndReason.SHUTDOWN for call in ended)
        assert not any(task.get_name().startswith("deadline:") for task in asyncio.all_tasks())
        with pytest.raises(CallConflict):
            service.start("p_new")
        with pytest.raises(CallConflict):
            service.answer(connected.call_id)
        await service.aclose()

    asyncio.run(scenario())


def test_unknown_call_does_not_create_state():
    async def scenario():
        service = CallService()
        try:
            with pytest.raises(CallNotFound):
                service.answer("missing")
            with pytest.raises(CallNotFound):
                await service.wait_ended("missing")
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_unanswered_call_cannot_complete_and_shutdown_preserves_expired_ring():
    async def scenario():
        clock = Clock()
        service = CallService(clock=clock)
        call = service.start("p_demo")
        try:
            with pytest.raises(CallConflict):
                service.end(call.call_id, EndReason.COMPLETED)
            clock.now = call.ring_deadline
        finally:
            await service.aclose()
        ended = await service.wait_ended(call.call_id)
        assert ended.state == CallState.NO_ANSWER
        assert ended.reason == EndReason.RING_TIMEOUT

    asyncio.run(scenario())


@pytest.mark.parametrize("person_id", ["", "../person", "person with spaces", "p" * 129])
def test_invalid_person_id_does_not_take_an_active_slot(person_id):
    async def scenario():
        service = CallService()
        try:
            with pytest.raises(ValueError):
                service.start(person_id)
            assert service.start("p_demo").state == CallState.RINGING
        finally:
            await service.aclose()

    asyncio.run(scenario())
