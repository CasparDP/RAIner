"""Corpus backend selection + Postgres/pgvector mapping (no live DB needed)."""

from rainer import Config
from rainer.agent import ResearchAgent
from rainer.backends import create_paper_db, create_paper_search
from rainer.papers import PaperDB
from rainer.pg_backend import PgPaperDB, PgVectorSearch, _normalize_dsn, _row_to_paper
from rainer.search import PaperSearch


def _pg_config() -> Config:
    cfg = Config()
    cfg.provider.name = "ollama"  # construct agent without keys/network
    cfg.data.backend = "postgres"
    cfg.data.postgres_dsn = "postgresql+psycopg://u:p@h:5432/db"
    return cfg


def test_default_backend_is_duckdb():
    assert Config().data.backend == "duckdb"


def test_factory_selects_duckdb_by_default():
    cfg = Config()
    db = create_paper_db(cfg)
    search = create_paper_search(cfg, db)
    assert isinstance(db, PaperDB)
    assert isinstance(search, PaperSearch)


def test_factory_selects_postgres_and_is_lazy():
    cfg = _pg_config()
    db = create_paper_db(cfg)
    search = create_paper_search(cfg, db)
    assert isinstance(db, PgPaperDB)
    assert isinstance(search, PgVectorSearch)
    assert db._conn is None  # constructing must not open a connection


def test_agent_uses_postgres_backend():
    agent = ResearchAgent(mode="search", config=_pg_config())
    assert isinstance(agent.paper_db, PgPaperDB)
    assert isinstance(agent.paper_search, PgVectorSearch)


def test_normalize_dsn_strips_sqlalchemy_driver():
    assert _normalize_dsn("postgresql+psycopg://u:p@h/db") == "postgresql://u:p@h/db"
    assert _normalize_dsn("postgresql://u:p@h/db") == "postgresql://u:p@h/db"


def test_row_to_paper_mapping():
    row = (
        "10.1/x", "A Title", "Smith, J.", 2020, "J. Finance",
        "1234-5678", "Abstract text", "99", "http://ssrn/99",
    )
    p = _row_to_paper(row)
    assert p.id == "10.1/x" and p.doi == "10.1/x"
    assert p.title == "A Title"
    assert p.year == 2020
    assert p.journal_name == "J. Finance"
    assert p.journal_issn == "1234-5678"
    assert p.ssrn_id == "99"
