"""Load the paper corpus into Postgres + pgvector by re-embedding abstracts.

Reads paper metadata + abstracts from the DuckDB corpus (produced by cite-hustle),
recomputes embeddings with the SAME model rainer queries with (all-MiniLM-L6-v2),
and upserts into the `papers` + `paper_embeddings` tables. Re-embedding guarantees
stored and query vectors share one space — see docs.

Usage:
    rainer-load-pg \\
        --duckdb /path/to/cite-hustle/DB/articles.duckdb \\
        --dsn postgresql://user:pass@host:5432/db \\
        [--limit N] [--batch-size 256]

Requires the optional extra: pip install "rainer[postgres]"
"""

from __future__ import annotations

import argparse

import psycopg
from pgvector.psycopg import register_vector

from .config import get_config
from .papers import PaperDB
from .pg_backend import _normalize_dsn

EMBEDDING_DIM = 384  # all-MiniLM-L6-v2

SCHEMA_SQL = f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS papers (
    doi        TEXT PRIMARY KEY,
    title      TEXT,
    authors    TEXT,
    year       INTEGER,
    journal    TEXT,
    issn       TEXT,
    abstract   TEXT,
    ssrn_id    TEXT,
    ssrn_url   TEXT,
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS paper_embeddings (
    doi       TEXT PRIMARY KEY REFERENCES papers(doi),
    embedding vector({EMBEDDING_DIM})
);

CREATE INDEX IF NOT EXISTS ix_paper_embeddings_hnsw
    ON paper_embeddings USING hnsw (embedding vector_cosine_ops);
"""

UPSERT_PAPER = """
INSERT INTO papers
    (doi, title, authors, year, journal, issn, abstract, ssrn_id, ssrn_url, updated_at)
VALUES
    (%(doi)s, %(title)s, %(authors)s, %(year)s, %(journal)s, %(issn)s, %(abstract)s,
     %(ssrn_id)s, %(ssrn_url)s, now())
ON CONFLICT (doi) DO UPDATE SET
    title = EXCLUDED.title, authors = EXCLUDED.authors, year = EXCLUDED.year,
    journal = EXCLUDED.journal, issn = EXCLUDED.issn, abstract = EXCLUDED.abstract,
    ssrn_id = EXCLUDED.ssrn_id, ssrn_url = EXCLUDED.ssrn_url, updated_at = now();
"""

UPSERT_EMBEDDING = """
INSERT INTO paper_embeddings (doi, embedding)
VALUES (%(doi)s, %(embedding)s)
ON CONFLICT (doi) DO UPDATE SET embedding = EXCLUDED.embedding;
"""


def main() -> None:
    cfg = get_config()
    parser = argparse.ArgumentParser(description="Load the paper corpus into Postgres + pgvector.")
    parser.add_argument("--duckdb", default=cfg.data.duckdb_path, help="Path to articles.duckdb")
    parser.add_argument("--dsn", default=cfg.data.postgres_dsn, help="Postgres DSN")
    parser.add_argument("--model", default=cfg.embeddings.model, help="Embedding model")
    parser.add_argument("--limit", type=int, default=None, help="Limit papers (for testing)")
    parser.add_argument("--batch-size", type=int, default=256, help="Embedding batch size")
    args = parser.parse_args()

    dsn = _normalize_dsn(args.dsn)

    print(f"Reading papers from {args.duckdb} ...")
    papers = PaperDB(db_path=args.duckdb).get_papers_with_abstracts(limit=args.limit)
    print(f"  {len(papers)} papers with abstracts")
    if not papers:
        return

    print(f"Loading embedding model {args.model} ...")
    from sentence_transformers import SentenceTransformer

    embedder = SentenceTransformer(args.model)

    conn = psycopg.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
        register_vector(conn)

        total = 0
        for start in range(0, len(papers), args.batch_size):
            batch = papers[start : start + args.batch_size]
            vectors = embedder.encode([p.abstract or "" for p in batch])
            with conn.cursor() as cur:
                for paper, vec in zip(batch, vectors):
                    cur.execute(
                        UPSERT_PAPER,
                        {
                            "doi": paper.id,
                            "title": paper.title,
                            "authors": paper.authors,
                            "year": paper.year,
                            "journal": paper.journal_name,
                            "issn": paper.journal_issn,
                            "abstract": paper.abstract,
                            "ssrn_id": paper.ssrn_id,
                            "ssrn_url": paper.ssrn_url,
                        },
                    )
                    cur.execute(UPSERT_EMBEDDING, {"doi": paper.id, "embedding": vec})
            conn.commit()
            total += len(batch)
            print(f"  upserted {total}/{len(papers)}")
        print(f"Done. {total} papers loaded into Postgres.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
