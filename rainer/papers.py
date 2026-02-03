"""DuckDB interface for paper metadata."""

from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel

from .config import get_config


class Paper(BaseModel):
    """A paper from the database."""

    id: str  # Using DOI as primary ID
    title: str
    authors: str | None = None
    year: int | None = None
    abstract: str | None = None
    doi: str | None = None
    ssrn_id: str | None = None
    ssrn_url: str | None = None
    journal_name: str | None = None
    journal_issn: str | None = None
    publisher: str | None = None

    @property
    def author_list(self) -> list[str]:
        """Parse authors into a list."""
        if not self.authors:
            return []
        # Handle common formats: "Smith, John; Jones, Jane" or "John Smith, Jane Jones"
        if ";" in self.authors:
            return [a.strip() for a in self.authors.split(";")]
        # Handle " and " separator
        if " and " in self.authors:
            return [a.strip() for a in self.authors.split(" and ")]
        return [a.strip() for a in self.authors.split(",")]

    @property
    def first_author_surname(self) -> str:
        """Get first author's surname for citation."""
        authors = self.author_list
        if not authors:
            return "Unknown"
        first = authors[0]
        # Handle "Surname, Firstname" format
        if "," in first:
            return first.split(",")[0].strip()
        # Handle "Firstname Surname" format
        parts = first.split()
        return parts[-1] if parts else "Unknown"

    @property
    def doi_url(self) -> str | None:
        """Get DOI URL."""
        if self.doi:
            return f"https://doi.org/{self.doi}"
        return None

    @property
    def ssrn_page_url(self) -> str | None:
        """Get SSRN URL."""
        if self.ssrn_url:
            return self.ssrn_url
        if self.ssrn_id:
            return f"https://papers.ssrn.com/sol3/papers.cfm?abstract_id={self.ssrn_id}"
        return None

    def short_cite(self) -> str:
        """Short citation: Author (Year)."""
        year_str = str(self.year) if self.year else "n.d."
        return f"{self.first_author_surname} ({year_str})"


class PaperDB:
    """Interface to the DuckDB papers database."""

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = get_config().data.duckdb_path
        self.db_path = Path(db_path).expanduser()
        self._conn: duckdb.DuckDBPyConnection | None = None

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        """Lazy connection."""
        if self._conn is None:
            self._conn = duckdb.connect(str(self.db_path), read_only=True)
        return self._conn

    def _row_to_paper(self, row: tuple, columns: list[str]) -> Paper:
        """Convert a database row to a Paper object."""
        data = dict(zip(columns, row))
        return Paper(
            id=data.get("doi", ""),
            title=data.get("title", ""),
            authors=data.get("authors"),
            year=data.get("year"),
            abstract=data.get("abstract"),
            doi=data.get("doi"),
            ssrn_id=data.get("ssrn_id"),
            ssrn_url=data.get("ssrn_url"),
            journal_name=data.get("journal_name"),
            journal_issn=data.get("journal_issn"),
            publisher=data.get("publisher"),
        )

    def get_paper(self, doi: str) -> Paper | None:
        """Get a single paper by DOI."""
        result = self.conn.execute(
            """
            SELECT 
                a.doi, a.title, a.authors, a.year,
                a.journal_name, a.journal_issn, a.publisher,
                s.abstract, s.ssrn_id, s.ssrn_url
            FROM articles a
            LEFT JOIN ssrn_pages s ON a.doi = s.doi
            WHERE a.doi = ?
            LIMIT 1
            """,
            [doi],
        ).fetchone()
        if result:
            columns = [
                "doi", "title", "authors", "year",
                "journal_name", "journal_issn", "publisher",
                "abstract", "ssrn_id", "ssrn_url"
            ]
            return self._row_to_paper(result, columns)
        return None

    def get_papers(self, dois: list[str]) -> list[Paper]:
        """Get multiple papers by DOI."""
        papers = []
        for doi in dois:
            paper = self.get_paper(doi)
            if paper:
                papers.append(paper)
        return papers

    def search_by_title(self, query: str, limit: int = 10) -> list[Paper]:
        """Search papers by title (case-insensitive)."""
        result = self.conn.execute(
            """
            SELECT 
                a.doi, a.title, a.authors, a.year,
                a.journal_name, a.journal_issn, a.publisher,
                s.abstract, s.ssrn_id, s.ssrn_url
            FROM articles a
            LEFT JOIN ssrn_pages s ON a.doi = s.doi
            WHERE LOWER(a.title) LIKE LOWER(?)
            LIMIT ?
            """,
            [f"%{query}%", limit],
        ).fetchall()
        columns = [
            "doi", "title", "authors", "year",
            "journal_name", "journal_issn", "publisher",
            "abstract", "ssrn_id", "ssrn_url"
        ]
        return [self._row_to_paper(row, columns) for row in result]

    def search_by_author(self, author: str, limit: int = 20) -> list[Paper]:
        """Search papers by author."""
        result = self.conn.execute(
            """
            SELECT 
                a.doi, a.title, a.authors, a.year,
                a.journal_name, a.journal_issn, a.publisher,
                s.abstract, s.ssrn_id, s.ssrn_url
            FROM articles a
            LEFT JOIN ssrn_pages s ON a.doi = s.doi
            WHERE LOWER(a.authors) LIKE LOWER(?)
            ORDER BY a.year DESC
            LIMIT ?
            """,
            [f"%{author}%", limit],
        ).fetchall()
        columns = [
            "doi", "title", "authors", "year",
            "journal_name", "journal_issn", "publisher",
            "abstract", "ssrn_id", "ssrn_url"
        ]
        return [self._row_to_paper(row, columns) for row in result]

    def search_by_journal(self, journal: str, limit: int = 50) -> list[Paper]:
        """Search papers by journal name."""
        result = self.conn.execute(
            """
            SELECT 
                a.doi, a.title, a.authors, a.year,
                a.journal_name, a.journal_issn, a.publisher,
                s.abstract, s.ssrn_id, s.ssrn_url
            FROM articles a
            LEFT JOIN ssrn_pages s ON a.doi = s.doi
            WHERE LOWER(a.journal_name) LIKE LOWER(?)
            ORDER BY a.year DESC
            LIMIT ?
            """,
            [f"%{journal}%", limit],
        ).fetchall()
        columns = [
            "doi", "title", "authors", "year",
            "journal_name", "journal_issn", "publisher",
            "abstract", "ssrn_id", "ssrn_url"
        ]
        return [self._row_to_paper(row, columns) for row in result]

    def get_papers_by_year(self, year: int, limit: int = 50) -> list[Paper]:
        """Get papers from a specific year."""
        result = self.conn.execute(
            """
            SELECT 
                a.doi, a.title, a.authors, a.year,
                a.journal_name, a.journal_issn, a.publisher,
                s.abstract, s.ssrn_id, s.ssrn_url
            FROM articles a
            LEFT JOIN ssrn_pages s ON a.doi = s.doi
            WHERE a.year = ?
            LIMIT ?
            """,
            [year, limit],
        ).fetchall()
        columns = [
            "doi", "title", "authors", "year",
            "journal_name", "journal_issn", "publisher",
            "abstract", "ssrn_id", "ssrn_url"
        ]
        return [self._row_to_paper(row, columns) for row in result]

    def get_papers_with_abstracts(self, limit: int | None = None) -> list[Paper]:
        """Get all papers that have abstracts (for embedding)."""
        query = """
            SELECT 
                a.doi, a.title, a.authors, a.year,
                a.journal_name, a.journal_issn, a.publisher,
                s.abstract, s.ssrn_id, s.ssrn_url
            FROM articles a
            INNER JOIN ssrn_pages s ON a.doi = s.doi
            WHERE s.abstract IS NOT NULL 
              AND s.abstract != ''
              AND LENGTH(s.abstract) > 50
        """
        if limit:
            query += f" LIMIT {limit}"
        
        result = self.conn.execute(query).fetchall()
        columns = [
            "doi", "title", "authors", "year",
            "journal_name", "journal_issn", "publisher",
            "abstract", "ssrn_id", "ssrn_url"
        ]
        return [self._row_to_paper(row, columns) for row in result]

    def fulltext_search(self, query: str, limit: int = 20) -> list[Paper]:
        """
        Use DuckDB's FTS index for full-text search.
        Falls back to LIKE search if FTS fails.
        """
        try:
            # Try FTS search on articles
            result = self.conn.execute(
                """
                SELECT 
                    a.doi, a.title, a.authors, a.year,
                    a.journal_name, a.journal_issn, a.publisher,
                    s.abstract, s.ssrn_id, s.ssrn_url,
                    fts_main_articles.match_bm25(a.doi, ?) AS score
                FROM articles a
                LEFT JOIN ssrn_pages s ON a.doi = s.doi
                WHERE score IS NOT NULL
                ORDER BY score DESC
                LIMIT ?
                """,
                [query, limit],
            ).fetchall()
            columns = [
                "doi", "title", "authors", "year",
                "journal_name", "journal_issn", "publisher",
                "abstract", "ssrn_id", "ssrn_url", "score"
            ]
            return [self._row_to_paper(row[:-1], columns[:-1]) for row in result]
        except Exception:
            # Fallback to title search
            return self.search_by_title(query, limit)

    def get_random_papers(self, n: int = 5) -> list[Paper]:
        """Get random papers (for testing/exploration)."""
        result = self.conn.execute(
            f"""
            SELECT 
                a.doi, a.title, a.authors, a.year,
                a.journal_name, a.journal_issn, a.publisher,
                s.abstract, s.ssrn_id, s.ssrn_url
            FROM articles a
            LEFT JOIN ssrn_pages s ON a.doi = s.doi
            ORDER BY RANDOM() 
            LIMIT {n}
            """
        ).fetchall()
        columns = [
            "doi", "title", "authors", "year",
            "journal_name", "journal_issn", "publisher",
            "abstract", "ssrn_id", "ssrn_url"
        ]
        return [self._row_to_paper(row, columns) for row in result]

    def count_papers(self) -> int:
        """Count total papers in database."""
        result = self.conn.execute("SELECT COUNT(*) FROM articles").fetchone()
        return result[0] if result else 0

    def count_papers_with_abstracts(self) -> int:
        """Count papers that have abstracts."""
        result = self.conn.execute(
            """
            SELECT COUNT(*) FROM articles a
            INNER JOIN ssrn_pages s ON a.doi = s.doi
            WHERE s.abstract IS NOT NULL AND s.abstract != ''
            """
        ).fetchone()
        return result[0] if result else 0

    def get_stats(self) -> dict[str, Any]:
        """Get database statistics."""
        return {
            "total_papers": self.count_papers(),
            "papers_with_abstracts": self.count_papers_with_abstracts(),
            "year_range": self._get_year_range(),
            "top_journals": self._get_top_journals(5),
        }

    def _get_year_range(self) -> tuple[int, int] | None:
        """Get min and max year."""
        result = self.conn.execute(
            "SELECT MIN(year), MAX(year) FROM articles"
        ).fetchone()
        if result and result[0] and result[1]:
            return (result[0], result[1])
        return None

    def _get_top_journals(self, n: int = 5) -> list[tuple[str, int]]:
        """Get top journals by paper count."""
        result = self.conn.execute(
            """
            SELECT journal_name, COUNT(*) as cnt 
            FROM articles 
            WHERE journal_name IS NOT NULL
            GROUP BY journal_name 
            ORDER BY cnt DESC 
            LIMIT ?
            """,
            [n],
        ).fetchall()
        return [(row[0], row[1]) for row in result]

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
