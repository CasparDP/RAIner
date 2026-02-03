"""Citation formatting in various styles."""

import re
from typing import Literal

from .papers import Paper


def format_bibtex(paper: Paper) -> str:
    """Format paper as BibTeX entry."""
    # Generate citation key: AuthorYear
    key = f"{paper.first_author_surname.lower()}{paper.year or 'nd'}"
    # Remove special characters from key
    key = re.sub(r"[^a-z0-9]", "", key)

    # Determine entry type
    entry_type = "article"  # Could be extended based on metadata

    lines = [f"@{entry_type}{{{key},"]
    lines.append(f'  title = {{{paper.title}}},')

    if paper.authors:
        lines.append(f'  author = {{{paper.authors}}},')

    if paper.year:
        lines.append(f"  year = {{{paper.year}}},")

    if paper.doi:
        lines.append(f'  doi = {{{paper.doi}}},')

    if paper.ssrn_id:
        lines.append(f'  note = {{SSRN: {paper.ssrn_id}}},')
        lines.append(f'  url = {{{paper.ssrn_url}}},')

    if paper.journal_name:
        lines.append(f'  journal = {{{paper.journal_name}}},')

    lines.append("}")

    return "\n".join(lines)


def format_inline(paper: Paper, include_title: bool = False) -> str:
    """
    Format paper as inline citation with parenthetical reference.

    Example: (Smith, 2020) or (Smith, 2020, "Paper Title")
    """
    parts = [paper.first_author_surname]

    if paper.year:
        parts.append(str(paper.year))

    if include_title:
        short_title = paper.title[:50] + "..." if len(paper.title) > 50 else paper.title
        parts.append(f'"{short_title}"')

    return "(" + ", ".join(parts) + ")"


def format_quarto(paper: Paper) -> str:
    """
    Format paper as Quarto/Pandoc citation.

    Example: @smith2020
    """
    key = f"{paper.first_author_surname.lower()}{paper.year or 'nd'}"
    key = re.sub(r"[^a-z0-9]", "", key)
    return f"@{key}"


def format_reference_list_entry(paper: Paper, style: str = "apa") -> str:
    """
    Format paper for a reference list.

    APA-ish style:
    Smith, J., & Jones, M. (2020). Paper Title. DOI: 10.1234/example
    """
    parts = []

    # Authors
    if paper.authors:
        parts.append(paper.authors)

    # Year
    year_str = f"({paper.year})" if paper.year else "(n.d.)"
    parts.append(year_str)

    # Title
    parts.append(f"{paper.title}.")

    # DOI or SSRN
    if paper.doi:
        parts.append(f"https://doi.org/{paper.doi}")
    elif paper.ssrn_url:
        parts.append(paper.ssrn_url)

    return " ".join(parts)


class CitationFormatter:
    """Format citations for a collection of papers."""

    def __init__(
        self,
        style: Literal["inline", "quarto", "bibtex"] = "inline",
        include_reference_list: bool = True,
    ):
        self.style = style
        self.include_reference_list = include_reference_list
        self._cited_papers: dict[str, Paper] = {}

    def cite(self, paper: Paper) -> str:
        """Cite a paper and track it for reference list."""
        self._cited_papers[paper.id] = paper

        if self.style == "bibtex":
            return format_quarto(paper)  # In-text still uses @key
        elif self.style == "quarto":
            return format_quarto(paper)
        else:  # inline
            return format_inline(paper)

    def cite_multiple(self, papers: list[Paper]) -> str:
        """Cite multiple papers together."""
        if self.style == "bibtex" or self.style == "quarto":
            citations = [self.cite(p) for p in papers]
            return "[" + "; ".join(citations) + "]"
        else:
            for p in papers:
                self._cited_papers[p.id] = p
            authors_years = [
                f"{p.first_author_surname}, {p.year or 'n.d.'}" for p in papers
            ]
            return "(" + "; ".join(authors_years) + ")"

    def get_reference_list(self) -> str:
        """Generate the reference list for all cited papers."""
        if not self._cited_papers:
            return ""

        # Sort by first author surname, then year
        sorted_papers = sorted(
            self._cited_papers.values(),
            key=lambda p: (p.first_author_surname.lower(), p.year or 0),
        )

        lines = ["## References", ""]
        for paper in sorted_papers:
            lines.append(f"- {format_reference_list_entry(paper)}")

        return "\n".join(lines)

    def get_bibtex(self) -> str:
        """Generate BibTeX entries for all cited papers."""
        if not self._cited_papers:
            return ""

        entries = []
        for paper in self._cited_papers.values():
            entries.append(format_bibtex(paper))

        return "\n\n".join(entries)

    def get_full_output(self) -> str:
        """Get citations summary with reference list and/or BibTeX."""
        parts = []

        if self.include_reference_list and self.style != "bibtex":
            parts.append(self.get_reference_list())

        if self.style in ("bibtex", "quarto"):
            parts.append("\n## BibTeX Entries\n")
            parts.append("```bibtex")
            parts.append(self.get_bibtex())
            parts.append("```")

        return "\n".join(parts)

    def reset(self) -> None:
        """Clear tracked citations."""
        self._cited_papers.clear()

    @property
    def cited_count(self) -> int:
        """Number of unique papers cited."""
        return len(self._cited_papers)
