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
