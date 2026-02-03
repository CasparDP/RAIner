# CLAUDE.md - RAiner Project Documentation

## Overview

RAiner is an open-source academic research assistant that runs locally. It uses:
- **DuckDB** for paper metadata storage
- **ChromaDB** for semantic vector search over abstracts
- **Multiple LLM providers**: Ollama, OpenAI, Anthropic, Google Gemini, OpenRouter
- **Sentence Transformers** for embeddings (all-MiniLM-L6-v2)

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
    ├── memory.py           # Session persistence (JSON files)
    ├── papers.py           # DuckDB interface for paper metadata
    ├── search.py           # ChromaDB vector search
    ├── pdf.py              # PDF/document parsing via docling (optional)
    ├── citations.py        # BibTeX/inline/Quarto citation formatting
    ├── output.py           # Markdown file generation
    ├── chunking.py         # Large document splitting
    ├── embed.py            # Script to create ChromaDB embeddings
    └── data/
        └── eur_databases.json  # EUR library database access list
```

## Database Schema

The DuckDB database (`articles.duckdb`) has these tables:

### `articles` (main paper metadata)
| Column | Type | Description |
|--------|------|-------------|
| doi | VARCHAR | Primary key |
| title | VARCHAR | Paper title |
| authors | VARCHAR | Author names |
| year | INTEGER | Publication year |
| journal_issn | VARCHAR | Journal ISSN |
| journal_name | VARCHAR | Journal name |
| publisher | VARCHAR | Publisher name |
| created_at | TIMESTAMP | Record creation |
| updated_at | TIMESTAMP | Record update |

### `ssrn_pages` (SSRN-specific data, joined via DOI)
| Column | Type | Description |
|--------|------|-------------|
| doi | VARCHAR | Primary key, links to articles |
| ssrn_url | VARCHAR | Full SSRN page URL |
| ssrn_id | VARCHAR | SSRN paper ID |
| abstract | VARCHAR | Paper abstract |
| pdf_url | VARCHAR | Link to PDF |
| pdf_downloaded | BOOLEAN | Whether PDF was downloaded |
| pdf_file_path | VARCHAR | Local path to PDF |
| match_score | INTEGER | Matching confidence |
| scraped_at | TIMESTAMP | When scraped |
| error_message | VARCHAR | Any scraping errors |

### `journals` (journal metadata)
| Column | Type | Description |
|--------|------|-------------|
| issn | VARCHAR | Primary key |
| name | VARCHAR | Journal name |
| field | VARCHAR | Research field |
| publisher | VARCHAR | Publisher |

### FTS Indexes
- `fts_main_articles` - Full-text search on articles
- `fts_main_ssrn_pages` - Full-text search on SSRN pages

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
- Core agent loop with multi-provider tool calling
- Supported providers: ollama, ollama-cloud, openai, openrouter, anthropic, google
- Tools: `search_papers`, `get_paper_details`, `format_citation`, `save_output`, `refresh_eur_database_index`, `search_eur_databases`
- System prompts per mode: feedback, writing, review, search
- Anti-hallucination rules: `CITATION_RULES` and `DATA_SOURCE_RULES`
- EUR database verification via `rainer/data/eur_databases.json` (43 databases tracked)
- Tracks citations via `CitationFormatter`
- Runtime provider switching via `switch_provider()`

### `memory.py` - Session Management
- Sessions stored as JSON in `~/.local/share/rainer/sessions/`
- `ConversationMemory` wraps `Session` with auto-save
- Supports resume via session ID

### `cli.py` - Terminal Interface
- Uses `rich` for formatting, `prompt_toolkit` for input
- Commands: `/help`, `/mode`, `/provider`, `/model`, `/sessions`, `/resume`, `/load`, `/loadpaper`, `/papers`, `/save`, `/refs`, `/bibtex`, `/stats`, `/clear`, `/quit`
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
```

## Tool Calling Schema

The agent exposes these tools to the LLM:

```python
search_papers(query: str, top_k: int = 10, year_min: int = None, year_max: int = None)
# Returns: list of {id, title, authors, year, relevance_score, abstract_snippet}

get_paper_details(paper_id: str)  # paper_id is DOI
# Returns: {id, title, authors, year, abstract, doi, ssrn_id, doi_url, ssrn_url}

format_citation(paper_id: str)
# Returns: {citation, bibtex} - formatted citation string

save_output(title: str, content: str)
# Returns: {saved_to: filepath}

refresh_eur_database_index(force: bool = False, max_age_hours: int = 24)
# Returns: {refreshed, fetched_at, scraped_count, known_databases_count, source_url, note}
# Loads EUR library database list for verification

search_eur_databases(query: str, top_k: int = 10)
# Returns: {matches: [{name, url, access, status, notes, verified}], not_available_warning: [...]}
# Searches EUR database list to verify data availability for students
```

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
| feedback | inline | Student draft review with data feasibility audit |
| writing | quarto (@key) | Paper writing assistance |
| review | inline | Reviewer report writing |
| search | bibtex | Literature discovery |

### Feedback Mode Output Format

In feedback mode, the agent produces a structured report:

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

## Common Issues

1. **"Collection not found"** - Run `rainer-embed` first
2. **"No papers with abstracts"** - Check `ssrn_pages` table has abstracts
3. **Import errors** - Ensure folder is `rainer/` not `RAiner/`
4. **Ollama connection failed** - Check `ollama serve` is running or use `ollama-cloud`
5. **"API key not set"** - Set the required env variable (OPENAI_API_KEY, GOOGLE_API_KEY, etc.)
6. **Provider tool calling errors** - Different providers have varying tool calling support
