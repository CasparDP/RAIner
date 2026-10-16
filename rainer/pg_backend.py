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

# Hybrid search: reciprocal rank fusion of vector and full-text candidates.
RRF_K = 60
CANDIDATES = 50  # candidates fetched per ranker before fusion
HNSW_EF_SEARCH = 200  # pgvector's default (40) is too shallow when a year filter drops rows


# lookup(): a title matches at word_similarity >= 0.5 (cited title missing its subtitle)
# or whole-title similarity >= 0.3 (typos, reordering).
TITLE_WORD_SIMILARITY = 0.5
TITLE_SIMILARITY = 0.3


def embedding_text(title: str | None, abstract: str | None) -> str:
    """Text embedded for a paper: title + abstract, or the title alone without an abstract."""
    return f"{title}. {abstract}" if abstract else title or ""


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
            # autocommit: read-only use, and SET LOCAL in search() must end with its transaction
            self._conn = psycopg.connect(self.dsn, autocommit=True)
            register_vector(self._conn)
        return self._conn

    def get_paper(self, paper_id: str) -> Paper | None:
        with self.conn.cursor() as cur:
            cur.execute(
                f"SELECT {_PAPER_COLUMNS} FROM papers WHERE doi = %s", (paper_id,)
            )
            row = cur.fetchone()
        return _row_to_paper(row) if row else None

    def lookup(
        self,
        title: str | None = None,
        author: str | None = None,
        year: int | None = None,
        limit: int = 10,
    ) -> list[Paper]:
        """Find papers by approximate title and/or author surname; year only orders results."""
        conditions: list[str] = []
        params: dict[str, object] = {"limit": limit}
        order: list[str] = []
        if year is not None:
            params["year"] = year
            order.append("coalesce(abs(year - %(year)s) <= 1, false) DESC")
        if title:
            params["title"] = title
            # The operators are word_similarity >= 0.5 OR similarity >= 0.3 (thresholds set below);
            # unlike the functions they can use the trigram index.
            conditions.append("(%(title)s <%% title OR title %% %(title)s)")
            order.append(
                "greatest(word_similarity(%(title)s, title), similarity(title, %(title)s)) DESC"
            )
        if author:
            escaped = author.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params["author"] = f"%{escaped}%"
            conditions.append("authors ILIKE %(author)s")
        if not conditions:
            return []
        order.append("year DESC NULLS LAST")
        sql = (
            f"SELECT {_PAPER_COLUMNS} FROM papers WHERE {' AND '.join(conditions)} "
            f"ORDER BY {', '.join(order)} LIMIT %(limit)s"
        )
        with self.conn.pipeline(), self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute(f"SET LOCAL pg_trgm.word_similarity_threshold = {TITLE_WORD_SIMILARITY}")
            cur.execute(f"SET LOCAL pg_trgm.similarity_threshold = {TITLE_SIMILARITY}")
            cur.execute(sql, params)
            return [_row_to_paper(row) for row in cur.fetchall()]

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
        params: dict[str, object] = {"qv": qv, "q": query, "k": top_k}
        if year_min is not None:
            conditions += " AND p.year >= %(ymin)s"
            params["ymin"] = year_min
        if year_max is not None:
            conditions += " AND p.year <= %(ymax)s"
            params["ymax"] = year_max

        # Vector and full-text candidates are fused by reciprocal rank in one statement;
        # the returned score stays the cosine similarity so its meaning does not change.
        # Papers without an abstract only enter through full text: their title-only embeddings
        # crowd out better matches on topical queries (blind-judged benchmark, 2026-10-16).
        sql = f"""
            WITH vec AS (
                SELECT doi, row_number() OVER (ORDER BY dist) AS rnk FROM (
                    SELECT p.doi, e.embedding <=> %(qv)s AS dist
                    FROM paper_embeddings e JOIN papers p ON p.doi = e.doi
                    WHERE p.abstract IS NOT NULL{conditions}
                    ORDER BY dist
                    LIMIT {CANDIDATES}
                ) v
            ), fts AS (
                SELECT doi, row_number() OVER (ORDER BY rank DESC) AS rnk FROM (
                    SELECT p.doi, ts_rank_cd(p.search_tsv, tsq.q) AS rank
                    FROM papers p, websearch_to_tsquery('english', %(q)s) AS tsq(q)
                    WHERE p.search_tsv @@ tsq.q{conditions}
                    ORDER BY rank DESC
                    LIMIT {CANDIDATES}
                ) t
            ), fused AS (
                SELECT doi, sum(1.0 / ({RRF_K} + rnk)) AS rrf
                FROM (SELECT doi, rnk FROM vec UNION ALL SELECT doi, rnk FROM fts) c
                GROUP BY doi
                ORDER BY rrf DESC, doi
                LIMIT %(k)s
            )
            SELECT {", ".join(f"p.{c.strip()}" for c in _PAPER_COLUMNS.split(","))},
                   1 - (e.embedding <=> %(qv)s) AS score
            FROM fused f
            JOIN papers p ON p.doi = f.doi
            JOIN paper_embeddings e ON e.doi = f.doi
            ORDER BY f.rrf DESC, p.doi
        """
        conn = self.paper_db.conn
        # The explicit transaction scopes SET LOCAL to this query; the pipeline batches
        # BEGIN + SET + SELECT into one network round trip (matters over a WAN hop).
        with conn.pipeline(), conn.transaction(), conn.cursor() as cur:
            cur.execute(f"SET LOCAL hnsw.ef_search = {HNSW_EF_SEARCH}")
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
        if not paper:
            return []
        text = embedding_text(paper.title, paper.abstract)
        return [r for r in self.search(text, top_k=top_k + 1) if r.paper.id != doi][:top_k]
