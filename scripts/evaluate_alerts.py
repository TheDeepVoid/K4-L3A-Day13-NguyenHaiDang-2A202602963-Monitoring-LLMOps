"""Evaluate config/alert_rules.yaml against data/logs.jsonl.

An alert rule that nobody has ever seen fire is a rule you cannot trust. This
recomputes every rule from the structured log and prints OK / FIRING, so the
same workload that feeds the dashboard also proves the alerts.

Usage:
    python scripts/evaluate_alerts.py
    python scripts/evaluate_alerts.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from app.cli import configure_utf8_stdio  # noqa: E402
from app.metrics import percentile  # noqa: E402

REQUIRED_FIELDS = ("name", "severity", "condition", "duration", "type", "channel", "owner", "runbook")


def load_records(path: Path) -> list[dict]:
    records = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def window(records: list[dict], minutes: int) -> list[dict]:
    """Records in the last `minutes`, measured back from the newest record."""
    stamps = [parse_ts(r["ts"]) for r in records if r.get("ts")]
    if not stamps:
        return []
    end = max(stamps)
    start = end - timedelta(minutes=minutes)
    return [r for r in records if r.get("ts") and start <= parse_ts(r["ts"]) <= end]


def signal_value(signal: str, records: list[dict]) -> float | None:
    """Compute one alert signal from the log. Same maths as the dashboard."""
    sent = [r for r in records if r.get("event") == "response_sent"]
    received = [r for r in records if r.get("event") == "request_received"]
    failed = [r for r in records if r.get("event") == "request_failed"]

    if signal == "latency_p95_ms":
        values = [r["latency_ms"] for r in sent if isinstance(r.get("latency_ms"), (int, float))]
        return float(percentile(values, 95)) if values else None
    if signal == "ttft_p95_ms":
        values = [r["ttft_ms"] for r in sent if isinstance(r.get("ttft_ms"), (int, float))]
        return float(percentile(values, 95)) if values else None
    if signal == "error_rate_pct":
        return (len(failed) / len(received) * 100) if received else None
    if signal == "retrieval_success_rate_pct":
        rows = [r for r in records if r.get("tool_success") is not None]
        if not rows:
            return None
        return sum(1 for r in rows if r["tool_success"] is True) / len(rows) * 100
    if signal == "quality_mean":
        values = [r["quality_score"] for r in sent if isinstance(r.get("quality_score"), (int, float))]
        return sum(values) / len(values) if values else None
    if signal == "cost_per_minute_usd":
        if not sent:
            return None
        stamps = sorted(parse_ts(r["ts"]) for r in sent)
        span = max(1.0, (stamps[-1] - stamps[0]).total_seconds() / 60)
        return sum(r.get("cost_usd", 0.0) for r in sent) / span
    raise SystemExit(f"unknown alert signal: {signal}")


def breached(value: float, operator: str, threshold: float) -> bool:
    return value > threshold if operator == ">" else value < threshold


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "alert_rules.yaml")
    parser.add_argument("--logs", type=Path, default=REPO_ROOT / "data" / "logs.jsonl")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    rules = yaml.safe_load(args.config.read_text(encoding="utf-8"))["alerts"]
    records = load_records(args.logs)

    results = []
    for rule in rules:
        missing = [f for f in REQUIRED_FIELDS if not rule.get(f)]
        if missing:
            raise SystemExit(f"alert '{rule.get('name')}' is missing required fields: {missing}")
        if rule.get("type") != "symptom-based":
            raise SystemExit(f"alert '{rule['name']}' must be symptom-based")

        rows = window(records, int(rule.get("window_minutes", 10)))
        value = signal_value(rule["signal"], rows)
        firing = value is not None and breached(value, rule["operator"], float(rule["threshold"]))
        errors = Counter(r.get("error_type") for r in rows if r.get("event") == "request_failed")
        results.append(
            {
                "name": rule["name"],
                "severity": rule["severity"],
                "state": "FIRING" if firing else "OK",
                "signal": rule["signal"],
                "value": None if value is None else round(value, 4),
                "operator": rule["operator"],
                "threshold": rule["threshold"],
                "window_minutes": rule.get("window_minutes"),
                "for_minutes": rule.get("for_minutes"),
                "samples": len(rows),
                "error_breakdown": dict(errors),
                "slack_channel": rule.get("slack_channel"),
                "owner": rule["owner"],
                "runbook": rule["runbook"],
            }
        )

    firing = [r for r in results if r["state"] == "FIRING"]
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 1 if firing else 0

    print(f"alert rules: {args.config.relative_to(REPO_ROOT)}")
    print(f"log records in file: {len(records)}\n")
    header = f"{'alert':<22}{'state':<9}{'signal':<26}{'value':>12}  {'rule':<16}{'n':>4}  owner"
    print(header)
    print("-" * len(header))
    for r in results:
        value = "-" if r["value"] is None else f"{r['value']:g}"
        rule = f"{r['operator']} {r['threshold']}"
        print(
            f"{r['name']:<22}{r['state']:<9}{r['signal']:<26}{value:>12}  {rule:<16}"
            f"{r['samples']:>4}  {r['owner']}"
        )
    for r in firing:
        print(f"\n  {r['name']} -> Slack {r['slack_channel']} ({r['owner']}), runbook {r['runbook']}")
        if r["error_breakdown"]:
            print(f"    error breakdown in window: {r['error_breakdown']}")
    print(f"\n{len(firing)} of {len(results)} alerts firing")
    return 1 if firing else 0


if __name__ == "__main__":
    raise SystemExit(main())
