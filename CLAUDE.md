# CLAUDE.md - RAiner Project Documentation

## Overview

RAiner is an open-source academic research assistant that runs locally. It uses:
- **DuckDB** for paper metadata storage
- **ChromaDB** for semantic vector search over abstracts
- **Multiple LLM providers**: Ollama, OpenAI, Anthropic, Google Gemini, OpenRouter
- **Sentence Transformers** for embeddings (all-MiniLM-L6-v2)
- **Quarto-compatible output**: generates `.qmd` files with YAML frontmatter (default `format: pdf`)

## Project Structure

```
RAiner/
├── pyproject.toml          # Poetry configuration
├── config.example.yaml     # Example config file
├── README.md               # User documentation
├── CLAUDE.md               # This file - developer/AI documentation
└── rainer/                 # Python package (lowercase!)
    ├── __init__.py         # Package exports
    ├── cli.py              # Terminal UI entry point
    ├── agent.py            # Core agent with tool calling loop
    ├── config.py           # Pydantic configuration management
    ├── providers.py        # LLM provider adapters (Ollama, OpenAI, Anthropic, Google, OpenRouter)
    ├── prompts.py          # Prompt loading utilities (resolves mode -> markdown file)
    ├── memory.py           # Session persistence (JSON files)
    ├── papers.py           # DuckDB interface for paper metadata
    ├── search.py           # ChromaDB vector search
    ├── pdf.py              # PDF/document parsing via docling (optional)
    ├── citations.py        # BibTeX/inline/Quarto citation formatting
    ├── output.py           # Quarto/Markdown (.qmd) file generation with YAML frontmatter
    ├── chunking.py         # Large document splitting
    ├── embed.py            # Script to create ChromaDB embeddings
    ├── batch.py            # Batch processing (parallel feedback/review on multiple files)
    ├── mcp_server.py       # MCP server exposing RAiner tools (FastMCP-based)
    ├── data/
    │   └── eur_databases.json  # EUR library database access list (curated)
    └── prompts/            # Mode-specific system prompt templates (Markdown)
        ├── feedback.md
        ├── writing.md
        ├── review.md
        ├── search.md
        └── exam-review.md
```

## Database Schema

The DuckDB database (`articles.duckdb`) has three tables:

- **`articles`**: Main paper metadata (DOI as PK, title, authors, year, journal info, timestamps)
- **`ssrn_pages`**: SSRN-specific data joined via DOI (abstract, URLs, PDF paths, match score)
- **`journals`**: Journal metadata (ISSN as PK, name, field, publisher)
- **FTS indexes**: `fts_main_articles`, `fts_main_ssrn_pages`

## Key Components

### `papers.py` - PaperDB
- Connects to DuckDB with read-only access
- Joins `articles` and `ssrn_pages` tables to get full paper info
- Methods: `get_paper(doi)`, `search_by_title()`, `search_by_author()`, `fulltext_search()`, `get_papers_with_abstracts()`
- The `Paper` model includes helper properties like `first_author_surname`, `doi_url`, `ssrn_page_url`, `short_cite()`

### `search.py` - PaperSearch
- Lazy initialization (doesn't fail if ChromaDB missing)
- Falls back to DuckDB fulltext search if embeddings unavailable
- Uses cosine similarity via ChromaDB's HNSW index
- Methods: `search(query)`, `search_multiple(queries)`, `find_similar_to_paper(doi)`

### `agent.py` - ResearchAgent
- Core agent loop with tool calling; delegates LLM calls to `providers.py` adapters
- Tools: `search_papers`, `get_paper_details`, `format_citation`, `save_output`, `refresh_eur_database_index`, `search_eur_databases`
- System prompts loaded via `prompts.py` per mode
- Anti-hallucination rules: `CITATION_RULES` and `DATA_SOURCE_RULES`
- EUR database verification via `rainer/data/eur_databases.json` (curated list; **note**: this file must be created/populated manually)
- Tracks citations via `CitationFormatter`
- Runtime provider switching via `switch_provider()`

### `providers.py` - Provider Adapters
- Abstract `ProviderAdapter` base class with `generate()` and `format_tool_result()`
- Concrete adapters: `OllamaAdapter`, `OpenAIAdapter`, `AnthropicAdapter`, `GoogleAdapter`
- `OpenAIToolsMixin` shared by Ollama and OpenAI for tool schema formatting
- Canonical `ToolCall` dataclass normalizes provider-specific responses

### `prompts.py` - Prompt Loading
- Loads mode-specific system prompts from `rainer/prompts/*.md`
- Valid modes: `feedback`, `writing`, `review`, `search`, `exam-review`
- Supports config override for prompt directory via `output.prompt_dir`

### `mcp_server.py` - MCP Server
- Exposes RAiner paper search, retrieval, and citation tools via FastMCP
- Entry point: `poetry run rainer-mcp`
- Tools mirror the agent's tool set (search, get paper, format citation, etc.)

### `batch.py` - Batch Processing
- `rainer batch feedback *.pdf --workers 3`: non-interactive bulk feedback/review
- `process_single_file()` runs in a worker process (own config, agent, memory)
- `ProcessPoolExecutor` for parallelism; `--workers 1` for sequential
- Auto-saves .qmd per file with student_name/draft_title from `_parse_draft_name()`

### `memory.py` - Session Management
- Sessions stored as JSON in `~/.local/share/rainer/sessions/`
- `ConversationMemory` wraps `Session` with auto-save
- Supports resume via session ID

### `cli.py` - Terminal Interface
- Uses `rich` for formatting, `prompt_toolkit` for input
- Subcommand routing: `rainer batch ...` routes to `batch.py`; no subcommand enters interactive mode
- `load_file_raw()` raises `LoadError` on failure (used by batch); `load_file()` wraps it with console output (interactive)
- Workflow-oriented commands:
  - `/load <file>`: load a draft/document (PDF, DOCX, MD, TXT) into the base session
  - `/review [extra prompt]`: run a one-shot structured reviewer-style report on the loaded draft (creates a dedicated `review` session, copies draft context)
  - `/feedback [extra prompt]`: run a one-shot structured student-facing feedback report on the loaded draft (creates a dedicated `feedback` session, copies draft context)
  - Normal chat: ad-hoc questions and literature search using the current base session
- Additional commands: `/help`, `/mode`, `/provider`, `/model`, `/sessions`, `/resume`, `/loadpaper`, `/papers`, `/save`, `/refs`, `/bibtex`, `/stats`, `/info`, `/clear`, `/quit`
- CLI flags: `-p/--provider`, `--model`, `-m/--mode`, `-c/--config`, `-r/--resume`, `-l/--load`

### `pdf.py` - Document Parsing (Optional)
- Lazy loading of docling (optional dependency)
- `parse_document()`: Parses PDF, DOCX, TXT, MD files
- `extract_paper_info()`: Extracts title, authors, abstract from parsed content
- Falls back gracefully when docling not installed
- Install with: `poetry install --with pdf`

## Configuration

Config loaded from (in order):
1. `./config.yaml`
2. `~/.config/rainer/config.yaml`

Key settings:
```yaml
provider:
  name: ollama  # ollama, ollama-cloud, openai, openrouter, anthropic, google
  model: qwen2.5:14b

data:
  duckdb_path: ~/path/to/articles.duckdb
  chroma_path: ~/path/to/chroma
  chroma_collection: paper_abstracts
```

## Providers

| Provider | Env Variable | Example Models |
|----------|--------------|----------------|
| ollama | - | qwen2.5:14b, llama3:8b |
| ollama-cloud | OLLAMA_API_KEY | kimi-k2-thinking |
| openai | OPENAI_API_KEY | gpt-4o, gpt-4o-mini |
| openrouter | OPENROUTER_API_KEY | qwen/qwen3-14b |
| anthropic | ANTHROPIC_API_KEY | claude-sonnet-4-20250514 |
| google | GOOGLE_API_KEY | gemini-3.0-flash |

Switch providers at runtime:
```bash
# CLI flags
rainer --provider openai --model gpt-4o
rainer -p google --model gemini-2.0-flash

# Runtime commands
/provider                     # Show current
/provider openai gpt-4o       # Switch provider and model
/model gpt-4o-mini            # Switch model only
```

## Setup Commands

```bash
# Install
cd RAiner
poetry install

# Create embeddings (first time)
poetry run rainer-embed \
  --duckdb "~/Dropbox/Github Data/cite-hustle/DB/articles.duckdb" \
  --chroma "~/Dropbox/Github Data/cite-hustle/DB/chroma" \
  --collection paper_abstracts

# Run
poetry run rainer

# Run as MCP server (for Claude Desktop / MCP-compatible clients)
poetry run rainer-mcp

# Batch process multiple files
rainer batch feedback submissions/*.pdf --workers 3
rainer batch feedback *.pdf -e "Focus on methodology" -p anthropic
```

## Agent Tools

The agent exposes six tools to the LLM: `search_papers`, `get_paper_details`, `format_citation`, `save_output`, `refresh_eur_database_index`, `search_eur_databases`. See `agent.py` for full schemas and return types.

## EUR Database Verification

The agent includes tools to verify whether databases/datasets are available at Erasmus University Rotterdam:

- **`rainer/data/eur_databases.json`**: Curated list of 43 databases with access details
- **Status tracking**: active, trial, expiring, expired, cancelled
- **Not available list**: Databases confirmed unavailable (FactSet, Preqin, etc.)
- **Public sources**: SEC EDGAR, World Bank, Eurostat, etc.

In **feedback mode**, the agent must:
1. Call `refresh_eur_database_index` at start of session
2. Call `search_eur_databases` for each dataset the student mentions
3. Label data sources as VERIFIED / UNVERIFIED / ASSUMED

Update `eur_databases.json` when EUR database access changes. Check [EDSC news](https://www.eur.nl/en/library/research-support/edsc/edsc-news) for updates.

## Modes

| Mode | Citation Style | Use Case |
|------|---------------|----------|
| feedback | inline | Student draft review with data feasibility audit (used by `/feedback` workflow) |
| writing | quarto (@key) | Paper writing assistance (used when writing with loaded reference papers) |
| review | inline | Reviewer report writing (used by `/review` workflow) |
| search | bibtex | Literature discovery (default base mode when starting RAiner) |
| exam-review | inline | Exam/quiz quality review (used by `/review` on exam documents) |

### Feedback Mode Output Format

In feedback mode (invoked via `/feedback` on a loaded draft), the agent produces a structured report:

```
A. Executive summary (max 6 bullets; include feasibility verdict)
B. My understanding of your study (RQ, hypotheses, sample, variables, strategy)
C. Strengths
D. Biggest risks / threats to validity (ranked)
E. Data & Feasibility Audit (required)
   - E1. Data requirements table
   - E2. Data availability check (VERIFIED via EUR tools / UNVERIFIED)
   - E3. Practical data acquisition plan
F. Methods & identification feedback
G. Literature & citations needed (tool-backed only)
H. Concrete revision checklist (10-15 items)
I. Clarifying questions
```

## Development Notes

- Package must be lowercase `rainer/` (not `RAiner/`) for Python imports
- DuckDB connection is read-only to prevent accidental modifications
- ChromaDB uses cosine similarity space
- Session files are JSON for easy debugging
- Embedding model must match between `embed.py` and `search.py`
- Output:
  - Default output extension is configurable via `output.default_extension` (defaults to `.qmd`)
  - YAML frontmatter includes a Quarto `format:` block (default `pdf: default`)
  - Feedback reports include `draft_title` and (heuristically parsed) `student_name` fields based on the loaded draft name (e.g. `"Ashkan Issazadeh – Draft proposal – v1 – 23 feb"` → `student_name="Ashkan Issazadeh"`, `draft_title="Draft proposal"`)
- Workflows:
  - Base session typically starts in `search` mode; you load a draft once with `/load`
  - `/review` and `/feedback` create dedicated sessions with the appropriate mode and copied draft context, without needing to restart RAiner

## Common Issues

1. **"Collection not found"** - Run `rainer-embed` first
2. **"No papers with abstracts"** - Check `ssrn_pages` table has abstracts
3. **Import errors** - Ensure folder is `rainer/` not `RAiner/`
4. **Ollama connection failed** - Check `ollama serve` is running or use `ollama-cloud`
5. **"API key not set"** - Set the required env variable (OPENAI_API_KEY, GOOGLE_API_KEY, etc.)
6. **Provider tool calling errors** - Different providers have varying tool calling support
