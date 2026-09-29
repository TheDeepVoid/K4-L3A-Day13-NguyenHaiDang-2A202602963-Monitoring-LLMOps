from __future__ import annotations

from contextlib import contextmanager

from app import agent as agent_module


class ManagedPrompt:
    version = 3
    # The SDK drops a prompt link that carries no name, so the fake needs one
    # for propagate_attributes() to behave like the real managed prompt.
    name = "day13-chat"

    def compile(self, **variables: str) -> str:
        return (
            f"Feature={variables['feature']}\n"
            f"Docs={variables['docs']}\n"
            f"Question={variables['message']}"
        )


class RecordingObservation:
    def __init__(self, **kwargs) -> None:
        self.init = kwargs
        self.updates: list[dict] = []

    def update(self, **kwargs) -> None:
        self.updates.append(kwargs)


class RecordingLangfuseClient:
    def __init__(self) -> None:
        self.prompt = ManagedPrompt()
        self.span_updates: list[dict] = []
        self.observations: list[RecordingObservation] = []

    def get_prompt(self, name: str, **kwargs):
        return self.prompt

    def update_current_span(self, **kwargs) -> None:
        self.span_updates.append(kwargs)

    def start_as_current_observation(self, **kwargs):
        observation = RecordingObservation(**kwargs)
        self.observations.append(observation)
        return _observation_scope(observation)


@contextmanager
def _observation_scope(observation: RecordingObservation):
    yield observation


def test_agent_records_prompt_version_with_v4_observation_api(monkeypatch) -> None:
    monkeypatch.setenv("LANGFUSE_PROMPT_NAME", "day13-chat")
    monkeypatch.setenv("LANGFUSE_PROMPT_LABEL", "production")
    client = RecordingLangfuseClient()
    monkeypatch.setattr(agent_module, "get_langfuse_client", lambda: client)
    monkeypatch.setattr(agent_module, "tracing_enabled", lambda: True)

    propagated: list[dict] = []

    @contextmanager
    def record_attributes(**kwargs):
        propagated.append(kwargs)
        yield

    monkeypatch.setattr(agent_module, "propagate_attributes", record_attributes)

    agent = agent_module.LabAgent()
    agent_module.LabAgent.run.__wrapped__(
        agent,
        user_id="student-01",
        feature="qa",
        session_id="session-01",
        message="Explain traces",
        correlation_id="req-12345678",
    )

    span_update = client.span_updates[-1]
    assert span_update["metadata"] == {
        "doc_count": 1,
        "query_preview": "Explain traces",
        "prompt_name": "day13-chat",
        "prompt_label": "production",
        "prompt_version": "3",
        "prompt_source": "langfuse",
        "prompt_fetch_error": "",
    }
    assert span_update["version"] == "3"
    assert propagated[0]["metadata"]["correlation_id"] == "req-12345678"
    assert propagated[-1]["prompt"] is client.prompt


def test_agent_creates_retriever_prompt_and_generation_observations(monkeypatch) -> None:
    """The tree must separate retrieval, the prompt lookup and the model call,
    with the most specific observation types and the usage/cost needed for model
    analytics. resolve-prompt exists because a synchronous fetch to an external
    service that sits outside every span is a latency you cannot explain."""
    client = RecordingLangfuseClient()
    monkeypatch.setattr(agent_module, "get_langfuse_client", lambda: client)
    monkeypatch.setattr(agent_module, "tracing_enabled", lambda: True)

    agent = agent_module.LabAgent()
    result = agent_module.LabAgent.run.__wrapped__(
        agent,
        user_id="student-01",
        feature="qa",
        session_id="session-01",
        message="Explain traces",
        correlation_id="req-12345678",
    )

    by_name = {obs.init["name"]: obs for obs in client.observations}
    assert set(by_name) == {"retrieve-context", "resolve-prompt", "generate-response"}

    retrieval = by_name["retrieve-context"]
    assert retrieval.init["as_type"] == "retriever"
    assert retrieval.updates[0]["metadata"]["doc_count"] == 1

    prompt_span = by_name["resolve-prompt"]
    assert prompt_span.init["as_type"] == "span"
    assert prompt_span.updates[0]["output"] == {"source": "langfuse", "version": "3"}

    generation = by_name["generate-response"]
    assert generation.init["as_type"] == "generation"
    assert generation.init["model"] == "claude-sonnet-4-5"
    assert generation.init["prompt"] is client.prompt

    generation_update = generation.updates[0]
    assert generation_update["usage_details"] == {
        "input": result.tokens_in,
        "output": result.tokens_out,
    }
    # Ingested cost must equal the cost reported to the client and the logs.
    assert round(sum(generation_update["cost_details"].values()), 6) == result.cost_usd
    assert generation_update["completion_start_time"] is not None


def test_agent_scrubs_pii_before_it_reaches_langfuse(monkeypatch) -> None:
    client = RecordingLangfuseClient()
    monkeypatch.setattr(agent_module, "get_langfuse_client", lambda: client)
    monkeypatch.setattr(agent_module, "tracing_enabled", lambda: True)

    agent = agent_module.LabAgent()
    agent_module.LabAgent.run.__wrapped__(
        agent,
        user_id="student-01",
        feature="qa",
        session_id="session-01",
        message="My email is real.student@vinuni.edu.vn and card 4111 1111 1111 1111",
        correlation_id="req-12345678",
    )

    traced = repr([obs.init for obs in client.observations]) + repr(
        [obs.updates for obs in client.observations]
    ) + repr(client.span_updates)
    for leak in ("real.student@vinuni.edu.vn", "4111 1111 1111 1111"):
        assert leak not in traced, leak
    assert "[REDACTED_EMAIL]" in traced
