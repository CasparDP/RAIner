"""FeedbackEngine — a stable, embeddable facade over the agent.

This is the single entrypoint external services (e.g. a web worker) should use to
run a one-shot feedback pass. It wraps the `load_draft -> chat -> collect references`
sequence so callers never depend on `ResearchAgent` internals, and it takes:

- an injected `Config` (per-job; no global singleton mutation), and
- an optional `memory_context` string (cross-submission memory the caller assembles,
  e.g. "don't re-raise rejected suggestions; follow up on deferred ones").

Example:
    from rainer import FeedbackEngine, Config
    result = FeedbackEngine(config=my_config).run(
        draft_text=text, mode="feedback", memory_context=prior_decisions
    )
    pdf_source = result.report_markdown
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .agent import ResearchAgent
from .config import Config

DEFAULT_PROMPT = "Please review the loaded draft and produce your full structured report."


class IncompleteReportError(RuntimeError):
    """The run produced no usable report: it ran out of steps without an answer, came back
    empty, or lacks most of the mode's required sections. Raised so a service can retry or
    fail the job instead of publishing it."""


@dataclass
class FeedbackResult:
    """Outcome of a single feedback run."""

    report_markdown: str
    references: str = ""
    bibtex: str = ""
    provider: str | None = None
    model: str | None = None
    mode: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class FeedbackEngine:
    """Run rainer feedback on a draft and return the report + metadata."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config

    def run(
        self,
        *,
        draft_text: str,
        mode: str = "feedback",
        draft_name: str = "draft",
        metadata: dict[str, Any] | None = None,
        memory_context: str | None = None,
        prompt: str | None = None,
    ) -> FeedbackResult:
        agent = ResearchAgent(mode=mode, config=self.config)
        agent.load_draft(draft_text, name=draft_name, metadata=metadata)

        user_prompt = prompt or DEFAULT_PROMPT
        if memory_context:
            user_prompt += (
                "\n\n[Memory from previous submissions]\n"
                "Do NOT re-raise suggestions the student rejected; follow up on "
                "deferred ones; acknowledge resolved ones.\n" + memory_context
            )

        report = agent.chat(user_prompt)

        missing, required = agent.required_heading_gap(report)
        run_info = {
            "steps": getattr(agent, "last_run_steps", None),
            "missing_headings": missing,
        }
        # Fail only on gross incompleteness: the heading check is a substring match, so a
        # single reworded heading must not throw away an otherwise good (and costly) report.
        if getattr(agent, "last_run_incomplete", False):
            raise IncompleteReportError(f"no report within the step budget ({run_info})")
        if not report.strip():
            raise IncompleteReportError(f"empty report ({run_info})")
        if required and len(missing) * 2 > required:
            raise IncompleteReportError(
                f"report lacks {len(missing)} of {required} required sections ({run_info})"
            )

        return FeedbackResult(
            report_markdown=report,
            references=agent.get_references(),
            bibtex=agent.get_bibtex(),
            provider=agent.provider_type,
            model=agent.model,
            mode=mode,
            metadata=run_info,
        )
