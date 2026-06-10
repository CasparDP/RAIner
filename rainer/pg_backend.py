"""Postgres + pgvector corpus backend.

Drop-in alternatives to PaperDB / PaperSearch (DuckDB + ChromaDB) for the deployed,
multi-user setup. Same query semantics — cosine similarity over abstract embeddings —
but against a shared Postgres so several stateless workers can read one corpus.

Requires the optional `[postgres]` extra: `pip install "rainer[postgres]"`
(psycopg + pgvector). Imports are done at module load, so this module is only
imported when `data.backend == "postgres"` (see backends.py).

The query embedding is produced by the SAME model rainer uses everywhere
(all-MiniLM-L6-v2), guaranteeing stored and query vectors share one space.
"""

from __future__ import annotations

import psycopg
from pgvector.psycopg import register_vector

from .config import Config, get_config
from .papers import Paper
from .search import SearchResult

# Columns selected (and their order) for mapping a row to a Paper.
_PAPER_COLUMNS = "doi, title, authors, year, journal, issn, abstract, ssrn_id, ssrn_url"


def _normalize_dsn(dsn: str | None) -> str:
    """Turn a SQLAlchemy-style URL into a libpq DSN psycopg accepts."""
    if not dsn:
        raise ValueError(
            "Postgres backend selected but data.postgres_dsn is not set "
            "(expected e.g. postgresql://user:pass@host:5432/db)."
        )
    return dsn.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )


def _row_to_paper(row) -> Paper:
    """Map a (_PAPER_COLUMNS-ordered) row tuple to a Paper."""
    return Paper(
        id=row[0],
        title=row[1] or "",
        authors=row[2],
        year=row[3],
        journal_name=row[4],
        journal_issn=row[5],
        abstract=row[6],
        doi=row[0],
        ssrn_id=row[7],
        ssrn_url=row[8],
    )


class PgPaperDB:
    """Paper metadata over Postgres (mirrors the parts of PaperDB the agent uses)."""

    def __init__(self, config: Config | None = None, dsn: str | None = None):
        cfg = config or get_config()
        self.dsn = _normalize_dsn(dsn or cfg.data.postgres_dsn)
        self._conn: psycopg.Connection | None = None

    @property
    def conn(self) -> psycopg.Connection:
        """Lazy connection (registers the pgvector type adapter)."""
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self.dsn)
            register_vector(self._conn)
        return self._conn

    def get_paper(self, paper_id: str) -> Paper | None:
        with self.conn.cursor() as cur:
            cur.execute(
                f"SELECT {_PAPER_COLUMNS} FROM papers WHERE doi = %s", (paper_id,)
            )
            row = cur.fetchone()
        return _row_to_paper(row) if row else None

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()


class PgVectorSearch:
    """Semantic search over abstract embeddings using pgvector cosine distance."""

    def __init__(
        self,
        config: Config | None = None,
        paper_db: PgPaperDB | None = None,
        dsn: str | None = None,
        embedding_model: str | None = None,
    ):
        cfg = config or get_config()
        self.dsn = _normalize_dsn(dsn or cfg.data.postgres_dsn)
        self.embedding_model_name = embedding_model or cfg.embeddings.model
        self.paper_db = paper_db or PgPaperDB(config=cfg, dsn=self.dsn)
        self._embedder = None  # lazy: avoids importing sentence-transformers/torch at import

    def _embed(self, text: str):
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer

            self._embedder = SentenceTransformer(self.embedding_model_name)
        return self._embedder.encode(text)

    def search(
        self,
        query: str,
        top_k: int = 10,
        year_min: int | None = None,
        year_max: int | None = None,
    ) -> list[SearchResult]:
        qv = self._embed(query)

        conditions = ""
        params: dict[str, object] = {"qv": qv, "k": top_k}
        if year_min is not None:
            conditions += " AND p.year >= %(ymin)s"
            params["ymin"] = year_min
        if year_max is not None:
            conditions += " AND p.year <= %(ymax)s"
            params["ymax"] = year_max

        sql = f"""
            SELECT {", ".join(f"p.{c.strip()}" for c in _PAPER_COLUMNS.split(","))},
                   1 - (e.embedding <=> %(qv)s) AS score
            FROM papers p
            JOIN paper_embeddings e ON p.doi = e.doi
            WHERE TRUE{conditions}
            ORDER BY e.embedding <=> %(qv)s
            LIMIT %(k)s
        """
        with self.paper_db.conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        results: list[SearchResult] = []
        for row in rows:
            paper = _row_to_paper(row[:9])
            score = float(row[9])
            results.append(SearchResult(paper=paper, score=score))
        return results

    def find_similar_to_paper(self, doi: str, top_k: int = 10) -> list[SearchResult]:
        paper = self.paper_db.get_paper(doi)
        if not paper or not paper.abstract:
            return []
        return [r for r in self.search(paper.abstract, top_k=top_k + 1) if r.paper.id != doi][
            :top_k
        ]
