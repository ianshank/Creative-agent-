"""Structured logging: configuration, formatters, event records, stage timing."""

from __future__ import annotations

import io
import json
import logging

import pytest

from creative_agent.harness.logging import (
    LOGGER_NAMESPACE,
    configure_logging,
    get_logger,
    log_event,
    timed_stage,
)


@pytest.fixture(autouse=True)
def _restore_logging() -> object:
    """Logging is global; leave the namespace as we found it."""
    logger = logging.getLogger(LOGGER_NAMESPACE)
    saved = (list(logger.handlers), logger.level, logger.propagate)
    yield
    logger.handlers = saved[0]
    logger.setLevel(saved[1])
    logger.propagate = saved[2]


class TestGetLogger:
    def test_namespaces_module_loggers(self) -> None:
        assert get_logger("harness.pipeline").name == "creative_agent.harness.pipeline"

    def test_already_namespaced_names_pass_through(self) -> None:
        assert get_logger("creative_agent.harness.state").name == ("creative_agent.harness.state")
        assert get_logger(LOGGER_NAMESPACE).name == LOGGER_NAMESPACE


class TestConfigureLogging:
    def test_text_format_renders_context(self) -> None:
        stream = io.StringIO()
        configure_logging("INFO", "text", stream=stream)
        log_event(get_logger("t"), logging.INFO, "review.started", artifact_id="doc", cycle=2)
        output = stream.getvalue()
        assert "review.started" in output
        assert "artifact_id='doc'" in output and "cycle=2" in output

    def test_json_format_is_one_object_per_line(self) -> None:
        stream = io.StringIO()
        configure_logging("INFO", "json", stream=stream)
        log_event(get_logger("t"), logging.INFO, "review.started", artifact_id="doc")
        payload = json.loads(stream.getvalue().strip())
        assert payload["event"] == "review.started"
        assert payload["artifact_id"] == "doc"
        assert payload["level"] == "INFO"

    def test_level_filters_lower_records(self) -> None:
        stream = io.StringIO()
        configure_logging("WARNING", "text", stream=stream)
        logger = get_logger("t")
        log_event(logger, logging.INFO, "quiet.event")
        log_event(logger, logging.WARNING, "loud.event")
        assert "quiet.event" not in stream.getvalue()
        assert "loud.event" in stream.getvalue()

    def test_reconfiguration_replaces_handlers(self) -> None:
        configure_logging("INFO", "text", stream=io.StringIO())
        second = io.StringIO()
        configure_logging("INFO", "text", stream=second)
        assert len(logging.getLogger(LOGGER_NAMESPACE).handlers) == 1
        log_event(get_logger("t"), logging.INFO, "only.once")
        assert second.getvalue().count("only.once") == 1

    def test_does_not_touch_the_root_logger(self) -> None:
        before = list(logging.getLogger().handlers)
        configure_logging("DEBUG", "text", stream=io.StringIO())
        assert logging.getLogger().handlers == before
        assert not logging.getLogger(LOGGER_NAMESPACE).propagate

    @pytest.mark.parametrize(("level", "log_format"), [("SHOUTING", "text"), ("INFO", "yaml")])
    def test_invalid_configuration_rejected(self, level: str, log_format: str) -> None:
        with pytest.raises(ValueError):
            configure_logging(level, log_format, stream=io.StringIO())


class TestTimedStage:
    def test_success_logs_start_and_done_with_duration(self) -> None:
        stream = io.StringIO()
        configure_logging("DEBUG", "json", stream=stream)
        with timed_stage(get_logger("t"), "llm.call", kind="row") as extra:
            extra["model"] = "test-model"
        events = [json.loads(line) for line in stream.getvalue().splitlines()]
        assert [e["event"] for e in events] == ["llm.call.start", "llm.call.done"]
        assert events[1]["model"] == "test-model"
        assert events[1]["duration_ms"] >= 0
        assert events[1]["kind"] == "row"

    def test_failure_logs_error_and_reraises(self) -> None:
        stream = io.StringIO()
        configure_logging("DEBUG", "json", stream=stream)
        with pytest.raises(RuntimeError), timed_stage(get_logger("t"), "review"):
            raise RuntimeError("boom")
        events = [json.loads(line) for line in stream.getvalue().splitlines()]
        assert events[-1]["event"] == "review.failed"
        assert events[-1]["error_type"] == "RuntimeError"
        assert events[-1]["level"] == "ERROR"

    def test_debug_events_suppressed_at_info(self) -> None:
        stream = io.StringIO()
        configure_logging("INFO", "text", stream=stream)
        with timed_stage(get_logger("t"), "llm.call"):
            pass
        assert stream.getvalue() == ""
