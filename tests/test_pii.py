from __future__ import annotations

import json
from pathlib import Path

from app import logging_config
from app.logging_config import get_logger
from app.pii import scrub_text, scrub_value


def test_scrub_email() -> None:
    out = scrub_text("Email me at student@vinuni.edu.vn")
    assert "student@" not in out
    assert "REDACTED_EMAIL" in out


def test_scrub_common_vietnamese_phone_formats() -> None:
    phone_numbers = (
        "0901234567",
        "090 123 4567",
        "090.123.4567",
        "090-123-4567",
        "+84 90 123 4567",
    )

    for phone_number in phone_numbers:
        out = scrub_text(f"Contact: {phone_number}")
        assert phone_number not in out
        assert "REDACTED_PHONE_VN" in out


def test_scrub_cccd_both_plain_and_grouped() -> None:
    for cccd in ("001202012345", "001 202 012 345"):
        out = scrub_text(f"CCCD {cccd}")
        assert cccd not in out
        assert "REDACTED_CCCD" in out


def test_scrub_credit_card_both_plain_and_grouped() -> None:
    for card in ("4111111111111111", "4111 1111 1111 1111", "4111-1111-1111-1111"):
        out = scrub_text(f"card {card}")
        assert card not in out
        assert "REDACTED_CREDIT_CARD" in out


def test_scrub_passport_and_vietnamese_address() -> None:
    assert "REDACTED_PASSPORT" in scrub_text("Passport C1234567 issued 2024")

    # Both accented and unaccented spellings occur in real Vietnamese input.
    for address in (
        "Số 12, Phường Bến Nghé, Quận 1",
        "So 12, Phuong Ben Nghe, Quan 1",
    ):
        out = scrub_text(f"Ship to {address}")
        assert "Bến Nghé" not in out and "Ben Nghe" not in out
        assert "REDACTED_VN_ADDRESS" in out


def test_scrub_value_walks_nested_structures() -> None:
    scrubbed = scrub_value(
        {
            "message": "mail me at a@b.com",
            "nested": {"phones": ["0901234567"], "count": 3},
            "keep": 42,
        }
    )
    assert scrubbed["message"] == "mail me at [REDACTED_EMAIL]"
    assert scrubbed["nested"]["phones"] == ["[REDACTED_PHONE_VN]"]
    assert scrubbed["nested"]["count"] == 3
    assert scrubbed["keep"] == 42


def test_scrub_text_does_not_mangle_operational_values() -> None:
    # Field values the dashboard and validator depend on must survive scrubbing.
    for safe in (
        "req-1a2b3c4d",
        "claude-sonnet-4-5",
        "2026-09-29T08:15:35.575593Z",
        "response_sent",
        "student-01",
    ):
        assert scrub_text(safe) == safe


def test_scrub_event_redacts_pii_from_a_real_log_pipeline(
    tmp_path: Path, monkeypatch
) -> None:
    """The scrubber must run before the file writer, not after."""
    log_path = tmp_path / "logs.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_path)
    logging_config.configure_logging()

    # A call site that forgot summarize_text entirely: only the processor can
    # save this line.
    get_logger().info(
        "raw_event",
        service="api",
        payload={
            "message": "student@vinuni.edu.vn",
            "cccd": "001202012345",
            "card": "4111 1111 1111 1111",
        },
    )

    written = log_path.read_text(encoding="utf-8")
    for leak in ("student@vinuni.edu.vn", "001202012345", "4111 1111 1111 1111"):
        assert leak not in written, leak

    event = json.loads(written.splitlines()[0])
    assert event["payload"]["message"] == "[REDACTED_EMAIL]"
    assert event["payload"]["cccd"] == "[REDACTED_CCCD]"
    assert event["payload"]["card"] == "[REDACTED_CREDIT_CARD]"


def test_scrub_event_redacts_pii_in_rendered_traceback(monkeypatch) -> None:
    scrubbed = logging_config.scrub_event(
        None,
        "error",
        {"exception": "ValueError: bad email student@vinuni.edu.vn in field"},
    )
    assert "student@vinuni.edu.vn" not in scrubbed["exception"]
    assert "[REDACTED_EMAIL]" in scrubbed["exception"]
