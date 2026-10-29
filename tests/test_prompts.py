"""Tests for prompt loading utilities."""

from tempfile import TemporaryDirectory

from rainer.config import get_config, set_config
from rainer.prompts import (
    DEFAULT_PROMPT_DIR,
    PROMPT_FILES,
    load_all_prompts,
    load_prompt,
    resolve_prompt,
)


def test_prompt_files_exist():
    """Ensure all prompt files exist on disk."""
    for mode, filename in PROMPT_FILES.items():
        path = DEFAULT_PROMPT_DIR / filename
        assert path.exists(), f"Missing prompt file for mode '{mode}': {path}"


def test_load_prompt_returns_non_empty_text():
    """Prompt templates should load as non-empty strings."""
    for mode in PROMPT_FILES:
        text = load_prompt(mode)
        assert isinstance(text, str)
        assert text.strip(), f"Prompt for mode '{mode}' is empty"


def test_load_all_prompts_returns_all_modes():
    """load_all_prompts should include every mode."""
    prompts = load_all_prompts()
    assert set(prompts.keys()) == set(PROMPT_FILES.keys())
    for mode, text in prompts.items():
        assert isinstance(text, str)
        assert text.strip(), f"Prompt for mode '{mode}' is empty"


def test_resolve_prompt_fallback():
    """resolve_prompt should return fallback when file is missing."""
    fallback = "fallback prompt"
    config = get_config()
    original_prompt_dir = config.output.prompt_dir
    try:
        with TemporaryDirectory() as tmp_dir:
            config.output.prompt_dir = tmp_dir
            set_config(config)
            text = resolve_prompt("feedback", fallback=fallback)
            assert text == fallback
    finally:
        config.output.prompt_dir = original_prompt_dir
        set_config(config)


def test_every_mode_forbids_time_estimates():
    """No mode may hand a student durations or schedules (see ResearchAgent.TIME_ESTIMATE_RULES)."""
    from rainer.agent import ResearchAgent
    from rainer.config import Config

    for mode in ("feedback", "feedback_hyp_rd", "feedback_results", "feedback_final", "review"):
        prompt = ResearchAgent(mode=mode, config=Config())._build_prompt()
        assert ResearchAgent.TIME_ESTIMATE_RULES in prompt
        assert "expected effort" not in prompt


def test_every_report_mode_gets_math_rules():
    """Reports are rendered through pandoc, so every report mode carries the math rules."""
    from rainer.agent import ResearchAgent
    from rainer.config import Config

    for mode in ("feedback", "feedback_hyp_rd", "feedback_results", "feedback_final", "grading"):
        prompt = ResearchAgent(mode=mode, config=Config())._build_prompt()
        assert ResearchAgent.MATH_RULES in prompt
        assert "rendered to PDF via Quarto" not in prompt


def test_required_headings_appear_in_their_prompts():
    """The heading check and the prompt must change together, or every run costs a follow-up."""
    from rainer.agent import ResearchAgent
    from rainer.prompts import resolve_prompt

    for mode, layouts in ResearchAgent._REQUIRED_HEADING_LAYOUTS.items():
        prompt = resolve_prompt(mode)
        for layout in layouts:
            for heading in layout:
                assert heading in prompt, f"{mode}: '{heading}' not in prompt"


def test_final_feedback_gives_no_verdict():
    from rainer.prompts import resolve_prompt

    prompt = resolve_prompt("feedback_final")
    for phrase in ("PASS LIKELY", "BORDERLINE", "Bottom-line verdict", "Grading-matrix", "to pass"):
        assert phrase not in prompt
    assert "NO-JUDGMENT RULE" in prompt
