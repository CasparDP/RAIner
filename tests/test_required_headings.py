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


def _idea_report(n_steps, n_papers=3):
    steps = "\n".join(f"{i}. **Step {i}**\n   - why it matters" for i in range(1, n_steps + 1))
    papers = "\n".join(f"{i}. Paper {i} (JAR)" for i in range(1, n_papers + 1))
    return (
        "## A. My understanding of your idea\n- Topic: STATED\n\n"
        f"## B. Your next steps\n{steps}\n\n"
        f"## C. Suggested reading\n{papers}\n\n"
        "## D. Data check\n- Compustat: VERIFIED\n"
    )


def test_idea_item_cap(make_agent):
    agent, _ = make_agent("feedback_idea")
    assert agent._check_formatting_issues(_idea_report(10, 10)) == []
    issues = agent._check_formatting_issues(_idea_report(11))
    assert len(issues) == 1 and "B. Your next steps" in issues[0] and "11" in issues[0]
    issues = agent._check_formatting_issues(_idea_report(5, 12))
    assert len(issues) == 1 and "C. Suggested reading" in issues[0]


def test_idea_cap_asks_for_a_merged_complete_report(make_agent):
    """Over the cap: one follow-up for a complete report; the reply is used whole, not cut."""
    too_long, merged = _idea_report(12), _idea_report(9)
    agent, adapter = make_agent("feedback_idea", replies=[too_long, merged])
    assert agent.chat("Give feedback on my idea.") == merged
    follow_up = adapter.seen_messages[1][-1]["content"]
    assert "12 numbered items" in follow_up and "COMPLETE report" in follow_up


def test_idea_cap_still_exceeded_returns_report_uncut(make_agent):
    too_long = _idea_report(12)
    agent, _ = make_agent("feedback_idea", replies=[too_long, too_long])
    assert agent.chat("Give feedback on my idea.") == too_long
