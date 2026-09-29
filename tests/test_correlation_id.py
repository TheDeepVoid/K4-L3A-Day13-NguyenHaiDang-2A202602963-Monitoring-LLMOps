from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx

from app import logging_config
from app.main import app
from app.middleware import resolve_correlation_id
from app.pii import hash_user_id

GENERATED_ID = re.compile(r"^req-[0-9a-f]{8}$")

CHAT_BODY = {
    "user_id": "student-01",
    "session_id": "session-01",
    "feature": "qa",
    "message": "Explain observability",
}


def post_chat(headers: dict[str, str] | None = None) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.post("/chat", json=CHAT_BODY, headers=headers or {})

    return asyncio.run(send())


def read_events(log_path: Path) -> list[dict]:
    lines = log_path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_inbound_correlation_id_is_reused_when_it_is_safe() -> None:
    assert resolve_correlation_id("edge-abc.123") == "edge-abc.123"
    assert resolve_correlation_id("  req-0123abcd  ") == "req-0123abcd"


def test_unsafe_or_missing_correlation_id_is_replaced_by_a_generated_one() -> None:
    generated = resolve_correlation_id(None)
    assert GENERATED_ID.match(generated), generated

    # A header from an untrusted client must not be able to inject newlines,
    # quotes or unbounded length into the log sink.
    for hostile in (
        "bad\r\n{\"injected\": 1}",
        'quote"break',
        "x" * 200,
        "",
    ):
        assert GENERATED_ID.match(resolve_correlation_id(hostile)), hostile


def test_response_exposes_correlation_id_and_response_time(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(logging_config, "LOG_PATH", tmp_path / "logs.jsonl")

    response = post_chat()

    assert response.status_code == 200
    correlation_id = response.headers["x-request-id"]
    assert GENERATED_ID.match(correlation_id), correlation_id
    assert float(response.headers["x-response-time-ms"]) > 0
    assert response.json()["correlation_id"] == correlation_id


def test_inbound_correlation_id_is_echoed_in_header_and_log(
    monkeypatch, tmp_path: Path
) -> None:
    log_path = tmp_path / "logs.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_path)

    response = post_chat({"x-request-id": "upstream-42"})

    assert response.headers["x-request-id"] == "upstream-42"
    events = read_events(log_path)
    api_events = [e for e in events if e["service"] == "api"]
    assert api_events
    assert {e["correlation_id"] for e in api_events} == {"upstream-42"}


def test_request_log_carries_enrichment_context(monkeypatch, tmp_path: Path) -> None:
    log_path = tmp_path / "logs.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_path)

    post_chat()

    received = next(e for e in read_events(log_path) if e["event"] == "request_received")
    # user_id is hashed, never logged raw.
    assert received["user_id_hash"] == hash_user_id(CHAT_BODY["user_id"])
    assert CHAT_BODY["user_id"] not in json.dumps(received)
    assert received["session_id"] == "session-01"
    assert received["feature"] == "qa"
    assert received["model"] == "claude-sonnet-4-5"
    assert received["env"] == "dev"


def test_context_does_not_leak_between_requests(monkeypatch, tmp_path: Path) -> None:
    log_path = tmp_path / "logs.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_path)

    first = post_chat().json()["correlation_id"]

    # A route that binds no context at all must not inherit the previous one.
    async def hit_health() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            (await client.get("/health")).raise_for_status()

    asyncio.run(hit_health())
    second = post_chat().json()["correlation_id"]

    assert first != second
    sent = [e for e in read_events(log_path) if e["event"] == "response_sent"]
    assert [e["correlation_id"] for e in sent] == [first, second]
