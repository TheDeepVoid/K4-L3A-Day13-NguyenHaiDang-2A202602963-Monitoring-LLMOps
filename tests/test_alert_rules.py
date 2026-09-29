from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ALERTS = REPO_ROOT / "config" / "alert_rules.yaml"


def write_logs(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def log(ts: str, event: str, **extra) -> dict:
    return {
        "ts": ts,
        "level": "info",
        "service": "api",
        "event": event,
        "correlation_id": "req-0000000a",
        **extra,
    }


def evaluate(tmp_path: Path, records: list[dict]) -> tuple[list[dict], subprocess.CompletedProcess]:
    logs = tmp_path / "logs.jsonl"
    write_logs(logs, records)
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "evaluate_alerts.py"),
         "--logs", str(logs), "--json"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    return json.loads(result.stdout) if result.stdout.strip() else [], result


def states(rows: list[dict]) -> dict[str, str]:
    return {r["name"]: r["state"] for r in rows}


def test_every_alert_has_the_fields_the_lab_requires() -> None:
    rules = yaml.safe_load(ALERTS.read_text(encoding="utf-8"))["alerts"]
    assert len(rules) >= 3
    for rule in rules:
        for field in ("name", "severity", "condition", "duration", "type", "channel",
                      "owner", "runbook"):
            assert rule.get(field), f"{rule.get('name')} is missing {field}"
        # Symptom-based, and it must say where it pages and who owns it.
        assert rule["type"] == "symptom-based"
        assert rule["channel"] == "slack"
        assert rule["slack_channel"].startswith("#")
        assert rule["runbook"].startswith("docs/alerts.md#")
        # The machine-readable twin the evaluator uses.
        for field in ("signal", "operator", "threshold", "window_minutes", "for_minutes"):
            assert field in rule, f"{rule['name']} is missing {field}"
        assert rule["operator"] in {">", "<"}
        assert rule["duration"] == f"{rule['for_minutes']} minutes"
        # The runbook anchor has to exist. GitHub derives anchors from heading
        # text, so the runbook carries an explicit <a id="alert-N"> next to it.
        anchor = rule["runbook"].split("#")[1]
        runbook = (REPO_ROOT / "docs" / "alerts.md").read_text(encoding="utf-8")
        assert f'<a id="{anchor}"></a>' in runbook, f"runbook anchor {anchor} is missing"


def workload(count: int, **response: object) -> list[dict]:
    """`count` successful requests spread over three minutes, so every record
    falls inside the shortest alert window (5 minutes)."""
    records = []
    for i in range(count):
        stamp = f"2026-09-29T10:0{i % 3}:{i // 3:02d}Z"
        records.append(log(stamp, "request_received"))
        records.append(log(stamp, "response_sent", **response))
    return records


def test_healthy_traffic_keeps_every_alert_quiet(tmp_path: Path) -> None:
    records = workload(
        15,
        latency_ms=150, ttft_ms=50, tokens_in=30, tokens_out=120,
        cost_usd=0.002, quality_score=0.9, tool_name="retrieval", tool_success=True,
    )
    rows, result = evaluate(tmp_path, records)

    assert result.returncode == 0, result.stdout
    assert set(states(rows).values()) == {"OK"}


def test_slow_retrieval_fires_the_latency_alert_only(tmp_path: Path) -> None:
    records = workload(
        15,
        latency_ms=4000, ttft_ms=50, tokens_in=30, tokens_out=120,
        cost_usd=0.002, quality_score=0.9, tool_name="retrieval", tool_success=True,
    )
    rows, result = evaluate(tmp_path, records)

    assert result.returncode == 1
    current = states(rows)
    assert current["slow_answers"] == "FIRING"
    # Latency is slow but everything works: the other rules must stay quiet,
    # otherwise they are cause-based rather than symptom-based.
    assert current["answers_failing"] == "OK"
    assert current["retrieval_quality_drop"] == "OK"
    assert current["token_cost_runaway"] == "OK"
    slow = next(r for r in rows if r["name"] == "slow_answers")
    assert slow["value"] > slow["threshold"]
    assert slow["slack_channel"] == "#day13-oncall"


def test_tool_failure_fires_errors_and_retrieval_but_not_latency(tmp_path: Path) -> None:
    records = []
    for i in range(15):
        stamp = f"2026-09-29T10:0{i % 3}:{i // 3:02d}Z"
        records.append(log(stamp, "request_received"))
        records.append(
            log(stamp, "request_failed", error_type="RuntimeError",
                tool_name="retrieval", tool_success=False)
        )
    rows, _ = evaluate(tmp_path, records)

    current = states(rows)
    assert current["answers_failing"] == "FIRING"
    assert current["retrieval_quality_drop"] == "FIRING"
    # No response at all, so there is no latency sample to judge.
    assert current["slow_answers"] == "OK"
    failed = next(r for r in rows if r["name"] == "answers_failing")
    assert failed["error_breakdown"] == {"RuntimeError": 15}


def test_output_token_spike_fires_only_the_cost_alert(tmp_path: Path) -> None:
    records = workload(
        15,
        latency_ms=200, ttft_ms=50, tokens_in=30, tokens_out=600,
        cost_usd=0.02, quality_score=0.9, tool_name="retrieval", tool_success=True,
    )
    rows, _ = evaluate(tmp_path, records)

    current = states(rows)
    assert current["token_cost_runaway"] == "FIRING"
    assert current["slow_answers"] == "OK"
    assert current["answers_failing"] == "OK"


def test_empty_log_produces_no_false_pages(tmp_path: Path) -> None:
    rows, result = evaluate(tmp_path, [])

    # A missing signal must not read as a breach.
    assert result.returncode == 0, result.stdout
    assert all(r["value"] is None for r in rows)
    assert set(states(rows).values()) == {"OK"}
