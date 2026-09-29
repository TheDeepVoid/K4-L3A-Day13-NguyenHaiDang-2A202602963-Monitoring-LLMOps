"""Render the six-panel operations dashboard from data/logs.jsonl.

config/dashboard.yaml is the contract, not a description: this script reads the
panel list, the events, the fields, the unit and the threshold from it and
refuses to run if a panel id it does not know about appears there. The `query`
strings in the YAML are pseudocode (docs/DASHBOARD_SETUP.md), so the maths is
implemented here in Python instead of being copied into another tool.

Usage:
    python scripts/build_dashboard.py                     # -> data/dashboard.html
    python scripts/build_dashboard.py --output out.html
    python scripts/build_dashboard.py --anchor now        # wall-clock window
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from app.cli import configure_utf8_stdio  # noqa: E402
from app.metrics import percentile  # noqa: E402

KNOWN_PANELS = {"latency", "traffic", "errors", "cost", "tokens", "quality"}


# --------------------------------------------------------------------------- data


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


def select_window(records: list[dict], minutes: int, anchor: str) -> tuple[datetime, datetime]:
    stamps = [parse_ts(r["ts"]) for r in records if r.get("ts")]
    if not stamps:
        now = datetime.now(timezone.utc)
        return now - timedelta(minutes=minutes), now
    end = datetime.now(timezone.utc) if anchor == "now" else max(stamps)
    return end - timedelta(minutes=minutes), end


def in_window(records: list[dict], start: datetime, end: datetime) -> list[dict]:
    """Inclusive on both ends: `end` is the "as of" moment, so the newest record
    must be included - a half-open window silently drops it when the window is
    anchored on the latest log line."""
    return [
        r
        for r in records
        if r.get("ts") and start <= parse_ts(r["ts"]) <= end
    ]


def by_minute(records: list[dict]) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        buckets[parse_ts(r["ts"]).strftime("%H:%M")].append(r)
    return dict(sorted(buckets.items()))


def mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0


# ------------------------------------------------------------------- svg helpers


def svg_line_series(series: list[tuple[str, float]], threshold: dict, unit: str) -> str:
    """Bar chart of one value per time bucket with a dashed threshold line."""
    width, height, pad_l, pad_b, pad_t = 460, 150, 44, 26, 12
    if not series:
        return '<p class="empty">no data in window</p>'

    values = [v for _, v in series]
    thr_value = threshold.get("value")
    # A panel may legitimately have no threshold line (traffic uses the value
    # only for its tile), so never mix None into the scale.
    top = max(values + ([thr_value] if isinstance(thr_value, (int, float)) else [])) or 1
    inner_w = width - pad_l - 8
    step = inner_w / len(series)
    bar_w = max(3, step * 0.62)

    parts = [
        f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" '
        f'aria-label="time series in {unit}">'
    ]
    # y axis: 0 and top
    for frac in (0.0, 0.5, 1.0):
        y = pad_t + (height - pad_t - pad_b) * (1 - frac)
        value = top * frac
        parts.append(
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - 8}" y2="{y:.1f}" class="grid"/>'
            f'<text x="{pad_l - 6}" y="{y + 3:.1f}" class="axis" text-anchor="end">'
            f"{value:.0f}</text>"
        )
    for i, (label, value) in enumerate(series):
        x = pad_l + i * step + (step - bar_w) / 2
        bar_h = (height - pad_t - pad_b) * (value / top)
        y = height - pad_b - bar_h
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" class="bar">'
            f"<title>{escape(label)}: {value:.4g} {escape(unit)}</title></rect>"
        )
        if len(series) <= 8 or i % max(1, len(series) // 6) == 0:
            parts.append(
                f'<text x="{x + bar_w / 2:.1f}" y="{height - 8}" class="axis" '
                f'text-anchor="middle">{escape(label)}</text>'
            )
    # threshold line
    thr = threshold.get("value")
    if isinstance(thr, (int, float)):
        y = pad_t + (height - pad_t - pad_b) * (1 - thr / top)
        parts.append(
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - 8}" y2="{y:.1f}" class="threshold"/>'
            f'<text x="{width - 8}" y="{y - 4:.1f}" class="threshold-label" text-anchor="end">'
            f"threshold {escape(threshold.get('operator', ''))} {thr:g}</text>"
        )
    parts.append("</svg>")
    return "".join(parts)


def svg_bars(items: list[tuple[str, float]], threshold: dict, unit: str, color: str) -> str:
    """Horizontal bars, used for percentile tiles and error breakdowns."""
    if not items:
        return '<p class="empty">no data in window</p>'
    width, row_h, pad_l, pad_r = 460, 26, 130, 56
    height = row_h * len(items) + 8
    top = max([v for _, v in items] + [threshold.get("value", 0) if isinstance(threshold.get("value"), (int, float)) else 0]) or 1
    inner_w = width - pad_l - pad_r
    parts = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="bars in {unit}">']
    for i, (label, value) in enumerate(items):
        y = 4 + i * row_h
        bar_w = inner_w * (value / top)
        parts.append(
            f'<text x="{pad_l - 8}" y="{y + 15}" class="axis" text-anchor="end">{escape(label)}</text>'
            f'<rect x="{pad_l}" y="{y + 4}" width="{bar_w:.1f}" height="{row_h - 12}" class="bar {color}"/>'
            f'<text x="{pad_l + bar_w + 6:.1f}" y="{y + 15}" class="value">{value:.4g}</text>'
        )
    thr = threshold.get("value")
    if isinstance(thr, (int, float)) and thr <= top:
        x = pad_l + inner_w * (thr / top)
        parts.append(
            f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{height - 4}" class="threshold"/>'
        )
    parts.append("</svg>")
    return "".join(parts)


def status_for(value: float, threshold: dict) -> str:
    thr = threshold.get("value")
    if not isinstance(thr, (int, float)):
        return "na"
    ok = value <= thr if threshold.get("operator") == "lte" else value >= thr
    return "ok" if ok else "breach"


# ----------------------------------------------------------------- panel builders


def panel_latency(records, config) -> tuple[str, str, str]:
    rows = [r for r in records if r.get("event") == "response_sent"]
    lat = [r["latency_ms"] for r in rows if isinstance(r.get("latency_ms"), (int, float))]
    ttft = [r["ttft_ms"] for r in rows if isinstance(r.get("ttft_ms"), (int, float))]
    threshold = config["threshold"]
    items = [
        ("latency_p50", percentile(lat, 50)),
        ("latency_p95", percentile(lat, 95)),
        ("latency_p99", percentile(lat, 99)),
        ("ttft_p95", percentile(ttft, 95)),
    ]
    series = [
        (minute, percentile([r["latency_ms"] for r in group if isinstance(r.get("latency_ms"), (int, float))], 95))
        for minute, group in by_minute(rows).items()
    ]
    tiles = "".join(
        f'<div class="tile {status_for(v, {"aggregation": a, "operator": threshold["operator"], "value": threshold["value"]})}">'
        f'<span class="tile-label">{escape(a)}</span><span class="tile-value">{v:.0f}</span>'
        f'<span class="tile-unit">ms</span></div>'
        for a, v in items
    )
    return tiles, svg_line_series(series, threshold, "ms"), "ms"


def panel_traffic(records, config) -> tuple[str, str, str]:
    rows = [r for r in records if r.get("event") == "request_received"]
    minutes = by_minute(rows)
    series = [(m, float(len(g))) for m, g in minutes.items()]
    total = len(rows)
    rate = total / max(1, len(minutes)) if minutes else 0.0
    threshold = config["threshold"]
    tiles = (
        f'<div class="tile {status_for(rate, {"aggregation": "rate_per_minute", "operator": "gte", "value": threshold["value"]})}">'
        f'<span class="tile-label">rate_per_minute</span>'
        f'<span class="tile-value">{rate:.1f}</span><span class="tile-unit">req/min</span></div>'
        f'<div class="tile"><span class="tile-label">count</span>'
        f'<span class="tile-value">{total}</span><span class="tile-unit">requests</span></div>'
    )
    return tiles, svg_line_series(series, {"value": None}, "requests"), "requests/minute"


def panel_errors(records, config) -> tuple[str, str, str]:
    received = [r for r in records if r.get("event") == "request_received"]
    failed = [r for r in records if r.get("event") == "request_failed"]
    error_rate = (len(failed) / len(received) * 100) if received else 0.0
    breakdown = Counter(r.get("error_type") or "unknown" for r in failed)
    tool_rows = [r for r in records if r.get("tool_success") is not None]
    tool_ok = sum(1 for r in tool_rows if r["tool_success"] is True)
    tool_rate = (tool_ok / len(tool_rows) * 100) if tool_rows else 100.0
    threshold = config["threshold"]

    tiles = (
        f'<div class="tile {status_for(error_rate, {"aggregation": "error_rate_pct", "operator": "lte", "value": threshold["value"]})}">'
        f'<span class="tile-label">error_rate_pct</span>'
        f'<span class="tile-value">{error_rate:.1f}</span><span class="tile-unit">%</span></div>'
        f'<div class="tile {"ok" if tool_rate >= 90 else "breach"}">'
        f'<span class="tile-label">tool_success_rate_pct</span>'
        f'<span class="tile-value">{tool_rate:.1f}</span><span class="tile-unit">%</span></div>'
    )
    items = [(k, float(v)) for k, v in breakdown.most_common()] or [("no errors", 0.0)]
    breakdown_svg = svg_bars(items, {"value": None}, "count", "red")
    return tiles, breakdown_svg, "percent"


def panel_cost(records, config) -> tuple[str, str, str]:
    rows = [r for r in records if r.get("event") == "response_sent"]
    total = sum(r.get("cost_usd", 0.0) for r in rows)
    series = [
        (m, round(sum(r.get("cost_usd", 0.0) for r in g), 6)) for m, g in by_minute(rows).items()
    ]
    threshold = config["threshold"]
    tiles = (
        f'<div class="tile {status_for(total, {"aggregation": "total", "operator": "lte", "value": threshold["value"]})}">'
        f'<span class="tile-label">total</span>'
        f'<span class="tile-value">{total:.4f}</span><span class="tile-unit">USD</span></div>'
        f'<div class="tile"><span class="tile-label">requests priced</span>'
        f'<span class="tile-value">{len(rows)}</span><span class="tile-unit">responses</span></div>'
    )
    return tiles, svg_line_series(series, threshold, "USD"), "usd"


def panel_tokens(records, config) -> tuple[str, str, str]:
    rows = [r for r in records if r.get("event") == "response_sent"]
    tin = sum(r.get("tokens_in", 0) for r in rows)
    tout = sum(r.get("tokens_out", 0) for r in rows)
    total = tin + tout
    threshold = config["threshold"]
    tiles = "".join(
        f'<div class="tile {status_for(v, {"aggregation": "sum_by_field", "operator": "lte", "value": threshold["value"]})}">'
        f'<span class="tile-label">{label}</span><span class="tile-value">{v}</span>'
        f'<span class="tile-unit">tokens</span></div>'
        for label, v in (("tokens_in", tin), ("tokens_out", tout), ("total", total))
    )
    series = [
        (m, float(sum(r.get("tokens_in", 0) + r.get("tokens_out", 0) for r in g)))
        for m, g in by_minute(rows).items()
    ]
    return tiles, svg_line_series(series, threshold, "tokens"), "tokens"


def panel_quality(records, config) -> tuple[str, str, str]:
    rows = [r for r in records if r.get("event") == "response_sent"]
    scores = [r["quality_score"] for r in rows if isinstance(r.get("quality_score"), (int, float))]
    value = mean(scores)
    threshold = config["threshold"]
    tiles = (
        f'<div class="tile {status_for(value, {"aggregation": "mean", "operator": "gte", "value": threshold["value"]})}">'
        f'<span class="tile-label">mean</span><span class="tile-value">{value:.2f}</span>'
        f'<span class="tile-unit">score 0-1</span></div>'
        f'<div class="tile"><span class="tile-label">scored</span>'
        f'<span class="tile-value">{len(scores)}</span><span class="tile-unit">responses</span></div>'
    )
    series = [
        (m, mean([r["quality_score"] for r in g if isinstance(r.get("quality_score"), (int, float))]))
        for m, g in by_minute(rows).items()
    ]
    return tiles, svg_line_series(series, {"value": threshold["value"], "operator": "lte"}, "score"), "score_0_to_1"


BUILDERS = {
    "latency": panel_latency,
    "traffic": panel_traffic,
    "errors": panel_errors,
    "cost": panel_cost,
    "tokens": panel_tokens,
    "quality": panel_quality,
}


# ------------------------------------------------------------------------ render


CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 24px; background: #f5f7fa; color: #12181f;
       font: 14px/1.45 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
header { display: flex; justify-content: space-between; align-items: baseline;
         flex-wrap: wrap; gap: 8px; margin-bottom: 4px; }
h1 { font-size: 20px; margin: 0; }
.sub { color: #5b6875; font-size: 12px; }
.banner { background: #e8f0fe; border: 1px solid #b9cdf5; border-radius: 6px;
          padding: 10px 12px; margin: 12px 0 18px; font-size: 13px; }
.grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
.panel { background: #fff; border: 1px solid #dde3ea; border-radius: 8px; padding: 14px 16px 8px; }
.panel h2 { font-size: 14px; margin: 0 0 2px; }
.panel .meta { color: #6b7885; font-size: 11px; margin-bottom: 10px; }
.tiles { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.tile { border: 1px solid #dde3ea; border-radius: 6px; padding: 6px 9px; min-width: 92px; }
.tile .tile-label { display: block; font-size: 10px; color: #6b7885; text-transform: uppercase;
                    letter-spacing: .03em; }
.tile .tile-value { font-size: 19px; font-weight: 600; }
.tile .tile-unit { font-size: 10px; color: #8a96a3; margin-left: 3px; }
.tile.ok { border-color: #b7e0c2; background: #f2fbf5; }
.tile.breach { border-color: #f0b3b3; background: #fdf3f3; }
.tile.na { background: #f7f9fb; }
.chart { width: 100%; height: auto; }
.grid-line { stroke: #eef1f5; stroke-width: 1; }
.bar { fill: #4c7cf3; }
.bar.red { fill: #e2685f; }
.axis { font-size: 9px; fill: #8a96a3; }
.value { font-size: 10px; fill: #3a4652; font-weight: 600; }
.threshold { stroke: #d9822b; stroke-width: 1.5; stroke-dasharray: 4 3; }
.threshold-label { font-size: 9px; fill: #b3621a; }
.empty { color: #98a4b0; font-size: 12px; padding: 22px 0; }
footer { margin-top: 18px; color: #6b7885; font-size: 11px; }
code { background: #eceff3; padding: 1px 4px; border-radius: 3px; }
"""


def render(config: dict, records: list[dict], window: tuple[datetime, datetime], generated: datetime) -> str:
    start, end = window
    dash = config["dashboard"]
    unknown = {p["id"] for p in dash["panels"]} - KNOWN_PANELS
    if unknown:
        raise SystemExit(f"config/dashboard.yaml has panels this renderer does not implement: {sorted(unknown)}")

    panels = []
    for panel in dash["panels"]:
        builder = BUILDERS[panel["id"]]
        tiles, chart, unit = builder(records, panel)
        threshold = panel["threshold"]
        panels.append(
            f'<section class="panel">'
            f"<h2>{escape(panel['title'])}</h2>"
            f"<div class=\"meta\">id={escape(panel['id'])} &middot; unit={escape(unit)} "
            f"&middot; events={escape(','.join(panel['events']))} "
            f"&middot; threshold: {escape(threshold['aggregation'])} "
            f"{escape(threshold['operator'])} {threshold['value']} "
            f"&middot; source={escape(panel['source'])}</div>"
            f'<div class="tiles">{tiles}</div>{chart}</section>'
        )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{escape(dash['title'])}</title>
<meta http-equiv="refresh" content="{dash['refresh_seconds']}">
<style>{CSS}</style></head>
<body>
<header>
  <div>
    <h1>{escape(dash['title'])}</h1>
    <div class="sub">schema_version {dash['schema_version']} &middot;
      time_range {dash['time_range_minutes']} min &middot;
      refresh every {dash['refresh_seconds']}s</div>
  </div>
  <div class="sub">generated {generated.strftime('%Y-%m-%d %H:%M:%S UTC')}</div>
</header>
<div class="banner">
  Window <b>{start.strftime('%H:%M:%S')}</b> &rarr; <b>{end.strftime('%H:%M:%S')}</b>
  ({dash['time_range_minutes']} min, anchored on the newest log record).
  Source of truth: <code>data/logs.jsonl</code> via the contract in
  <code>config/dashboard.yaml</code>. {len(records)} log records in window.
  Regenerate with <code>python scripts/build_dashboard.py</code>.
</div>
<div class="grid">{''.join(panels)}</div>
<footer>
  Dashed orange line = threshold from config/dashboard.yaml.
  latency panel: P50/P95/P99 + TTFT P95. errors panel: error rate, breakdown by
  error_type, retrieval success (tool_success_rate_pct).
</footer>
</body></html>"""


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "dashboard.yaml")
    parser.add_argument("--logs", type=Path, default=REPO_ROOT / "data" / "logs.jsonl")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "data" / "dashboard.html")
    parser.add_argument(
        "--anchor",
        choices=["latest", "now"],
        default="latest",
        help="latest: window ends at the newest log record (default); now: wall clock",
    )
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    records = load_records(args.logs)
    window = select_window(records, config["dashboard"]["time_range_minutes"], args.anchor)
    in_window_records = in_window(records, *window)

    html = render(config, in_window_records, window, datetime.now(timezone.utc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8")

    print(f"wrote {args.output} ({len(in_window_records)} records in window, "
          f"{len(config['dashboard']['panels'])} panels)")
    for panel in config["dashboard"]["panels"]:
        print(f"  - {panel['id']:<9} unit={panel['unit']:<20} "
              f"threshold {panel['threshold']['aggregation']} "
              f"{panel['threshold']['operator']} {panel['threshold']['value']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
