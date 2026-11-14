from fastapi.testclient import TestClient

from checkin_backend.app import create_app
from checkin_backend.config import Settings

KEY = {"X-Demo-Key": "test-key-only"}


def test_resend_sends_the_last_alert_again_without_creating_anything():
    with TestClient(create_app(Settings(demo_key="test-key-only"))) as client:
        desk = client.app.state.desk
        person = desk.people.get("p_demo")
        url = "/v1/debug/families/f_demo/resend"

        assert client.post(url, headers=KEY).status_code == 404  # nothing sent yet

        alert = desk.delivery.publish("call_a", person, "change_noticed", "final",
                                      signs=[{"sign": "face", "confirmed": True}])
        desk.delivery.publish("call_b", person, "no_answer", "final")
        assert client.post(url, headers=KEY).status_code == 409  # no family phone registered

        assert client.post("/v1/devices", headers=KEY, json={
            "device_id": "fam_phone", "role": "family", "family_id": "f_demo"}).status_code == 204
        response = client.post(url, headers=KEY)
        assert response.status_code == 200
        body = response.json()
        # The latest alert, not the later no-answer message.
        assert body == {"message_id": alert.message_id, "call_id": "call_a", "kind": "change_noticed",
                        "text": alert.text, "family_devices": 1}

        events = list(desk.hub._history["fam_phone"])
        assert [e.type for e in events] == ["message.resend"]
        assert events[0].data["message_id"] == alert.message_id
        assert events[0].event_id.startswith(f"{alert.message_id}_resend_")

        # A second resend gets its own event ID; the family's message list is unchanged.
        client.post(url, headers=KEY)
        events = list(desk.hub._history["fam_phone"])
        assert len({e.event_id for e in events}) == 2
        messages = client.get("/v1/families/f_demo/messages", headers=KEY).json()
        assert [m["message_id"] for m in messages].count(alert.message_id) == 1
        assert len(messages) == 2

        # A named call: its own message, also a plain one.
        by_call = client.post(url, headers=KEY, params={"call_id": "call_b"}).json()
        assert by_call["kind"] == "no_answer"
        assert client.post(url, headers=KEY, params={"call_id": "nope"}).status_code == 404
