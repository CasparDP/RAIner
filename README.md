# RAiner

An open-source research assistant for academic work. Built to run locally with Ollama or cloud providers.

## Features

- **Multiple modes**: Student feedback, writing assistance, review reports, literature search
- **Vector search**: Find relevant papers using semantic search via ChromaDB
- **Fallback search**: Works with keyword search even before embeddings are created
- **Citation management**: Automatic formatting in multiple styles (inline, Quarto, BibTeX)
- **EUR database verification**: Verify data availability at Erasmus University Library (43 databases tracked)
- **Data feasibility audits**: Structured feedback on whether student research designs are feasible
- **Anti-hallucination controls**: Only cite papers from search results, verify database access claims
- **Session persistence**: Save and resume conversations
- **Provider flexibility**: Ollama (local/cloud), OpenAI, Anthropic, Google Gemini, OpenRouter
- **File output**: Generate markdown reports with proper citations

## Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/RAiner.git
cd RAiner

# Install with Poetry
poetry install

# Activate the virtual environment
poetry shell
```

## Quick Start

### 1. Copy and configure

```bash
mkdir -p ~/.config/rainer
cp config.example.yaml ~/.config/rainer/config.yaml
```

Edit `~/.config/rainer/config.yaml`:

```yaml
provider:
  name: ollama-cloud  # or ollama for local
  model: qwen2.5:14b

data:
  duckdb_path: PATH TO DUCKDB
  chroma_path: PATH TO CHROMA
  chroma_collection: paper_abstracts
```

### 2. Set API key (for Ollama Cloud)

```bash
export OLLAMA_API_KEY="your_key_here"
```

### 3. Create embeddings (optional but recommended)

```bash
poetry run rainer-embed \
  --duckdb "~/Dropbox/Github Data/cite-hustle/DB/articles.duckdb" \
  --chroma "~/Dropbox/Github Data/cite-hustle/DB/chroma"
```

This embeds all paper abstracts for semantic search. Without this, RAiner falls back to keyword search.

### 4. Enable PDF support (optional)

```bash
poetry install --with pdf
```

This installs [docling](https://github.com/docling-project/docling) for parsing PDF, DOCX, PPTX, and other document formats. Without this, only text files (.txt, .md) are supported.

### 5. Run RAiner

```bash
poetry run rainer
```

## Usage

### Start the assistant

```bash
# Interactive mode selection
rainer

# Start in specific mode
rainer --mode feedback

# Resume a session
rainer --resume abc123

# Load a document
rainer --load draft.md --mode feedback
```

### Commands

| Command | Description |
|---------|-------------|
| `/help` | Show help |
| `/mode <mode>` | Switch mode (feedback, writing, review, search) |
| `/sessions` | List recent sessions |
| `/resume <id>` | Resume a previous session |
| `/load <file>` | Load a draft for feedback/review (supports PDF) |
| `/loadpaper <file>` | Load a reference paper PDF for writing mode |
| `/papers` | Show loaded reference papers |
| `/save [filename]` | Save conversation to markdown |
| `/refs` | Show current reference list |
| `/bibtex` | Show BibTeX entries |
| `/stats` | Show database statistics |
| `/clear` | Start new session |
| `/quit` | Exit |

### Modes

#### Student Feedback (`feedback`)
Review student drafts with comprehensive feedback including:
- Research question and hypothesis assessment
- Data feasibility audit (verifies EUR database access)
- Literature gap identification with tool-backed citations
- Structured revision checklist

The agent automatically verifies whether required databases (WRDS, Compustat, Bloomberg, etc.) are available at EUR.

```
[feedback] > Here's a draft: "Market microstructure affects price discovery..."
```

#### Writing Assistance (`writing`)
Help write papers with proper Quarto-style citations. Load reference papers as PDFs to cite from.

```
[writing] > /loadpaper ~/papers/smith2020.pdf
[writing] > Help me write an introduction about high-frequency trading, citing the loaded paper
```

#### Review Reports (`review`)
Write reviewer reports, check literature coverage.

```
[review] > Review this manuscript for missing key references...
```

#### Literature Search (`search`)
Find papers, get BibTeX entries.

```
[search] > Find papers about market maker inventory management after 2015
```

## Output Formats

### Inline Citations (feedback, review modes)
```
The evidence suggests price discovery occurs primarily in limit orders (Smith, 2020; Jones, 2021).
```

### Quarto Citations (writing mode)
```
The evidence suggests price discovery occurs primarily in limit orders [@smith2020; @jones2021].
```

### BibTeX (search mode, always available via `/bibtex`)
```bibtex
@article{smith2020,
  title = {Price Discovery in Limit Order Markets},
  author = {Smith, John and Doe, Jane},
  year = {2020},
  doi = {10.1234/example},
}
```

## EUR Database Verification

RAiner includes built-in verification of database access at Erasmus University Rotterdam. When reviewing student drafts, the assistant:

1. Checks if required databases are available via EUR subscriptions
2. Labels data sources as **VERIFIED** or **UNVERIFIED**
3. Suggests alternatives for unavailable databases

### Tracked Databases (43 total)

| Status | Examples |
|--------|----------|
| **Active** | WRDS, Compustat, CRSP, Orbis, LSEG Workspace, Morningstar |
| **Trial** | PitchBook (until Nov 2025), Revelio Labs (until Jun 2026) |
| **Cancelled** | Bloomberg (Apr 2024), RavenPack (Mar 2025) |
| **Not Available** | FactSet, Capital IQ, Preqin, Audit Analytics |

The database list is maintained in `rainer/data/eur_databases.json`. Update this file when EUR database access changes.

## Project Structure

```
RAiner/
├── pyproject.toml          # Poetry config
├── config.example.yaml     # Example configuration
├── README.md
├── CLAUDE.md               # Developer documentation
└── rainer/                 # Python package (lowercase!)
    ├── __init__.py
    ├── cli.py              # Terminal interface
    ├── agent.py            # Core agent with tool calling
    ├── config.py           # Configuration management
    ├── memory.py           # Session persistence
    ├── papers.py           # DuckDB interface
    ├── search.py           # ChromaDB vector search
    ├── citations.py        # Citation formatting
    ├── output.py           # Markdown output
    ├── chunking.py         # Document chunking
    ├── embed.py            # Embedding creation script
    └── data/
        └── eur_databases.json  # EUR library database list
```

## Development

```bash
# Install with dev dependencies
poetry install

# Run tests
poetry run pytest

# Format code
poetry run ruff format .
poetry run ruff check --fix .
```

## Roadmap

- [x] Add OpenAI and Anthropic provider support
- [x] Add Google Gemini and OpenRouter provider support
- [x] EUR database verification tools
- [x] Data feasibility audits in feedback mode
- [x] PDF parsing via docling integration
- [ ] Export to Word documents

## License

MIT
