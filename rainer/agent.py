"""Core research assistant agent with tool calling."""

import json
import re
import time
from pathlib import Path
from typing import Any, Literal

import anthropic as anthropic_sdk
import ollama
import openai as openai_sdk
from google import genai
from google.genai import types as google_types
from pydantic import BaseModel

from .chunking import chunk_text
from .citations import CitationFormatter
from .config import get_config
from .memory import ConversationMemory
from .papers import Paper, PaperDB
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
                "description": "Search the academic paper database for papers relevant to a query. Use this to find papers that support or relate to specific claims, concepts, or topics.",
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
                "description": "Get full details about a specific paper by its ID. Use this after searching to get complete information.",
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
                "name": "save_output",
                "description": "Save the current response or generated content to a markdown file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Title for the output file",
                        },
                        "content": {
                            "type": "string",
                            "description": "The markdown content to save",
                        },
                    },
                    "required": ["title", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "refresh_eur_database_index",
                "description": "Fetch and cache the Erasmus University Library A–Z database list (https://libguides.eur.nl/az/databases). Use this before making any claims about database availability at EUR. Returns cache metadata and a short summary.",
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
                            "description": "If cache is newer than this, do not refetch unless force=true (default: 24)",
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
                "name": "search_eur_databases",
                "description": "Search the cached EUR Library A–Z database index by keyword(s) and return matching database titles and URLs. Use this to verify whether a named database/dataset is available via EUR subscriptions.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Keyword query, e.g., 'WRDS', 'Compustat', 'Bloomberg', 'Orbis', 'FactSet', 'Datastream'",
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
6. Use ONLY the exact titles, authors, and years returned by the tools
7. If search returns no relevant results, say so honestly - do not make up citations

WORKFLOW: Search first → Get details if needed → Then cite. Never skip the search step."""

    # EUR database list loaded from JSON file for comprehensive coverage
    # Update rainer/data/eur_databases.json when database access changes
    _EUR_DB_JSON_PATH = Path(__file__).parent / "data" / "eur_databases.json"
    _EUR_DB_CACHE: dict | None = None

    @classmethod
    def _load_eur_databases(cls) -> dict:
        """Load EUR database list from JSON file (cached)."""
        if cls._EUR_DB_CACHE is not None:
            return cls._EUR_DB_CACHE

        try:
            with open(cls._EUR_DB_JSON_PATH, encoding="utf-8") as f:
                cls._EUR_DB_CACHE = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            # Fallback to minimal hardcoded list if JSON fails
            cls._EUR_DB_CACHE = {
                "databases": [
                    {"name": "WRDS", "aliases": ["wrds", "compustat", "crsp"], "status": "active"},
                    {"name": "LSEG Workspace", "aliases": ["lseg", "eikon", "datastream"], "status": "active"},
                    {"name": "Orbis", "aliases": ["orbis", "bvd"], "status": "active"},
                ],
                "not_available": [],
                "_load_error": str(e),
            }
        return cls._EUR_DB_CACHE

    DATA_SOURCE_RULES = """
CRITICAL DATA & DATABASE VERIFICATION RULES - YOU MUST FOLLOW THESE:
1. NEVER claim a dataset/database is available at EUR unless verified via search_eur_databases results in THIS conversation
2. ALWAYS run refresh_eur_database_index at the start of feedback mode (mandatory), then verify relevant names with search_eur_databases
3. If you cannot verify availability, label it UNVERIFIED and propose feasible alternatives (public sources like SEC EDGAR, or other verified EUR databases)
4. NEVER invent licensing/access details, variable coverage, fields, sample coverage, or download options
5. Clearly label statements as VERIFIED / UNVERIFIED / ASSUMED when discussing data access or content
6. If feasibility depends on unknown student choices (country/timeframe/unit), ask focused clarifying questions and provide fallback designs
"""

    # System prompts for different modes
    SYSTEM_PROMPTS = {
        "feedback": """You are an academic research expert providing a STUDENT-FACING FEEDBACK REPORT on a student draft.

You MUST prioritize feasibility: the study must be doable using either (a) public data (e.g., SEC EDGAR) or (b) data available via Erasmus University Library databases (verify against https://libguides.eur.nl/az/databases using tools).

MANDATORY FIRST STEP (Data verification):
- Call refresh_eur_database_index at the start of the assignment (before you comment on feasibility).
- For each dataset/database the student mentions OR that the design implicitly requires (e.g., Compustat/CRSP/Orbis/Bloomberg/Refinitiv/FactSet/Datastream/IBES/etc.), call search_eur_databases to VERIFY availability at EUR.
- If a database/dataset is not verified, label it UNVERIFIED and propose feasible alternatives (public or EUR-verified).

Your role is to:
1. Restate the research question, hypotheses, unit of analysis, geography, timeframe, main variables, and (if applicable) identification strategy in your own words.
2. Provide constructive feedback to improve theory, contribution, and clarity.
3. Run a Data & Feasibility Audit (required; see output format).
4. Identify claims that need citation support and use search_papers to find relevant literature.
5. Suggest specific papers that could strengthen the argument; explain WHY each is relevant to a specific claim.
6. Provide a concrete revision checklist.

HALLUCINATION CONTROLS:
- Follow CITATION_RULES and DATA_SOURCE_RULES strictly.
- Separate data statements into VERIFIED / UNVERIFIED / ASSUMED.
- If you cannot verify feasibility, ask focused clarifying questions and provide fallback designs.

OUTPUT FORMAT (use these headings):
A. Executive summary (max 6 bullets; include #1 feasibility verdict)
B. My understanding of your study (RQ, hypotheses, sample, variables, strategy)
C. Strengths
D. Biggest risks / threats to validity (ranked)
E. Data & Feasibility Audit (required)
   - E1. Data requirements table (unit, sample, timeframe, key variables, sources)
   - E2. Data availability check
       * Public sources (verified as public)
       * EUR databases (VERIFIED via search_eur_databases results)
       * UNVERIFIED items + feasible alternatives
   - E3. Practical data acquisition plan (steps, expected effort, what to download)
F. Methods & identification feedback (what would convince a reader)
G. Literature & citations needed (use tool-backed citations only)
H. Concrete revision checklist (10–15 actionable items)
I. Clarifying questions (only the minimum needed)

Current mode: Student Feedback (inline citations + reference list)""",
        "writing": """You are an academic research assistant helping to write papers.

Your role is to:
1. Help structure arguments and identify gaps in literature coverage
2. Find relevant papers from the database to support claims
3. Suggest how to integrate citations naturally into the text
4. Format citations in Quarto style (@author2020) for use with Pandoc/Quarto

FEASIBILITY & NON-HALLUCINATION:
- Do not describe access to proprietary datasets unless the user explicitly confirms access OR you have verified EUR database availability in this conversation.
- When dataset access is unclear, write conditional language and add a short TODO list at the end.

Help craft academic prose that integrates sources smoothly.

Current mode: Writing Assistance (Quarto citations + BibTeX output)""",
        "review": """You are an academic research assistant helping to write review reports.

Your role is to:
1. Evaluate manuscripts by checking claims against the literature
2. Identify missing key references
3. Assess the positioning of the work relative to existing literature
4. Suggest additional papers the authors should cite or engage with
5. Evaluate data availability/replicability and whether the empirical strategy is feasible

HALLUCINATION CONTROLS:
- Follow CITATION_RULES and DATA_SOURCE_RULES.
- Do not claim a data source is available unless verified.

Be thorough but fair. Structure comments into Major vs Minor issues.

Current mode: Review Reports (inline citations + reference list)""",
        "search": """You are an academic research assistant helping with literature searches.

Your role is to:
1. Find papers relevant to specific research questions using the search_papers tool
2. Summarize what the literature says about topics
3. Identify key papers and authors in a field
4. Provide BibTeX entries for papers to add to reference managers

IMPORTANT: After searching and finding relevant papers, you MUST provide a final text response summarizing what you found. Do NOT keep searching indefinitely. One or two searches is usually sufficient.

Workflow:
1. Use search_papers to find relevant papers (1-2 searches max)
2. Optionally use get_paper_details for important papers
3. ALWAYS end with a summary response listing the papers found and their relevance
4. State corpus limitations if relevant (e.g., coverage of the local database)

Focus on comprehensive coverage and accurate bibliographic information.

Current mode: Literature Search (BibTeX output)""",
    }

    def __init__(
        self,
        mode: Literal["feedback", "writing", "review", "search"] = "feedback",
        memory: ConversationMemory | None = None,
    ):
        self.config = get_config()
        self.mode = mode
        self.memory = memory or ConversationMemory.new(mode=mode)

        # Initialize tools
        self.paper_db = PaperDB()
        self.paper_search = PaperSearch(paper_db=self.paper_db)

        # Citation formatter based on mode
        mode_config = getattr(self.config.modes, mode)
        self.citation_formatter = CitationFormatter(
            style=mode_config.citation_style,
            include_reference_list=(mode_config.citation_style != "bibtex"),
        )

        # Registry of papers seen in this session (for hallucination prevention)
        # Only papers returned by search_papers can be cited
        self.seen_papers: dict[str, dict] = {}  # DOI -> paper info

        # Initialize LLM client
        self._init_llm()

    def _init_llm(self) -> None:
        """Initialize the LLM client based on config."""
        self.provider_type = self.config.provider.name
        self.model = self.config.provider.model

        if self.provider_type == "ollama":
            # Local Ollama - for cloud models, user must run `ollama signin` first
            host = self.config.provider.ollama.base_url
            self.client = ollama.Client(host=host)

        elif self.provider_type == "ollama-cloud":
            # Direct Ollama Cloud API access (no local Ollama needed)
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
            if name == "refresh_eur_database_index":
                # Cache the EUR A–Z database index (best-effort; page is dynamic).
                # This tool is intentionally conservative: it only reports what it can extract.
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
                            "note": "Using cached EUR database index (recent enough).",
                        },
                    )

                # Best-effort fetch: in some environments this won't work (no network/tooling).
                # We must not hallucinate.
                items: list[dict[str, str]] = []
                source_url = "https://libguides.eur.nl/az/databases"

                # Attempt to fetch using an internal lightweight client if available;
                # otherwise provide an explicit failure.
                html: str | None = None
                fetch_error: str | None = None
                try:
                    # If the environment doesn't allow HTTP here, this will fail
                    # and we will return a clear error.
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
                    # Heuristic extraction: look for anchor tags that likely represent
                    # database entries. We only return what we can confidently extract
                    # as (title, url).
                    # NOTE: The LibGuides directory is often JS-rendered; extraction
                    # may be incomplete.
                    for m in re.finditer(
                        r'<a[^>]+href="([^"]+)"[^>]*>([^<]{2,200})</a>',
                        html,
                        flags=re.IGNORECASE,
                    ):
                        href = m.group(1).strip()
                        title = re.sub(r"\s+", " ", m.group(2).strip())
                        if not title:
                            continue
                        # Filter obvious navigation/boilerplate links
                        if title.lower() in (
                            "login",
                            "report a problem.",
                            "ask a question",
                            "search e-journals",
                        ):
                            continue
                        if "libguides.eur.nl" in href or href.startswith("http"):
                            items.append({"title": title, "url": href})
                    # De-duplicate by (title, url)
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

                # Load EUR database list to get count
                eur_data = self._load_eur_databases()
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
                            "warning": "Could not fetch the live EUR database list (network error or "
                            "JS-rendered page). However, search_eur_databases can still match against "
                            f"{known_count} known EUR databases from the curated JSON list.",
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
                        "note": "LibGuides is JS-rendered, so scraped results may be incomplete. "
                        f"However, {known_count} known EUR databases are available "
                        "via the curated JSON list (search_eur_databases will use both).",
                    },
                )

            elif name == "search_eur_databases":
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

                # Load EUR database list from JSON
                eur_data = self._load_eur_databases()
                databases = eur_data.get("databases", [])
                not_available = eur_data.get("not_available", [])

                # Search against known EUR databases
                matches = []
                for db in databases:
                    db_name = db.get("name", "")
                    aliases = db.get("aliases", [])
                    status = db.get("status", "unknown")
                    url = db.get("url", "https://libguides.eur.nl/az/databases")
                    access = db.get("access", "")
                    notes = db.get("notes", "")
                    contains = db.get("contains", [])

                    # Check if query matches name, aliases, or contained databases
                    haystack = f"{db_name} {' '.join(aliases)} {' '.join(contains)}".lower()
                    if q in haystack or any(q in alias or alias in q for alias in aliases):
                        matches.append({
                            "name": db_name,
                            "url": url,
                            "access": access,
                            "status": status,
                            "notes": notes,
                            "source": "eur_database_list",
                            "verified": status == "active",
                        })
                    if len(matches) >= top_k:
                        break

                # Also check not_available list to warn user
                unavailable_matches = []
                for db in not_available:
                    db_name = db.get("name", "")
                    aliases = db.get("aliases", [])
                    notes = db.get("notes", "")
                    haystack = f"{db_name} {' '.join(aliases)}".lower()
                    if q in haystack or any(q in alias or alias in q for alias in aliases):
                        unavailable_matches.append({
                            "name": db_name,
                            "status": "not_available",
                            "notes": notes,
                        })

                # Check scraped cache as fallback
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
                            scraped_matches.append({
                                "name": title,
                                "url": url,
                                "source": "scraped_index",
                                "verified": False,
                            })
                        if len(scraped_matches) >= 3:  # Limit scraped results
                            break

                # Build response
                all_matches = matches + scraped_matches
                all_matches = all_matches[:top_k]

                if not all_matches and not unavailable_matches:
                    return ToolResult(
                        name=name,
                        result={
                            "matches": [],
                            "suggestion": f"No EUR database found matching '{query}'. "
                            "This does NOT mean it's unavailable—check libguides.eur.nl/az/databases manually "
                            "or contact edsc@eur.nl. Mark as UNVERIFIED in your response.",
                        },
                    )

                result = {"matches": all_matches}
                if unavailable_matches:
                    result["not_available_warning"] = unavailable_matches
                if eur_data.get("_load_error"):
                    result["warning"] = f"Using fallback data: {eur_data['_load_error']}"

                return ToolResult(name=name, result=result)

            elif name == "search_papers":
                results = self.paper_search.search(
                    query=arguments["query"],
                    top_k=arguments.get("top_k", 10),
                    year_min=arguments.get("year_min"),
                    year_max=arguments.get("year_max"),
                )
                # Format results for the LLM
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
                    # Track this paper as seen (Part C: hallucination prevention)
                    self.seen_papers[r.paper.id] = {
                        "title": r.paper.title,
                        "authors": r.paper.authors,
                        "year": r.paper.year,
                    }
                return ToolResult(name=name, result=formatted)

            elif name == "get_paper_details":
                paper_id = arguments["paper_id"]
                # Part C: Validate paper was seen in search results
                if paper_id not in self.seen_papers:
                    return ToolResult(
                        name=name,
                        result=None,
                        success=False,
                        error=f"Paper '{paper_id}' was not found in search results. "
                        f"You must use search_papers first to find papers before getting details. "
                        f"Do NOT make up paper IDs.",
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

            elif name == "format_citation":
                paper_id = arguments["paper_id"]
                # Part C: Validate paper was seen in search results
                if paper_id not in self.seen_papers:
                    return ToolResult(
                        name=name,
                        result=None,
                        success=False,
                        error=f"Paper '{paper_id}' was not found in search results. "
                        f"You must use search_papers first to find papers before citing. "
                        f"Do NOT make up paper IDs or citations.",
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

            elif name == "save_output":
                from .output import MarkdownWriter

                writer = MarkdownWriter(citation_formatter=self.citation_formatter)
                writer.set_title(arguments["title"])
                writer.add_text(arguments["content"])
                filepath = writer.write()
                return ToolResult(
                    name=name,
                    result={"saved_to": str(filepath)},
                )

            else:
                return ToolResult(
                    name=name,
                    result=None,
                    success=False,
                    error=f"Unknown tool: {name}",
                )

        except Exception as e:
            return ToolResult(name=name, result=None, success=False, error=str(e))

    def _get_openai_tools(self) -> list[dict]:
        """Convert tools to OpenAI format (also used by Ollama and OpenRouter)."""
        return self.TOOLS

    def _get_anthropic_tools(self) -> list[dict]:
        """Convert tools to Anthropic format."""
        tools = []
        for tool in self.TOOLS:
            func = tool["function"]
            tools.append(
                {
                    "name": func["name"],
                    "description": func["description"],
                    "input_schema": func["parameters"],
                }
            )
        return tools

    def _get_google_tools(self) -> list:
        """Create Python function wrappers for Google's automatic function calling."""

        # Create wrapper functions that the Google SDK can call automatically
        def search_papers(
            query: str,
            top_k: int = 10,
            year_min: int | None = None,
            year_max: int | None = None,
        ) -> str:
            """Search the academic paper database for papers relevant to a query.

            Args:
                query: Natural language search query describing what you're looking for
                top_k: Number of results to return (default: 10)
                year_min: Minimum publication year filter (optional)
                year_max: Maximum publication year filter (optional)

            Returns:
                JSON string with search results
            """
            result = self._execute_tool(
                "search_papers",
                {
                    "query": query,
                    "top_k": top_k,
                    "year_min": year_min,
                    "year_max": year_max,
                },
            )
            return json.dumps(
                {"success": result.success, "result": result.result, "error": result.error}
            )

        def get_paper_details(paper_id: str) -> str:
            """Get full details about a specific paper by its ID.

            Args:
                paper_id: The paper ID (DOI)

            Returns:
                JSON string with paper details
            """
            result = self._execute_tool("get_paper_details", {"paper_id": paper_id})
            return json.dumps(
                {"success": result.success, "result": result.result, "error": result.error}
            )

        def format_citation(paper_id: str) -> str:
            """Format a paper citation in the appropriate style for the current mode.

            Args:
                paper_id: The paper ID to cite

            Returns:
                JSON string with formatted citation
            """
            result = self._execute_tool("format_citation", {"paper_id": paper_id})
            return json.dumps(
                {"success": result.success, "result": result.result, "error": result.error}
            )

        def save_output(title: str, content: str) -> str:
            """Save the current response or generated content to a markdown file.

            Args:
                title: Title for the output file
                content: The markdown content to save

            Returns:
                JSON string with save confirmation
            """
            result = self._execute_tool("save_output", {"title": title, "content": content})
            return json.dumps(
                {"success": result.success, "result": result.result, "error": result.error}
            )

        def refresh_eur_database_index(
            force: bool = False, max_age_hours: int = 24
        ) -> str:
            """Fetch and cache the Erasmus University Library A–Z database list.

            Args:
                force: Force refresh even if cache is recent (default: false)
                max_age_hours: If cache is newer than this, do not refetch (default: 24)

            Returns:
                JSON string with cache metadata and summary
            """
            result = self._execute_tool(
                "refresh_eur_database_index",
                {"force": force, "max_age_hours": max_age_hours},
            )
            return json.dumps(
                {"success": result.success, "result": result.result, "error": result.error}
            )

        def search_eur_databases(query: str, top_k: int = 10) -> str:
            """Search the cached EUR Library A–Z database index by keyword(s).

            Args:
                query: Keyword query, e.g., 'WRDS', 'Compustat', 'Bloomberg'
                top_k: Maximum number of matches to return (default: 10)

            Returns:
                JSON string with matching database titles and URLs
            """
            result = self._execute_tool(
                "search_eur_databases", {"query": query, "top_k": top_k}
            )
            return json.dumps(
                {"success": result.success, "result": result.result, "error": result.error}
            )

        return [
            search_papers,
            get_paper_details,
            format_citation,
            save_output,
            refresh_eur_database_index,
            search_eur_databases,
        ]

    def _call_ollama(self, messages: list[dict]) -> tuple[str, list[dict] | None]:
        """Make Ollama API call and return (content, tool_calls)."""
        response = self.client.chat(
            model=self.model,
            messages=messages,
            tools=self._get_openai_tools(),
        )
        message = response["message"]
        content = message.get("content", "")
        tool_calls = message.get("tool_calls")
        return content, tool_calls

    def _call_openai(self, messages: list[dict]) -> tuple[str, list[dict] | None]:
        """Make OpenAI/OpenRouter API call and return (content, tool_calls)."""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=self._get_openai_tools(),
        )
        message = response.choices[0].message
        content = message.content or ""

        # Normalize tool_calls to match our internal format
        tool_calls = None
        if message.tool_calls:
            tool_calls = []
            for tc in message.tool_calls:
                args = tc.function.arguments
                if isinstance(args, str):
                    args = json.loads(args)
                tool_calls.append(
                    {
                        "id": tc.id,
                        "function": {
                            "name": tc.function.name,
                            "arguments": args,
                        },
                    }
                )
        return content, tool_calls

    def _call_anthropic(self, messages: list[dict]) -> tuple[str, list[dict] | None]:
        """Make Anthropic API call and return (content, tool_calls)."""
        # Extract system message and convert messages for Anthropic format
        system_content = ""
        anthropic_messages = []

        for msg in messages:
            if msg["role"] == "system":
                system_content = msg["content"]
            elif msg["role"] == "tool":
                # Anthropic uses tool_result in user messages
                anthropic_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.get("tool_call_id", ""),
                                "content": msg["content"],
                            }
                        ],
                    }
                )
            elif msg["role"] == "assistant" and msg.get("tool_calls"):
                # Convert assistant message with tool calls
                content_blocks = []
                if msg.get("content"):
                    content_blocks.append({"type": "text", "text": msg["content"]})
                for tc in msg["tool_calls"]:
                    content_blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id", tc["function"]["name"]),
                            "name": tc["function"]["name"],
                            "input": tc["function"]["arguments"],
                        }
                    )
                anthropic_messages.append({"role": "assistant", "content": content_blocks})
            else:
                anthropic_messages.append(msg)

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system_content,
            messages=anthropic_messages,
            tools=self._get_anthropic_tools(),
        )

        # Parse response
        content = ""
        tool_calls = None

        for block in response.content:
            if block.type == "text":
                content = block.text
            elif block.type == "tool_use":
                if tool_calls is None:
                    tool_calls = []
                tool_calls.append(
                    {
                        "id": block.id,
                        "function": {
                            "name": block.name,
                            "arguments": block.input,
                        },
                    }
                )

        return content, tool_calls

    def _call_google(self, messages: list[dict]) -> tuple[str, list[dict] | None]:
        """Make Google Gemini API call using chat with automatic function calling.

        The SDK handles function calling automatically, including thought signatures.
        Returns (content, None) since tools are executed automatically.
        """
        # Extract system instruction and build history
        system_instruction = None
        history = []

        for msg in messages:
            if msg["role"] == "system":
                system_instruction = msg["content"]
            elif msg["role"] == "user":
                history.append(
                    google_types.Content(
                        role="user", parts=[google_types.Part.from_text(text=msg["content"])]
                    )
                )
            elif msg["role"] == "assistant":
                # Skip tool_calls since SDK handles them automatically
                if msg.get("content"):
                    history.append(
                        google_types.Content(
                            role="model", parts=[google_types.Part.from_text(text=msg["content"])]
                        )
                    )
            # Skip "tool" role messages - SDK handles tool results automatically

        # Get the last user message as the current input
        current_message = None
        if history and history[-1].role == "user":
            current_message = history.pop()

        if not current_message:
            return "", None

        # Create chat with history and config
        config = google_types.GenerateContentConfig(
            tools=self._get_google_tools(),
            system_instruction=system_instruction,
        )

        chat = self.client.chats.create(
            model=self.model,
            history=history if history else None,
            config=config,
        )

        # Send message - SDK will automatically execute function calls
        response = chat.send_message(current_message.parts[0].text)

        # Return the final text response (tools already executed)
        content = response.text if response.text else ""
        return content, None  # No tool_calls - they were handled automatically

    def _format_tool_message(self, tool_call_id: str, tool_name: str, result_content: str) -> dict:
        """Format tool result message for the current provider."""
        if self.provider_type in ("ollama", "ollama-cloud"):
            return {"role": "tool", "content": result_content}
        elif self.provider_type in ("openai", "openrouter"):
            return {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": result_content,
            }
        elif self.provider_type == "anthropic":
            return {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": result_content,
            }
        elif self.provider_type == "google":
            return {
                "role": "tool",
                "tool_name": tool_name,
                "content": result_content,
            }
        return {"role": "tool", "content": result_content}

    def chat(self, user_input: str) -> str:
        """
        Process user input and return assistant response.

        Handles multi-turn conversation with tool calling.
        """
        # Add user message to memory
        self.memory.add("user", user_input)

        # Build messages for LLM (include citation and data source rules to prevent hallucinations)
        system_prompt = (
            self.SYSTEM_PROMPTS[self.mode]
            + "\n"
            + self.CITATION_RULES
            + "\n"
            + self.DATA_SOURCE_RULES
        )

        # Include loaded draft content in context (for feedback/review modes)
        draft_context = ""
        if self.memory.get_context("draft_loaded"):
            draft_name = self.memory.get_context("draft_name") or "draft"
            draft_content = self.memory.get_context("draft_content") or ""
            draft_metadata = self.memory.get_context("draft_metadata") or {}

            if draft_content:
                draft_context = f"\n\n--- LOADED DOCUMENT: {draft_name} ---\n"
                if draft_metadata.get("format"):
                    draft_context += f"[Format: {draft_metadata['format'].upper()}]\n"
                draft_context += f"\n{draft_content}\n\n--- END OF DOCUMENT ---"

        # Include loaded reference papers in context (for writing mode)
        papers_context = ""
        ref_papers = self.memory.get_context("reference_papers")
        if ref_papers and self.mode == "writing":
            papers_context = "\n\n--- LOADED REFERENCE PAPERS ---\n"
            for i, paper in enumerate(ref_papers, 1):
                title = paper.get("title", paper.get("name", f"Paper {i}"))
                authors = paper.get("authors", "")
                abstract = paper.get("abstract", "")
                content = paper.get("content", "")

                papers_context += f"\n## Reference Paper {i}: {title}\n"
                if authors:
                    papers_context += f"Authors: {authors}\n"
                if abstract:
                    papers_context += f"Abstract: {abstract}\n"
                papers_context += f"\nContent:\n{content}\n"
                papers_context += "\n---\n"
            papers_context += "--- END OF REFERENCE PAPERS ---\n"
            papers_context += "\nYou can cite these papers in your writing. Use the information above.\n"

        # Build full system prompt with loaded content
        full_system_prompt = system_prompt
        if draft_context:
            full_system_prompt += draft_context
        if papers_context:
            full_system_prompt += papers_context

        messages = [
            {"role": "system", "content": full_system_prompt},
            *self.memory.get_messages(),
        ]

        # Agent loop - keep going until we get a final response
        max_iterations = 15
        content = ""  # Initialize content to track last response

        for iteration in range(max_iterations):
            # Call the appropriate provider
            if self.provider_type in ("ollama", "ollama-cloud"):
                content, tool_calls = self._call_ollama(messages)
            elif self.provider_type in ("openai", "openrouter"):
                content, tool_calls = self._call_openai(messages)
            elif self.provider_type == "anthropic":
                content, tool_calls = self._call_anthropic(messages)
            elif self.provider_type == "google":
                content, tool_calls = self._call_google(messages)
            else:
                raise ValueError(f"Unknown provider: {self.provider_type}")

            # Check if there are tool calls
            if tool_calls:
                # Add assistant's message with tool calls first (once per response)
                messages.append(
                    {
                        "role": "assistant",
                        "content": content,
                        "tool_calls": tool_calls,
                    }
                )

                # Process each tool call and collect results
                for tool_call in tool_calls:
                    func = tool_call["function"]
                    tool_name = func["name"]
                    tool_args = func.get("arguments", {})
                    tool_call_id = tool_call.get("id", tool_name)

                    # Execute the tool
                    result = self._execute_tool(tool_name, tool_args)

                    # Add tool result to conversation
                    tool_result_content = json.dumps(
                        {"success": result.success, "result": result.result, "error": result.error}
                    )

                    # Add tool result in provider-specific format
                    messages.append(
                        self._format_tool_message(tool_call_id, tool_name, tool_result_content)
                    )

                    # Also track in memory
                    self.memory.add("tool", tool_result_content, tool_name=tool_name)

            else:
                # No tool calls - check if we have content for final response
                if content:
                    self.memory.add("assistant", content)
                    return content
                # Some models return empty content after tool results
                # Continue the loop to give the model another chance to respond
                continue

        # If we hit max iterations, return whatever content we have
        if content:
            self.memory.add("assistant", content)
            return content
        return "I apologize, but I wasn't able to complete the request within the allowed steps. Try a more specific query."

    def load_draft(
        self, content: str, name: str = "draft", metadata: dict | None = None
    ) -> str:
        """Load a draft into context for feedback/review."""
        from .chunking import estimate_tokens

        # Store in context
        self.memory.set_context("draft_loaded", True)
        self.memory.set_context("draft_name", name)
        self.memory.set_context("draft_content", content)
        if metadata:
            self.memory.set_context("draft_metadata", metadata)

        # Check if chunking needed
        tokens = estimate_tokens(content)
        if tokens > 4000:
            chunks = chunk_text(content)
            self.memory.set_context("draft_chunks", len(chunks))
            msg = f"Loaded draft '{name}' ({tokens} tokens, split into {len(chunks)} chunks)"
        else:
            msg = f"Loaded draft '{name}' ({tokens} tokens)"

        # Add format info if from PDF/docx
        if metadata and metadata.get("format") in ("pdf", "docx"):
            msg += f" [parsed from {metadata['format'].upper()}]"

        return msg

    def load_reference_paper(
        self, content: str, name: str = "paper", metadata: dict | None = None
    ) -> str:
        """
        Load a reference paper into context for writing mode.

        Unlike drafts, reference papers are stored as citable sources that
        the agent can reference when helping write.
        """
        from .chunking import estimate_tokens
        from .pdf import extract_paper_info

        # Extract paper info
        paper_info = extract_paper_info(content, metadata)

        # Store reference papers in a list (can load multiple)
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

        # Build response message
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
