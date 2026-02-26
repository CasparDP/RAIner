"""Prompt loading utilities for RAiner."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from .config import get_config

PromptMode = Literal["feedback", "writing", "review", "search"]

DEFAULT_PROMPT_DIR = Path(__file__).parent / "prompts"

PROMPT_FILES: dict[PromptMode, str] = {
    "feedback": "feedback.md",
    "writing": "writing.md",
    "review": "review.md",
    "search": "search.md",
}


class PromptNotFoundError(FileNotFoundError):
    """Raised when a prompt file cannot be found."""


def get_prompt_dir() -> Path:
    """Return the prompt directory, allowing config override."""
    config = get_config()
    prompt_dir = getattr(config.output, "prompt_dir", None)
    if prompt_dir:
        return Path(prompt_dir).expanduser()
    return DEFAULT_PROMPT_DIR


def load_prompt(mode: PromptMode) -> str:
    """Load a prompt template for the given mode."""
    prompt_dir = get_prompt_dir()
    filename = PROMPT_FILES[mode]
    path = prompt_dir / filename
    if not path.exists():
        raise PromptNotFoundError(f"Prompt not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def load_all_prompts() -> dict[PromptMode, str]:
    """Load all prompt templates."""
    return {mode: load_prompt(mode) for mode in PROMPT_FILES}


def resolve_prompt(mode: PromptMode, fallback: str | None = None) -> str:
    """Load prompt or return fallback if not found."""
    try:
        return load_prompt(mode)
    except PromptNotFoundError:
        if fallback is not None:
            return fallback
        raise
