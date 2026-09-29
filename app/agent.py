from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import metrics
from .mock_llm import FakeLLM
from .mock_rag import retrieve
from .pii import hash_user_id, scrub_text, summarize_text
from .prompt_management import resolve_prompt
from .tracing import get_langfuse_client, observe, propagate_attributes, tracing_enabled

# Input price / output price in USD per 1M tokens for the model this lab mocks.
INPUT_PRICE_PER_MTOK = 3.0
OUTPUT_PRICE_PER_MTOK = 15.0


@dataclass
class AgentResult:
    answer: str
    latency_ms: int
    ttft_ms: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    quality_score: float


class LabAgent:
    def __init__(self, model: str = "claude-sonnet-4-5") -> None:
        self.model = model
        self.llm = FakeLLM(model=model)

    @observe(name="lab-agent-run", as_type="agent", capture_input=False, capture_output=False)
    def run(
        self,
        user_id: str,
        feature: str,
        session_id: str,
        message: str,
        correlation_id: str,
    ) -> AgentResult:
        # capture_input/capture_output are off on the root observation because the
        # decorator would otherwise ship every argument - including the raw
        # user_id and message - to Langfuse. Trace input/output are set
        # explicitly below instead, with PII already scrubbed.
        langfuse_client = get_langfuse_client()
        with propagate_attributes(
            user_id=hash_user_id(user_id),
            session_id=session_id,
            tags=["lab", feature, self.model],
            trace_name="day13-agent-request",
            environment=os.getenv("APP_ENV", "dev"),
            metadata={
                "feature": feature,
                "model": self.model,
                "correlation_id": correlation_id,
            },
        ):
            started = time.perf_counter()

            # Child 1: retrieval. Typed `retriever` rather than a generic `span`
            # so RAG-specific analytics and Agent Graph nodes work, and so a slow
            # vector store is separable from a slow model call.
            with langfuse_client.start_as_current_observation(
                as_type="retriever",
                name="retrieve-context",
                input={"query": scrub_text(message)},
            ) as retrieval:
                docs = retrieve(message)
                retrieval.update(
                    output={"documents": [scrub_text(doc) for doc in docs]},
                    metadata={"doc_count": len(docs), "corpus": "mock_rag"},
                )

            prompt = resolve_prompt(
                langfuse_client,
                feature=feature,
                docs=docs,
                message=message,
                enabled=tracing_enabled(),
            )

            # Child 2: the model call, as a sibling of retrieval. Passing
            # `prompt` links this generation to the exact prompt version used,
            # so prompt changes can be compared across traces.
            with propagate_attributes(prompt=prompt.managed_prompt):
                # Wall-clock, not time.perf_counter(): completion_start_time is
                # an absolute timestamp, and perf_counter is a monotonic counter
                # that would produce a wildly negative time-to-first-token.
                llm_started_at = datetime.now(timezone.utc)
                with langfuse_client.start_as_current_observation(
                    as_type="generation",
                    name="generate-response",
                    model=self.model,
                    prompt=prompt.managed_prompt,
                    input=[
                        {
                            "role": "user",
                            "content": scrub_text(prompt.text),
                        }
                    ],
                    model_parameters={"max_tokens": 1024},
                ) as generation:
                    response = self.llm.generate(prompt.text)
                    cost_breakdown = self._cost_breakdown(
                        response.usage.input_tokens, response.usage.output_tokens
                    )
                    # The fake model sleeps 50ms before "streaming" starts;
                    # recording that as completion_start_time is what populates
                    # time-to-first-token on the generation.
                    completion_start_time = llm_started_at + timedelta(
                        milliseconds=response.ttft_ms
                    )
                    generation.update(
                        output=[
                            {
                                "role": "assistant",
                                "content": scrub_text(response.text),
                            }
                        ],
                        usage_details={
                            "input": response.usage.input_tokens,
                            "output": response.usage.output_tokens,
                        },
                        # Ingested cost takes priority over Langfuse's inferred
                        # price, so the number in the trace always matches the
                        # number in the logs and on the cost dashboard panel.
                        cost_details=cost_breakdown,
                        completion_start_time=completion_start_time,
                        metadata={
                            "ttft_ms": response.ttft_ms,
                            "prompt_source": prompt.source,
                        },
                    )

            quality_score = self._heuristic_quality(message, response.text, docs)
            latency_ms = int((time.perf_counter() - started) * 1000)
            cost_usd = self._estimate_cost(
                response.usage.input_tokens, response.usage.output_tokens
            )

            # Root observation update last, so trace-level input/output (which
            # Langfuse derives from the root observation) is the user question
            # and the final answer - what a reviewer needs at a glance.
            langfuse_client.update_current_span(
                input=[
                    {
                        "role": "user",
                        "content": scrub_text(message),
                    }
                ],
                output=[
                    {
                        "role": "assistant",
                        "content": scrub_text(response.text),
                    }
                ],
                metadata={
                    "doc_count": len(docs),
                    "query_preview": summarize_text(message),
                    "prompt_name": prompt.name,
                    "prompt_label": prompt.label,
                    "prompt_version": prompt.version,
                    "prompt_source": prompt.source,
                    "prompt_fetch_error": prompt.fetch_error or "",
                },
                version=prompt.version,
            )

        metrics.record_request(
            latency_ms=latency_ms,
            ttft_ms=response.ttft_ms,
            cost_usd=cost_usd,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            quality_score=quality_score,
        )

        return AgentResult(
            answer=response.text,
            latency_ms=latency_ms,
            ttft_ms=response.ttft_ms,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            cost_usd=cost_usd,
            quality_score=quality_score,
        )

    def _cost_breakdown(self, tokens_in: int, tokens_out: int) -> dict[str, float]:
        return {
            "input": (tokens_in / 1_000_000) * INPUT_PRICE_PER_MTOK,
            "output": (tokens_out / 1_000_000) * OUTPUT_PRICE_PER_MTOK,
        }

    def _estimate_cost(self, tokens_in: int, tokens_out: int) -> float:
        return round(sum(self._cost_breakdown(tokens_in, tokens_out).values()), 6)

    def _heuristic_quality(self, question: str, answer: str, docs: list[str]) -> float:
        score = 0.5
        if docs:
            score += 0.2
        if len(answer) > 40:
            score += 0.1
        if question.lower().split()[0:1] and any(token in answer.lower() for token in question.lower().split()[:3]):
            score += 0.1
        if "[REDACTED" in answer:
            score -= 0.2
        return round(max(0.0, min(1.0, score)), 2)
