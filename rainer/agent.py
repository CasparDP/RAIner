"""Core research assistant agent with tool calling."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Literal

import anthropic as anthropic_sdk
import ollama
import openai as openai_sdk
from google import genai
from pydantic import BaseModel

from .backends import create_paper_db, create_paper_search
from .chunking import chunk_text, estimate_tokens, extract_key_sections
from .citations import CitationFormatter
from .config import Config, get_config
from .memory import ConversationMemory
from .papers import PaperDB
from .prompts import resolve_prompt
from .providers import create_provider_adapter
from .search import PaperSearch


class ToolResult(BaseModel):
    """Result from a tool execution."""

    name: str
    result: Any
    success: bool = True
    error: str | None = None


class ResearchAgent:
    """
    The core research assistant agent.

    Handles conversation, tool calling, and coordination.
    """

    # Tool definitions for the LLM
    TOOLS = [
        {
            "type": "function",
            "function": {
                "name": "search_papers",
                "description": (
                    "Search the academic paper database for papers relevant to a query. "
                    "Use this to find papers that support or relate to specific claims, concepts, or topics."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural language search query describing what you're looking for",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of results to return (default: 10)",
                            "default": 10,
                        },
                        "year_min": {
                            "type": "integer",
                            "description": "Minimum publication year filter (optional)",
                        },
                        "year_max": {
                            "type": "integer",
                            "description": "Maximum publication year filter (optional)",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_paper_details",
                "description": (
                    "Get full details about a specific paper by its ID. "
                    "Use this after searching to get complete information."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "paper_id": {
                            "type": "string",
                            "description": "The paper ID",
                        },
                    },
                    "required": ["paper_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "format_citation",
                "description": "Format a paper citation in the appropriate style for the current mode.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "paper_id": {
                            "type": "string",
                            "description": "The paper ID to cite",
                        },
                    },
                    "required": ["paper_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "refresh_database_index",
                "description": (
                    "Fetch and cache the institution's library A–Z database list. Use this "
                    "before making any claims about database availability at the institution. "
                    "Returns cache metadata and a short summary."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "force": {
                            "type": "boolean",
                            "description": "Force refresh even if cache is recent (default: false)",
                            "default": False,
                        },
                        "max_age_hours": {
                            "type": "integer",
                            "description": (
                                "If cache is newer than this, do not refetch unless force=true "
                                "(default: 24)"
                            ),
                            "default": 24,
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_databases",
                "description": (
                    "Search the cached library A–Z database index by keyword(s) and return "
                    "matching database titles and URLs. Use this to verify whether a named "
                    "database/dataset is available via the institution's subscriptions."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Keyword query, e.g., 'WRDS', 'Compustat', 'Bloomberg', "
                                "'Orbis', 'FactSet', 'Datastream'"
                            ),
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Maximum number of matches to return (default: 10)",
                            "default": 10,
                        },
                    },
                    "required": ["query"],
                },
            },
        },
    ]

    # Anti-hallucination rules included in all prompts
    CITATION_RULES = """
CRITICAL CITATION RULES - YOU MUST FOLLOW THESE:
1. ONLY cite papers that were returned by the search_papers tool in THIS conversation
2. NEVER invent, fabricate, or guess paper titles, authors, DOIs, or years
3. NEVER cite papers from your training data - only use papers from search results
4. If you haven't searched yet, you CANNOT cite any papers - search first
5. When uncertain if a paper exists, use search_papers to verify before citing
6. Do NOT conclude that a cited paper is absent after one failed search
7. Verification sequence for cited papers: search the exact title in quotation marks if available; if that fails, search distinctive title keywords plus one author surname; if that fails, retry without relying on the year because online-first, working-paper, and print years may differ
8. Treat the year as a soft hint, not a hard filter, and be robust to initials, accents, capitalization, and author-order differences
9. Use ONLY the exact titles, authors, and years returned by the tools
10. If search returns no relevant results after targeted retries, say so honestly - do not make up citations

WORKFLOW: Search first → Get details if needed → Then cite. Never skip the search step.
"""

    # Institution database list loaded from JSON (the feasibility-audit source).
    # Default is the bundled file; override via config.data.databases_path.
    _DB_JSON_PATH = Path(__file__).parent / "data" / "databases.json"

    # {institution} is filled at runtime from config.institution.name in _build_prompt.
    DATA_SOURCE_RULES_TEMPLATE = """
CRITICAL DATA & DATABASE VERIFICATION RULES - YOU MUST FOLLOW THESE:
1. NEVER claim a dataset/database is available at {institution} unless verified via search_databases results in THIS conversation
2. ALWAYS run refresh_database_index at the start of feedback mode (mandatory), then verify relevant names with search_databases
3. If you cannot verify availability, label it UNVERIFIED and propose feasible alternatives (public sources like SEC EDGAR, or other verified {institution} databases)
4. NEVER invent licensing/access details, variable coverage, fields, sample coverage, or download options
5. Clearly label statements as VERIFIED / UNVERIFIED / ASSUMED when discussing data access or content
6. If feasibility depends on unknown student choices (country/timeframe/unit), ask focused clarifying questions and provide fallback designs
"""

    def __init__(
        self,
        mode: Literal[
            "feedback",
            "feedback_hyp_rd",
            "feedback_results",
            "feedback_final",
            "grading",
            "writing",
            "review",
            "search",
            "exam-review",
        ] = "feedback",
        memory: ConversationMemory | None = None,
        config: Config | None = None,
        paper_db: PaperDB | None = None,
        paper_search: PaperSearch | None = None,
    ):
        # Injectable config (defaults to the global singleton). Passing a per-job
        # config avoids mutating global state — required for concurrent/server use.
        self.config = config or get_config()
        self.mode = mode
        self.memory = memory or ConversationMemory.new(mode=mode)

        # Initialize tools. The backend (DuckDB+Chroma vs Postgres+pgvector) is
        # selected from config; explicit paper_db/paper_search override it.
        self.paper_db = paper_db or create_paper_db(self.config)
        self.paper_search = paper_search or create_paper_search(self.config, self.paper_db)

        # Citation formatter based on mode
        # Hyphenated mode names (e.g. "exam-review") map to underscore attrs ("exam_review")
        _mode_attr = mode.replace("-", "_")
        mode_config = getattr(self.config.modes, _mode_attr, self.config.modes.search)
        self.citation_formatter = CitationFormatter(
            style=mode_config.citation_style,
            include_reference_list=(mode_config.citation_style != "bibtex"),
        )

        # Registry of papers seen in this session (for hallucination prevention)
        self.seen_papers: dict[str, dict] = {}

        # Initialize LLM client and adapter
        self._init_llm()

    def _load_databases(self) -> dict:
        """Load the institution's database-access list from JSON (cached per instance).

        Path comes from config.data.databases_path, falling back to the bundled file.
        """
        if getattr(self, "_db_cache", None) is not None:
            return self._db_cache

        path = self.config.data.databases_path
        path = Path(path).expanduser() if path else self._DB_JSON_PATH
        try:
            with open(path, encoding="utf-8") as f:
                self._db_cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            self._db_cache = {
                "databases": [
                    {"name": "WRDS", "aliases": ["wrds", "compustat", "crsp"], "status": "active"},
                    {
                        "name": "LSEG Workspace",
                        "aliases": ["lseg", "eikon", "datastream"],
                        "status": "active",
                    },
                    {"name": "Orbis", "aliases": ["orbis", "bvd"], "status": "active"},
                ],
                "not_available": [],
                "_load_error": str(e),
            }
        return self._db_cache

    def _init_llm(self) -> None:
        """Initialize the LLM client based on config."""
        self.provider_type = self.config.provider.name
        self.model = self.config.provider.model

        if self.provider_type == "ollama":
            host = self.config.provider.ollama.base_url
            self.client = ollama.Client(host=host)

        elif self.provider_type == "ollama-cloud":
            host = self.config.provider.ollama_cloud.base_url
            api_key = self.config.provider.ollama_cloud.api_key
            if not api_key:
                raise ValueError("Ollama Cloud API key not set. Set OLLAMA_API_KEY in .env")
            headers = {"Authorization": f"Bearer {api_key}"}
            self.client = ollama.Client(host=host, headers=headers)

        elif self.provider_type == "openai":
            api_key = self.config.provider.openai.api_key
            if not api_key:
                raise ValueError("OpenAI API key not set. Set OPENAI_API_KEY in .env")
            self.client = openai_sdk.OpenAI(api_key=api_key)

        elif self.provider_type == "openrouter":
            api_key = self.config.provider.openrouter.api_key
            if not api_key:
                raise ValueError("OpenRouter API key not set. Set OPENROUTER_API_KEY in .env")
            self.client = openai_sdk.OpenAI(
                api_key=api_key,
                base_url=self.config.provider.openrouter.base_url,
            )

        elif self.provider_type == "azure-openai":
            az = self.config.provider.azure_openai
            if not az.api_key:
                raise ValueError("Azure OpenAI API key not set. Set AZURE_OPENAI_API_KEY in .env")
            if not az.endpoint:
                raise ValueError("Azure OpenAI endpoint not set. Set AZURE_OPENAI_ENDPOINT in .env")
            self.client = openai_sdk.AzureOpenAI(
                api_key=az.api_key,
                azure_endpoint=az.endpoint,
                api_version=az.api_version,
            )
            # Azure addresses the model by deployment name.
            if az.deployment:
                self.model = az.deployment

        elif self.provider_type == "anthropic":
            api_key = self.config.provider.anthropic.api_key
            if not api_key:
                raise ValueError("Anthropic API key not set. Set ANTHROPIC_API_KEY in .env")
            self.client = anthropic_sdk.Anthropic(api_key=api_key)

        elif self.provider_type == "google":
            api_key = self.config.provider.google.api_key
            if not api_key:
                raise ValueError("Google API key not set. Set GOOGLE_API_KEY in .env")
            self.client = genai.Client(api_key=api_key)

        else:
            raise ValueError(f"Unknown provider: {self.provider_type}")

        google_wrappers = self._get_google_tools() if self.provider_type == "google" else None
        self.adapter = create_provider_adapter(
            provider=self.provider_type,
            model=self.model,
            client=self.client,
            google_tool_wrappers=google_wrappers,
            temperature=self.config.provider.temperature,
        )

    def switch_provider(self, provider: str, model: str | None = None) -> None:
        """Switch to a different provider/model at runtime."""
        self.config.provider.name = provider  # type: ignore
        if model:
            self.config.provider.model = model
        self._init_llm()

    def clear_seen_papers(self) -> None:
        """Clear the seen papers registry. Call when starting a new session."""
        self.seen_papers.clear()

    def _execute_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute a tool and return the result."""
        try:
            if name == "refresh_database_index":
                force = bool(arguments.get("force", False))
                max_age_hours = int(arguments.get("max_age_hours", 24))
                cache_key = "_eur_db_index_cache_v1"
                now_ts = time.time()

                if not hasattr(self, cache_key):
                    setattr(self, cache_key, {"fetched_at": None, "items": [], "source_url": None})

                cache = getattr(self, cache_key)
                fetched_at = cache.get("fetched_at")
                age_ok = fetched_at is not None and (now_ts - float(fetched_at)) <= (
                    max_age_hours * 3600
                )

                if age_ok and not force:
                    return ToolResult(
                        name=name,
                        result={
                            "refreshed": False,
                            "fetched_at": cache.get("fetched_at"),
                            "count": len(cache.get("items", [])),
                            "source_url": cache.get("source_url"),
                            "note": "Using cached database index (recent enough).",
                        },
                    )

                items: list[dict[str, str]] = []
                source_url = self.config.institution.library_url

                html: str | None = None
                fetch_error: str | None = None
                try:
                    import urllib.request

                    req = urllib.request.Request(
                        source_url,
                        headers={"User-Agent": "RAiner/1.0 (+local academic assistant)"},
                    )
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        html = resp.read().decode("utf-8", errors="replace")
                except Exception as e:
                    fetch_error = str(e)

                if html:
                    for m in re.finditer(
                        r'<a[^>]+href="([^"]+)"[^>]*>([^<]{2,200})</a>',
                        html,
                        flags=re.IGNORECASE,
                    ):
                        href = m.group(1).strip()
                        title = re.sub(r"\s+", " ", m.group(2).strip())
                        if not title:
                            continue
                        if title.lower() in (
                            "login",
                            "report a problem.",
                            "ask a question",
                            "search e-journals",
                        ):
                            continue
                        if href.startswith("http"):
                            items.append({"title": title, "url": href})
                    seen = set()
                    deduped = []
                    for it in items:
                        key = (it["title"], it["url"])
                        if key in seen:
                            continue
                        seen.add(key)
                        deduped.append(it)
                    items = deduped

                cache.update(
                    {
                        "fetched_at": now_ts,
                        "items": items,
                        "source_url": source_url,
                    }
                )

                eur_data = self._load_databases()
                known_count = len(eur_data.get("databases", []))

                if fetch_error and not items:
                    return ToolResult(
                        name=name,
                        result={
                            "refreshed": True,
                            "fetched_at": now_ts,
                            "scraped_count": 0,
                            "known_databases_count": known_count,
                            "source_url": source_url,
                            "warning": (
                                "Could not fetch the live database list (network error or "
                                "JS-rendered page). However, search_databases can still match "
                                "against the curated JSON list."
                            ),
                            "error": fetch_error,
                        },
                        success=True,
                    )

                return ToolResult(
                    name=name,
                    result={
                        "refreshed": True,
                        "fetched_at": now_ts,
                        "scraped_count": len(items),
                        "known_databases_count": known_count,
                        "source_url": source_url,
                        "note": (
                            "The live A–Z list is JS-rendered, so scraped results may be incomplete. "
                            "However, the curated JSON list is available for matching."
                        ),
                    },
                )

            if name == "search_databases":
                query = str(arguments["query"]).strip()
                if not query:
                    return ToolResult(
                        name=name,
                        result=[],
                        success=False,
                        error="Query cannot be empty.",
                    )

                top_k = int(arguments.get("top_k", 10))
                q = query.lower()

                eur_data = self._load_databases()
                databases = eur_data.get("databases", [])
                not_available = eur_data.get("not_available", [])

                matches = []
                for db in databases:
                    db_name = db.get("name", "")
                    aliases = db.get("aliases", [])
                    status = db.get("status", "unknown")
                    url = db.get("url", self.config.institution.library_url)
                    access = db.get("access", "")
                    notes = db.get("notes", "")
                    contains = db.get("contains", [])

                    haystack = f"{db_name} {' '.join(aliases)} {' '.join(contains)}".lower()
                    if q in haystack or any(q in alias or alias in q for alias in aliases):
                        matches.append(
                            {
                                "name": db_name,
                                "url": url,
                                "access": access,
                                "status": status,
                                "notes": notes,
                                "source": "database_list",
                                "verified": status == "active",
                            }
                        )
                    if len(matches) >= top_k:
                        break

                unavailable_matches = []
                for db in not_available:
                    db_name = db.get("name", "")
                    aliases = db.get("aliases", [])
                    notes = db.get("notes", "")
                    haystack = f"{db_name} {' '.join(aliases)}".lower()
                    if q in haystack or any(q in alias or alias in q for alias in aliases):
                        unavailable_matches.append(
                            {
                                "name": db_name,
                                "status": "not_available",
                                "notes": notes,
                            }
                        )

                cache_key = "_eur_db_index_cache_v1"
                cache = getattr(self, cache_key, None)
                scraped_matches = []
                if cache and cache.get("items"):
                    matched_names = {m["name"].lower() for m in matches}
                    for it in cache.get("items", []):
                        title = it.get("title", "")
                        url = it.get("url", "")
                        hay = f"{title} {url}".lower()
                        if q in hay and title.lower() not in matched_names:
                            scraped_matches.append(
                                {
                                    "name": title,
                                    "url": url,
                                    "source": "scraped_index",
                                    "verified": False,
                                }
                            )
                        if len(scraped_matches) >= 3:
                            break

                all_matches = matches + scraped_matches
                all_matches = all_matches[:top_k]

                if not all_matches and not unavailable_matches:
                    return ToolResult(
                        name=name,
                        result={
                            "matches": [],
                            "suggestion": (
                                f"No database found matching '{query}' at {self.config.institution.name}. "
                                "This does NOT mean it's unavailable — check the library's A–Z list at "
                                f"{self.config.institution.library_url} manually, or ask your institution's "
                                "data service desk. Mark as UNVERIFIED in your response."
                            ),
                        },
                    )

                result = {"matches": all_matches}
                if unavailable_matches:
                    result["not_available_warning"] = unavailable_matches
                if eur_data.get("_load_error"):
                    result["warning"] = f"Using fallback data: {eur_data['_load_error']}"

                return ToolResult(name=name, result=result)

            if name == "search_papers":
                results = self.paper_search.search(
                    query=arguments["query"],
                    top_k=arguments.get("top_k", 10),
                    year_min=arguments.get("year_min"),
                    year_max=arguments.get("year_max"),
                )
                formatted = []
                for r in results:
                    paper_info = {
                        "id": r.paper.id,
                        "title": r.paper.title,
                        "authors": r.paper.authors,
                        "year": r.paper.year,
                        "relevance_score": round(r.score, 3),
                        "abstract_snippet": (
                            r.paper.abstract[:300] + "..."
                            if r.paper.abstract and len(r.paper.abstract) > 300
                            else r.paper.abstract
                        ),
                    }
                    formatted.append(paper_info)
                    self.seen_papers[r.paper.id] = {
                        "title": r.paper.title,
                        "authors": r.paper.authors,
                        "year": r.paper.year,
                    }
                return ToolResult(name=name, result=formatted)

            if name == "get_paper_details":
                paper_id = arguments["paper_id"]
                if paper_id not in self.seen_papers:
                    return ToolResult(
                        name=name,
                        result=None,
                        success=False,
                        error=(
                            f"Paper '{paper_id}' was not found in search results. "
                            "You must use search_papers first to find papers before getting details."
                        ),
                    )
                paper = self.paper_db.get_paper(paper_id)
                if paper:
                    return ToolResult(
                        name=name,
                        result={
                            "id": paper.id,
                            "title": paper.title,
                            "authors": paper.authors,
                            "year": paper.year,
                            "abstract": paper.abstract,
                            "doi": paper.doi,
                            "ssrn_id": paper.ssrn_id,
                            "doi_url": paper.doi_url,
                            "ssrn_url": paper.ssrn_url,
                        },
                    )
                return ToolResult(
                    name=name,
                    result=None,
                    success=False,
                    error=f"Paper not found in database: {paper_id}",
                )

            if name == "format_citation":
                paper_id = arguments["paper_id"]
                if paper_id not in self.seen_papers:
                    return ToolResult(
                        name=name,
                        result=None,
                        success=False,
                        error=(
                            f"Paper '{paper_id}' was not found in search results. "
                            "You must use search_papers first to find papers before citing."
                        ),
                    )
                paper = self.paper_db.get_paper(paper_id)
                if paper:
                    citation = self.citation_formatter.cite(paper)
                    return ToolResult(
                        name=name,
                        result={
                            "citation": citation,
                            "bibtex": self.citation_formatter.style in ("bibtex", "quarto"),
                        },
                    )
                return ToolResult(
                    name=name,
                    result=None,
                    success=False,
                    error=f"Paper not found in database: {paper_id}",
                )

            return ToolResult(
                name=name,
                result=None,
                success=False,
                error=f"Unknown tool: {name}",
            )

        except Exception as e:
            return ToolResult(name=name, result=None, success=False, error=str(e))

    def _get_google_tools(self) -> list:
        """Create Python function wrappers for Google's automatic function calling."""

        def _record_tool(name: str, result: ToolResult) -> str:
            payload = {"success": result.success, "result": result.result, "error": result.error}
            content = json.dumps(payload)
            # Use tool name as call id fallback
            self.memory.add("tool", content, tool_name=name, tool_call_id=name)
            return content

        def search_papers(
            query: str,
            top_k: int = 10,
            year_min: int | None = None,
            year_max: int | None = None,
        ) -> str:
            result = self._execute_tool(
                "search_papers",
                {
                    "query": query,
                    "top_k": top_k,
                    "year_min": year_min,
                    "year_max": year_max,
                },
            )
            return _record_tool("search_papers", result)

        def get_paper_details(paper_id: str) -> str:
            result = self._execute_tool("get_paper_details", {"paper_id": paper_id})
            return _record_tool("get_paper_details", result)

        def format_citation(paper_id: str) -> str:
            result = self._execute_tool("format_citation", {"paper_id": paper_id})
            return _record_tool("format_citation", result)

        def refresh_database_index(force: bool = False, max_age_hours: int = 24) -> str:
            result = self._execute_tool(
                "refresh_database_index",
                {"force": force, "max_age_hours": max_age_hours},
            )
            return _record_tool("refresh_database_index", result)

        def search_databases(query: str, top_k: int = 10) -> str:
            result = self._execute_tool("search_databases", {"query": query, "top_k": top_k})
            return _record_tool("search_databases", result)

        return [
            search_papers,
            get_paper_details,
            format_citation,
            refresh_database_index,
            search_databases,
        ]

    def _build_prompt(self) -> str:
        """Build base system prompt with configurable templates and rules."""
        fallback = ""
        base = resolve_prompt(self.mode, fallback=fallback)
        data_rules = self.DATA_SOURCE_RULES_TEMPLATE.format(
            institution=self.config.institution.name
        )
        return base + "\n" + self.CITATION_RULES + "\n" + data_rules

    def _build_draft_context(self) -> str:
        """Build context block for loaded drafts."""
        if not self.memory.get_context("draft_loaded"):
            return ""

        draft_name = self.memory.get_context("draft_name") or "draft"
        draft_metadata = self.memory.get_context("draft_metadata") or {}
        summary = self.memory.get_context("draft_summary") or ""
        full_content = self.memory.get_context("draft_content") or ""

        lines = [f"\n\n--- LOADED DOCUMENT: {draft_name} ---"]
        if draft_metadata.get("format"):
            lines.append(f"[Format: {draft_metadata['format'].upper()}]")

        # Version diff context (if v2+ and student tracking is active)
        version_context = self._build_version_diff_context()
        if version_context:
            lines.append(version_context)

        if summary:
            lines.append("\n[Draft Summary]")
            lines.append(summary)

        if full_content:
            lines.append("\n[Full Draft Content]")
            lines.append(full_content)

        lines.append("\n--- END OF DOCUMENT ---")
        return "\n".join(lines)

    def _build_version_diff_context(self) -> str:
        """Build diff context for v2+ drafts using student tracking DB."""
        student_id = self.memory.get_context("tracked_student_id")
        draft_version = self.memory.get_context("tracked_draft_version")

        if not student_id or not draft_version or draft_version < 2:
            return ""

        try:
            from .students import StudentDB

            sdb = StudentDB()
            diff = sdb.compute_version_diff(
                student_id=student_id,
                from_version=draft_version - 1,
                to_version=draft_version,
            )
            sdb.close()

            if not diff:
                return ""

            lines = [
                f"\n[Changes since v{diff.from_version}]",
                f"This is version {diff.to_version} of the draft by {diff.student_name}.",
                f"Changes: {diff.diff_summary}",
            ]

            if diff.previous_feedback:
                # Truncate long feedback to keep context manageable
                max_fb_chars = self.config.draft_context.max_section_chars * 2
                fb_text = diff.previous_feedback
                if len(fb_text) > max_fb_chars:
                    fb_text = fb_text[:max_fb_chars] + "\n... (truncated)"
                lines.append(f"\n[Previous feedback (v{diff.from_version})]")
                lines.append(fb_text)
                lines.append(
                    "\nIMPORTANT: Focus your feedback on whether the student addressed "
                    "the issues raised above. Acknowledge improvements. Do not repeat "
                    "feedback points that have already been resolved. Prioritize NEW "
                    "issues or issues that remain unaddressed."
                )

            return "\n".join(lines)

        except Exception:
            # Student DB not available or other error — degrade gracefully
            return ""

    def _build_reference_papers_context(self) -> str:
        """Build context block for loaded reference papers (writing mode)."""
        ref_papers = self.memory.get_context("reference_papers")
        if not ref_papers or self.mode != "writing":
            return ""

        lines = ["\n\n--- LOADED REFERENCE PAPERS ---"]
        for i, paper in enumerate(ref_papers, 1):
            title = paper.get("title", paper.get("name", f"Paper {i}"))
            authors = paper.get("authors", "")
            abstract = paper.get("abstract", "")
            content = paper.get("content", "")

            lines.append(f"\n## Reference Paper {i}: {title}")
            if authors:
                lines.append(f"Authors: {authors}")
            if abstract:
                lines.append(f"Abstract: {abstract}")
            lines.append(f"\nContent:\n{content}\n---")

        lines.append("--- END OF REFERENCE PAPERS ---")
        lines.append("\nYou can cite these papers in your writing. Use the information above.")
        return "\n".join(lines)

    def _extract_dataset_mentions(self, text: str) -> list[str]:
        """Extract likely dataset/database mentions from draft text."""
        if not text:
            return []
        hay = text.lower()
        eur_data = self._load_databases()
        names: set[str] = set()

        # From curated list
        for db in eur_data.get("databases", []):
            name = db.get("name", "")
            aliases = db.get("aliases", [])
            contains = db.get("contains", [])
            for term in [name, *aliases, *contains]:
                if term and term.lower() in hay:
                    names.add(name)

        # From not available list
        for db in eur_data.get("not_available", []):
            name = db.get("name", "")
            aliases = db.get("aliases", [])
            for term in [name, *aliases]:
                if term and term.lower() in hay:
                    names.add(name)

        # Common finance datasets (fallback)
        common = [
            "Compustat",
            "CRSP",
            "WRDS",
            "IBES",
            "FactSet",
            "Capital IQ",
            "Bloomberg",
            "Refinitiv",
            "Datastream",
            "Orbis",
            "Bureau van Dijk",
            "Worldscope",
            "S&P Global",
            "Morningstar",
            "PitchBook",
            "Preqin",
            "Dealogic",
        ]
        for term in common:
            if term.lower() in hay:
                names.add(term)

        return sorted(names)

    def _extract_search_topics(self, draft_content: str) -> list[str]:
        """Extract 2-3 deterministic search queries from draft content (no LLM)."""
        topics: list[str] = []
        hay = draft_content.lower()
        sections = extract_key_sections(draft_content)

        # Topic 1: opening sentences from abstract or introduction
        for section_name in ("abstract", "summary", "introduction", "preamble"):
            text = sections.get(section_name, "").strip()
            if len(text) > 50:
                sentences = re.split(r"(?<=[.!?])\s+", text)
                topics.append(" ".join(sentences[:3])[:300])
                break
        if not topics:
            # Fallback: first 60 words of the document
            snippet = " ".join(draft_content.split()[:60])
            if snippet:
                topics.append(snippet)

        # Topic 2: identification / econometric method
        method_map = [
            (["difference-in-differences", "diff-in-diff", " did "], "difference-in-differences causal identification"),
            (["regression discontinuity", " rdd "], "regression discontinuity design"),
            (["instrumental variable", " iv ", "2sls", "two-stage least squares"], "instrumental variables endogeneity"),
            (["event study", "cumulative abnormal return", " car "], "event study stock market reaction"),
            (["synthetic control"], "synthetic control causal inference"),
            (["fixed effects", "panel data"], "panel data fixed effects econometrics"),
            (["natural experiment"], "natural experiment quasi-experimental design"),
        ]
        for keywords, query in method_map:
            if any(kw in hay for kw in keywords):
                topics.append(query)
                break

        # Topic 3: hypothesis or contribution section opening
        for section_name in ("hypothesis", "contribution", "literature"):
            text = sections.get(section_name, "").strip()
            if len(text) > 50:
                sentences = re.split(r"(?<=[.!?])\s+", text)
                snippet = " ".join(sentences[:2])[:200]
                if snippet and snippet not in topics:
                    topics.append(snippet)
                    break

        return topics[:3]

    def _ensure_feedback_data_verification(self) -> None:
        """Auto-run database verification in feedback mode to keep feasibility checks consistent."""
        if self.mode not in ("feedback", "feedback_final", "grading"):
            return

        if not self.memory.get_context("draft_loaded"):
            return

        draft_content = self.memory.get_context("draft_content") or ""
        last_hash = self.memory.get_context("eur_verification_hash")
        current_hash = str(hash(draft_content))
        if last_hash == current_hash:
            return

        refresh = self._execute_tool("refresh_database_index", {})
        self.memory.set_context("eur_refresh_result", refresh.result)

        mentions = self._extract_dataset_mentions(draft_content)
        verification_results = {}
        for name in mentions:
            result = self._execute_tool("search_databases", {"query": name, "top_k": 5})
            verification_results[name] = result.result

        self.memory.set_context("eur_verification", verification_results)

        # Pre-run paper searches so the LLM has literature ready without re-searching
        topics = self._extract_search_topics(draft_content)
        pre_searched: dict[str, list] = {}
        for topic in topics:
            result = self._execute_tool("search_papers", {"query": topic, "top_k": 8})
            if result.success and result.result:
                pre_searched[topic] = result.result
        self.memory.set_context("pre_searched_papers", pre_searched)

        self.memory.set_context("eur_verification_hash", current_hash)

    def _build_data_verification_context(self) -> str:
        """Build context block with database verification results and pre-searched literature."""
        if self.mode not in ("feedback", "feedback_final", "grading"):
            return ""
        verification = self.memory.get_context("eur_verification") or {}
        refresh = self.memory.get_context("eur_refresh_result") or {}
        pre_searched = self.memory.get_context("pre_searched_papers") or {}
        if not verification and not refresh and not pre_searched:
            return ""

        lines = ["\n\n--- DATABASE VERIFICATION ---"]
        if refresh:
            lines.append("[Refresh Result]")
            lines.append(json.dumps(refresh, indent=2))
        if verification:
            lines.append("\n[Database Checks]")
            lines.append(json.dumps(verification, indent=2))
        lines.append("\n--- END DATABASE VERIFICATION ---")

        if pre_searched:
            lines.append(
                "\n\n--- PRE-SEARCHED LITERATURE ---\n"
                "These papers were retrieved before the conversation. "
                "Cite them directly without calling search_papers for these topics."
            )
            for topic, papers in pre_searched.items():
                lines.append(f"\n[Topic: {topic[:120]}]")
                lines.append(json.dumps(papers, indent=2))
            lines.append("\n--- END PRE-SEARCHED LITERATURE ---")

        return "\n".join(lines)

    def _build_review_context(self) -> str:
        """Build additional review/feedback context for structured prompting."""
        if self.mode not in ("feedback", "review"):
            return ""
        summary = self.memory.get_context("draft_summary") or ""
        sections = self.memory.get_context("draft_sections") or {}
        if not summary and not sections:
            return ""
        lines = ["\n\n--- REVIEW PIPELINE CONTEXT ---"]
        if summary:
            lines.append("[Summary]")
            lines.append(summary)
        if sections:
            max_chars = self.config.draft_context.max_section_chars
            lines.append("\n[Sections]")
            for name, content in sections.items():
                if content:
                    trimmed = content.strip()
                    if max_chars > 0 and len(trimmed) > max_chars:
                        trimmed = trimmed[:max_chars] + "..."
                    lines.append(f"\n## {name.title()}\n{trimmed}")
        lines.append("\n--- END REVIEW PIPELINE CONTEXT ---")
        return "\n".join(lines)

    def _get_missing_required_headings(self, content: str) -> list[str]:
        """Return missing required headings for feedback/review outputs."""
        if self.mode == "feedback":
            required = [
                "A. Executive summary",
                "B. My understanding of your study",
                "C. Strengths",
                "D. Biggest risks / threats to validity",
                "E. Data & Feasibility Audit",
                "F. Methods & identification feedback",
                "G. Literature & citations needed",
                "H. Concrete revision checklist",
                "I. Clarifying questions",
            ]
        elif self.mode == "feedback_hyp_rd":
            required = [
                "A. Executive summary",
                "B. Hypothesis Map",
                "C. Hypothesis",  # matches "Hypothesis–Analysis Alignment Audit"
                "D. Research Design Assessment",
                "E. Data",  # matches "Data–Hypothesis Feasibility Check"
                "F. Strengths",
                "G. Literature",
                "H. Revision checklist",
                "I. Clarifying questions",
            ]
        elif self.mode == "feedback_final":
            required = [
                "A. Bottom-line verdict",
                "B. Grading-matrix triage table",
                "C. My understanding of the thesis",
                "D. Minimum changes required to pass",
                "F. Required checks on core thesis elements",
                "G. Data and feasibility audit",
                "H. Citation audit",
                "I. Submission checklist",
                "J. Clarifying questions",
            ]
        elif self.mode == "grading":
            required = [
                "A. Suggested grade",
                "B. Grading-matrix assessment",
                "C. My understanding of the thesis",
                "D. Strengths and weaknesses",
                "E. Data and feasibility audit",
                "F. Citation audit",
                "G. Oral defense question set",
                "H. Risk flags",
                "I. Supervisor notes",
            ]
        elif self.mode == "review":
            required = [
                "Section 1",
                "Section 2",
                "Section 3",
            ]
        else:
            return []

        lower = content.lower()
        return [heading for heading in required if heading.lower() not in lower]

    def _build_missing_sections_request(self, missing: list[str]) -> str:
        """Build a follow-up request to fill missing sections."""
        lines = [
            "The response is missing required sections.",
            "Please add content under each missing heading below. Return ONLY the missing sections with their headings:",
        ]
        for heading in missing:
            lines.append(f"- {heading}")
        return "\n".join(lines)

    # -- Unicode math symbols that should be LaTeX instead --
    _UNICODE_MATH_CHARS = set("αβγδεζηθικλμνξπρστυφχψωΑΒΓΔΕΖΗΘΙΚΛΜΝΞΠΡΣΤΥΦΧΨΩ"
                              "²³¹⁰⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉"
                              "≤≥≠≈∑∏∫∂∞±×÷√∈∉⊂⊃∪∩∅∀∃")

    def _check_formatting_issues(self, content: str) -> list[str]:
        """Detect voice and math formatting violations in feedback output."""
        if self.mode not in ("feedback_hyp_rd", "feedback", "feedback_final"):
            return []

        issues: list[str] = []
        import re

        # Check for third-person voice violations (outside of the labeling system)
        voice_patterns = [
            r"\bthe student\b", r"\bthe author\b",
            r"\bthe student's\b", r"\bthe author's\b",
        ]
        for pattern in voice_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                issues.append(
                    f"Voice violation: found {len(matches)}x '{matches[0]}'. "
                    "Rewrite these to use 'you/your' (second person)."
                )
                break

        # Check for bare Unicode math outside of LaTeX delimiters
        # Strip LaTeX-delimited regions first to avoid false positives
        stripped = re.sub(r"\$\$.*?\$\$", "", content, flags=re.DOTALL)
        stripped = re.sub(r"\$[^$]+?\$", "", stripped)
        found_unicode = [ch for ch in stripped if ch in self._UNICODE_MATH_CHARS]
        if found_unicode:
            unique = set(found_unicode)
            examples = ", ".join(sorted(unique)[:5])
            issues.append(
                f"Math formatting violation: found bare Unicode math symbols ({examples}) "
                "outside of LaTeX delimiters. Wrap ALL math in $...$ or $$...$$."
            )

        return issues

    def _build_verification_request(self, issues: list[str]) -> str:
        """Build a follow-up request to fix formatting violations."""
        lines = [
            "VERIFICATION FAILED — please fix the following issues and return the COMPLETE corrected report:",
        ]
        for i, issue in enumerate(issues, 1):
            lines.append(f"{i}. {issue}")
        return "\n".join(lines)

    def chat(self, user_input: str) -> str:
        """
        Process user input and return assistant response.

        Handles multi-turn conversation with tool calling.
        """
        self.memory.add("user", user_input)

        system_prompt = self._build_prompt()

        draft_context = self._build_draft_context()
        papers_context = self._build_reference_papers_context()

        if self.mode in ("feedback", "feedback_final", "grading"):
            self._ensure_feedback_data_verification()
            system_prompt += self._build_data_verification_context()

        full_system_prompt = system_prompt + draft_context + papers_context

        messages = [
            {"role": "system", "content": full_system_prompt},
            *self.memory.get_messages(),
        ]

        max_iterations = 15
        content = ""
        follow_up_attempted = False

        for _ in range(max_iterations):
            content, tool_calls = self.adapter.generate(messages, self.TOOLS)

            if tool_calls:
                tool_call_dicts = [
                    {"id": tc.id, "function": {"name": tc.name, "arguments": tc.arguments}}
                    for tc in tool_calls
                ]
                messages.append(
                    {
                        "role": "assistant",
                        "content": content,
                        "tool_calls": tool_call_dicts,
                    }
                )

                for tc in tool_calls:
                    result = self._execute_tool(tc.name, tc.arguments)
                    tool_result_content = json.dumps(
                        {"success": result.success, "result": result.result, "error": result.error}
                    )

                    messages.append(
                        self.adapter.format_tool_result(tc.id, tc.name, tool_result_content)
                    )
                    self.memory.add(
                        "tool",
                        tool_result_content,
                        tool_name=tc.name,
                        tool_call_id=tc.id,
                    )
            else:
                if content:
                    missing = self._get_missing_required_headings(content)
                    if missing and not follow_up_attempted:
                        follow_up_attempted = True
                        messages.append({"role": "assistant", "content": content})
                        messages.append(
                            {
                                "role": "user",
                                "content": self._build_missing_sections_request(missing),
                            }
                        )
                        continue
                    # Programmatic verification of voice + math formatting
                    fmt_issues = self._check_formatting_issues(content)
                    if fmt_issues and not follow_up_attempted:
                        follow_up_attempted = True
                        messages.append({"role": "assistant", "content": content})
                        messages.append(
                            {
                                "role": "user",
                                "content": self._build_verification_request(fmt_issues),
                            }
                        )
                        continue
                    self.memory.add("assistant", content)
                    return content
                continue

        if content:
            self.memory.add("assistant", content)
            return content
        return "I wasn't able to complete the request within the allowed steps."

    def _build_draft_summary(self, content: str) -> str:
        """Build a deterministic summary from key sections without LLM calls."""
        if not content:
            return ""

        sections = extract_key_sections(content)
        lines = []
        for name in (
            "abstract",
            "introduction",
            "method",
            "data",
            "result",
            "discussion",
            "conclusion",
        ):
            snippet = sections.get(name)
            if snippet:
                compact = snippet.strip().replace("\n", " ")
                compact = compact[:400] + ("..." if len(compact) > 400 else "")
                lines.append(f"- {name.title()}: {compact}")
        return "\n".join(lines)

    def load_draft(self, content: str, name: str = "draft", metadata: dict | None = None) -> str:
        """Load a draft into context for feedback/review."""
        self.memory.set_context("draft_loaded", True)
        self.memory.set_context("draft_name", name)
        self.memory.set_context("draft_content", content)
        if metadata:
            self.memory.set_context("draft_metadata", metadata)

        tokens = estimate_tokens(content)
        if tokens > 4000:
            chunks = chunk_text(content)
            self.memory.set_context("draft_chunks", len(chunks))
            msg = f"Loaded draft '{name}' ({tokens} tokens, split into {len(chunks)} chunks)"
        else:
            msg = f"Loaded draft '{name}' ({tokens} tokens)"

        if metadata and metadata.get("format") in ("pdf", "docx"):
            msg += f" [parsed from {metadata['format'].upper()}]"

        # Review pipeline preprocessing
        self.memory.set_context("draft_sections", extract_key_sections(content))
        self.memory.set_context("draft_summary", self._build_draft_summary(content))

        return msg

    def load_reference_paper(
        self, content: str, name: str = "paper", metadata: dict | None = None
    ) -> str:
        """
        Load a reference paper into context for writing mode.
        """
        from .pdf import extract_paper_info

        paper_info = extract_paper_info(content, metadata)
        ref_papers = self.memory.get_context("reference_papers") or []

        paper_entry = {
            "name": name,
            "title": paper_info.get("title", name),
            "authors": paper_info.get("authors"),
            "abstract": paper_info.get("abstract"),
            "content": content,
            "word_count": paper_info.get("word_count", 0),
        }
        ref_papers.append(paper_entry)
        self.memory.set_context("reference_papers", ref_papers)

        tokens = estimate_tokens(content)
        title = paper_info.get("title", name)
        if len(title) > 60:
            title = title[:57] + "..."

        msg = f"Loaded reference paper: '{title}' ({tokens} tokens)"
        if paper_info.get("authors"):
            msg += f"\n  Authors: {paper_info['authors'][:80]}"
        if paper_info.get("abstract"):
            abstract_preview = paper_info["abstract"][:150]
            if len(paper_info["abstract"]) > 150:
                abstract_preview += "..."
            msg += f"\n  Abstract: {abstract_preview}"

        msg += f"\n  [Paper #{len(ref_papers)} - use in writing mode to cite/reference]"
        return msg

    def get_reference_papers_summary(self) -> str:
        """Get a summary of loaded reference papers."""
        ref_papers = self.memory.get_context("reference_papers") or []
        if not ref_papers:
            return "No reference papers loaded. Use /loadpaper to add papers."

        lines = [f"Loaded reference papers ({len(ref_papers)}):"]
        for i, paper in enumerate(ref_papers, 1):
            title = paper.get("title", paper.get("name", "Unknown"))
            if len(title) > 50:
                title = title[:47] + "..."
            authors = paper.get("authors", "")
            if authors and len(authors) > 30:
                authors = authors[:27] + "..."
            lines.append(f"  {i}. {title}")
            if authors:
                lines.append(f"     {authors}")

        return "\n".join(lines)

    def get_references(self) -> str:
        """Get the current reference list."""
        return self.citation_formatter.get_full_output()

    def get_bibtex(self) -> str:
        """Get BibTeX entries for cited papers."""
        return self.citation_formatter.get_bibtex()
