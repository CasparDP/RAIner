"""PDF and document parsing via docling (optional dependency)."""

import re
from pathlib import Path
from typing import NamedTuple


class ParsedDocument(NamedTuple):
    """Result of parsing a document."""

    content: str | None
    method: str  # 'docling', 'text', or error description
    metadata: dict | None  # Extracted metadata (title, authors, etc.)


# Lazy import to make docling optional
_docling_available: bool | None = None


def is_docling_available() -> bool:
    """Check if docling is installed."""
    global _docling_available
    if _docling_available is None:
        try:
            from docling.document_converter import DocumentConverter

            _docling_available = True
        except ImportError:
            _docling_available = False
    return _docling_available


def _extract_metadata_from_markdown(content: str) -> dict:
    """
    Extract basic metadata from parsed markdown content.

    Attempts to find title (first heading) and other info.
    """
    metadata = {}

    # Try to find first heading as title
    title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    if title_match:
        metadata["title"] = title_match.group(1).strip()

    # Try to find abstract
    abstract_match = re.search(
        r"(?:^|\n)(?:Abstract|ABSTRACT)[:\s]*\n(.+?)(?=\n\n|\n#|\Z)",
        content,
        re.IGNORECASE | re.DOTALL,
    )
    if abstract_match:
        metadata["abstract"] = abstract_match.group(1).strip()[:1000]

    return metadata


def _extract_metadata_from_docling(result) -> dict:
    """Extract metadata from docling conversion result."""
    metadata = {}

    # Try to get metadata from docling's document object
    doc = result.document
    if hasattr(doc, "metadata"):
        if hasattr(doc.metadata, "title") and doc.metadata.title:
            metadata["title"] = doc.metadata.title
        if hasattr(doc.metadata, "authors") and doc.metadata.authors:
            metadata["authors"] = doc.metadata.authors

    return metadata


def parse_pdf(filepath: Path) -> tuple[str | None, dict]:
    """
    Parse a PDF file and return extracted text as markdown.

    Returns: (content, metadata) or (None, {}) if parsing fails.
    """
    if not is_docling_available():
        return None, {}

    try:
        from docling.document_converter import DocumentConverter

        converter = DocumentConverter()
        result = converter.convert(str(filepath))
        content = result.document.export_to_markdown()

        # Extract metadata
        metadata = _extract_metadata_from_docling(result)
        if not metadata.get("title"):
            # Fall back to extracting from content
            metadata.update(_extract_metadata_from_markdown(content))

        return content, metadata

    except Exception as e:
        error_msg = str(e)
        # Check for common issues
        if "rt_detr_v2" in error_msg.lower() or "does not recognize this architecture" in error_msg:
            raise RuntimeError(
                "Docling requires transformers>=4.55.0 for RT-DETRv2 support. "
                "Run: pip install 'transformers>=4.55.0' or poetry update"
            ) from e
        raise


def parse_document(filepath: Path) -> ParsedDocument:
    """
    Parse any supported document format.

    Returns ParsedDocument with content, method, and metadata.
    """
    suffix = filepath.suffix.lower()

    # PDF requires docling
    if suffix == ".pdf":
        if not is_docling_available():
            return ParsedDocument(None, "docling_not_installed", None)
        try:
            content, metadata = parse_pdf(filepath)
            metadata["source_file"] = str(filepath)
            metadata["format"] = "pdf"
            return ParsedDocument(content, "docling", metadata)
        except Exception as e:
            return ParsedDocument(None, f"error: {e}", None)

    # Text-based formats - read directly
    if suffix in (".txt", ".md", ".markdown", ".tex"):
        try:
            content = filepath.read_text(encoding="utf-8")
            metadata = {
                "source_file": str(filepath),
                "format": suffix[1:],  # Remove the dot
            }
            # Try to extract metadata from markdown
            if suffix in (".md", ".markdown"):
                metadata.update(_extract_metadata_from_markdown(content))
            return ParsedDocument(content, "text", metadata)
        except Exception as e:
            return ParsedDocument(None, f"error: {e}", None)

    # Try docling for other rich formats (docx, pptx, etc.)
    if suffix in (".docx", ".pptx", ".xlsx", ".html", ".htm"):
        if not is_docling_available():
            return ParsedDocument(None, "docling_not_installed", None)
        try:
            from docling.document_converter import DocumentConverter

            converter = DocumentConverter()
            result = converter.convert(str(filepath))
            content = result.document.export_to_markdown()
            metadata = _extract_metadata_from_docling(result)
            metadata["source_file"] = str(filepath)
            metadata["format"] = suffix[1:]
            if not metadata.get("title"):
                metadata.update(_extract_metadata_from_markdown(content))
            return ParsedDocument(content, "docling", metadata)
        except Exception as e:
            return ParsedDocument(None, f"error: {e}", None)

    return ParsedDocument(None, "unsupported_format", None)


def extract_paper_info(content: str, metadata: dict | None = None) -> dict:
    """
    Extract academic paper information from parsed content.

    Used when loading reference papers in writing mode.
    Returns a dict with title, authors, abstract, and key sections.
    """
    info = metadata.copy() if metadata else {}

    # If no title from metadata, try to extract from content
    if not info.get("title"):
        # First heading or first line
        title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        if title_match:
            info["title"] = title_match.group(1).strip()
        else:
            # Use first non-empty line
            for line in content.split("\n"):
                line = line.strip()
                if line and not line.startswith("#"):
                    info["title"] = line[:200]
                    break

    # Extract abstract if not already found
    if not info.get("abstract"):
        abstract_match = re.search(
            r"(?:^|\n)(?:Abstract|ABSTRACT)[:\s]*\n(.+?)(?=\n\n|\n#|\n[A-Z][a-z]+:|\Z)",
            content,
            re.IGNORECASE | re.DOTALL,
        )
        if abstract_match:
            info["abstract"] = abstract_match.group(1).strip()[:2000]

    # Try to find author information
    if not info.get("authors"):
        # Look for common author patterns after title
        author_match = re.search(
            r"^(.+?)(?:\n\n|Abstract|ABSTRACT|Introduction|INTRODUCTION)",
            content[:3000],
            re.DOTALL,
        )
        if author_match:
            # Look for lines that might be author names (before abstract)
            potential_authors = author_match.group(1)
            # Simple heuristic: lines with names often have commas or "and"
            for line in potential_authors.split("\n")[1:5]:  # Skip title
                line = line.strip()
                if line and ("," in line or " and " in line.lower()):
                    if not any(
                        kw in line.lower()
                        for kw in ["abstract", "introduction", "university", "department"]
                    ):
                        info["authors"] = line
                        break

    # Store content length for reference
    info["content_length"] = len(content)
    info["word_count"] = len(content.split())

    return info
