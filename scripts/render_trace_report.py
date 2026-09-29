"""Render a trace list + waterfall from the real Langfuse data.

The Langfuse UI needs an interactive sign-in, which cannot be scripted from a
terminal without someone's password, so the waterfall is built from the same
public API the rest of the evidence uses. Nothing here is invented: every bar,
number and label comes from GET /api/public/v2/observations.

    python scripts/render_trace_report.py --since-minutes 90 --output /tmp/trace.html
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.cli import configure_utf8_stdio  # noqa: E402

FIELDS = "core,basic,time,io,metadata,model,usage,prompt,metrics,trace_context"
TYPE_COLOR = {
    "AGENT": "#334e68",
    "RETRIEVER": "#2f80ed",
    "GENERATION": "#7b5cd6",
    "SPAN": "#8a96a3",
}

CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 26px 30px; background: #f5f7fa; color: #12181f;
  font: 13px/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
h1 { font-size: 19px; margin: 0 0 2px; }
.sub { color: #5b6875; font-size: 12px; margin-bottom: 14px; }
.banner { background: #fff8e6; border: 1px solid #f0d9a0; border-radius: 6px;
  padding: 9px 12px; font-size: 12px; margin-bottom: 16px; color: #6b5316; }
h2 { font-size: 14px; margin: 22px 0 8px; }
table { border-collapse: collapse; width: 100%; background: #fff;
  border: 1px solid #dde3ea; border-radius: 8px; overflow: hidden; }
th { text-align: left; font-size: 10px; text-transform: uppercase; letter-spacing: .04em;
  color: #6b7885; background: #f0f3f7; padding: 8px 10px; }
td { padding: 7px 10px; border-top: 1px solid #eef1f5; font-variant-numeric: tabular-nums; }
tr.affected td { background: #fff5f5; }
.tag { display: inline-block; padding: 1px 7px; border-radius: 10px; color: #fff;
  font-size: 10px; font-weight: 600; letter-spacing: .02em; }
.wf { background: #fff; border: 1px solid #dde3ea; border-radius: 8px; padding: 14px 16px 10px; }
.wfrow { display: flex; align-items: center; margin-bottom: 6px; }
.wfname { width: 210px; font-size: 12px; }
.wftrack { position: relative; height: 22px; flex: 1; background:
  repeating-linear-gradient(90deg, #f4f6f9 0 1px, transparent 1px 100%) ;
  background-size: 10% 100%; border-radius: 3px; }
.wfbar { position: absolute; top: 3px; height: 16px; border-radius: 3px; }
.wfms { width: 78px; text-align: right; font-size: 11px; color: #3a4652;
  font-variant-numeric: tabular-nums; }
.axis { display: flex; justify-content: space-between; margin-left: 210px; padding-right: 84px;
  color: #8a96a3; font-size: 10px; border-top: 1px solid #eef1f5; padding-top: 4px; }
.detail { background: #fff; border: 1px solid #dde3ea; border-radius: 8px; padding: 14px 16px; }
pre { background: #f7f9fb; border: 1px solid #e6eaf0; border-radius: 6px; padding: 10px 12px;
  font-size: 11px; margin: 6px 0 0; white-space: pre-wrap; word-break: break-word;
  max-height: 250px; overflow-y: auto; }
.kv { display: grid; grid-template-columns: 190px 1fr; gap: 2px 12px; font-size: 12px; }
.kv div:nth-child(odd) { color: #6b7885; }
footer { margin-top: 18px; color: #6b7885; font-size: 11px; }
code { background: #eceff3; padding: 1px 4px; border-radius: 3px; }
"""


def root_of(obs: list[dict]) -> dict:
    """The root observation of a trace.

    parentObservationId is the physical parent, isRootObservation the logical
    one, and an application root may legitimately have a parent. Fall back to
    the earliest observation so a partially ingested trace still renders
    instead of crashing the whole report.
    """
    for candidate in (
        next((o for o in obs if o.get("isRootObservation")), None),
        next((o for o in obs if o.get("parentObservationId") is None), None),
    ):
        if candidate is not None:
            return candidate
    return min(obs, key=lambda o: o["startTime"])


def get(base: str, auth: str, **params) -> list[dict]:
    """Fetch every page.

    The v2 endpoint caps a page at 1000 rows and paginates with a cursor. A
    busy project easily exceeds that, and silently dropping the tail loses the
    root observation of some traces, which then render as if the root were a
    generation.
    """
    rows: list[dict] = []
    cursor = None
    for _ in range(20):  # bounded: 20k observations is far past this lab
        page_params = dict(params)
        if cursor:
            page_params["cursor"] = cursor
        url = f"{base}/api/public/v2/observations?{urllib.parse.urlencode(page_params)}"
        request = urllib.request.Request(url, headers={"Authorization": auth})
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = json.load(response)
        rows.extend(payload.get("data") or [])
        cursor = (payload.get("meta") or {}).get("cursor")
        if not cursor:
            break
    return rows


def maybe_json(value):
    if isinstance(value, str) and value[:1] in "[{":
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since-minutes", type=int, default=90)
    parser.add_argument("--output", type=Path, default=Path("trace-report.html"))
    parser.add_argument("--challenge-id", default="")
    parser.add_argument("--threshold-ms", type=int, default=2000)
    parser.add_argument(
        "--section", choices=["all", "list", "waterfall"], default="all",
        help="render only one part, so each screenshot stays readable",
    )
    parser.add_argument(
        "--max-traces", type=int, default=0,
        help="keep only the N most recent traces in the list (0 = all)",
    )
    args = parser.parse_args()

    base = os.environ["LANGFUSE_BASE_URL"]
    key = f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}"
    auth = "Basic " + base64.b64encode(key.encode()).decode()

    since = datetime.now(timezone.utc) - timedelta(minutes=args.since_minutes)
    rows = get(
        base, auth,
        fromStartTime=since.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        toStartTime=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        fields=FIELDS, limit="1000",
    )

    traces: dict[str, list[dict]] = {}
    for row in rows:
        traces.setdefault(row["traceId"], []).append(row)

    # Traces that actually localise a step: root + at least one child.
    interesting = {
        tid: obs for tid, obs in traces.items()
        if any(o.get("parentObservationId") for o in obs)
        and any(o["type"] == "GENERATION" for o in obs)
    }
    order = sorted(
        interesting.items(),
        key=lambda kv: min(parse_ts(o["startTime"]) for o in kv[1]),
    )
    if args.max_traces:
        order = order[-args.max_traces:]

    total = len(order)
    slow = 0
    list_rows = []
    for tid, obs in order:
        root = root_of(obs)
        gen = next((o for o in obs if o["type"] == "GENERATION"), None)
        ret = next((o for o in obs if o["type"] == "RETRIEVER"), None)
        metadata = maybe_json(root.get("metadata")) or {}
        latency = (root.get("latency") or 0) * 1000
        if latency > args.threshold_ms:
            slow += 1
        list_rows.append(
            "<tr class='{cls}'>"
            "<td><code>{cid}</code></td>"
            "<td><span class='tag' style='background:{color}'>{rtype}</span></td>"
            "<td>{ts}</td>"
            "<td>{lat:.0f} ms</td>"
            "<td>{ret:.3f} s</td>"
            "<td>{ttft:.2f} s</td>"
            "<td>{usage}</td>"
            "<td>${cost:.6f}</td>"
            "<td>{prompt}</td>"
            "<td>{name}</td>"
            "</tr>".format(
                cls="affected" if latency > args.threshold_ms else "",
                cid=html.escape(metadata.get("correlation_id") or "-"),
                color=TYPE_COLOR.get(root["type"], "#8a96a3"),
                rtype=root["type"],
                ts=parse_ts(root["startTime"]).strftime("%H:%M:%S"),
                lat=latency,
                ret=(ret.get("latency") or 0) if ret else 0,
                ttft=(gen.get("timeToFirstToken") or 0) if gen else 0,
                usage=f"{gen.get('inputUsage', 0)}/{gen.get('outputUsage', 0)}" if gen else "-",
                cost=(gen.get("totalCost") or 0) if gen else 0,
                prompt=html.escape(
                    f"{metadata.get('prompt_name')}:{metadata.get('prompt_version')}"
                    f" ({metadata.get('prompt_source')})"
                ) if metadata.get("prompt_name") else "-",
                name=html.escape(root.get("traceName") or "-"),
            )
        )

    # Waterfall for the slowest traces.
    # Waterfall for the slowest traces: the root cause is visible in one image.
    slowest = sorted(
        interesting.items(),
        key=lambda kv: -(root_of(kv[1]).get("latency") or 0),
    )[:2]
    waterfalls = []
    for tid, obs in slowest:
        obs = sorted(obs, key=lambda o: o["startTime"])
        root = root_of(obs)
        metadata = maybe_json(root.get("metadata")) or {}
        t0 = parse_ts(root["startTime"])
        span = max(root.get("latency") or 0.001, 0.001)
        rows_html = []
        for o in obs:
            start = (parse_ts(o["startTime"]) - t0).total_seconds()
            dur = o.get("latency") or 0
            left = max(0.0, start / span * 100)
            width = max(0.6, dur / span * 100)
            rows_html.append(
                f"<div class='wfrow'>"
                f"<div class='wfname'><span class='tag' style='background:{TYPE_COLOR.get(o['type'], '#8a96a3')}'>{o['type']}</span> {html.escape(o['name'])}</div>"
                f"<div class='wftrack'><div class='wfbar' style='left:{left:.2f}%;width:{width:.2f}%;"
                f"background:{TYPE_COLOR.get(o['type'], '#8a96a3')}'></div></div>"
                f"<div class='wfms'>{dur * 1000:.0f} ms</div></div>"
            )
        ticks = "".join(
            f"<span>{span * f:.3f}s</span>" for f in (0, 0.25, 0.5, 0.75, 1)
        )
        gen = next((o for o in obs if o["type"] == "GENERATION"), None)
        detail = ""
        if gen is not None:
            detail = (
                "<h2>Observation detail - the generation, from the API</h2><div class='detail'><div class='kv'>"
                f"<div>name</div><div>{html.escape(gen['name'])} ({gen['type'].lower()})</div>"
                f"<div>model</div><div>{html.escape(str(gen.get('model')))}</div>"
                f"<div>model_parameters</div><div>{html.escape(json.dumps(gen.get('modelParameters') or {}))}</div>"
                f"<div>usage_details</div><div>{html.escape(json.dumps(maybe_json(gen.get('usageDetails')) or {}))}</div>"
                f"<div>cost_details</div><div>{html.escape(json.dumps(maybe_json(gen.get('costDetails')) or {}))} (ingested, not inferred)</div>"
                f"<div>time_to_first_token</div><div>{gen.get('timeToFirstToken')} s</div>"
                f"<div>prompt link</div><div>{html.escape(str(gen.get('promptName')))} v{html.escape(str(gen.get('promptVersion')))}</div>"
                f"<div>observation id</div><div><code>{gen['id']}</code></div>"
                f"<div>input</div><div><pre>{html.escape(json.dumps(maybe_json(gen.get('input')), ensure_ascii=False, indent=1)[:700])}</pre></div>"
                f"<div>output</div><div><pre>{html.escape(json.dumps(maybe_json(gen.get('output')), ensure_ascii=False, indent=1)[:400])}</pre></div>"
                "</div></div>"
            )

        waterfalls.append(
            f"<h2>Waterfall &mdash; trace <code>{tid}</code></h2>"
            f"<div class='sub'>correlation_id <code>{html.escape(metadata.get('correlation_id', '-'))}</code> "
            f"&middot; session {html.escape(str(metadata.get('session_id') or root.get('sessionId')))} "
            f"&middot; user {html.escape(str(root.get('userId')))} "
            f"&middot; total {root.get('latency'):.3f}s</div>"
            f"<div class='wf'>{''.join(rows_html)}<div class='axis'>{ticks}</div></div>{detail}"
        )

    challenge = (
        f"challenge {args.challenge_id} &middot; affected feature requests above "
        f"{args.threshold_ms} ms are highlighted"
        if args.challenge_id else ""
    )

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Langfuse traces - Day 13 lab</title><style>{CSS}</style></head><body>
<h1>Langfuse trace list and waterfall</h1>
<div class="sub">project day13-k4-l3a-2A202602963 &middot; source
  GET /api/public/v2/observations &middot; window last {args.since_minutes} min
  &middot; generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</div>
<div class="banner">Rendered from the Langfuse public API, not from a UI screenshot:
  the Langfuse web interface needs an interactive sign-in that cannot be scripted
  from a terminal without someone's credentials. Every value below is the value
  Langfuse returned. {challenge}</div>
{f"<h2>Trace list &mdash; {total} traces, {slow} slower than {args.threshold_ms} ms</h2>"
 f"<table><tr><th>correlation_id</th><th>root type</th><th>start</th><th>total</th>"
 f"<th>retrieval</th><th>ttft</th><th>tokens in/out</th><th>cost</th>"
 f"<th>prompt</th><th>trace name</th></tr>{''.join(list_rows)}</table>"
 if args.section in ("all", "list") else ""}
{''.join(waterfalls) if args.section in ("all", "waterfall") else ""}
<footer>Span colours: agent = dark, retriever = blue, generation = purple,
  prompt lookup = grey. Bars are positioned and sized from each observation's
  own start time and latency.</footer>
</body></html>"""

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(page, encoding="utf-8")
    print(f"wrote {args.output}  traces={total} slow={slow}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
