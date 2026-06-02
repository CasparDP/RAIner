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
├── tests/                  # pytest test suite
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
    ├── students.py         # Student tracking DB (DuckDB: drafts, feedback, versions)
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
        ├── feedback_hyp_rd.md
        ├── feedback_results.md
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
- Tools: `search_papers`, `get_paper_details`, `format_citation`, `refresh_eur_database_index`, `search_eur_databases`
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
- Valid modes: `feedback`, `feedback_hyp_rd`, `feedback_results`, `writing`, `review`, `search`, `exam-review`
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
  - `/feedback [extra prompt]`: run a one-shot feedback report on the loaded draft. Inherits the current mode if it is `feedback`, `feedback_hyp_rd`, or `feedback_results`; otherwise defaults to `feedback`.
  - Normal chat: ad-hoc questions and literature search using the current base session
- Additional commands: `/help`, `/mode`, `/provider`, `/model`, `/sessions`, `/resume`, `/loadpaper`, `/papers`, `/save`, `/refs`, `/bibtex`, `/stats`, `/info`, `/register`, `/students`, `/versions`, `/import-feedback`, `/clear`, `/quit`
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
  temperature: 0.1  # lower = more deterministic; Ollama defaults to 0.3

data:
  duckdb_path: ~/path/to/articles.duckdb       # paper metadata (read-only)
  chroma_path: ~/path/to/chroma                # embeddings (read-only)
  chroma_collection: paper_abstracts
  sessions_path: ~/.local/share/rainer/sessions
  students_db_path: /path/to/students.duckdb   # student tracking DB (read/write)

output:
  directory: /path/to/output                   # generated .qmd / .pdf feedback reports

draft_context:
  max_context_tokens: 8000  # increase for large-context models
  max_section_chars: 2000
  include_full_draft: false
```

### Cross-machine setup (Dropbox sync)

The project is configured to sync the student tracking DB and output files across
machines via Dropbox at `/Users/casparm2/Dropbox/Github Data/rainer/`:

- `data/students.duckdb` — longitudinal student tracking DB (writable)
- `output/` — generated feedback / review reports (`.qmd`, `.pdf`)

The paper database (`articles.duckdb`) and ChromaDB embeddings live under
`/Users/casparm2/Dropbox/Github Data/cite-hustle/DB/` and are read-only for RAiner
(written by the separate `cite-hustle` scraper).

**DuckDB + Dropbox safety rules** (important — WAL file corruption risk):

1. Always fully quit RAiner before switching machines. This flushes the write-ahead
   log (`students.duckdb.wal`) so only the main `.duckdb` file needs to sync.
2. Wait for Dropbox to show "up to date" before opening RAiner on the other machine.
3. Never run RAiner on two machines simultaneously — concurrent writes to the
   synced DB can corrupt it.
4. If a `.wal` file lingers after quitting, that's a sign the previous session
   didn't close cleanly. Open RAiner once locally to let DuckDB replay/flush it
   before syncing.

The `output/` folder has no such constraints — plain files, safe to sync freely.

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

# Run tests
poetry run pytest
```

## Agent Tools

The agent exposes five tools to the LLM: `search_papers`, `get_paper_details`, `format_citation`, `refresh_eur_database_index`, `search_eur_databases`. See `agent.py` for full schemas and return types. File saving is handled by the CLI auto-save path in `cli.py` after `/feedback`, `/review`, or `/exam-review`; the LLM does not save files itself.

When adding/removing a tool, edit **three** places in `agent.py`: the `TOOLS` schema dict, the `_execute_tool` handler branch, and the `_get_google_tools()` wrapper + return list (Google provider uses Python function wrappers instead of JSON schemas).

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
| feedback_hyp_rd | inline | Hypothesis & research design focused feedback (select via `/mode feedback_hyp_rd`) |
| feedback_results | inline | Results-design consistency feedback for drafts with empirical output (select via `/mode feedback_results`) |
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

### Feedback Hyp-RD Mode Output Format

The `feedback_hyp_rd` mode focuses on hypothesis quality and research design alignment. It classifies submissions into one of three paths:

1. **Full draft** — standard path with hypothesis map, alignment audit, design assessment, lightweight data check
2. **Partial submission** — only hypothesis/design section submitted; feedback scoped to what's present, with explicit "cannot evaluate" list
3. **Insufficient hypotheses/design** — constructive fallback that proposes testable hypotheses and feasible research designs

Uses SUBMISSION_FIDELITY rules (STATED / INFERRED / NOT FOUND labeling) to prevent hallucinating what the student wrote.

```
Full draft output:
A. Executive summary (hypothesis–analysis alignment verdict)
B. Hypothesis Map (per-hypothesis table: mechanism, direction, test, variables, verdict)
C. Hypothesis–Analysis Alignment Audit (test mapping, DV/IV operationalization, confounds)
D. Research Design Assessment (identification strategy, assumptions, alternatives)
E. Data–Hypothesis Feasibility Check (lightweight)
F. Strengths
G. Literature & citations needed
H. Revision checklist (hypothesis/design fixes front-loaded)
I. Clarifying questions

Insufficient output:
A. RQ Assessment
B. Proposed Hypotheses (labeled as suggestions)
C. Proposed Research Designs (labeled as suggestions)
D. Data Feasibility
E. Strengths
F. Building-block revision checklist
G. Clarifying questions
```

### Feedback Results Mode Output Format

The `feedback_results` mode evaluates whether reported results deliver what the research design promised. Uses STATED / INFERRED / NOT FOUND labeling throughout; every comment must be grounded in a specific passage, table, or equation in the draft. Citations require a full DOI.

Classifies submissions into three paths:

1. **Full draft with results** — has RQ, hypotheses, methods, and actual empirical output
2. **Partial: methods but no results** — design present, results absent or placeholder
3. **Insufficient** — too much missing to evaluate meaningfully

```
Full draft output:
A. Executive summary (results-design fidelity verdict; every bullet cites a specific section/table)
B. Results-Design Consistency Audit (per hypothesis: promised test vs. delivered test, sign alignment, interpretation)
C. Specification Audit (FE, SE, controls, sample — flag silent deviations from methods description)
D. Results Interpretation (are coefficients/tables read correctly?)
E. Missing Analyses (only flagged if design section explicitly promised them)
F. Threats to Validity: Addressed vs. Open
G. Strengths
H. Literature & citations needed (tool-backed; full DOI required inline and in reference list)
I. Revision checklist (front-loaded with consistency fixes)
J. Clarifying questions

Partial output (no results yet):
A. Executive summary (design readiness)
B. Design Readiness Audit (per hypothesis: is the test fully specified?)
C. Pre-flight checklist (variable construction, sample restrictions, robustness to plan in advance)
D. What I cannot evaluate without results
E. Clarifying questions
```

## Student Tracking

RAiner includes a longitudinal student tracking system (`students.py`) for managing draft versions and feedback over time.

### Database Schema (`students.duckdb`)

- **`students`**: student_id (PK), name (unique), email, cohort, created_at
- **`drafts`**: draft_id (PK), student_id (FK), version (auto-incremented per student), filename, content_hash (dedup), raw_text, token_count, loaded_at
- **`feedback_runs`**: run_id (PK), draft_id (FK), mode, provider, model, session_id, feedback_text, feedback_edited (optional), edited_at, created_at

### CLI Commands

- `/register <name> [email]` — register a student (auto-created on `/load` if name parseable from filename)
- `/students` — progress overview table (drafts, versions, feedback runs per student)
- `/versions [student name]` — list all draft versions for a student
- `/import-feedback <file>` — import your edited feedback for the most recent feedback run

### Automatic Behavior

- **On `/load`**: If the filename follows the `"Name – Title"` pattern, the student is auto-registered and the draft stored with an auto-incremented version. Duplicate content (same hash) is detected and not re-stored.
- **On `/feedback`**: The LLM response is auto-stored as a feedback run linked to the tracked draft.
- **On v2+ drafts**: The agent injects a `[Changes since v{N-1}]` block into the system prompt with a diff summary, changed sections, and the previous feedback. The LLM is instructed to focus on whether previous issues were addressed.

### Configuration

```yaml
data:
  # Default: ./data/students.duckdb
  # Current setup (Dropbox-synced across machines):
  students_db_path: "/Users/casparm2/Dropbox/Github Data/rainer/data/students.duckdb"
```

See the [Cross-machine setup](#cross-machine-setup-dropbox-sync) section above for
the DuckDB + Dropbox safety rules (fully quit RAiner before switching machines,
never run on two machines simultaneously).

## Draft Context Architecture

- `draft_content` (set in `load_draft()`): full document text, always stored without truncation — this is what `_build_draft_context()` injects into the system prompt as `[Full Draft Content]`
- `draft_excerpt` (legacy key): still copied between sessions in `cli.py` but no longer written by `load_draft()` or read by the agent — do not use
- `_build_review_context()` in `agent.py`: dead code, not called from `chat()` — do not re-introduce its call; the full draft in `_build_draft_context()` supersedes it

## Development Notes

- Package must be lowercase `rainer/` (not `RAiner/`) for Python imports
- DuckDB connection is read-only to prevent accidental modifications
- ChromaDB uses cosine similarity space
- Session files are JSON for easy debugging
- Embedding model must match between `embed.py` and `search.py`
- The `output/` directory needs a `_quarto.yml` to control PDF defaults (author, paper size, fonts); see `output.local-backup/_quarto.yml` for a template
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
