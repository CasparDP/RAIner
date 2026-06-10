"""ResearchAgent accepts an injected Config and does not mutate the global one."""

from rainer import Config, get_config
from rainer.agent import ResearchAgent


def _ollama_config(model: str) -> Config:
    cfg = Config()
    cfg.provider.name = "ollama"  # no API key / network needed to construct
    cfg.provider.model = model
    return cfg


def test_injected_config_is_used():
    cfg = _ollama_config("custom-model-xyz")
    agent = ResearchAgent(mode="search", config=cfg)
    assert agent.config is cfg
    assert agent.provider_type == "ollama"
    assert agent.model == "custom-model-xyz"


def test_no_global_mutation():
    before = get_config().provider.model
    agent = ResearchAgent(mode="search", config=_ollama_config("isolated-model"))
    assert agent.model == "isolated-model"
    assert get_config().provider.model == before  # global left untouched


def test_data_backends_use_injected_config(tmp_path):
    cfg = _ollama_config("m")
    cfg.data.duckdb_path = str(tmp_path / "papers.duckdb")
    cfg.data.chroma_path = str(tmp_path / "chroma")
    agent = ResearchAgent(mode="search", config=cfg)
    assert str(agent.paper_db.db_path).endswith("papers.duckdb")
    assert str(agent.paper_search.chroma_path).endswith("chroma")
