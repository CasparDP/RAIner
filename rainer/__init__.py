"""RAiner - An open-source research assistant for academic work."""

import os

# Disable ChromaDB telemetry before any chromadb imports
os.environ["ANONYMIZED_TELEMETRY"] = "False"

__version__ = "0.1.0"

from .agent import ResearchAgent
from .citations import CitationFormatter
from .config import Config, get_config, load_config
from .memory import ConversationMemory, Session, SessionManager
from .papers import Paper, PaperDB
from .search import PaperSearch

__all__ = [
    "ResearchAgent",
    "Config",
    "get_config",
    "load_config",
    "ConversationMemory",
    "Session",
    "SessionManager",
    "Paper",
    "PaperDB",
    "PaperSearch",
    "CitationFormatter",
]
