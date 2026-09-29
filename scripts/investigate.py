"""Run the lab investigation loop: metrics -> logs -> traces -> root cause.

The lab asks for this to be done by hand, and it is worth doing by hand once so
the steps are understood. This script performs the same three steps repeatably,
so an investigation can be re-run on the same workload and produce comparable
evidence instead of a one-off screenshot.

    1. METRICS  which window is bad, and what kind of bad is it?
    2. LOGS     which requests in that window carry the symptom?
    3. TRACES   which span inside one of those requests is responsible?

Step 3 needs the Langfuse project, so it is skipped (with a clear message) when
no credentials are configured. Steps 1 and 2 read only data/logs.jsonl.

Usage:
    python scripts/investigate.py
    python scripts/investigate.py --latency-threshold 3000 --top 3
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.cli import configure_utf8_stdio  # noqa: E402
from app.metrics import percentile  # noqa: E402

OBSERVATION_FIELDS = "core,basic,time,metadata,metrics,model,usage,prompt,io,trace_context"


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
    return sorted(records, key=lambda r: r.get("ts", ""))


def sent(records: list[dict]) -> list[dict]:
    return [r for r in records if r.get("event") == "response_sent"]


# ------------------------------------------------------------------- step 1/2


def step_metrics(records: list[dict]) -> None:
    print("=" * 78)
    print("STEP 1 - METRICS: what is the symptom and when does it start?")
    print("=" * 78)
    if not sent(records):
        print("no response_sent records yet - run scripts/load_test.py first")
        return

    per_minute: dict[str, list[dict]] = {}
    for r in sent(records):
        per_minute.setdefault(r["ts"][:16], []).append(r)

    print(f"{'minute':<18}{'n':>4}{'p50':>9}{'p95':>9}{'ttft_p95':>10}{'cost':>10}")
    print("-" * 60)
    for minute, rows in sorted(per_minute.items()):
        lat = [r["latency_ms"] for r in rows]
        ttft = [r["ttft_ms"] for r in rows if isinstance(r.get("ttft_ms"), (int, float))]
        cost = sum(r.get("cost_usd", 0.0) for r in rows)
        print(
            f"{minute:<18}{len(rows):>4}{percentile(lat, 50):>9.0f}{percentile(lat, 95):>9.0f}"
            f"{percentile(ttft, 95):>10.0f}{cost:>10.4f}"
        )

    lat = [r["latency_ms"] for r in sent(records)]
    print(f"\nwindow p50={percentile(lat, 50):.0f} ms  p95={percentile(lat, 95):.0f} ms  "
          f"p99={percentile(lat, 99):.0f} ms  max={max(lat)} ms")
    errors = [r for r in records if r.get("event") == "request_failed"]
    print(f"errors in window: {len(errors)}  "
          f"({', '.join(sorted({e.get('error_type', '?') for e in errors})) or 'none'})")
    print("The p95 column is what the SLO is judged on; a p50 that stays flat while p95")
    print("climbs is the signature of a tail problem, not a general slowdown.\n")


def step_logs(records: list[dict], threshold: int, top: int) -> list[dict]:
    print("=" * 78)
    print(f"STEP 2 - LOGS: requests slower than {threshold} ms, and their correlation_id")
    print("=" * 78)
    slow = sorted(
        (r for r in sent(records) if r.get("latency_ms", 0) > threshold),
        key=lambda r: r["latency_ms"],
        reverse=True,
    )
    if not slow:
        print(f"no request exceeded {threshold} ms - the symptom is not in this log\n")
        return []

    print(f"{'correlation_id':<18}{'latency_ms':>11}{'ttft_ms':>9}{'tokens':>10}{'session':>10}  message")
    print("-" * 100)
    for r in slow[:top]:
        preview = (r.get("payload") or {}).get("message_preview", "")
        tokens = f"{r.get('tokens_in', 0)}/{r.get('tokens_out', 0)}"
        print(f"{r['correlation_id']:<18}{r['latency_ms']:>11}{r.get('ttft_ms', 0):>9}"
              f"{tokens:>10}{r.get('session_id', ''):>10}  {preview[:44]}")
    print(f"\n{len(slow)} of {len(sent(records))} responses exceeded {threshold} ms.")
    print("correlation_id is the only field needed for step 3: it is also the trace\n"
          "metadata key, so one identifier joins the log and the trace.\n")
    return slow[:top]


# --------------------------------------------------------------------- step 3


def langfuse_traces(correlation_ids: list[str], since: datetime) -> dict[str, list[dict]]:
    base = os.environ["LANGFUSE_BASE_URL"]
    key = f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}"
    auth = "Basic " + base64.b64encode(key.encode()).decode()

    out: dict[str, list[dict]] = {}
    for correlation_id in correlation_ids:
        params = {
            "fromStartTime": since.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "toStartTime": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "fields": OBSERVATION_FIELDS,
            "filter": json.dumps([
                {
                    "type": "stringObject",
                    "column": "metadata",
                    "operator": "=",
                    "value": correlation_id,
                    "key": "correlation_id",
                }
            ]),
            "limit": "50",
        }
        url = f"{base}/api/public/v2/observations?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={"Authorization": auth})
        with urllib.request.urlopen(request, timeout=60) as response:
            rows = json.load(response)["data"]
        if rows:
            out[correlation_id] = rows
    return out


def step_traces(
    correlation_ids: list[str], since: datetime, retries: int = 6, retry_wait: float = 5.0
) -> None:
    print("=" * 78)
    print("STEP 3 - TRACES: which span is responsible inside those requests?")
    print("=" * 78)
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        print("no LANGFUSE_* credentials in the environment - skipping.")
        print("export the values from .env (or run with --env-file) to include this step\n")
        return

    # Trace ingestion is asynchronous: the SDK exports in the background and the
    # API needs a few seconds before the rows are queryable. Retrying here beats
    # telling the operator "no trace found" for a request that is merely late.
    traces: dict[str, list[dict]] = {}
    for attempt in range(1, retries + 1):
        try:
            traces = langfuse_traces(correlation_ids, since)
        except Exception as exc:  # network, auth or a missing trace
            print(f"could not reach Langfuse: {type(exc).__name__}: {exc}\n")
            return
        if traces:
            if attempt > 1:
                print(f"  (traces became queryable after {attempt} attempts)")
            break
        if attempt < retries:
            time.sleep(retry_wait)

    if not traces:
        print("no trace found for these correlation_ids.\n"
              "Either the traces have not been ingested yet, or the code that produced\n"
              "them predates the CP2 instrumentation - a root observation with no child\n"
              "spans cannot localise a step.\n")
        return

    for correlation_id, rows in traces.items():
        rows.sort(key=lambda r: r.get("startTime", ""))
        root = next((r for r in rows if r.get("parentObservationId") is None), None)
        print(f"\ncorrelation_id {correlation_id}  ->  traceId {rows[0]['traceId']}")
        if root:
            metadata = root.get("metadata") or {}
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            print(f"  trace      : {root.get('traceName')}  user={root.get('userId')} "
                  f"session={root.get('sessionId')}  env={root.get('environment')}")
            print(f"  prompt     : {metadata.get('prompt_name')}:{metadata.get('prompt_version')} "
                  f"({metadata.get('prompt_source')})")

        children = [r for r in rows if r.get("parentObservationId")]
        children.sort(key=lambda r: -(r.get("latency") or 0))
        total = root.get("latency") if root else 0
        for child in children:
            latency = child.get("latency") or 0
            share = (latency / total * 100) if total else 0
            bar = "#" * max(1, int(share / 5))
            print(f"  {child['type']:<10} {child['name']:<20} {latency:>7.3f}s "
                  f"{share:>5.1f}% of trace  {bar}")
            if child["type"] == "GENERATION":
                print(f"             model={child.get('model')} "
                      f"ttft={child.get('timeToFirstToken')} "
                      f"usage={child.get('totalUsage')} cost={child.get('totalCost')}")
        if children:
            slowest = children[0]
            print(f"  -> slowest span: {slowest['name']} ({slowest.get('latency')}s of "
                  f"{total}s). The root cause is inside this span, not in the model call.")

    print()


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs", type=Path, default=REPO_ROOT / "data" / "logs.jsonl")
    parser.add_argument("--latency-threshold", type=int, default=3000,
                        help="ms; a request above this is treated as symptomatic")
    parser.add_argument("--top", type=int, default=3, help="how many slow requests to trace")
    parser.add_argument("--skip-traces", action="store_true")
    parser.add_argument(
        "--trace-retries",
        type=int,
        default=6,
        help="how many times to re-query Langfuse while traces are still ingesting",
    )
    parser.add_argument(
        "--trace-retry-wait",
        type=float,
        default=5.0,
        help="seconds between those retries",
    )
    args = parser.parse_args()

    records = load_records(args.logs)
    if not records:
        print(f"no records in {args.logs}; start the API and run scripts/load_test.py")
        return 1

    first = datetime.fromisoformat(records[0]["ts"].replace("Z", "+00:00"))

    print(f"log file : {args.logs}")
    print(f"records  : {len(records)}  ({records[0]['ts']} .. {records[-1]['ts']})\n")

    step_metrics(records)
    slow = step_logs(records, args.latency_threshold, args.top)
    if slow and not args.skip_traces:
        step_traces(
            [r["correlation_id"] for r in slow],
            first - timedelta(minutes=5),
            retries=args.trace_retries,
            retry_wait=args.trace_retry_wait,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
