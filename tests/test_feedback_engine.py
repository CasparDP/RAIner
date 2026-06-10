"""FeedbackEngine facade: config injection, draft loading, memory_context, result."""

from rainer import Config, FeedbackEngine
from rainer.agent import ResearchAgent


class _FakeAdapter:
    """Returns a canned report with no tool calls; records messages it sees."""

    def __init__(self):
        self.seen_messages = []

    def generate(self, messages, tools):
        self.seen_messages.append(messages)
        return ("# Feedback\n\nLooks solid.", None)

    def format_tool_result(self, tool_call_id, tool_name, result_content):
        return {"role": "tool", "content": result_content}


def test_feedback_engine_runs(monkeypatch):
    fake = _FakeAdapter()

    def fake_init_llm(self):
        self.provider_type = "azure-openai"
        self.model = "my-deployment"
        self.client = None
        self.adapter = fake

    monkeypatch.setattr(ResearchAgent, "_init_llm", fake_init_llm)

    engine = FeedbackEngine(config=Config())
    result = engine.run(
        draft_text="My study examines X using a panel of firms.",
        mode="search",  # avoids the feedback-mode EUR/literature pre-pass in a unit test
        draft_name="d1",
        memory_context="REJECTED: drop the second hypothesis",
    )

    assert "Feedback" in result.report_markdown
    assert result.provider == "azure-openai"
    assert result.model == "my-deployment"

    # The memory context must reach the model (appended to the user prompt).
    user_texts = [
        m["content"]
        for msgs in fake.seen_messages
        for m in msgs
        if m.get("role") == "user"
    ]
    assert any("drop the second hypothesis" in t for t in user_texts)
