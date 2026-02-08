"""MCP server for RAiner literature database.

Exposes paper search, retrieval, and citation formatting tools
for use with Claude and other MCP-compatible LLMs.

Run with: poetry run rainer-mcp
"""

import json
from enum import Enum
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .citations import CitationFormatter, format_bibtex, format_inline, format_quarto
from .config import get_config
from .papers import Paper, PaperDB
from .search import PaperSearch

# Initialize MCP server
mcp = FastMCP("rainer_mcp")

# Lazy-initialized singletons
_paper_db: PaperDB | None = None
_paper_search: PaperSearch | None = None


def get_paper_db() -> PaperDB:
    """Get or create PaperDB instance."""
    global _paper_db
    if _paper_db is None:
        _paper_db = PaperDB()
    return _paper_db


def get_paper_search() -> PaperSearch:
    """Get or create PaperSearch instance."""
    global _paper_search
    if _paper_search is None:
        _paper_search = PaperSearch(paper_db=get_paper_db())
    return _paper_search


# --- Response Formatting ---


class ResponseFormat(str, Enum):
    """Output format for tool responses."""

    MARKDOWN = "markdown"
    JSON = "json"


class CitationStyle(str, Enum):
    """Citation formatting style."""

    BIBTEX = "bibtex"
    INLINE = "inline"
    QUARTO = "quarto"


def paper_to_dict(paper: Paper) -> dict:
    """Convert Paper to dictionary for JSON output."""
    return {
        "doi": paper.doi,
        "title": paper.title,
        "authors": paper.authors,
        "year": paper.year,
        "abstract": paper.abstract,
        "journal": paper.journal_name,
        "doi_url": paper.doi_url,
        "ssrn_url": paper.ssrn_page_url,
        "short_cite": paper.short_cite(),
    }


def format_paper_markdown(paper: Paper, include_abstract: bool = True) -> str:
    """Format a single paper as markdown."""
    lines = [f"**{paper.title}**"]
    if paper.authors:
        lines.append(f"*{paper.authors}*")
    meta = []
    if paper.year:
        meta.append(str(paper.year))
    if paper.journal_name:
        meta.append(paper.journal_name)
    if meta:
        lines.append(" · ".join(meta))
    if paper.doi_url:
        lines.append(f"DOI: {paper.doi_url}")
    if paper.ssrn_page_url and not paper.doi_url:
        lines.append(f"SSRN: {paper.ssrn_page_url}")
    if include_abstract and paper.abstract:
        # Truncate very long abstracts
        abstract = paper.abstract[:800] + "..." if len(paper.abstract) > 800 else paper.abstract
        lines.append(f"\n> {abstract}")
    return "\n".join(lines)


def format_papers_response(
    papers: list[Paper],
    response_format: ResponseFormat,
    include_abstracts: bool = True,
) -> str:
    """Format a list of papers for response."""
    if not papers:
        return "No papers found."

    if response_format == ResponseFormat.JSON:
        return json.dumps([paper_to_dict(p) for p in papers], indent=2)

    # Markdown format
    lines = [f"Found {len(papers)} paper(s):\n"]
    for i, paper in enumerate(papers, 1):
        lines.append(f"### {i}. {format_paper_markdown(paper, include_abstracts)}")
        lines.append("")
    return "\n".join(lines)


# --- Tools ---


@mcp.tool(
    name="search_papers",
    annotations={
        "title": "Search Papers (Keyword/Fulltext)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def search_papers(
    query: str = Field(
        description="Search query - searches titles, authors, and abstracts",
    ),
    limit: int = Field(
        default=10,
        description="Maximum number of results to return (1-50)",
    ),
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for readable text, 'json' for structured data",
    ),
) -> str:
    """Search papers by keyword in titles, authors, and content.

    Uses DuckDB full-text search when available, falls back to LIKE search.
    Good for finding papers when you know specific terms, author names, or topics.
    """
    db = get_paper_db()
    papers = db.fulltext_search(query, limit=limit)
    return format_papers_response(papers, response_format)


@mcp.tool(
    name="semantic_search",
    annotations={
        "title": "Semantic Search (Vector Similarity)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def semantic_search(
    query: str = Field(
        description="Natural language query describing the research topic or concept",
    ),
    limit: int = Field(
        default=10,
        description="Maximum number of results to return (1-50)",
    ),
    min_similarity: float = Field(
        default=0.3,
        description="Minimum similarity threshold (0-1). Higher = more relevant but fewer results",
    ),
    year_min: Optional[int] = Field(
        default=None,
        description="Only include papers from this year or later",
    ),
    year_max: Optional[int] = Field(
        default=None,
        description="Only include papers from this year or earlier",
    ),
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for readable text, 'json' for structured data",
    ),
) -> str:
    """Search papers using semantic similarity to find conceptually related work.

    Uses vector embeddings of paper abstracts to find papers that are semantically
    similar to your query, even if they don't contain the exact keywords.
    Ideal for literature review and finding related research.

    Falls back to keyword search if vector database is not available.
    """
    search = get_paper_search()

    if not search.is_available:
        # Fall back to keyword search with a note
        db = get_paper_db()
        papers = db.fulltext_search(query, limit=limit)
        result = format_papers_response(papers, response_format, include_abstracts=True)
        return f"*Note: Vector search unavailable ({search.init_error}). Using keyword search.*\n\n{result}"

    results = search.search(
        query=query,
        top_k=limit,
        min_similarity=min_similarity,
        year_min=year_min,
        year_max=year_max,
    )

    if not results:
        return "No papers found matching your query."

    if response_format == ResponseFormat.JSON:
        data = [
            {**paper_to_dict(r.paper), "similarity_score": round(r.score, 3)}
            for r in results
        ]
        return json.dumps(data, indent=2)

    # Markdown format with scores
    lines = [f"Found {len(results)} semantically similar paper(s):\n"]
    for i, result in enumerate(results, 1):
        score_pct = f"{result.score * 100:.0f}%"
        lines.append(f"### {i}. [{score_pct} match] {format_paper_markdown(result.paper)}")
        lines.append("")
    return "\n".join(lines)


@mcp.tool(
    name="get_paper",
    annotations={
        "title": "Get Paper by DOI",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def get_paper(
    doi: str = Field(
        description="DOI of the paper to retrieve (e.g., '10.1111/jofi.12345')",
    ),
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for readable text, 'json' for structured data",
    ),
) -> str:
    """Retrieve full details of a specific paper by its DOI."""
    db = get_paper_db()
    paper = db.get_paper(doi)

    if paper is None:
        return f"Paper not found with DOI: {doi}"

    if response_format == ResponseFormat.JSON:
        return json.dumps(paper_to_dict(paper), indent=2)

    return format_paper_markdown(paper, include_abstract=True)


@mcp.tool(
    name="search_by_author",
    annotations={
        "title": "Search Papers by Author",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def search_by_author(
    author: str = Field(
        description="Author name or partial name to search for",
    ),
    limit: int = Field(
        default=20,
        description="Maximum number of results to return (1-100)",
    ),
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for readable text, 'json' for structured data",
    ),
) -> str:
    """Find papers by a specific author.

    Searches author names (partial matches supported). Results sorted by year descending.
    """
    db = get_paper_db()
    papers = db.search_by_author(author, limit=limit)
    return format_papers_response(papers, response_format, include_abstracts=False)


@mcp.tool(
    name="search_by_journal",
    annotations={
        "title": "Search Papers by Journal",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def search_by_journal(
    journal: str = Field(
        description="Journal name or partial name to search for",
    ),
    limit: int = Field(
        default=50,
        description="Maximum number of results to return (1-100)",
    ),
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for readable text, 'json' for structured data",
    ),
) -> str:
    """Find papers published in a specific journal.

    Searches journal names (partial matches supported). Results sorted by year descending.
    """
    db = get_paper_db()
    papers = db.search_by_journal(journal, limit=limit)
    return format_papers_response(papers, response_format, include_abstracts=False)


@mcp.tool(
    name="format_citation",
    annotations={
        "title": "Format Citation",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def format_citation(
    doi: str = Field(
        description="DOI of the paper to cite",
    ),
    style: CitationStyle = Field(
        default=CitationStyle.BIBTEX,
        description="Citation style: 'bibtex' for BibTeX entry, 'inline' for (Author, Year), 'quarto' for @authorYear",
    ),
) -> str:
    """Format a paper citation in the specified style."""
    db = get_paper_db()
    paper = db.get_paper(doi)

    if paper is None:
        return f"Paper not found with DOI: {doi}"

    if style == CitationStyle.BIBTEX:
        return format_bibtex(paper)
    elif style == CitationStyle.QUARTO:
        return format_quarto(paper)
    else:  # inline
        return format_inline(paper)


@mcp.tool(
    name="format_citations",
    annotations={
        "title": "Format Multiple Citations",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def format_citations(
    dois: list[str] = Field(
        description="List of DOIs to cite",
    ),
    style: CitationStyle = Field(
        default=CitationStyle.BIBTEX,
        description="Citation style: 'bibtex' for BibTeX entries, 'inline' for (Author, Year), 'quarto' for @authorYear",
    ),
    include_reference_list: bool = Field(
        default=True,
        description="Include formatted reference list (for inline/quarto styles)",
    ),
) -> str:
    """Format citations for multiple papers.

    Useful for generating a bibliography or reference list for a document.
    """
    db = get_paper_db()
    formatter = CitationFormatter(
        style=style.value,  # type: ignore
        include_reference_list=include_reference_list,
    )

    found_papers = []
    not_found = []

    for doi in dois:
        paper = db.get_paper(doi)
        if paper:
            formatter.cite(paper)
            found_papers.append(paper)
        else:
            not_found.append(doi)

    if not found_papers:
        return f"No papers found. DOIs not in database: {', '.join(not_found)}"

    output_parts = []

    if style == CitationStyle.BIBTEX:
        output_parts.append("## BibTeX Entries\n")
        output_parts.append("```bibtex")
        output_parts.append(formatter.get_bibtex())
        output_parts.append("```")
    else:
        # For inline/quarto, show the in-text citations
        output_parts.append("## In-text Citations\n")
        for paper in found_papers:
            if style == CitationStyle.QUARTO:
                output_parts.append(f"- {paper.title}: `{format_quarto(paper)}`")
            else:
                output_parts.append(f"- {paper.title}: {format_inline(paper)}")

        if include_reference_list:
            output_parts.append("\n" + formatter.get_reference_list())

        if style == CitationStyle.QUARTO:
            output_parts.append("\n## BibTeX (for Quarto)\n")
            output_parts.append("```bibtex")
            output_parts.append(formatter.get_bibtex())
            output_parts.append("```")

    if not_found:
        output_parts.append(f"\n*DOIs not found in database: {', '.join(not_found)}*")

    return "\n".join(output_parts)


@mcp.tool(
    name="get_database_stats",
    annotations={
        "title": "Get Database Statistics",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def get_database_stats() -> str:
    """Get statistics about the literature database.

    Returns counts, year range, and top journals.
    """
    db = get_paper_db()
    stats = db.get_stats()

    search = get_paper_search()
    vector_count = search.count_documents() if search.is_available else 0

    lines = [
        "## Literature Database Statistics\n",
        f"- **Total papers**: {stats['total_papers']:,}",
        f"- **Papers with abstracts**: {stats['papers_with_abstracts']:,}",
        f"- **Papers with embeddings**: {vector_count:,}",
    ]

    if stats["year_range"]:
        lines.append(f"- **Year range**: {stats['year_range'][0]} - {stats['year_range'][1]}")

    if stats["top_journals"]:
        lines.append("\n### Top Journals by Paper Count\n")
        for journal, count in stats["top_journals"]:
            lines.append(f"- {journal}: {count:,} papers")

    if not search.is_available:
        lines.append(f"\n*Vector search status: {search.init_error}*")

    return "\n".join(lines)


def main():
    """Run the MCP server."""
    import sys

    # Check if database exists
    config = get_config()
    from pathlib import Path

    db_path = Path(config.data.duckdb_path).expanduser()
    if not db_path.exists():
        print(f"Error: Database not found at {db_path}", file=sys.stderr)
        print("Please ensure the DuckDB database path is configured correctly.", file=sys.stderr)
        sys.exit(1)

    # Run with stdio transport (default for local MCP servers)
    mcp.run()


if __name__ == "__main__":
    main()
