"""Command-line interface for RAiner."""

import sys
from pathlib import Path
from typing import Literal

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from .agent import ResearchAgent
from .chunking import estimate_tokens
from .config import get_config, load_config, set_config
from .memory import ConversationMemory, SessionManager
from .output import MarkdownWriter, _parse_draft_name
from .students import StudentDB

console = Console()

# Prompt styling
PROMPT_STYLE = Style.from_dict(
    {
        "prompt": "#00aa00 bold",
        "mode": "#888888",
    }
)


def print_welcome() -> None:
    """Print welcome message."""
    console.print(
        Panel(
            "[bold blue]RAiner[/bold blue] - Research Assistant\n"
            "An open-source academic research assistant\n\n"
            "Commands: /help, /load, /review, /feedback, /exam-review, /provider, /model, /sessions, /save, /refs, /quit",
            title="Welcome",
            border_style="blue",
        )
    )


def print_help() -> None:
    """Print help information."""
    help_text = """
## Commands

| Command | Description |
|---------|-------------|
| `/help` | Show this help |
| `/provider [name] [model]` | Show/switch LLM provider |
| `/model <name>` | Switch model |
| `/sessions` | List recent sessions |
| `/resume <id>` | Resume a previous session |
| `/load <file>` | Load a document (PDF, DOCX, XLSX, MD, TXT) |
| `/loadpaper <file>` | Load a reference paper (PDF) for writing mode |
| `/review [extra prompt]` | Run a structured review workflow on the loaded draft |
| `/feedback [extra prompt]` | Run a structured student feedback report on the loaded draft |
| `/exam-review [extra prompt]` | Run a structured exam quality review on the loaded exam |
| `/papers` | Show loaded reference papers |
| `/save [filename]` | Save conversation to a Quarto/Markdown file (default `.qmd`) |
| `/refs` | Show current reference list |
| `/bibtex` | Show BibTeX entries |
| `/register <name> [email]` | Register a student for tracking |
| `/students` | List registered students and progress |
| `/versions [student name]` | Show draft versions for a student |
| `/import-feedback <file>` | Import your edited feedback for the current draft |
| `/stats` | Show database statistics |
| `/clear` | Clear conversation (new base session) |
| `/quit` or `/exit` | Exit |

## Workflows

- Use `/load` once to load a document (PDF, DOCX, XLSX, MD, TXT).
- Then trigger specific workflows with commands:
  - `/review` to generate a reviewer-style report
  - `/feedback` to generate a student-facing feedback report
  - `/exam-review` to review an exam for ambiguities and missing ingredients
  - Normal chat for ad-hoc questions and literature search

## Providers

- **ollama**: Local Ollama instance
- **ollama-cloud**: Ollama Cloud API (OLLAMA_API_KEY)
- **openai**: OpenAI API (OPENAI_API_KEY)
- **openrouter**: OpenRouter API (OPENROUTER_API_KEY)
- **anthropic**: Anthropic API (ANTHROPIC_API_KEY)
- **google**: Google Gemini API (GOOGLE_API_KEY)

## Tips

- Use `/load` for drafts, then `/review` or `/feedback` as needed
- Use `/loadpaper` for reference PDFs in writing workflows
- Supports PDF, DOCX, XLSX, TXT, MD (PDF/DOCX/XLSX require: `poetry install --with pdf`)
- Use specific queries: "papers about market microstructure after 2020"
- Ask for citations: "what papers support the claim that..."
- Switch providers: `/provider openai gpt-4o`
"""
    console.print(Markdown(help_text))


def select_mode() -> Literal["feedback", "writing", "review", "search"]:
    """Deprecated: interactive mode selection (kept for CLI compatibility).

    The CLI now starts in a neutral search-oriented mode by default.
    Prefer using workflow commands like /review and /feedback instead
    of selecting a global mode at startup.
    """
    return "search"  # type: ignore[return-value]


def list_sessions(manager: SessionManager) -> None:
    """List recent sessions."""
    sessions = manager.list_sessions(limit=10)

    if not sessions:
        console.print("[dim]No previous sessions found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="cyan")
    table.add_column("Mode")
    table.add_column("Title")
    table.add_column("Updated")
    table.add_column("Messages")

    for session in sessions:
        table.add_row(
            session.id,
            session.mode,
            (session.title or "Untitled")[:40],
            session.updated_at.strftime("%Y-%m-%d %H:%M"),
            str(len(session.messages)),
        )

    console.print(table)


class LoadError(Exception):
    """Raised when a file cannot be loaded."""


def load_file_raw(filepath: str) -> tuple[str, dict | None]:
    """Load a file and return its contents.

    Supports PDF, DOCX, and text formats via docling (if installed).
    Raises LoadError on failure instead of printing to console.

    Returns: (content, metadata) tuple
    """
    from .pdf import is_docling_available, parse_document

    # Strip surrounding quotes (single or double) from the filepath
    filepath = filepath.strip()
    if (filepath.startswith("'") and filepath.endswith("'")) or (
        filepath.startswith('"') and filepath.endswith('"')
    ):
        filepath = filepath[1:-1]

    path = Path(filepath).expanduser()
    if not path.exists():
        raise LoadError(f"File not found: {filepath}")

    result = parse_document(path)

    if result.method == "docling_not_installed":
        raise LoadError(
            "PDF/DOCX parsing requires docling. Install with: poetry install --with pdf"
        )

    if result.method.startswith("error:"):
        raise LoadError(f"Error reading file: {result.method}")

    if result.method == "unsupported_format":
        supported = ".txt, .md, .tex"
        if is_docling_available():
            supported += ", .pdf, .docx, .pptx, .xlsx, .html"
        raise LoadError(f"Unsupported file format: {path.suffix} (supported: {supported})")

    return result.content, result.metadata


def load_file(filepath: str) -> tuple[str | None, dict | None]:
    """Load a file, printing errors to console.

    Wrapper around load_file_raw for interactive use.
    Returns: (content, metadata) tuple, or (None, None) on error.
    """
    try:
        content, metadata = load_file_raw(filepath)
        from .pdf import parse_document

        path = Path(filepath.strip()).expanduser()
        result = parse_document(path)
        if result.method == "docling":
            console.print(f"[dim]Parsed {path.suffix} via docling[/dim]")
        return content, metadata
    except LoadError as e:
        console.print(f"[red]{e}[/red]")
        return None, None


def show_stats(agent: ResearchAgent) -> None:
    """Show database statistics."""
    stats = agent.paper_db.get_stats()

    console.print("\n[bold]Database Statistics[/bold]\n")

    table = Table(show_header=False, box=None)
    table.add_column("Metric", style="cyan")
    table.add_column("Value")

    table.add_row("Total papers", f"{stats['total_papers']:,}")
    table.add_row("Papers with abstracts", f"{stats['papers_with_abstracts']:,}")

    if stats["year_range"]:
        table.add_row("Year range", f"{stats['year_range'][0]} - {stats['year_range'][1]}")

    # Vector search status
    if agent.paper_search.is_available:
        table.add_row(
            "Vector search",
            f"[green]✓ Enabled[/green] ({agent.paper_search.count_documents():,} embedded)",
        )
    else:
        table.add_row("Vector search", f"[yellow]✗ Not available[/yellow]")

    console.print(table)

    if stats["top_journals"]:
        console.print("\n[bold]Top Journals[/bold]")
        for name, count in stats["top_journals"]:
            console.print(f"  {name}: {count:,}")

    console.print()


def main() -> None:
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="RAiner - Research Assistant")
    subparsers = parser.add_subparsers(dest="command")

    # --- Batch subcommand ---
    batch_parser = subparsers.add_parser("batch", help="Batch process files non-interactively")
    batch_parser.add_argument(
        "workflow",
        choices=["feedback", "feedback_hyp_rd", "review"],
        help="Workflow to run on each file",
    )
    batch_parser.add_argument("files", nargs="+", help="Files to process")
    batch_parser.add_argument(
        "-e", "--extra-prompt", type=str, default="", help="Extra prompt appended to workflow"
    )
    batch_parser.add_argument(
        "-w", "--workers", type=int, default=2, help="Parallel workers (default: 2)"
    )
    batch_parser.add_argument("-o", "--output-dir", type=str, help="Override output directory")
    batch_parser.add_argument("-c", "--config", type=str, help="Path to config file")
    batch_parser.add_argument(
        "-p",
        "--provider",
        choices=["ollama", "ollama-cloud", "openrouter", "openai", "anthropic", "google"],
        help="LLM provider to use",
    )
    batch_parser.add_argument("--model", type=str, help="Model name (overrides config default)")

    # --- Default interactive mode (no subcommand) ---
    parser.add_argument("-c", "--config", type=str, help="Path to config file")
    parser.add_argument(
        "-m",
        "--mode",
        choices=["feedback", "feedback_hyp_rd", "writing", "review", "search", "exam-review"],
        help="Start in specific mode",
    )
    parser.add_argument(
        "-p",
        "--provider",
        choices=["ollama", "ollama-cloud", "openrouter", "openai", "anthropic", "google"],
        help="LLM provider to use",
    )
    parser.add_argument("--model", type=str, help="Model name (overrides config default)")
    parser.add_argument("-r", "--resume", type=str, help="Resume session by ID")
    parser.add_argument("-l", "--load", type=str, help="Load a document file")

    args = parser.parse_args()

    # --- Route to batch mode ---
    if args.command == "batch":
        from .batch import run_batch

        run_batch(
            workflow=args.workflow,
            files=args.files,
            extra_prompt=args.extra_prompt,
            workers=args.workers,
            config_path=args.config,
            provider=args.provider,
            model=args.model,
            output_dir=args.output_dir,
        )
        return

    # Load config
    config = load_config(args.config) if args.config else load_config()

    # Override provider/model from CLI args
    if args.provider:
        config.provider.name = args.provider  # type: ignore
    if args.model:
        config.provider.model = args.model

    set_config(config)

    print_welcome()

    # Session management
    session_manager = SessionManager()

    # Student tracking DB (lazy — created on first use or /register)
    student_db: StudentDB | None = None

    def get_student_db() -> StudentDB:
        nonlocal student_db
        if student_db is None:
            student_db = StudentDB()
        return student_db

    # Resume or new session
    if args.resume:
        memory = ConversationMemory.load(args.resume)
        if memory is None:
            console.print(f"[red]Session not found: {args.resume}[/red]")
            return
        mode = memory.mode
        console.print(f"[green]Resumed session {args.resume} ({mode} mode)[/green]")
    else:
        # Start in a neutral default mode; workflows are driven by commands
        mode = args.mode or "search"
        memory = ConversationMemory.new(mode=mode)

    # Initialize agent
    agent = ResearchAgent(mode=mode, memory=memory)  # type: ignore

    # Check vector search status
    if not agent.paper_search.is_available:
        console.print(
            Panel(
                f"[yellow]Vector search not available.[/yellow]\n"
                f"{agent.paper_search.init_error}\n\n"
                f"Falling back to keyword search. Run:\n"
                f"[cyan]rainer-embed --duckdb <path> --chroma <path>[/cyan]",
                title="Warning",
                border_style="yellow",
            )
        )

    # Load file if specified
    if args.load:
        content, metadata = load_file(args.load)
        if content:
            result = agent.load_draft(content, name=Path(args.load).name, metadata=metadata)
            console.print(f"[green]{result}[/green]")

    # Setup prompt history
    history_path = Path.home() / ".local" / "share" / "rainer" / "history"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    session: PromptSession = PromptSession(history=FileHistory(str(history_path)))

    # Show session info
    search_status = "vector" if agent.paper_search.is_available else "keyword"
    console.print(
        f"[dim]Session: {memory.session_id} | Mode: {mode} | "
        f"Provider: {agent.provider_type}/{agent.model} | "
        f"Papers: {agent.paper_db.count_papers():,} | Search: {search_status}[/dim]\n"
    )

    # Main loop
    while True:
        try:
            # Get input
            prompt_text = f"[{mode}] > "
            user_input = session.prompt(prompt_text).strip()

            if not user_input:
                continue

            # Handle commands
            if user_input.startswith("/"):
                parts = user_input.split(maxsplit=1)
                cmd = parts[0].lower()
                cmd_arg = parts[1] if len(parts) > 1 else ""

                if cmd in ("/quit", "/exit", "/q"):
                    console.print("[dim]Goodbye![/dim]")
                    break

                elif cmd == "/help":
                    print_help()

                elif cmd == "/mode":
                    if cmd_arg in ("feedback", "feedback_hyp_rd", "writing", "review", "search"):
                        mode = cmd_arg  # type: ignore
                        memory = ConversationMemory.new(mode=mode)
                        agent = ResearchAgent(mode=mode, memory=memory)  # type: ignore
                        hint = (
                            " (advanced; prefer /review and /feedback for one-shot workflows)"
                            if cmd_arg in ("feedback", "writing", "review", "search")
                            else ""
                        )
                        console.print(
                            f"[green]Switched to {mode} mode{hint}[/green]"
                        )
                    else:
                        console.print(
                            "[yellow]Usage: /mode <feedback|feedback_hyp_rd|writing|review|search>[/yellow]"
                        )

                elif cmd == "/sessions":
                    list_sessions(session_manager)

                elif cmd == "/resume":
                    if cmd_arg:
                        new_memory = ConversationMemory.load(cmd_arg)
                        if new_memory:
                            memory = new_memory
                            mode = memory.mode  # type: ignore
                            agent = ResearchAgent(mode=mode, memory=memory)  # type: ignore
                            console.print(f"[green]Resumed session {cmd_arg}[/green]")
                        else:
                            console.print(f"[red]Session not found: {cmd_arg}[/red]")
                    else:
                        console.print("[yellow]Usage: /resume <session_id>[/yellow]")

                elif cmd == "/load":
                    if cmd_arg:
                        content, metadata = load_file(cmd_arg)
                        if content:
                            draft_filename = Path(cmd_arg).name
                            result = agent.load_draft(
                                content, name=draft_filename, metadata=metadata
                            )
                            console.print(f"[green]{result}[/green]")

                            # Auto-store in student DB if student name parseable
                            try:
                                student_name, draft_title = _parse_draft_name(
                                    Path(cmd_arg).stem
                                )
                                if student_name:
                                    sdb = get_student_db()
                                    student = sdb.register_student(student_name)
                                    tokens = estimate_tokens(content)
                                    draft = sdb.store_draft(
                                        student_id=student.student_id,
                                        content=content,
                                        filename=draft_filename,
                                        token_count=tokens,
                                    )
                                    # Store references for later use in /feedback
                                    memory.set_context("tracked_student_id", student.student_id)
                                    memory.set_context("tracked_draft_id", draft.draft_id)
                                    memory.set_context("tracked_draft_version", draft.version)
                                    console.print(
                                        f"[dim]Tracked: {student.name} v{draft.version}[/dim]"
                                    )
                            except Exception as e:
                                console.print(
                                    f"[dim]Student tracking skipped: {e}[/dim]"
                                )
                    else:
                        console.print("[yellow]Usage: /load <filepath>[/yellow]")
                        console.print(
                            "[dim]Supports: .txt, .md, .pdf, .docx, .xlsx (pdf/docx/xlsx require docling)[/dim]"
                        )

                elif cmd == "/loadpaper":
                    # Load a reference paper (for writing mode)
                    if cmd_arg:
                        content, metadata = load_file(cmd_arg)
                        if content:
                            result = agent.load_reference_paper(
                                content, name=Path(cmd_arg).name, metadata=metadata
                            )
                            console.print(f"[green]{result}[/green]")
                    else:
                        console.print("[yellow]Usage: /loadpaper <filepath>[/yellow]")
                        console.print(
                            "[dim]Load a PDF/document as a reference paper for writing mode[/dim]"
                        )

                elif cmd == "/review":
                    if not memory.get_context("draft_loaded"):
                        console.print(
                            "[yellow]No draft loaded. Use /load <file> before running /review.[/yellow]"
                        )
                    else:
                        source_memory = memory
                        review_mode: Literal["review"] = "review"
                        memory = ConversationMemory.new(mode=review_mode)
                        for key in (
                            "draft_loaded",
                            "draft_name",
                            "draft_content",
                            "draft_metadata",
                            "draft_sections",
                            "draft_summary",
                            "draft_excerpt",
                            "eur_verification",
                            "eur_refresh_result",
                            "eur_verification_hash",
                        ):
                            value = source_memory.get_context(key)
                            if value is not None:
                                memory.set_context(key, value)
                        agent = ResearchAgent(mode=review_mode, memory=memory)  # type: ignore
                        console.print(f"[green]Started review session {memory.session_id}[/green]")
                        review_prompt = (
                            f"Run a structured review report on the loaded draft. {cmd_arg}"
                            if cmd_arg
                            else "Run a structured review report on the loaded draft."
                        )
                        console.print()
                        with console.status("[bold green]Generating review...", spinner="dots"):
                            response = agent.chat(review_prompt)
                        console.print(Markdown(response))
                        console.print()

                elif cmd == "/feedback":
                    if not memory.get_context("draft_loaded"):
                        console.print(
                            "[yellow]No draft loaded. Use /load <file> before running /feedback.[/yellow]"
                        )
                    else:
                        source_memory = memory
                        feedback_mode = mode if mode in ("feedback", "feedback_hyp_rd") else "feedback"
                        memory = ConversationMemory.new(mode=feedback_mode)
                        for key in (
                            "draft_loaded",
                            "draft_name",
                            "draft_content",
                            "draft_metadata",
                            "draft_sections",
                            "draft_summary",
                            "draft_excerpt",
                            "eur_verification",
                            "eur_refresh_result",
                            "eur_verification_hash",
                        ):
                            value = source_memory.get_context(key)
                            if value is not None:
                                memory.set_context(key, value)
                        agent = ResearchAgent(mode=feedback_mode, memory=memory)  # type: ignore
                        console.print(
                            f"[green]Started feedback session {memory.session_id}[/green]"
                        )
                        if feedback_mode == "feedback_hyp_rd":
                            base_feedback = "Run a structured hypothesis and research design feedback report on the loaded draft."
                        else:
                            base_feedback = "Run a structured student-facing feedback report on the loaded draft."
                        feedback_prompt = (
                            f"{base_feedback} {cmd_arg}" if cmd_arg else base_feedback
                        )
                        console.print()
                        with console.status("[bold green]Generating feedback...", spinner="dots"):
                            response = agent.chat(feedback_prompt)
                        console.print(Markdown(response))
                        console.print()

                        # Auto-store feedback in student DB
                        tracked_draft_id = source_memory.get_context("tracked_draft_id")
                        if tracked_draft_id:
                            try:
                                sdb = get_student_db()
                                run = sdb.store_feedback(
                                    draft_id=tracked_draft_id,
                                    mode=feedback_mode,
                                    feedback_text=response,
                                    provider=agent.provider_type,
                                    model=agent.model,
                                    session_id=memory.session_id,
                                )
                                # Carry tracking context into the new session
                                memory.set_context("tracked_draft_id", tracked_draft_id)
                                memory.set_context("tracked_feedback_run_id", run.run_id)
                                student_id = source_memory.get_context("tracked_student_id")
                                if student_id:
                                    memory.set_context("tracked_student_id", student_id)
                                console.print(
                                    f"[dim]Feedback stored (run {run.run_id})[/dim]"
                                )
                            except Exception as e:
                                console.print(
                                    f"[dim]Feedback tracking skipped: {e}[/dim]"
                                )

                elif cmd == "/exam-review":
                    if not memory.get_context("draft_loaded"):
                        console.print(
                            "[yellow]No exam loaded. Use /load <file> before running /exam-review.[/yellow]"
                        )
                        console.print(
                            "[dim]Supports: .pdf, .docx, .xlsx, .txt, .md[/dim]"
                        )
                    else:
                        source_memory = memory
                        exam_review_mode: Literal["exam-review"] = "exam-review"
                        memory = ConversationMemory.new(mode=exam_review_mode)
                        for key in (
                            "draft_loaded",
                            "draft_name",
                            "draft_content",
                            "draft_metadata",
                            "draft_sections",
                            "draft_summary",
                            "draft_excerpt",
                        ):
                            value = source_memory.get_context(key)
                            if value is not None:
                                memory.set_context(key, value)
                        agent = ResearchAgent(mode=exam_review_mode, memory=memory)  # type: ignore
                        console.print(
                            f"[green]Started exam-review session {memory.session_id}[/green]"
                        )
                        exam_prompt = (
                            f"Run a structured exam quality review on the loaded exam. {cmd_arg}"
                            if cmd_arg
                            else "Run a structured exam quality review on the loaded exam."
                        )
                        console.print()
                        with console.status("[bold green]Reviewing exam...", spinner="dots"):
                            response = agent.chat(exam_prompt)
                        console.print(Markdown(response))
                        console.print()

                elif cmd == "/papers":
                    # Show loaded reference papers
                    summary = agent.get_reference_papers_summary()
                    console.print(summary)

                elif cmd == "/save":
                    writer = MarkdownWriter(citation_formatter=agent.citation_formatter)
                    writer.set_title(f"Session {memory.session_id}")

                    # Add conversation
                    for msg in memory.session.messages:
                        if msg.role == "user":
                            writer.add_section("User", msg.content)
                        elif msg.role == "assistant":
                            writer.add_text(msg.content)

                    filename = cmd_arg if cmd_arg else None
                    filepath = writer.write(filename)
                    console.print(f"[green]Saved to: {filepath}[/green]")

                elif cmd == "/refs":
                    refs = agent.get_references()
                    if refs:
                        console.print(Markdown(refs))
                    else:
                        console.print("[dim]No citations yet.[/dim]")

                elif cmd == "/bibtex":
                    bibtex = agent.get_bibtex()
                    if bibtex:
                        console.print(Panel(bibtex, title="BibTeX", border_style="green"))
                    else:
                        console.print("[dim]No citations yet.[/dim]")

                elif cmd == "/stats":
                    show_stats(agent)

                elif cmd == "/info":
                    # Show basic session and draft status
                    draft_loaded = memory.get_context("draft_loaded") or False
                    draft_name = memory.get_context("draft_name") or "None"
                    mode_label = mode
                    console.print(
                        f"[dim]Session: {memory.session_id} | Base mode: {mode_label} | "
                        f"Draft loaded: {'yes' if draft_loaded else 'no'} | "
                        f"Draft name: {draft_name}[/dim]"
                    )

                elif cmd == "/provider":
                    if cmd_arg:
                        valid = [
                            "ollama",
                            "ollama-cloud",
                            "openrouter",
                            "openai",
                            "anthropic",
                            "google",
                        ]
                        parts = cmd_arg.split(maxsplit=1)
                        new_provider = parts[0]
                        new_model = parts[1] if len(parts) > 1 else None
                        if new_provider in valid:
                            try:
                                agent.switch_provider(new_provider, new_model)
                                msg = f"[green]Switched to {new_provider}"
                                if new_model:
                                    msg += f" ({new_model})"
                                console.print(msg + "[/green]")
                            except ValueError as e:
                                console.print(f"[red]{e}[/red]")
                        else:
                            console.print(
                                f"[yellow]Invalid provider. Choose: {', '.join(valid)}[/yellow]"
                            )
                    else:
                        console.print(
                            f"[cyan]Current: {agent.provider_type} / {agent.model}[/cyan]"
                        )

                elif cmd == "/model":
                    if cmd_arg:
                        try:
                            agent.switch_provider(agent.provider_type, cmd_arg)
                            console.print(f"[green]Switched to model: {cmd_arg}[/green]")
                        except ValueError as e:
                            console.print(f"[red]{e}[/red]")
                    else:
                        console.print(f"[cyan]Current model: {agent.model}[/cyan]")

                elif cmd == "/register":
                    if cmd_arg:
                        parts = cmd_arg.split(maxsplit=1)
                        # Check if last part looks like an email
                        tokens_list = cmd_arg.rsplit(maxsplit=1)
                        if len(tokens_list) == 2 and "@" in tokens_list[1]:
                            reg_name = tokens_list[0]
                            reg_email = tokens_list[1]
                        else:
                            reg_name = cmd_arg
                            reg_email = None
                        sdb = get_student_db()
                        student = sdb.register_student(reg_name, email=reg_email)
                        console.print(
                            f"[green]Registered: {student.name}"
                            + (f" ({student.email})" if student.email else "")
                            + f" [id={student.student_id}][/green]"
                        )
                    else:
                        console.print("[yellow]Usage: /register <name> [email][/yellow]")

                elif cmd == "/students":
                    sdb = get_student_db()
                    overview = sdb.get_progress_overview()
                    if not overview:
                        console.print("[dim]No students registered yet. Use /register <name>[/dim]")
                    else:
                        table = Table(title="Student Progress")
                        table.add_column("Name", style="bold")
                        table.add_column("Email", style="dim")
                        table.add_column("Drafts", justify="center")
                        table.add_column("Latest", justify="center")
                        table.add_column("Feedback runs", justify="center")
                        table.add_column("Last activity", style="dim")
                        for row in overview:
                            last = row["last_submission"] or row["last_feedback"] or ""
                            if last:
                                last = last[:10]  # Date only
                            table.add_row(
                                row["name"],
                                row["email"] or "",
                                str(row["num_drafts"] or 0),
                                f"v{row['latest_version']}" if row["latest_version"] else "-",
                                str(row["num_feedback_runs"] or 0),
                                last,
                            )
                        console.print(table)

                elif cmd == "/versions":
                    sdb = get_student_db()
                    if cmd_arg:
                        student = sdb.get_student_by_name(cmd_arg)
                    else:
                        # Try current draft's student
                        sid = memory.get_context("tracked_student_id")
                        student = None
                        if sid:
                            students = sdb.list_students()
                            student = next((s for s in students if s.student_id == sid), None)
                    if not student:
                        console.print("[yellow]Student not found. Usage: /versions <student name>[/yellow]")
                    else:
                        drafts = sdb.get_drafts_for_student(student.student_id)
                        if not drafts:
                            console.print(f"[dim]No drafts stored for {student.name}[/dim]")
                        else:
                            table = Table(title=f"Drafts: {student.name}")
                            table.add_column("Version", justify="center")
                            table.add_column("Filename")
                            table.add_column("Tokens", justify="right")
                            table.add_column("Loaded", style="dim")
                            table.add_column("Feedback", justify="center")
                            for d in drafts:
                                fb = sdb.get_feedback_for_draft(d.draft_id)
                                table.add_row(
                                    f"v{d.version}",
                                    d.filename,
                                    f"{d.token_count:,}",
                                    d.loaded_at[:10] if d.loaded_at else "",
                                    str(len(fb)) if fb else "-",
                                )
                            console.print(table)

                elif cmd == "/import-feedback":
                    run_id = memory.get_context("tracked_feedback_run_id")
                    if not run_id:
                        console.print(
                            "[yellow]No feedback run to update. Run /feedback first.[/yellow]"
                        )
                    elif not cmd_arg:
                        console.print("[yellow]Usage: /import-feedback <filepath>[/yellow]")
                    else:
                        try:
                            edited_path = Path(cmd_arg.strip().strip("'\"")).expanduser()
                            edited_text = edited_path.read_text(encoding="utf-8")
                            sdb = get_student_db()
                            sdb.store_edited_feedback(run_id, edited_text)
                            console.print(
                                f"[green]Imported edited feedback for run {run_id}[/green]"
                            )
                        except FileNotFoundError:
                            console.print(f"[red]File not found: {cmd_arg}[/red]")
                        except Exception as e:
                            console.print(f"[red]Import failed: {e}[/red]")

                elif cmd == "/clear":
                    memory = ConversationMemory.new(mode=mode)
                    agent = ResearchAgent(mode=mode, memory=memory)  # type: ignore
                    console.print("[green]Started new session[/green]")

                else:
                    console.print(f"[yellow]Unknown command: {cmd}[/yellow]")

                continue

            # Regular chat
            console.print()  # Spacing
            with console.status("[bold green]Thinking...", spinner="dots"):
                response = agent.chat(user_input)

            console.print(Markdown(response))
            console.print()

        except KeyboardInterrupt:
            console.print("\n[dim]Use /quit to exit[/dim]")
        except EOFError:
            break
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")


if __name__ == "__main__":
    main()
