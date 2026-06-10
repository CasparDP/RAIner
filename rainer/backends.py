"""Corpus backend factory.

Selects the paper metadata + vector-search implementation from config:
- "duckdb"   -> PaperDB + PaperSearch (local DuckDB + ChromaDB; the default)
- "postgres" -> PgPaperDB + PgVectorSearch (Postgres + pgvector; imported lazily so
                the psycopg/pgvector deps are only needed when actually selected)
"""

from __future__ import annotations

from .config import Config, get_config
from .papers import PaperDB
from .search import PaperSearch


def create_paper_db(config: Config | None = None):
    cfg = config or get_config()
    if cfg.data.backend == "postgres":
        from .pg_backend import PgPaperDB

        return PgPaperDB(config=cfg)
    return PaperDB(config=cfg)


def create_paper_search(config: Config | None = None, paper_db=None):
    cfg = config or get_config()
    if cfg.data.backend == "postgres":
        from .pg_backend import PgVectorSearch

        return PgVectorSearch(config=cfg, paper_db=paper_db)
    return PaperSearch(config=cfg, paper_db=paper_db)
