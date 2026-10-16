"""Load the paper corpus into Postgres + pgvector by re-embedding title + abstract.

Reads paper metadata + abstracts from the DuckDB corpus (produced by cite-hustle),
recomputes embeddings with the SAME model rainer queries with (all-MiniLM-L6-v2),
and upserts into the `papers` + `paper_embeddings` tables. Every paper is loaded;
papers without an abstract are embedded from their title. Re-embedding guarantees
stored and query vectors share one space — see docs.

Usage:
    rainer-load-pg \\
        --duckdb /path/to/cite-hustle/DB/articles.duckdb \\
        --dsn postgresql://user:pass@host:5432/db \\
        [--limit N] [--offset N] [--batch-size 256]

The load is idempotent. Papers are processed in DOI order, so an interrupted run can
resume with --offset set to the last printed count.

Requires the optional extra: pip install "rainer[postgres]"
"""

from __future__ import annotations

import argparse

import psycopg
from pgvector.psycopg import register_vector

from .config import get_config
from .papers import PaperDB
from .pg_backend import _normalize_dsn, embedding_text

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

-- Shared schema contract for literature recall (rainer loader <-> mattie migration 0013).
-- Paste verbatim; names and expressions must match exactly in both repos.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Generated full-text column on papers (title weight A, authors B, abstract C).
ALTER TABLE papers ADD COLUMN IF NOT EXISTS search_tsv tsvector GENERATED ALWAYS AS (
    setweight(to_tsvector('english'::regconfig, coalesce(title, '')), 'A') ||
    setweight(to_tsvector('english'::regconfig, coalesce(authors, '')), 'B') ||
    setweight(to_tsvector('english'::regconfig, coalesce(abstract, '')), 'C')
) STORED;

CREATE INDEX IF NOT EXISTS ix_papers_search_tsv ON papers USING gin (search_tsv);
CREATE INDEX IF NOT EXISTS ix_papers_title_trgm ON papers USING gin (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_papers_authors_trgm ON papers USING gin (authors gin_trgm_ops);

-- Existing (unchanged): papers(doi PK, title, authors, year, journal, issn, abstract, ssrn_id,
-- ssrn_url, updated_at); paper_embeddings(doi PK FK, embedding vector(384));
-- ix_paper_embeddings_hnsw USING hnsw (embedding vector_cosine_ops).
-- papers.abstract stays nullable: papers without an SSRN abstract are now loaded too.
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
    parser.add_argument(
        "--offset", type=int, default=0, help="Skip the first N papers (DOI order) to resume"
    )
    parser.add_argument("--batch-size", type=int, default=256, help="Embedding batch size")
    args = parser.parse_args()

    dsn = _normalize_dsn(args.dsn)

    print(f"Reading papers from {args.duckdb} ...")
    all_papers = PaperDB(db_path=args.duckdb).get_papers_for_loading(limit=args.limit)
    papers = all_papers[args.offset :]
    print(f"  {len(all_papers)} papers; loading {len(papers)} (offset {args.offset})")
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

        total = args.offset
        for start in range(0, len(papers), args.batch_size):
            batch = papers[start : start + args.batch_size]
            vectors = embedder.encode([embedding_text(p.title, p.abstract) for p in batch])
            with conn.cursor() as cur:
                # One round trip per batch: the target DB can be a WAN hop away.
                cur.executemany(
                    UPSERT_PAPER,
                    [
                        {
                            "doi": p.id,
                            "title": p.title,
                            "authors": p.authors,
                            "year": p.year,
                            "journal": p.journal_name,
                            "issn": p.journal_issn,
                            "abstract": p.abstract,
                            "ssrn_id": p.ssrn_id,
                            "ssrn_url": p.ssrn_url,
                        }
                        for p in batch
                    ],
                )
                cur.executemany(
                    UPSERT_EMBEDDING,
                    [{"doi": p.id, "embedding": v} for p, v in zip(batch, vectors)],
                )
            conn.commit()
            total += len(batch)
            print(f"  upserted {total}/{len(all_papers)}")
        print(f"Done. {total - args.offset} papers loaded into Postgres.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
