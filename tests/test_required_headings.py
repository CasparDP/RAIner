"""Required-headings check: alternative layouts per mode and the missing-sections follow-up."""

import pytest

from rainer import Config
from rainer.agent import ResearchAgent

RESULTS_FULL = """A. Executive summary
B. Results-Design Consistency Audit
C. Specification Audit
D. Results Interpretation
E. Missing Analyses
F. Threats to Validity: Addressed vs. Open
G. Strengths
H. Literature & citations needed
I. Revision checklist
J. Clarifying questions"""

RESULTS_PARTIAL = """A. Executive summary
B. Design Readiness Audit
C. Pre-flight checklist
D. What I cannot evaluate without results
E. Clarifying questions"""


class _ScriptedAdapter:
    """Returns the scripted replies in order, with no tool calls."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen_messages = []

    def generate(self, messages, tools):
        self.seen_messages.append(list(messages))
        return (self.replies.pop(0), None)

    def format_tool_result(self, tool_call_id, tool_name, result_content):
        return {"role": "tool", "content": result_content}


@pytest.fixture
def make_agent(monkeypatch):
    def _make(mode, replies=()):
        adapter = _ScriptedAdapter(replies)

        def fake_init_llm(self):
            self.provider_type = "ollama"
            self.model = "fake"
            self.client = None
            self.adapter = adapter

        monkeypatch.setattr(ResearchAgent, "_init_llm", fake_init_llm)
        return ResearchAgent(mode=mode, config=Config()), adapter

    return _make


def test_full_and_partial_results_layouts_pass(make_agent):
    agent, _ = make_agent("feedback_results")
    assert agent._get_missing_required_headings(RESULTS_FULL) == []
    assert agent._get_missing_required_headings(RESULTS_PARTIAL) == []


def test_missing_heading_reported_from_closest_layout(make_agent):
    agent, _ = make_agent("feedback_results")
    report = RESULTS_FULL.replace("J. Clarifying questions", "")
    assert agent._get_missing_required_headings(report) == ["J. Clarifying questions"]


def test_hyp_rd_insufficient_layout_passes(make_agent):
    agent, _ = make_agent("feedback_hyp_rd")
    report = """A. Research Question Assessment
B. Proposed Hypotheses
C. Proposed Research Designs
D. Data Feasibility
E. What you DID provide well
F. Building-block revision checklist
G. Clarifying questions"""
    assert agent._get_missing_required_headings(report) == []


def test_modes_without_required_headings(make_agent):
    agent, _ = make_agent("search")
    assert agent._get_missing_required_headings("anything") == []


def test_follow_up_sections_are_appended_to_original_report(make_agent):
    incomplete = RESULTS_FULL.replace("J. Clarifying questions", "")
    agent, adapter = make_agent(
        "feedback_results", replies=[incomplete, "J. Clarifying questions\n- Which FE?"]
    )

    result = agent.chat("Run the report.")

    assert result.startswith("A. Executive summary")
    assert result.endswith("J. Clarifying questions\n- Which FE?")
    assert len(adapter.seen_messages) == 2
    assert agent.memory.get_messages()[-1]["content"] == result


def test_complete_report_in_follow_up_is_not_duplicated(make_agent):
    incomplete = RESULTS_FULL.replace("J. Clarifying questions", "")
    agent, _ = make_agent("feedback_results", replies=[incomplete, RESULTS_FULL])

    assert agent.chat("Run the report.") == RESULTS_FULL
