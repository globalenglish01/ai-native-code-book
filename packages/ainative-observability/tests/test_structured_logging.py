from __future__ import annotations

import io
import json
import logging

from ainative_observability.structured_logging import (
    JsonFormatter,
    SensitiveDataFilter,
    install_structured_logging,
)


def _logger_with_stream(name: str, **filter_kwargs) -> tuple[logging.Logger, io.StringIO]:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(SensitiveDataFilter(**filter_kwargs))
    logger.addHandler(handler)
    return logger, stream


def test_output_is_valid_json_with_expected_standard_fields():
    logger, stream = _logger_with_stream("test.json_shape")
    logger.info("hello world")

    payload = json.loads(stream.getvalue().strip())
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.json_shape"
    assert payload["message"] == "hello world"
    assert "timestamp" in payload


def test_extra_fields_are_included_in_json_output():
    logger, stream = _logger_with_stream("test.extra_fields")
    logger.info("user action", extra={"user_id": "u1", "action": "login"})

    payload = json.loads(stream.getvalue().strip())
    assert payload["user_id"] == "u1"
    assert payload["action"] == "login"


def test_extra_field_matching_sensitive_key_is_redacted_regardless_of_value():
    logger, stream = _logger_with_stream("test.sensitive_extra")
    logger.info("login", extra={"password": "hunter2", "user_id": "u1"})

    payload = json.loads(stream.getvalue().strip())
    assert payload["password"] == "[REDACTED]"
    assert payload["user_id"] == "u1"


def test_sensitive_key_match_is_case_insensitive():
    logger, stream = _logger_with_stream("test.case_insensitive")
    logger.info("login", extra={"PASSWORD": "hunter2"})

    payload = json.loads(stream.getvalue().strip())
    assert payload["PASSWORD"] == "[REDACTED]"


def test_fstring_style_message_with_embedded_secret_is_redacted():
    """The real-world bug class this guards against: a caller uses f-string
    concatenation instead of structured extra= fields, embedding a secret
    directly in the message text — the filter must catch this too, not
    just the extra= field case."""
    logger, stream = _logger_with_stream("test.fstring_secret")
    logger.debug('api_key: "sk-abcdefghijklmnop123456" was used for this call')

    payload = json.loads(stream.getvalue().strip())
    assert "sk-abcdefghijklmnop123456" not in payload["message"]
    assert "[REDACTED]" in payload["message"]


def test_bearer_token_in_message_text_is_redacted():
    logger, stream = _logger_with_stream("test.bearer")
    logger.info("calling downstream with Bearer abcdefghijklmnopqrstuvwxyz1234567890")

    payload = json.loads(stream.getvalue().strip())
    assert "abcdefghijklmnopqrstuvwxyz1234567890" not in payload["message"]


def test_debug_level_logging_is_not_exempt_from_redaction():
    """A real incident this mirrors: a debug-mode env var flips on full
    prompt/response logging that bypasses redaction entirely. The filter
    must apply identically regardless of log level."""
    logger, stream = _logger_with_stream("test.debug_not_exempt")
    logger.debug("debug dump", extra={"password": "hunter2"})

    payload = json.loads(stream.getvalue().strip())
    assert payload["password"] == "[REDACTED]"


def test_non_sensitive_message_passes_through_unchanged():
    logger, stream = _logger_with_stream("test.benign")
    logger.info("request handled successfully")

    payload = json.loads(stream.getvalue().strip())
    assert payload["message"] == "request handled successfully"


def test_filter_never_raises_even_on_malformed_record():
    """The filter must return True and never crash the logging pipeline,
    even if record.msg is something unexpected (e.g. not a plain string)."""
    logger, stream = _logger_with_stream("test.malformed")
    logger.info(12345)  # non-string msg

    assert stream.getvalue()  # something was logged, no crash


def test_exception_info_is_included_as_a_formatted_string_field():
    logger, stream = _logger_with_stream("test.exc_info")
    try:
        raise ValueError("something broke")
    except ValueError:
        logger.exception("operation failed")

    payload = json.loads(stream.getvalue().strip())
    assert "exc_info" in payload
    assert "ValueError: something broke" in payload["exc_info"]


def test_install_structured_logging_attaches_both_formatter_and_filter():
    logger = logging.getLogger("test.install_helper")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    stream = io.StringIO()

    handler = install_structured_logging(logger, handler_factory=lambda: logging.StreamHandler(stream))
    logger.info("test", extra={"secret": "abc"})

    payload = json.loads(stream.getvalue().strip())
    assert payload["secret"] == "[REDACTED]"
    assert isinstance(handler, logging.Handler)
