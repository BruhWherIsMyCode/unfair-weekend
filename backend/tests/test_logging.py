"""Exercise file logging through requests, streams, concurrent jobs and a complete synthetic call."""

import asyncio
import json
import logging
import os
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from checkin_backend.app import create_app
from checkin_backend.config import Settings
from checkin_backend.logging_config import LOG_DIR, LoggingSettings, configure_logging, log_context, log_event
from checkin_backend.provider_io import ProviderError, provider_bytes
from checkin_backend.session_logging import SessionEventLog
from checkin_backend.store import Store
from tests.client_sim import SimClient
from tests.test_call_service import KEY, Server, run_call


@pytest.fixture(autouse=True)
def restore_logging():
    """Process logging must not replace pytest's capture handlers or escape this module's tests."""
    names = ("", "checkin_backend", "analysis", "uvicorn", "uvicorn.error", "uvicorn.access")
    states = [(logger, list(logger.handlers), logger.level, logger.propagate, logger.disabled)
              for name in names for logger in [logging.getLogger(name)]]
    yield
    added = set()
    for logger, handlers, level, propagate, disabled in states:
        added.update(handler for handler in logger.handlers if handler not in handlers)
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.propagate = propagate
        logger.disabled = disabled
    for handler in added:
        if getattr(handler, "_checkin_handler", False):
            handler.close()


def outputs(tmp_path, **overrides):
    return LoggingSettings(file=str(tmp_path / "logs" / "backend.log"),
                           error_file=str(tmp_path / "logs" / "errors.log"), console=False, **overrides)


def entries(path):
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def test_environment_defaults_and_one_file_override(monkeypatch):
    for name in ("LOG_FILE", "LOG_ERROR_FILE", "LOG_CONSOLE"):
        monkeypatch.delenv(name, raising=False)
    settings = LoggingSettings.from_environment()
    assert settings.file == str(LOG_DIR / "backend.log")
    assert settings.error_file == str(LOG_DIR / "errors.log")
    monkeypatch.setenv("LOG_ERROR_FILE", "")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    assert LoggingSettings.from_environment() == replace(settings, error_file=None, level="DEBUG")


@pytest.mark.parametrize("overrides", [
    {"level": "TRACE"}, {"max_bytes": 0}, {"max_bytes": True}, {"backup_count": 0},
    {"file": "/tmp/checkin-duplicate.log", "error_file": "/tmp/checkin-duplicate.log"},
])
def test_invalid_logging_settings_fail_before_opening_files(overrides):
    with pytest.raises(ValueError):
        LoggingSettings(**overrides)


def test_files_append_without_duplicate_handlers_and_exceptions_omit_content(tmp_path):
    settings = outputs(tmp_path)
    root_handlers = [handler for handler in logging.getLogger().handlers
                     if not getattr(handler, "_checkin_handler", False)]
    configure_logging(settings, secrets=('a-secret-with-"quotes"\nand-newlines',))
    logger = logging.getLogger("checkin_backend.test")
    logger.info("first start")
    configure_logging(settings, secrets=('a-secret-with-"quotes"\nand-newlines',))
    assert all(handler in logging.getLogger().handlers for handler in root_handlers)
    with log_context(call_id="call_test"):
        log_event(logger, logging.INFO, "second start", token='a-secret-with-"quotes"\nand-newlines')
        try:
            raise RuntimeError("private-answer-and-provider-token")
        except RuntimeError:
            logger.exception("operation failed")
    main = entries(tmp_path / "logs" / "backend.log")
    errors = entries(tmp_path / "logs" / "errors.log")
    assert [e["message"] for e in main] == ["first start", "second start", "operation failed"]
    assert main[1]["token"] == "[redacted]"
    assert len(errors) == 1 and errors[0] == main[2]
    assert errors[0]["call_id"] == "call_test"
    assert errors[0]["exception"]["type"] == "RuntimeError"
    assert errors[0]["exception"]["stack"][-1]["function"] == (
        "test_files_append_without_duplicate_handlers_and_exceptions_omit_content")
    assert "private-answer" not in (tmp_path / "logs" / "errors.log").read_text()
    assert main[0]["timestamp"].endswith("Z") and "call_id" not in main[0]
    if os.name == "posix":
        assert (tmp_path / "logs" / "backend.log").stat().st_mode & 0o777 == 0o600


def test_rotation_is_bounded_and_utf8_json_remains_readable(tmp_path):
    configure_logging(outputs(tmp_path, max_bytes=600, backup_count=2))
    for number in range(20):
        log_event(logging.getLogger("checkin_backend.test"), logging.ERROR, "rotated — įrašas", number=number)
    for name in ("backend.log", "errors.log"):
        files = list((tmp_path / "logs").glob(name + "*"))
        assert {p.name for p in files} == {name, name + ".1", name + ".2"}
        assert all(entries(path) and path.stat().st_size <= 600 for path in files)
        assert entries(tmp_path / "logs" / name)[-1]["number"] == 19


def test_event_loop_failure_omits_task_representation(tmp_path):
    configure_logging(outputs(tmp_path))
    logging.getLogger("asyncio").error("Task exception was never retrieved\nfuture: private task arguments")
    [record] = entries(tmp_path / "logs" / "errors.log")
    assert record["message"] == "Task exception was never retrieved"
    assert "private" not in (tmp_path / "logs" / "errors.log").read_text()


def test_console_and_level_configuration_and_excluding_http_client_debug(tmp_path, capsys):
    configure_logging(LoggingSettings(file=None, error_file=None, level="WARNING"))
    logger = logging.getLogger("checkin_backend.test")
    logger.info("hidden info")
    logging.getLogger("httpx").error("provider url with private query string")
    logger.warning("visible warning")
    records = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert [e["message"] for e in records] == ["visible warning"]
    assert not list(tmp_path.glob("**/*.log"))


def test_requests_have_distinct_ids_and_log_route_status_and_no_body_or_query(tmp_path):
    configure_logging(outputs(tmp_path))
    app = create_app(Settings(demo_key=KEY, call_runner="scripted"))

    @app.get("/boom")
    async def boom():
        raise ValueError("private exception detail")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/v1/health?token=query-secret", headers={"X-Demo-Key": "wrong-secret"})
        assert response.status_code == 401
        response = client.post("/v1/devices", json={"transcript": "private-body"}, headers={"X-Demo-Key": KEY})
        assert response.status_code == 400
        assert client.get("/unmatched-private-path").status_code == 404
        assert client.get("/boom").status_code == 500
    text = (tmp_path / "logs" / "backend.log").read_text()
    assert all(private not in text for private in (
        "query-secret", "wrong-secret", "private-body", "unmatched-private-path", "private exception detail"))
    logs = entries(tmp_path / "logs" / "backend.log")
    rejection = next(e for e in logs if e["message"] == "http.rejected" and e["status"] == 401)
    assert rejection["route"] == "/v1/health"
    other = next(e for e in logs if e["message"] == "http.rejected" and e["status"] == 400)
    assert rejection["request_id"] != other["request_id"]
    failure = next(e for e in entries(tmp_path / "logs" / "errors.log") if e["message"] == "http.failed")
    assert failure["status"] == 500 and failure["exception"]["type"] == "ValueError"
    assert any(e["route"] == "unmatched" for e in logs if e["message"] == "http.completed")


def test_concurrent_context_and_writer_thread_errors_stay_with_their_call(tmp_path):
    configure_logging(outputs(tmp_path))
    logger = logging.getLogger("checkin_backend.test")
    store = Store(tmp_path / "checkin.sqlite3")
    store.open([])

    def failing_job(db):
        raise ValueError("private job detail")

    async def call(name):
        with log_context(call_id=name, request_id="request_" + name):
            await asyncio.sleep(0)
            logger.info("in call")
            store._submit("injected failure", failing_job)

    async def scenario():
        await asyncio.gather(call("call_a"), call("call_b"))
        await store.flush()
        logger.info("outside calls")

    try:
        asyncio.run(scenario())
    finally:
        store.close()
    logs = entries(tmp_path / "logs" / "backend.log")
    calls = [e for e in logs if e["message"] == "in call"]
    errors = entries(tmp_path / "logs" / "errors.log")
    assert {e["call_id"] for e in calls} == {"call_a", "call_b"}
    assert {e["call_id"] for e in errors} == {"call_a", "call_b"}
    assert all(e["request_id"] == "request_" + e["call_id"] for e in errors)
    assert "call_id" not in next(e for e in logs if e["message"] == "outside calls")
    assert "private job detail" not in (tmp_path / "logs" / "errors.log").read_text()


def test_session_events_keep_existing_callback_and_exclude_conversational_content(tmp_path):
    configure_logging(outputs(tmp_path))
    from analysis.session.call_record import LiveLog

    shown = []
    original = LiveLog(shown.append)
    events = SessionEventLog(original, "call_session")
    events.append({"kind": "own_line", "text": "private conversational content"})
    events.append({"kind": "attempt_finished", "attempt_id": "A1", "outcome": "interrupted",
                   "interruption": "camera_unavailable", "reading": {"value": 0.5}})
    assert events == original == shown
    logs = entries(tmp_path / "logs" / "backend.log")
    assert logs[-1]["call_id"] == "call_session" and logs[-1]["attempt_id"] == "A1"
    assert logs[-1]["interruption"] == "camera_unavailable"
    assert "reading" not in logs[-1]
    assert "private conversational content" not in (tmp_path / "logs" / "backend.log").read_text()


def test_provider_failures_log_status_and_duration_without_request_data(tmp_path):
    configure_logging(outputs(tmp_path))

    async def scenario():
        async def respond(request):
            return httpx.Response(503, text="private provider response")

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with log_context(call_id="call_provider"):
                with pytest.raises(ProviderError):
                    await provider_bytes(client, "POST", "https://example.test?token=private-query", provider="test",
                                         limit=1024, timeout_s=1, headers={"Authorization": "Bearer private-key"},
                                         json={"answer": "private-answer"})

    asyncio.run(scenario())
    text = (tmp_path / "logs" / "backend.log").read_text()
    assert "private" not in text
    failure = entries(tmp_path / "logs" / "errors.log")[-1]
    assert failure["call_id"] == "call_provider" and failure["status"] == 503
    assert failure["duration_ms"] >= 0


@pytest.mark.anyio
async def test_complete_http_sse_call_is_logged_without_per_sample_noise(tmp_path):
    configure_logging(outputs(tmp_path))
    async with Server("scripted") as server:
        sim = SimClient(base=server.base, key=KEY)
        status = await run_call(server, sim)
    logs = entries(tmp_path / "logs" / "backend.log")
    call_id = status["call_id"]
    kinds = {e["message"] for e in logs if e.get("call_id") == call_id}
    assert {"call.started", "call.answered", "call.driver_completed", "call.ended", "client.command_sent",
            "client.capture_finished", "family.message_published"} <= kinds
    opened = next(i for i, e in enumerate(logs) if e["message"] == "http.stream_opened")
    finished = next(i for i, e in enumerate(logs) if e["message"] == "call.driver_completed")
    assert opened < finished  # The stream opened while the call was still running.
    assert not any(e.get("route", "").endswith("/samples") for e in logs)
    assert not any("data_base64" in e or "text" in e or "audio" in e for e in logs)
