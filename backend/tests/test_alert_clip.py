import base64

import httpx
import pytest

from checkin_backend.delivery import ClipShelf, FamilyDelivery
from checkin_backend.hub import Device, DeviceHub
from checkin_backend.link import ClientLink
from checkin_backend.people import Person
from checkin_backend.speech import SpeechGateway
from tests.test_call_service import KEY, Server

PERSON = Person("p_demo", "Ona", "she", "f_demo")
CONFIRMED_FACE = [{"sign": "face", "side": "left", "confirmed": True,
                   "attempts": [{"value": 1, "usual_low": 0, "usual_high": 0.5, "outside": True}] * 2}]


def frame(n: int) -> bytes:
    return b"\xff\xd8" + bytes([n]) + b"\xff\xd9"


def family_feed():
    hub = DeviceHub()
    hub.register(Device("family_phone", "family", None, "f_demo"))
    _, queue = hub.subscribe("family_phone")
    return hub, queue


def test_shelf_joins_both_tries_in_order_and_expires():
    now = [0.0]
    shelf = ClipShelf(ttl_s=60, clock=lambda: now[0])
    clip = shelf.put("call_1", [[(400, frame(3)), (0, frame(1)), (200, frame(2))], [(0, frame(4)), (200, frame(5))]])
    assert clip.frames == (frame(1), frame(2), frame(3), frame(4), frame(5))
    assert clip.period_ms == 200
    body = clip.as_json()
    assert [base64.b64decode(f) for f in body["frames"]] == list(clip.frames)
    now[0] = 61
    assert shelf.get(clip.clip_id) is None
    assert shelf.put("call_1", [[]]) is None


def test_change_message_carries_a_clip_and_the_final_summary_keeps_it():
    hub, queue = family_feed()
    delivery = FamilyDelivery(hub)
    first = delivery.publish("call_1", PERSON, "change_noticed", "mid_call", CONFIRMED_FACE,
                             clip=[[(0, frame(1)), (200, frame(2))]])
    assert first.clip_url and first.clip_url.startswith("/v1/clips/")
    clip_id = first.clip_url.rsplit("/", 1)[1]
    assert delivery.clips.get(clip_id).frames == (frame(1), frame(2))
    final = delivery.publish("call_1", PERSON, "as_usual", "final", clip=None)
    assert final.kind == "change_noticed" and final.clip_url == first.clip_url
    assert queue.qsize() == 2
    assert queue.get_nowait().data["clip_url"] == first.clip_url


def test_calm_messages_never_carry_a_clip():
    hub, _ = family_feed()
    delivery = FamilyDelivery(hub)
    message = delivery.publish("call_2", PERSON, "as_usual", "final", clip=[[(0, frame(1))]])
    assert message.clip_url is None


@pytest.mark.anyio
async def test_link_picks_the_task_behind_the_confirmed_sign_and_releases_frames():
    hub, queue = family_feed()
    link = ClientLink(call_id="call_3", person=PERSON, hub=hub, speech=SpeechGateway(api_key=""),
                      delivery=FamilyDelivery(hub))
    link._frames = {
        "att_smile_1": ("smile", [(0, frame(1)), (200, frame(2))]),
        "att_arms_1": ("arms", [(0, frame(9))]),
        "att_smile_2": ("smile", [(0, frame(3))]),
    }
    assert link.alert_frames([{"sign": "speech", "confirmed": True}]) is None
    assert link.alert_frames([{"sign": "arm", "confirmed": False}]) is None
    assert link.alert_frames([{"sign": "arm", "confirmed": True}]) == [[(0, frame(9))]]
    await link.publish({"status": "change_noticed", "signs": CONFIRMED_FACE}, "mid_call")
    message = queue.get_nowait().data
    clip = link.delivery.clips.get(message["clip_url"].rsplit("/", 1)[1])
    assert clip.frames == (frame(1), frame(2), frame(3))
    link.release_frames()
    assert link.alert_frames(CONFIRMED_FACE) is None


@pytest.mark.anyio
async def test_clip_route_needs_the_demo_key_and_forgets_unknown_clips():
    async with Server("scripted") as server:
        delivery = server.app.state.desk.delivery
        clip = delivery.clips.put("call_x", [[(0, frame(1)), (250, frame(2))]])
        async with httpx.AsyncClient(base_url=server.base) as http:
            denied = await http.get(f"/v1/clips/{clip.clip_id}")
            ok = await http.get(f"/v1/clips/{clip.clip_id}", headers={"X-Demo-Key": KEY})
            missing = await http.get("/v1/clips/clip_nope", headers={"X-Demo-Key": KEY})
    assert denied.status_code == 401
    assert ok.status_code == 200 and ok.headers["cache-control"] == "no-store"
    body = ok.json()
    assert body["period_ms"] == 250 and body["format"] == "jpeg"
    assert [base64.b64decode(f) for f in body["frames"]] == [frame(1), frame(2)]
    assert missing.status_code == 404
