# Alert runbooks

Each alert is written on a user symptom, not on a suspected cause. The runbook
therefore starts by confirming the symptom, then narrows the cause with the
trace, and only then mitigates. If you find yourself reading a cause in the
alert name, the rule is written wrong.

Every runbook assumes you have the log file and the Langfuse project for this
lab:

```bash
curl -s http://127.0.0.1:8000/metrics | jq .                    # in-process snapshot
python scripts/evaluate_alerts.py                               # recompute all rules
jq -c 'select(.event=="response_sent")' data/logs.jsonl | tail -20
```

Alert rules live in `../config/alert_rules.yaml` and are re-evaluated from the
same log the dashboard reads, so the two can never disagree.

---
<a id="alert-1"></a>

## Alert 1: `slow_answers`

- **Name:** `slow_answers`
- **Severity:** high
- **Condition:** p95 of `response_sent.latency_ms` > 3000 ms — users are waiting more than three seconds.
- **Duration:** 10 minutes (window 10 minutes, `for_minutes: 10`)
- **SLI/SLO:** `fast_successful_requests` — this alert is the SLO's early warning.
- **Channel:** Slack `#day13-oncall` · **Owner:** backend-oncall
- **Verified firing:** with `--scenario rag_slow`, p95 = 5416 ms → FIRING; on the healthy baseline p95 = 877 ms → OK.

### Impact on users

Answers still arrive, but slowly enough that the request looks hung. The SLO's
`good_event` (`latency_ms <= 3000`) stops being satisfied, so the error budget
starts burning for as long as the alert is firing.

### Three first checks

1. **Which step is slow?** Get the slow `correlation_id` and open the matching
   trace. In the tree, compare `retrieve-context` against `generate-response`:

   ```bash
   jq -r 'select(.event=="response_sent" and .latency_ms>3000)
          | "\(.correlation_id) \(.latency_ms)ms ttft=\(.ttft_ms)ms"' data/logs.jsonl
   ```

   A slow `retrieve-context` is a retrieval problem. A slow `generate-response`
   with a high `ttft_ms` is a model problem. Both fast but a slow parent means
   time is being spent outside the two observed steps.

2. **Is traffic up?** Compare the traffic panel against the same window. If
   request rate roughly quadrupled, the latency may be a capacity symptom, not
   a regression: `rate_per_minute` from `evaluate_alerts.py` and the traffic
   panel answer this in one step.

3. **Did something change?** Check the prompt version in the trace metadata
   (`prompt_name`, `prompt_version`, `prompt_source`) and roll it back if the
   change is recent:

   ```bash
   python scripts/prompt_ops.py show
   python scripts/prompt_ops.py rollback
   ```

### Mitigation

- **If retrieval is the slow span:** disable the slow path and degrade to the
  fallback answer rather than letting users wait:
  `python scripts/inject_incident.py --scenario rag_slow --disable`.
  Reducing the retrieval deadline (or serving a cached corpus) is the real fix.
- **If the model call is the slow span:** roll back the prompt to the last known
  good version, then reduce `max_tokens` — cost and latency move together.
- **If traffic is the cause:** shed load; do not page the model team.
- Freeze prompt and model changes until p95 is back under 1.5 s, because every
  further change spends the same error budget.

---
<a id="alert-2"></a>

## Alert 2: `answers_failing`

- **Name:** `answers_failing`
- **Severity:** critical
- **Condition:** `count(request_failed) / count(request_received) * 100` > 2%.
- **Duration:** 5 minutes (window 5 minutes, `for_minutes: 5`)
- **SLI/SLO:** `fast_successful_requests` (failed requests count against the denominator).
- **Channel:** Slack `#day13-oncall` · **Owner:** backend-oncall
- **Verified firing:** with `--scenario tool_fail`, error rate = 100% → FIRING; baseline 0.0% → OK.

### Impact on users

Every failing request returns HTTP 500 with an empty body. This is total
unavailability for the affected requests, and the shortest window of all
alerts: an outage is not self-healing, so waiting costs budget.

### Three first checks

1. **What is the error, and is it one error or many?**

   ```bash
   jq -r 'select(.event=="request_failed") | .error_type' data/logs.jsonl | sort | uniq -c
   ```

   A single dominant `error_type` points at one dependency. A mix means
   something broader, such as memory pressure or a bad deploy.

2. **Which step raised it?** `error_type` plus `tool_name` on the failed line
   says whether the failure came from a tool or from the service itself. The
   trace for the same `correlation_id` shows the span carrying the error level.

3. **Is it all traffic or a slice?** Group failures by `feature` or by
   `user_id_hash`:

   ```bash
   jq -r 'select(.event=="request_failed") | .feature' data/logs.jsonl | sort | uniq -c
   ```

   One feature only means a scoped change or a scoped dependency, not an
   outage.

### Mitigation

- Turn the failing dependency off so requests take the fallback path, trading
  quality for availability:
  `python scripts/inject_incident.py --scenario tool_fail --disable`.
- If the failure is a code regression, roll back the deploy before anything
  else; the error budget is not the priority during an outage.
- Post the incident ID in the Slack thread. Every mitigation step should leave
  a log line you can point at later.

---
<a id="alert-3"></a>

## Alert 3: `retrieval_quality_drop`

- **Name:** `retrieval_quality_drop`
- **Severity:** medium
- **Condition:** `count(tool_success == true) / count(tool_success != null) * 100` < 90%.
- **Duration:** 15 minutes (window 15 minutes, `for_minutes: 15`)
- **SLI/SLO:** `retrieval_available`.
- **Channel:** Slack `#day13-llm-platform` · **Owner:** ml-platform
- **Verified firing:** with `--scenario tool_fail`, retrieval success = 0% → FIRING; baseline 100% → OK.

### Why this exists separately

A retrieval that fails loudly is already covered by `answers_failing`. This
alert exists for the quiet failure: retrieval returns HTTP 200 with an empty or
irrelevant document set, the model answers confidently from nothing, and
**neither latency nor error rate moves**. Only a dedicated retrieval signal
catches it, which is why the window is 15 minutes rather than 5.

### Impact on users

Answers are fluent, well-formatted and ungrounded. Reports of "the bot is
making things up" arrive before any other signal says anything is wrong.

### Three first checks

1. **Is the corpus still matching?** Compare `doc_count` in the trace metadata
   against the healthy baseline. A drop from 1 to a fallback document is the
   signature of a broken index or an unparsed query.
2. **What is being returned?** Read the `retrieve-context` span output in the
   trace. The documents themselves are in the observation, so no guessing is
   needed.
3. **Did the query format change?** Compare `query_preview` in the trace
   metadata with the log's `message_preview`. Different phrasing means the
   keyword matcher stopped matching, which is a silent failure by construction.

### Mitigation

- Revert the most recent retrieval or embedding change; the fallback document
  keeps users served while quality is restored.
- If a prompt change altered how context is requested, roll the prompt back
  (`python scripts/prompt_ops.py rollback`).
- Add the affected `correlation_id` to the report as evidence: this is the
  alert that has no latency or error story attached to it.

---
<a id="alert-4"></a>

## Alert 4: `token_cost_runaway`

- **Name:** `token_cost_runaway`
- **Severity:** medium
- **Condition:** spend above 0.05 USD per minute sustained for 15 minutes.
- **Duration:** 15 minutes (window 15 minutes, `for_minutes: 15`)
- **SLI/SLO:** `daily_token_cost` (95% of days under 2.5 USD).
- **Channel:** Slack `#day13-llm-platform` · **Owner:** ml-platform
- **Verified firing:** with `--scenario cost_spike`, 0.0755 USD/min → FIRING; baseline 0.0242 USD/min → OK.

### Impact on users

No user-visible failure. The cost is that the `daily_token_cost` objective
burns, and eventually a real budget or a rate limit is hit.

### Three first checks

1. **Is traffic up, or is each request more expensive?** Compare the traffic
   panel with the cost panel over the same window. Traffic flat plus cost up
   means each request got more expensive; both up means a traffic change.

2. **Which requests are expensive?** Sort the log by `cost_usd`:

   ```bash
   jq -r 'select(.event=="response_sent")
          | "\(.cost_usd) \(.correlation_id) tokens=\(.tokens_in)/\(.tokens_out)"' \
     data/logs.jsonl | sort -rn | head
   ```

   Then look up the worst one's `prompt_version` in the trace metadata. One
   prompt version dominating the spend is a prompt problem.

3. **Did the output length change?** Compare `tokens_out` against the baseline
   (118–175 tokens per response in my run). A jump of roughly 4x is the
   signature of a runaway generation, not normal variance.

### Mitigation

- Roll back the prompt version if the spend started with a prompt change.
- Cap `max_tokens` in the generation observation; it is the cheapest lever and
  is visible in the trace afterwards.
- Turn off the incident to confirm the diagnosis before shipping a fix:
  `python scripts/inject_incident.py --scenario cost_spike --disable`.
- Re-measure on the same workload afterwards; a cost claim without before/after
  numbers on the same input is not evidence.
