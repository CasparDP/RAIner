"""Text chunking utilities for large documents."""

import re
from typing import Iterator

from .config import get_config


def estimate_tokens(text: str) -> int:
    """
    Rough token estimate.
    
    Rule of thumb: ~4 characters per token for English.
    """
    return len(text) // 4


def chunk_text(
    text: str,
    max_tokens: int | None = None,
    overlap_tokens: int | None = None,
    preserve_paragraphs: bool = True,
) -> list[str]:
    """
    Split text into chunks suitable for processing.

    Args:
        text: The text to chunk
        max_tokens: Maximum tokens per chunk (default from config)
        overlap_tokens: Overlap between chunks (default from config)
        preserve_paragraphs: Try to split at paragraph boundaries

    Returns:
        List of text chunks
    """
    config = get_config()
    if max_tokens is None:
        max_tokens = config.chunking.max_chunk_tokens
    if overlap_tokens is None:
        overlap_tokens = config.chunking.overlap_tokens

    # Convert to rough character limits
    max_chars = max_tokens * 4
    overlap_chars = overlap_tokens * 4

    # If text is short enough, return as-is
    if estimate_tokens(text) <= max_tokens:
        return [text]

    chunks = []

    if preserve_paragraphs:
        # Split by paragraphs first
        paragraphs = re.split(r"\n\s*\n", text)
        current_chunk = []
        current_length = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            para_length = len(para)

            # If single paragraph exceeds max, split it further
            if para_length > max_chars:
                # Flush current chunk
                if current_chunk:
                    chunks.append("\n\n".join(current_chunk))
                    current_chunk = []
                    current_length = 0

                # Split long paragraph by sentences
                for sent_chunk in _chunk_by_sentences(para, max_chars, overlap_chars):
                    chunks.append(sent_chunk)
                continue

            # Check if adding this paragraph exceeds limit
            if current_length + para_length + 2 > max_chars:
                # Save current chunk
                chunks.append("\n\n".join(current_chunk))

                # Start new chunk with overlap
                if overlap_chars > 0 and current_chunk:
                    # Include last paragraph(s) as overlap
                    overlap_text = current_chunk[-1] if current_chunk else ""
                    current_chunk = [overlap_text, para] if overlap_text else [para]
                    current_length = len("\n\n".join(current_chunk))
                else:
                    current_chunk = [para]
                    current_length = para_length
            else:
                current_chunk.append(para)
                current_length += para_length + 2

        # Don't forget the last chunk
        if current_chunk:
            chunks.append("\n\n".join(current_chunk))
    else:
        # Simple character-based chunking
        chunks = _chunk_by_chars(text, max_chars, overlap_chars)

    return chunks


def _chunk_by_sentences(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    """Split text by sentences when paragraphs are too long."""
    # Simple sentence splitting (could be improved with NLP)
    sentences = re.split(r"(?<=[.!?])\s+", text)

    chunks = []
    current_chunk = []
    current_length = 0

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue

        sent_length = len(sent)

        if current_length + sent_length + 1 > max_chars:
            if current_chunk:
                chunks.append(" ".join(current_chunk))
            current_chunk = [sent]
            current_length = sent_length
        else:
            current_chunk.append(sent)
            current_length += sent_length + 1

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks


def _chunk_by_chars(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    """Simple character-based chunking with overlap."""
    chunks = []
    start = 0

    while start < len(text):
        end = start + max_chars

        # Try to break at a space
        if end < len(text):
            space_idx = text.rfind(" ", start, end)
            if space_idx > start:
                end = space_idx

        chunks.append(text[start:end].strip())

        # Move start back by overlap
        start = end - overlap_chars

    return chunks


def iter_chunks(
    text: str,
    max_tokens: int | None = None,
    overlap_tokens: int | None = None,
) -> Iterator[tuple[int, int, str]]:
    """
    Iterate over chunks with index information.

    Yields: (chunk_index, total_chunks, chunk_text)
    """
    chunks = chunk_text(text, max_tokens, overlap_tokens)
    total = len(chunks)
    for i, chunk in enumerate(chunks):
        yield i, total, chunk


def extract_key_sections(text: str) -> dict[str, str]:
    """
    Extract key sections from a document (e.g., Introduction, Methods, etc.)

    Returns dict mapping section name to content.
    """
    # Common section headers in academic papers
    section_patterns = [
        r"(?i)^#+\s*(?:[\d.]+\s+)?(abstract|summary)",
        r"(?i)^#+\s*(?:[\d.]+\s+)?(introduction|background)",
        r"(?i)^#+\s*(?:[\d.]+\s+)?(literature\s+review|related\s+work)",
        r"(?i)^#+\s*(?:[\d.]+\s+)?(method|methodology|data|sample)",
        r"(?i)^#+\s*(?:[\d.]+\s+)?(result|finding|analysis)",
        r"(?i)^#+\s*(?:[\d.]+\s+)?(discussion)",
        r"(?i)^#+\s*(?:[\d.]+\s+)?(conclusion|concluding)",
        r"(?i)^#+\s*(?:[\d.]+\s+)?(reference|bibliography)",
    ]

    sections: dict[str, str] = {}
    current_section = "preamble"
    current_content: list[str] = []

    for line in text.split("\n"):
        # Check if this line is a section header
        is_header = False
        for pattern in section_patterns:
            match = re.match(pattern, line)
            if match:
                # Save previous section
                if current_content:
                    sections[current_section] = "\n".join(current_content).strip()

                # Start new section
                current_section = match.group(1).lower().strip()
                current_content = []
                is_header = True
                break

        if not is_header:
            current_content.append(line)

    # Save last section
    if current_content:
        sections[current_section] = "\n".join(current_content).strip()

    return sections
