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

        return FeedbackResult(
            report_markdown=report,
            references=agent.get_references(),
            bibtex=agent.get_bibtex(),
            provider=agent.provider_type,
            model=agent.model,
            mode=mode,
        )
