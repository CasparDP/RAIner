"""Programmatic math-formatting checks and SDK retry settings."""

import pytest

from rainer import Config
from rainer.agent import LLM_MAX_RETRIES, ResearchAgent


def _check(mode: str, content: str) -> list[str]:
    return ResearchAgent(mode=mode, config=Config())._check_formatting_issues(content)


@pytest.mark.parametrize(
    "content",
    [
        "The coefficient $ \\beta $ is positive.",
        "The coefficient $\\beta_1 $ is positive.",
        "Use \\(\\beta\\) here.",
        "Display:\n\\[ Y = \\alpha \\]",
    ],
)
def test_flags_pandoc_unsafe_math(content):
    issues = _check("feedback_results", content)
    assert any("Math formatting violation" in issue for issue in issues)


@pytest.mark.parametrize(
    "content",
    [
        "The coefficient $\\beta$ on $\\text{ROA}_{it}$ is $p < 0.01$.",
        "$$ Y_{it} = \\alpha + \\beta X_{it} $$",
        "It costs $5 and $10 later, or $ 20 in total.",
    ],
)
def test_accepts_clean_math_and_dollar_amounts(content):
    assert _check("feedback_results", content) == []


def test_voice_check_covers_student_facing_modes_only():
    text = "The student uses $\\beta$."
    assert _check("grading", text) == []
    assert _check("review", text) == []
    for mode in ResearchAgent.STUDENT_FACING_MODES:
        assert any("Voice violation" in issue for issue in _check(mode, text)), mode


def test_voice_check_ignores_cited_authors():
    # Seen in live runs: phrases about cited papers triggered a needless full rewrite.
    text = "I searched the author surname; check the author names of Griffin et al. (2021)."
    assert _check("feedback", text) == []


@pytest.mark.parametrize("api", ["chat", "responses"])
def test_azure_client_retries_rate_limits(api):
    cfg = Config()
    cfg.provider.name = "azure-openai"
    cfg.provider.azure_openai.api_key = "test-key"
    cfg.provider.azure_openai.endpoint = "https://example.openai.azure.com"
    cfg.provider.azure_openai.deployment = "gpt-test"
    cfg.provider.azure_openai.api = api
    agent = ResearchAgent(mode="search", config=cfg)
    assert agent.client.max_retries == LLM_MAX_RETRIES == 8


@pytest.mark.parametrize("provider", ["openai", "openrouter", "anthropic"])
def test_other_clients_retry_rate_limits(provider):
    cfg = Config()
    cfg.provider.name = provider
    getattr(cfg.provider, provider).api_key = "test-key"
    agent = ResearchAgent(mode="search", config=cfg)
    assert agent.client.max_retries == LLM_MAX_RETRIES
