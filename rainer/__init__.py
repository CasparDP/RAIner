"""RAiner - An open-source research assistant for academic work."""

import logging
import os
import warnings

# Disable ChromaDB/PostHog telemetry before any chromadb imports
os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["CHROMA_TELEMETRY"] = "False"
os.environ["POSTHOG_DISABLED"] = "True"

# Use cached HuggingFace models without checking for updates (avoids timeouts)
os.environ["HF_HUB_OFFLINE"] = "1"

# Suppress the ChromaDB telemetry warning that leaks through despite being disabled
warnings.filterwarnings("ignore", message=".*telemetry.*")
warnings.filterwarnings("ignore", message=".*capture.*takes.*positional argument.*")

# Suppress chromadb telemetry logging
logging.getLogger("chromadb.telemetry").setLevel(logging.CRITICAL)
logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)

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
