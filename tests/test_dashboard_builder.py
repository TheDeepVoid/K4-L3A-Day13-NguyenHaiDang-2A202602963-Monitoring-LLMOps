from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

PANEL_IDS = ["latency", "traffic", "errors", "cost", "tokens", "quality"]


def write_logs(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def log(ts: str, event: str, **extra) -> dict:
    return {
        "ts": ts,
        "level": "info",
        "service": "api",
        "event": event,
        "correlation_id": "req-0000000a",
        **extra,
    }


def run_builder(logs: Path, output: Path) -> str:
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "build_dashboard.py"),
            "--logs",
            str(logs),
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return output.read_text(encoding="utf-8")


def panel_html(html: str, panel_id: str) -> str:
    """Slice out one panel section: tile names repeat across panels ('total'
    is both a cost and a token aggregate), so assertions must be scoped."""
    sections = html.split('<section class="panel">')[1:]
    for section in sections:
        if f"id={panel_id} " in section.split("</section>")[0]:
            return section
    raise AssertionError(f"panel {panel_id} not rendered")


def tiles(html: str, panel_id: str) -> dict[str, str]:
    section = panel_html(html, panel_id)
    return dict(
        re.findall(r'tile-label">([a-z_0-9]+)</span><span class="tile-value">([0-9.]+)', section)
    )


def breaches(html: str) -> list[str]:
    return re.findall(r'class="tile breach"><span class="tile-label">([a-z_0-9]+)<', html)


def test_dashboard_renders_exactly_the_six_contract_panels(tmp_path: Path) -> None:
    write_logs(tmp_path / "logs.jsonl", [log("2026-09-29T10:00:00Z", "request_received")])
    html = run_builder(tmp_path / "logs.jsonl", tmp_path / "out.html")

    for panel_id in PANEL_IDS:
        assert f"id={panel_id} " in html
    assert html.count('class="panel"') == 6
    # Panel title, unit and threshold are all visible, as docs/dashboard-spec.md asks.
    for label in ("unit=ms", "unit=usd", "unit=tokens", "unit=percent"):
        assert label in html
    assert "threshold: p95 lte 3000" in html


def test_dashboard_values_match_the_log_maths(tmp_path: Path) -> None:
    records = [
        log("2026-09-29T10:00:00Z", "request_received"),
        log("2026-09-29T10:00:00Z", "request_received"),
        log("2026-09-29T10:00:00Z", "request_received"),
        log("2026-09-29T10:00:00Z", "request_received"),
        log("2026-09-29T10:00:01Z", "request_received"),
        log("2026-09-29T10:00:01Z", "request_failed", error_type="RuntimeError",
            tool_name="retrieval", tool_success=False),
        *[
            log(
                "2026-09-29T10:00:0%dZ" % i,
                "response_sent",
                latency_ms=latency,
                ttft_ms=50,
                tokens_in=10,
                tokens_out=20,
                cost_usd=0.001,
                quality_score=quality,
                tool_name="retrieval",
                tool_success=True,
            )
            for i, (latency, quality) in enumerate([(100, 0.9), (200, 0.8), (300, 0.7), (400, 0.6)], start=2)
        ],
    ]
    write_logs(tmp_path / "logs.jsonl", records)
    html = run_builder(tmp_path / "logs.jsonl", tmp_path / "out.html")

    assert tiles(html, "latency") == {
        "latency_p50": "200",  # nearest-rank of [100,200,300,400]
        "latency_p95": "400",
        "latency_p99": "400",
        "ttft_p95": "50",
    }
    assert tiles(html, "traffic")["count"] == "5"
    # 1 failure out of 5 received
    assert tiles(html, "errors")["error_rate_pct"] == "20.0"
    # 4 successes out of 5 tool rows (the failure counts as a tool row too)
    assert tiles(html, "errors")["tool_success_rate_pct"] == "80.0"
    assert tiles(html, "cost")["total"] == "0.0040"  # USD is rendered to 4 decimals
    assert tiles(html, "tokens") == {
        "tokens_in": "40",
        "tokens_out": "80",
        "total": "120",
    }
    assert tiles(html, "quality")["mean"] == "0.75"


def test_dashboard_includes_the_newest_record(tmp_path: Path) -> None:
    """Regression: a half-open window dropped the record the window is
    anchored on, so the last request silently vanished from every panel."""
    records = [
        log("2026-09-29T10:00:0%dZ" % i, "response_sent", latency_ms=100 + i,
            ttft_ms=50, tokens_in=10, tokens_out=20, cost_usd=0.001,
            quality_score=0.9, tool_name="retrieval", tool_success=True)
        for i in range(1, 4)
    ]
    write_logs(tmp_path / "logs.jsonl", records)
    html = run_builder(tmp_path / "logs.jsonl", tmp_path / "out.html")

    assert tiles(html, "quality")["scored"] == "3"
    assert tiles(html, "cost")["total"] == "0.0030"


def test_dashboard_marks_a_threshold_breach(tmp_path: Path) -> None:
    records = [
        log("2026-09-29T10:00:01Z", "request_received"),
        log("2026-09-29T10:00:01Z", "request_failed", error_type="RuntimeError"),
        log("2026-09-29T10:00:01Z", "response_sent", latency_ms=9000, ttft_ms=50,
            tokens_in=10, tokens_out=20, cost_usd=0.001, quality_score=0.2,
            tool_name="retrieval", tool_success=False),
    ]
    write_logs(tmp_path / "logs.jsonl", records)
    html = run_builder(tmp_path / "logs.jsonl", tmp_path / "out.html")

    flagged = breaches(html)
    # 1 of 1 received failed -> 100% error rate against a 2% threshold;
    # quality mean 0.2 against a 0.75 floor.
    assert "error_rate_pct" in flagged
    assert "mean" in flagged


def test_dashboard_renders_without_any_logs(tmp_path: Path) -> None:
    (tmp_path / "logs.jsonl").write_text("", encoding="utf-8")
    html = run_builder(tmp_path / "logs.jsonl", tmp_path / "out.html")
    assert html.count('class="panel"') == 6
    assert "no data in window" in html


def test_dashboard_refuses_panels_it_does_not_implement(tmp_path: Path) -> None:
    payload = yaml.safe_load((REPO_ROOT / "config" / "dashboard.yaml").read_text(encoding="utf-8"))
    payload["dashboard"]["panels"][0]["id"] = "gpu"
    config_path = tmp_path / "dashboard.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
    write_logs(tmp_path / "logs.jsonl", [log("2026-09-29T10:00:00Z", "request_received")])

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "build_dashboard.py"),
         "--config", str(config_path), "--logs", str(tmp_path / "logs.jsonl"),
         "--output", str(tmp_path / "out.html")],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode != 0
    assert "gpu" in result.stderr
