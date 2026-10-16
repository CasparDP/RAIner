"""Literature recall: load-all corpus, hybrid search, lookup_paper.

The Postgres tests run against a throwaway database and auto-skip unless
RAINER_TEST_PG_DSN is set (they DROP the papers + paper_embeddings tables).
"""

# ruff: noqa: E402  (imports must follow the importorskip guard)
import os
import sys
import types

import duckdb
import numpy as np
import pytest

pytest.importorskip("psycopg", reason="poetry install --with postgres")

import psycopg
from pgvector.psycopg import register_vector

from rainer import Config, pg_loader
from rainer.agent import ResearchAgent
from rainer.papers import Paper, PaperDB
from rainer.pg_backend import PgPaperDB, PgVectorSearch, embedding_text

PG_DSN = os.environ.get("RAINER_TEST_PG_DSN")
needs_pg = pytest.mark.skipif(not PG_DSN, reason="set RAINER_TEST_PG_DSN to a throwaway database")

# (doi, title, authors, year, abstract)
PAPERS = [
    ("10.1/a", "Corporate Governance and Firm Value: Evidence from Emerging Markets",
     "John Smith; Ana Garcia", 2012, "We study boards and valuation in emerging markets " * 3),
    ("10.1/b", "Earnings Management around Equity Offerings", "Ana Garcia", 2013, None),
    ("10.1/c", "Auditor Tenure and Audit Quality", "Wei Chen; Peter Miller", 2005,
     "Long auditor tenure and audit quality are linked in this sample " * 3),
    ("10.1/d", "Tax Avoidance and Debt Pricing", "Peter Miller", 2008, "x" * 20),
]


def _make_duckdb(path) -> str:
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE articles (doi VARCHAR, title VARCHAR, authors VARCHAR, year INTEGER, "
        "journal_name VARCHAR, journal_issn VARCHAR, publisher VARCHAR)"
    )
    con.execute(
        "CREATE TABLE ssrn_pages (doi VARCHAR, abstract VARCHAR, ssrn_id VARCHAR, ssrn_url VARCHAR)"
    )
    # reversed so that doi ordering is actually exercised
    for doi, title, authors, year, abstract in reversed(PAPERS):
        con.execute("INSERT INTO articles VALUES (?, ?, ?, ?, 'J Test', '1111-2222', 'Pub')",
                    [doi, title, authors, year])
        if doi != "10.1/b":  # b has no ssrn page at all; d has a too-short abstract
            con.execute(
                "INSERT INTO ssrn_pages VALUES (?, ?, '1', 'http://ssrn/1')", [doi, abstract]
            )
    con.close()
    return str(path)


@pytest.fixture
def duck(tmp_path):
    return _make_duckdb(tmp_path / "articles.duckdb")


@pytest.fixture
def pg(monkeypatch):
    """Fresh schema in the throwaway database; yields the DSN."""
    with psycopg.connect(PG_DSN, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS paper_embeddings, papers CASCADE")
        conn.execute(pg_loader.SCHEMA_SQL)
    yield PG_DSN


def _vec(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).normal(size=384).astype(np.float32)
    return v / np.linalg.norm(v)


def _insert(dsn, rows, vectors):
    """rows: (doi, title, authors, year, abstract); vectors: one per row."""
    with psycopg.connect(dsn) as conn:
        register_vector(conn)
        for (doi, title, authors, year, abstract), vec in zip(rows, vectors):
            conn.execute(
                "INSERT INTO papers (doi, title, authors, year, abstract) VALUES (%s,%s,%s,%s,%s)",
                (doi, title, authors, year, abstract),
            )
            conn.execute("INSERT INTO paper_embeddings VALUES (%s, %s)", (doi, vec))


def _search(dsn, qvec) -> PgVectorSearch:
    search = PgVectorSearch(dsn=dsn)
    search._embed = lambda text: qvec
    return search


# ---------------------------------------------------------------- WP1: load every paper


def test_embedding_text():
    assert embedding_text("A Title", "An abstract") == "A Title. An abstract"
    assert embedding_text("A Title", None) == "A Title"


def test_get_papers_for_loading_keeps_all_papers_in_doi_order(duck):
    papers = PaperDB(db_path=duck).get_papers_for_loading()
    assert [p.id for p in papers] == ["10.1/a", "10.1/b", "10.1/c", "10.1/d"]
    by_id = {p.id: p for p in papers}
    assert by_id["10.1/a"].abstract and by_id["10.1/c"].abstract
    assert by_id["10.1/b"].abstract is None  # no ssrn page
    assert by_id["10.1/d"].abstract is None  # abstract of 50 chars or fewer
    assert [p.id for p in PaperDB(db_path=duck).get_papers_for_loading(limit=2)] == [
        "10.1/a",
        "10.1/b",
    ]
    # the ChromaDB path keeps its abstracts-only selection
    assert {p.id for p in PaperDB(db_path=duck).get_papers_with_abstracts()} == {
        "10.1/a",
        "10.1/c",
    }


@needs_pg
def test_loader_embeds_title_when_no_abstract_and_resumes(pg, duck, monkeypatch):
    texts: list[str] = []

    class FakeEmbedder:
        def __init__(self, model):
            pass

        def encode(self, batch):
            texts.extend(batch)
            return np.stack([_vec(len(t)) for t in batch])

    fake = types.ModuleType("sentence_transformers")
    fake.SentenceTransformer = FakeEmbedder
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)

    def run(*extra):
        monkeypatch.setattr(
            sys, "argv",
            ["rainer-load-pg", "--duckdb", duck, "--dsn", pg, "--batch-size", "2", *extra],
        )
        pg_loader.main()

    run()
    with psycopg.connect(pg) as conn:
        rows = dict(conn.execute("SELECT doi, abstract FROM papers").fetchall())
        n_emb = conn.execute("SELECT count(*) FROM paper_embeddings").fetchone()[0]
    assert set(rows) == {p[0] for p in PAPERS} and n_emb == 4
    assert rows["10.1/b"] is None and rows["10.1/d"] is None
    assert PAPERS[1][1] in texts  # embedded from the bare title
    assert any(t.startswith(PAPERS[0][1] + ". We study boards") for t in texts)

    # resume: --offset skips the first papers; re-running is idempotent
    texts.clear()
    run("--offset", "3")
    assert texts == [PAPERS[3][1]]
    run()
    with psycopg.connect(pg) as conn:
        assert conn.execute("SELECT count(*) FROM papers").fetchone()[0] == 4


@needs_pg
def test_schema_sql_is_idempotent(pg):
    with psycopg.connect(pg, autocommit=True) as conn:
        conn.execute(pg_loader.SCHEMA_SQL)  # second run on an existing table


# ---------------------------------------------------------------- WP2: hybrid search

TARGET_TITLE = "Disclosure Quality and the Cost of Capital in Emerging Markets"


def _hybrid_corpus(dsn):
    """15 papers with random vectors; the exact-title paper is the one farthest from qv."""
    vecs = [_vec(i) for i in range(15)]
    qv = vecs[0]
    far = int(np.argmin([float(v @ qv) for v in vecs]))
    rows = []
    for i in range(15):
        title = TARGET_TITLE if i == far else f"Unrelated study number {i} on bonds"
        rows.append((f"10.2/{i:02d}", title, f"Author{i} Surname", 2000 + i, f"Abstract {i}"))
    _insert(dsn, rows, vecs)
    return qv, vecs, rows[far][0]


@needs_pg
def test_exact_title_ranks_first_even_when_vector_is_far(pg):
    qv, _, target = _hybrid_corpus(pg)
    results = _search(pg, qv).search(f'"{TARGET_TITLE}"', top_k=5)
    assert results[0].paper.id == target
    search = _search(pg, qv)  # SET LOCAL must not leak into later queries
    search.search("anything", top_k=1)
    assert search.paper_db.conn.execute("SHOW hnsw.ef_search").fetchone()[0] == "40"


@needs_pg
def test_year_filter_still_returns_top_k(pg):
    qv, _, _ = _hybrid_corpus(pg)
    results = _search(pg, qv).search("bonds", top_k=5, year_min=2005)
    assert len(results) == 5
    assert all(r.paper.year >= 2005 for r in results)
    results = _search(pg, qv).search("bonds", top_k=5, year_min=2005, year_max=2009)
    assert len(results) == 5 and all(2005 <= r.paper.year <= 2009 for r in results)


@needs_pg
def test_scores_are_cosine_similarities_including_fulltext_only_hits(pg):
    qv, vecs, target = _hybrid_corpus(pg)
    results = _search(pg, qv).search(f'"{TARGET_TITLE}"', top_k=15)
    assert len(results) == 15
    by_id = {f"10.2/{i:02d}": v for i, v in enumerate(vecs)}
    for r in results:
        assert -1.0 <= r.score <= 1.0
        assert r.score == pytest.approx(float(by_id[r.paper.id] @ qv), abs=1e-4)


@needs_pg
def test_all_stop_word_query_falls_back_to_vector_only(pg):
    qv, _, _ = _hybrid_corpus(pg)
    results = _search(pg, qv).search("the of and", top_k=5)
    assert len(results) == 5
    assert results[0].paper.id == "10.2/00"  # nearest vector


# ---------------------------------------------------------------- WP3: lookup_paper


class FakeLookupDB:
    def __init__(self):
        self.calls = []
        self.papers = [
            Paper(id="10.9/z", title="A Cited Paper", authors="Jane Doe", year=2019,
                  abstract="a" * 400),
            Paper(id="10.9/y", title="Another Paper", authors="Jane Doe", year=2020),
        ]

    def lookup(self, title=None, author=None, year=None, limit=10):
        self.calls.append((title, author, year))
        return self.papers

    def get_paper(self, paper_id):
        return next((p for p in self.papers if p.id == paper_id), None)


def _agent() -> ResearchAgent:
    cfg = Config()
    cfg.provider.name = "ollama"
    agent = ResearchAgent(mode="search", config=cfg)
    agent.paper_db = FakeLookupDB()
    return agent


def test_lookup_paper_tool_schema():
    tool = next(t for t in ResearchAgent.TOOLS if t["function"]["name"] == "lookup_paper")
    props = tool["function"]["parameters"]["properties"]
    assert props["title"]["type"] == "string"
    assert props["author"]["type"] == "string"
    assert props["year"]["type"] == "integer"
    assert tool["function"]["parameters"]["required"] == []
    assert "verify a cited paper" in tool["function"]["description"]
    assert "lookup_paper" in ResearchAgent.CITATION_RULES


def test_lookup_paper_registers_results_as_seen():
    agent = _agent()
    res = agent._execute_tool("lookup_paper", {"title": "A Cited Paper", "year": 2019})
    assert res.success
    assert [r["id"] for r in res.result] == ["10.9/z", "10.9/y"]
    assert set(res.result[0]) == {"id", "title", "authors", "year", "abstract_snippet"}
    assert res.result[0]["abstract_snippet"].endswith("...")
    assert set(agent.seen_papers) == {"10.9/z", "10.9/y"}
    assert agent.paper_db.calls == [("A Cited Paper", None, 2019)]
    assert agent._execute_tool("get_paper_details", {"paper_id": "10.9/z"}).success


def test_lookup_paper_requires_title_or_author():
    agent = _agent()
    res = agent._execute_tool("lookup_paper", {"year": 2019})
    assert not res.success and "title or author" in res.error
    assert agent.paper_db.calls == []


@needs_pg
def test_pg_lookup_finds_title_without_subtitle_and_surname(pg):
    _insert(
        pg,
        PAPERS[:2]
        + [("10.1/e", "Corporate Governance Reform in Japan", "Kenji Tanaka", 2012, None)],
        [_vec(i) for i in range(3)],
    )
    db = PgPaperDB(dsn=pg)
    hits = db.lookup(title="Corporate Governance and Firm Value")
    assert hits[0].id == "10.1/a"
    assert "10.1/b" not in [h.id for h in hits]
    assert [h.id for h in db.lookup(author="Garcia")] == ["10.1/b", "10.1/a"]  # newest first
    assert [h.id for h in db.lookup(title="Earnings Management", author="Garcia")] == ["10.1/b"]
    assert db.lookup(title="Earnings Management", author="Tanaka") == []
    # a typo in the title still matches; a wrong year only reorders
    typo = db.lookup(title="Earnigns Managment around Equity Offerings", year=1990)
    assert typo[0].id == "10.1/b"
    assert db.lookup(author="Garcia", year=2011)[0].id == "10.1/a"
    assert db.lookup(author="Gar%") == []  # LIKE wildcards are escaped
    assert db.lookup() == []


def test_duckdb_lookup(duck):
    db = PaperDB(db_path=duck)
    assert db.lookup(title="Corporate Governance and Firm Value")[0].id == "10.1/a"
    assert db.lookup(title="Earnigns Managment around Equity Offerings")[0].id == "10.1/b"
    assert db.lookup(title="Quantum Chromodynamics") == []
    assert [p.id for p in db.lookup(author="garcia")] == ["10.1/b", "10.1/a"]
    assert [p.id for p in db.lookup(title="Earnings Management", author="Garcia")] == ["10.1/b"]
    assert db.lookup(author="Garcia", year=2011)[0].id == "10.1/a"  # year only reorders
    assert db.lookup() == []
