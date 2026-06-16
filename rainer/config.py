"""Configuration management for RAiner."""

import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load .env file if present (looks in current dir and parent dirs)
load_dotenv()


class OllamaConfig(BaseModel):
    """Local Ollama instance. For cloud models, run `ollama signin` first."""

    base_url: str = "http://localhost:11434"


class OllamaCloudConfig(BaseModel):
    """Direct Ollama Cloud API access (no local Ollama needed)."""

    base_url: str = "https://ollama.com"
    api_key: str | None = Field(default_factory=lambda: os.getenv("OLLAMA_API_KEY"))


class OpenRouterConfig(BaseModel):
    base_url: str = "https://openrouter.ai/api/v1"
    api_key: str | None = Field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY"))


class OpenAIConfig(BaseModel):
    api_key: str | None = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    model: str = "gpt-4o"


class AnthropicConfig(BaseModel):
    api_key: str | None = Field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY"))
    model: str = "claude-sonnet-4-20250514"


class GoogleConfig(BaseModel):
    api_key: str | None = Field(default_factory=lambda: os.getenv("GOOGLE_API_KEY"))
    model: str = "gemini-2.0-flash"


class AzureOpenAIConfig(BaseModel):
    """Azure OpenAI (OpenAI-API-compatible, EU-region capable).

    `deployment` is the Azure deployment name and is used as the model id. If unset,
    the top-level `provider.model` is used instead.
    """

    api_key: str | None = Field(default_factory=lambda: os.getenv("AZURE_OPENAI_API_KEY"))
    endpoint: str | None = Field(default_factory=lambda: os.getenv("AZURE_OPENAI_ENDPOINT"))
    api_version: str = Field(
        default_factory=lambda: os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
    )
    deployment: str | None = Field(default_factory=lambda: os.getenv("AZURE_OPENAI_DEPLOYMENT"))


class ProviderConfig(BaseModel):
    # ollama: local Ollama (use `ollama signin` for cloud models like gpt-oss:120b-cloud)
    # ollama-cloud: direct API access to ollama.com (requires OLLAMA_API_KEY)
    # openrouter: OpenRouter API (requires OPENROUTER_API_KEY)
    # openai: OpenAI API (requires OPENAI_API_KEY)
    # azure-openai: Azure OpenAI (requires AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT)
    # anthropic: Anthropic API (requires ANTHROPIC_API_KEY)
    # google: Google Gemini API (requires GOOGLE_API_KEY)
    name: Literal[
        "ollama", "ollama-cloud", "openrouter", "openai", "azure-openai", "anthropic", "google"
    ] = "ollama"
    model: str = "kimi-k2.5:cloud"
    temperature: float = 0.1
    # model: str = "qwen2.5:14b"  # Local Ollama model format
    # model: str = "gpt-oss:120b-cloud"  # Ollama cloud model (via local relay)
    # model: str = "gpt-oss:120b"  # Ollama cloud model (via direct API)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    ollama_cloud: OllamaCloudConfig = Field(default_factory=OllamaCloudConfig)
    openrouter: OpenRouterConfig = Field(default_factory=OpenRouterConfig)
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    azure_openai: AzureOpenAIConfig = Field(default_factory=AzureOpenAIConfig)
    anthropic: AnthropicConfig = Field(default_factory=AnthropicConfig)
    google: GoogleConfig = Field(default_factory=GoogleConfig)

    class Config:
        # Allow ollama-cloud as field name in YAML
        populate_by_name = True


class InstitutionConfig(BaseModel):
    """The institution whose library/database subscriptions the feasibility audit checks.

    Defaults to EUR (the project's origin). For other deployments, override `name` and
    `library_url`, and point `data.databases_path` at your own database-access JSON.
    """

    name: str = "EUR (Erasmus University Rotterdam)"
    library_url: str = "https://libguides.eur.nl/az/databases"


class DataConfig(BaseModel):
    # Corpus backend: "duckdb" (local DuckDB + ChromaDB, the default) or "postgres"
    # (Postgres + pgvector, for the deployed/multi-user setup). When "postgres",
    # set postgres_dsn; duckdb_path/chroma_path are then ignored for search.
    backend: Literal["duckdb", "postgres"] = "duckdb"
    postgres_dsn: str | None = None  # e.g. postgresql://user:pass@host:5432/db

    # Path to the institution's database-access JSON (the feasibility-audit source).
    # None = use the bundled default (rainer/data/databases.json).
    databases_path: str | None = None

    duckdb_path: str = "./data/papers.duckdb"
    chroma_path: str = "./data/chroma"
    chroma_collection: str = "paper_abstracts"
    sessions_path: str = "~/.local/share/rainer/sessions"
    students_db_path: str = "./data/students.duckdb"


class EmbeddingsConfig(BaseModel):
    model: str = "all-MiniLM-L6-v2"


class SearchConfig(BaseModel):
    top_k: int = 40
    min_similarity: float = 0.3


class ModeConfig(BaseModel):
    citation_style: Literal["inline", "quarto", "bibtex"] = "inline"
    bibtex_output: bool = False
    output_format: str = "markdown"


class ModesConfig(BaseModel):
    feedback: ModeConfig = Field(default_factory=lambda: ModeConfig(citation_style="inline"))
    feedback_final: ModeConfig = Field(
        default_factory=lambda: ModeConfig(citation_style="inline")
    )
    grading: ModeConfig = Field(default_factory=lambda: ModeConfig(citation_style="inline"))
    writing: ModeConfig = Field(
        default_factory=lambda: ModeConfig(citation_style="quarto", bibtex_output=True)
    )
    review: ModeConfig = Field(default_factory=lambda: ModeConfig(citation_style="bibtex"))
    search: ModeConfig = Field(default_factory=lambda: ModeConfig(citation_style="bibtex"))
    exam_review: ModeConfig = Field(
        default_factory=lambda: ModeConfig(citation_style="inline", bibtex_output=False)
    )


class OutputConfig(BaseModel):
    directory: str = "./output"
    prompt_dir: str | None = None
    include_doi: bool = True
    include_ssrn: bool = True
    default_extension: str = ".qmd"


class ChunkingConfig(BaseModel):
    max_chunk_tokens: int = 2000
    overlap_tokens: int = 200


class DraftContextConfig(BaseModel):
    # reduce to 4000 for small models
    max_context_tokens: int = 8000
    max_section_chars: int = 2000
    include_full_draft: bool = False


class Config(BaseModel):
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    institution: InstitutionConfig = Field(default_factory=InstitutionConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    embeddings: EmbeddingsConfig = Field(default_factory=EmbeddingsConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    modes: ModesConfig = Field(default_factory=ModesConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    draft_context: DraftContextConfig = Field(default_factory=DraftContextConfig)


def find_config_file() -> Path | None:
    """Find config file in standard locations."""
    locations = [
        Path("./config.yaml"),
        Path("./config.yml"),
        Path.home() / ".config" / "rainer" / "config.yaml",
        Path.home() / ".config" / "rainer" / "config.yml",
    ]
    for path in locations:
        if path.exists():
            return path
    return None


def load_config(config_path: str | Path | None = None) -> Config:
    """Load configuration from file or use defaults."""
    if config_path is None:
        config_path = find_config_file()

    if config_path is None:
        return Config()

    config_path = Path(config_path)
    if not config_path.exists():
        return Config()

    with open(config_path) as f:
        data = yaml.safe_load(f)

    return Config(**data)


# Global config instance
_config: Config | None = None


def get_config() -> Config:
    """Get the global config instance."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def set_config(config: Config) -> None:
    """Set the global config instance."""
    global _config
    _config = config
