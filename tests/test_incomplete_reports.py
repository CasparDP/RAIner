"""Incomplete answers must never pass as reports: cut-off provider responses raise, a run
that exhausts its steps gets one forced tool-free answer, and the engine rejects reports
that are empty or lack most required sections."""

from types import SimpleNamespace as _NS

import pytest

from rainer import Config, FeedbackEngine, IncompleteReportError
from rainer.agent import ResearchAgent
from rainer.providers import IncompleteResponseError, create_provider_adapter

FULL_REVIEW = "Section 1\nfine\n\nSection 2\nfine\n\nSection 3\nfine"


class _ScriptedAdapter:
    """Calls a tool on every normal step; answers `final_answer` from generate_final."""

    def __init__(self, final_answer="", final_calls_tool=False):
        self.final_answer = final_answer
        self.final_calls_tool = final_calls_tool
        self.steps = 0
        self.final_calls = 0

    def _tool_call(self):
        return [_NS(id=f"c{self.steps}", name="get_mode_info", arguments={})]

    def generate(self, messages, tools):
        self.steps += 1
        return ("Searching once more.", self._tool_call())

    def generate_final(self, messages, tools):
        self.final_calls += 1
        assert messages[-1]["role"] == "user"
        assert "Do not call any more tools" in messages[-1]["content"]
        if self.final_calls_tool:
            return ("", self._tool_call())
        return (self.final_answer, None)

    def format_tool_result(self, tool_call_id, tool_name, result_content):
        return {"role": "tool", "tool_call_id": tool_call_id, "content": result_content}


class _ReportAdapter:
    """Answers straight away with a fixed report."""

    def __init__(self, report):
        self.report = report

    def generate(self, messages, tools):
        return (self.report, None)

    def format_tool_result(self, tool_call_id, tool_name, result_content):
        return {"role": "tool", "content": result_content}


def _use(monkeypatch, adapter):
    def fake_init_llm(self):
        self.provider_type = "azure-openai"
        self.model = "my-deployment"
        self.client = None
        self.adapter = adapter

    monkeypatch.setattr(ResearchAgent, "_init_llm", fake_init_llm)


def _run(mode="review"):
    return FeedbackEngine(config=Config()).run(draft_text="A draft.", mode=mode)


# --- agent: out of steps ------------------------------------------------------


def test_exhausted_steps_get_one_tool_free_answer(monkeypatch):
    adapter = _ScriptedAdapter(final_answer=FULL_REVIEW)
    _use(monkeypatch, adapter)
    result = _run()
    assert adapter.final_calls == 1
    assert result.report_markdown == FULL_REVIEW
    assert "Searching once more." not in result.report_markdown
    assert result.metadata["missing_headings"] == []
    assert result.metadata["steps"] == adapter.steps + 1


def test_exhausted_steps_without_an_answer_raise(monkeypatch):
    _use(monkeypatch, _ScriptedAdapter(final_calls_tool=True))
    with pytest.raises(IncompleteReportError, match="step budget"):
        _run()


def test_rescue_falls_back_to_generate_for_adapters_without_generate_final(monkeypatch):
    class _Plain(_ReportAdapter):
        calls = 0

        def generate(self, messages, tools):
            self.calls += 1
            if messages[-1]["content"].startswith("You have used all available research steps"):
                return (FULL_REVIEW, None)
            return ("", [_NS(id=f"c{self.calls}", name="get_mode_info", arguments={})])

    _use(monkeypatch, _Plain(""))
    assert _run().report_markdown == FULL_REVIEW


# --- engine: heading threshold ------------------------------------------------


def test_one_missing_section_still_ships(monkeypatch):
    _use(monkeypatch, _ReportAdapter("Section 1\nfine\n\nSection 2\nfine"))
    result = _run()
    assert result.metadata["missing_headings"] == ["Section 3"]


def test_most_sections_missing_raises(monkeypatch):
    _use(monkeypatch, _ReportAdapter("Section 1\nonly this"))
    with pytest.raises(IncompleteReportError, match="2 of 3"):
        _run()


def test_mode_without_required_headings_is_not_checked(monkeypatch):
    _use(monkeypatch, _ReportAdapter("Here are some papers."))
    assert _run(mode="search").metadata["missing_headings"] == []


# --- providers: cut-off answers ------------------------------------------------


def _chat_client(finish_reason, record):
    def create(**kwargs):
        record.clear()
        record.update(kwargs)
        message = _NS(content="partial", tool_calls=None)
        return _NS(choices=[_NS(message=message, finish_reason=finish_reason)])

    return _NS(chat=_NS(completions=_NS(create=create)))


@pytest.mark.parametrize("reason", ["length", "content_filter"])
def test_chat_adapter_raises_on_cut_off_answer(reason):
    adapter = create_provider_adapter("azure-openai", "d", client=_chat_client(reason, {}))
    with pytest.raises(IncompleteResponseError, match=reason):
        adapter.generate([{"role": "user", "content": "hi"}], [])


def test_chat_adapter_final_call_forbids_tools():
    sent = {}
    adapter = create_provider_adapter("azure-openai", "d", client=_chat_client("stop", sent))
    assert adapter.generate_final([{"role": "user", "content": "hi"}], []) == ("partial", None)
    assert sent["tool_choice"] == "none"
    adapter.generate([{"role": "user", "content": "hi"}], [])
    assert "tool_choice" not in sent


def _responses_client(response, record):
    def create(**kwargs):
        record.clear()
        record.update(kwargs)
        return response

    return _NS(responses=_NS(create=create))


def _responses_adapter(response, record=None):
    client = _responses_client(response, record if record is not None else {})
    return create_provider_adapter(
        "azure-openai", "gpt-6.1-sol", client=client, azure_api="responses"
    )


def test_responses_adapter_raises_on_incomplete_before_parsing():
    broken_call = _NS(type="function_call", call_id="c1", name="search_papers", arguments='{"qu')
    response = _NS(
        status="incomplete",
        incomplete_details=_NS(reason="max_output_tokens"),
        output=[broken_call],
    )
    with pytest.raises(IncompleteResponseError, match="max_output_tokens"):
        _responses_adapter(response).generate([{"role": "user", "content": "hi"}], [])


def test_responses_adapter_raises_on_failed():
    response = _NS(status="failed", error=_NS(code="server_error"), output=[])
    with pytest.raises(IncompleteResponseError, match="failed"):
        _responses_adapter(response).generate([{"role": "user", "content": "hi"}], [])


def test_responses_adapter_final_call_forbids_tools():
    sent = {}
    text = _NS(type="message", content=[_NS(type="output_text", text="Report.")])
    adapter = _responses_adapter(_NS(status="completed", output=[text]), sent)
    assert adapter.generate_final([{"role": "user", "content": "hi"}], []) == ("Report.", None)
    assert sent["tool_choice"] == "none"
    adapter.generate([{"role": "user", "content": "hi"}], [])
    assert "tool_choice" not in sent
