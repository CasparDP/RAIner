"""Output formatting and file writing utilities."""

from datetime import datetime
from pathlib import Path

from .config import get_config
from .citations import CitationFormatter


class MarkdownWriter:
    """Write structured markdown output files."""

    def __init__(
        self,
        output_dir: str | Path | None = None,
        citation_formatter: CitationFormatter | None = None,
    ):
        if output_dir is None:
            output_dir = get_config().output.directory
        self.output_dir = Path(output_dir).expanduser()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.citation_formatter = citation_formatter
        self._sections: list[tuple[str, str]] = []  # (heading, content)
        self._title: str = "Untitled"
        self._metadata: dict[str, str] = {}

    def set_title(self, title: str) -> "MarkdownWriter":
        """Set document title."""
        self._title = title
        return self

    def add_metadata(self, key: str, value: str) -> "MarkdownWriter":
        """Add YAML frontmatter metadata."""
        self._metadata[key] = value
        return self

    def add_section(self, heading: str, content: str) -> "MarkdownWriter":
        """Add a section with heading."""
        self._sections.append((heading, content))
        return self

    def add_text(self, content: str) -> "MarkdownWriter":
        """Add text without heading."""
        self._sections.append(("", content))
        return self

    def build(self) -> str:
        """Build the complete markdown document."""
        lines = []

        # YAML frontmatter
        if self._metadata:
            lines.append("---")
            for key, value in self._metadata.items():
                # Handle multiline values
                if "\n" in value:
                    lines.append(f"{key}: |")
                    for line in value.split("\n"):
                        lines.append(f"  {line}")
                else:
                    lines.append(f"{key}: {value}")
            lines.append("---")
            lines.append("")

        # Title
        lines.append(f"# {self._title}")
        lines.append("")

        # Sections
        for heading, content in self._sections:
            if heading:
                lines.append(f"## {heading}")
                lines.append("")
            lines.append(content)
            lines.append("")

        # Citations / References
        if self.citation_formatter and self.citation_formatter.cited_count > 0:
            lines.append(self.citation_formatter.get_full_output())

        return "\n".join(lines)

    def write(self, filename: str | None = None) -> Path:
        """Write document to file."""
        if filename is None:
            # Generate filename from title and timestamp
            safe_title = "".join(c if c.isalnum() or c in " -_" else "" for c in self._title)
            safe_title = safe_title.replace(" ", "_")[:50]
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{safe_title}_{timestamp}.md"

        filepath = self.output_dir / filename
        content = self.build()
        filepath.write_text(content)
        return filepath

    def reset(self) -> None:
        """Clear all content."""
        self._sections.clear()
        self._title = "Untitled"
        self._metadata.clear()
        if self.citation_formatter:
            self.citation_formatter.reset()


def create_feedback_report(
    draft_name: str,
    suggestions: list[dict],
    citation_formatter: CitationFormatter,
    output_dir: str | Path | None = None,
) -> Path:
    """
    Create a formatted feedback report.

    Args:
        draft_name: Name of the draft being reviewed
        suggestions: List of suggestion dicts with keys:
            - location: Where in the draft
            - claim: The claim being made
            - suggestion: The feedback
            - papers: List of Paper objects to cite
        citation_formatter: Formatter for citations
        output_dir: Where to save
    """
    writer = MarkdownWriter(output_dir, citation_formatter)

    writer.set_title(f"Feedback on: {draft_name}")
    writer.add_metadata("date", datetime.now().strftime("%Y-%m-%d"))
    writer.add_metadata("type", "feedback")

    # Summary
    writer.add_section(
        "Summary",
        f"Found {len(suggestions)} suggestions for citation improvements.",
    )

    # Detailed suggestions
    suggestion_lines = []
    for i, sugg in enumerate(suggestions, 1):
        suggestion_lines.append(f"### Suggestion {i}")
        suggestion_lines.append("")
        if sugg.get("location"):
            suggestion_lines.append(f"**Location:** {sugg['location']}")
        if sugg.get("claim"):
            suggestion_lines.append(f"**Claim:** {sugg['claim']}")
        suggestion_lines.append("")
        suggestion_lines.append(sugg.get("suggestion", ""))
        suggestion_lines.append("")

        # Add citations
        if sugg.get("papers"):
            suggestion_lines.append("**Relevant papers:**")
            for paper in sugg["papers"]:
                cite = citation_formatter.cite(paper)
                suggestion_lines.append(f"- {cite}: {paper.title}")
            suggestion_lines.append("")

    writer.add_section("Detailed Suggestions", "\n".join(suggestion_lines))

    return writer.write()


def create_literature_review(
    topic: str,
    sections: dict[str, str],
    citation_formatter: CitationFormatter,
    output_dir: str | Path | None = None,
) -> Path:
    """
    Create a formatted literature review.

    Args:
        topic: The topic of the review
        sections: Dict mapping section names to content
        citation_formatter: Formatter for citations
        output_dir: Where to save
    """
    writer = MarkdownWriter(output_dir, citation_formatter)

    writer.set_title(f"Literature Review: {topic}")
    writer.add_metadata("date", datetime.now().strftime("%Y-%m-%d"))
    writer.add_metadata("type", "literature_review")

    for section_name, content in sections.items():
        writer.add_section(section_name, content)

    return writer.write()
