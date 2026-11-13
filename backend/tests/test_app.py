import pytest
from fastapi.testclient import TestClient

from checkin_backend.app import create_app
from checkin_backend.calls import CallState, EndReason
from checkin_backend.config import Settings
from checkin_backend.logging_config import LoggingSettings


def test_health_requires_demo_key_and_does_not_reveal_credentials():
    settings = Settings(demo_key="test-key-only")
    with TestClient(create_app(settings)) as client:
        for headers in ({}, {"X-Demo-Key": "wrong-key"}):
            response = client.get("/v1/health", headers=headers)
            assert response.status_code == 401
            assert response.json() == {
                "code": "unauthorized",
                "message": "A valid demo key is required.",
            }
            assert settings.demo_key not in response.text
        response = client.get("/v1/health", headers={"X-Demo-Key": settings.demo_key})
        assert response.status_code == 200
        assert response.json() == {"ok": True}
    assert settings.demo_key not in repr(settings)


def test_lifespan_closes_active_calls_and_separate_apps_have_separate_state():
    settings = Settings(demo_key="test-key-only")
    app = create_app(settings)
    with TestClient(app) as client:
        calls = app.state.calls
        call = client.portal.call(calls.start, "p_demo")
        client.portal.call(calls.answer, call.call_id)
        with TestClient(create_app(settings)) as other:
            assert other.app.state.calls is not calls
            other.portal.call(other.app.state.calls.start, "p_demo")
        assert client.portal.call(calls.get, call.call_id).state == CallState.CONNECTED
    assert calls.closed
    ended = calls.get(call.call_id)
    assert ended.state == CallState.ENDED
    assert ended.reason == EndReason.SHUTDOWN


def test_environment_factory_needs_no_provider_credentials(monkeypatch, tmp_path):
    database = str(tmp_path / "checkin.sqlite3")
    monkeypatch.setenv("DEMO_KEY", "test-key-only")
    monkeypatch.setenv("DATABASE_PATH", database)
    monkeypatch.setenv("RING_TIMEOUT_S", "7")
    monkeypatch.setenv("CALL_TIMEOUT_S", "90")
    monkeypatch.delenv("SIMLI_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert Settings.from_environment() == Settings(
        demo_key="test-key-only", ring_timeout_s=7, call_timeout_s=90, database_path=database,
        logging=LoggingSettings.from_environment(),
    )
    with TestClient(create_app()) as client:
        assert client.get("/v1/health", headers={"X-Demo-Key": "test-key-only"}).status_code == 200


def test_missing_demo_key_fails_before_startup(monkeypatch):
    monkeypatch.delenv("DEMO_KEY", raising=False)
    with pytest.raises(ValueError, match="DEMO_KEY is required"):
        create_app()


@pytest.mark.parametrize("name", ["ring_timeout_s", "call_timeout_s"])
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True])
def test_invalid_deadlines_are_rejected(name, value):
    with pytest.raises(ValueError, match="finite positive"):
        Settings(demo_key="test-key-only", **{name: value})
