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
from .config import get_config, load_config, set_config
from .memory import ConversationMemory, SessionManager
from .output import MarkdownWriter

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
            "Commands: /help, /mode, /provider, /model, /sessions, /save, /refs, /quit",
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
| `/mode <mode>` | Switch mode (feedback, writing, review, search) |
| `/provider [name] [model]` | Show/switch LLM provider |
| `/model <name>` | Switch model |
| `/sessions` | List recent sessions |
| `/resume <id>` | Resume a previous session |
| `/load <file>` | Load a draft/document for feedback/review |
| `/loadpaper <file>` | Load a reference paper (PDF) for writing mode |
| `/papers` | Show loaded reference papers |
| `/save [filename]` | Save conversation to markdown |
| `/refs` | Show current reference list |
| `/bibtex` | Show BibTeX entries |
| `/stats` | Show database statistics |
| `/clear` | Clear conversation (new session) |
| `/quit` or `/exit` | Exit |

## Modes

- **feedback**: Review student drafts, suggest citations, verify data feasibility
- **writing**: Help write papers with proper citations (load PDFs with /loadpaper)
- **review**: Write review reports for manuscripts
- **search**: Literature search, get BibTeX entries

## Providers

- **ollama**: Local Ollama instance
- **ollama-cloud**: Ollama Cloud API (OLLAMA_API_KEY)
- **openai**: OpenAI API (OPENAI_API_KEY)
- **openrouter**: OpenRouter API (OPENROUTER_API_KEY)
- **anthropic**: Anthropic API (ANTHROPIC_API_KEY)
- **google**: Google Gemini API (GOOGLE_API_KEY)

## Tips

- Use `/load` for drafts (feedback/review) or `/loadpaper` for reference PDFs (writing)
- Supports PDF, DOCX, TXT, MD (PDF/DOCX require: `poetry install --with pdf`)
- Use specific queries: "papers about market microstructure after 2020"
- Ask for citations: "what papers support the claim that..."
- Switch providers: `/provider openai gpt-4o`
"""
    console.print(Markdown(help_text))


def select_mode() -> Literal["feedback", "writing", "review", "search"]:
    """Interactive mode selection."""
    console.print("\n[bold]Select a mode:[/bold]\n")

    modes = [
        ("feedback", "Student Feedback", "Review drafts, suggest citations"),
        ("writing", "Writing Assistance", "Help write papers with citations"),
        ("review", "Review Reports", "Write reviewer reports"),
        ("search", "Literature Search", "Find papers, get BibTeX"),
    ]

    table = Table(show_header=True, header_style="bold")
    table.add_column("#", style="dim", width=3)
    table.add_column("Mode", style="cyan")
    table.add_column("Description")

    for i, (key, name, desc) in enumerate(modes, 1):
        table.add_row(str(i), name, desc)

    console.print(table)

    while True:
        choice = console.input("\n[bold]Enter number (1-4):[/bold] ").strip()
        if choice in ("1", "2", "3", "4"):
            idx = int(choice) - 1
            selected = modes[idx][0]
            console.print(f"\n[green]Selected: {modes[idx][1]}[/green]\n")
            return selected  # type: ignore
        console.print("[red]Invalid choice. Enter 1-4.[/red]")


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


def load_file(filepath: str) -> tuple[str | None, dict | None]:
    """
    Load a file and return its contents.

    Supports PDF, DOCX, and text formats via docling (if installed).

    Returns: (content, metadata) tuple
    """
    from .pdf import parse_document, is_docling_available

    path = Path(filepath).expanduser()
    if not path.exists():
        console.print(f"[red]File not found: {filepath}[/red]")
        return None, None

    result = parse_document(path)

    if result.method == "docling_not_installed":
        console.print(f"[yellow]PDF/DOCX parsing requires docling. Install with:[/yellow]")
        console.print("[yellow]  poetry install --with pdf[/yellow]")
        console.print(f"[dim]For text files, rename to .txt or .md[/dim]")
        return None, None

    if result.method.startswith("error:"):
        console.print(f"[red]Error reading file: {result.method}[/red]")
        return None, None

    if result.method == "unsupported_format":
        supported = ".txt, .md, .tex"
        if is_docling_available():
            supported += ", .pdf, .docx, .pptx, .xlsx, .html"
        console.print(f"[red]Unsupported file format: {path.suffix}[/red]")
        console.print(f"[dim]Supported formats: {supported}[/dim]")
        return None, None

    if result.method == "docling":
        console.print(f"[dim]Parsed {path.suffix} via docling[/dim]")

    return result.content, result.metadata


def show_stats(agent: ResearchAgent) -> None:
    """Show database statistics."""
    stats = agent.paper_db.get_stats()
    
    console.print("\n[bold]Database Statistics[/bold]\n")
    
    table = Table(show_header=False, box=None)
    table.add_column("Metric", style="cyan")
    table.add_column("Value")
    
    table.add_row("Total papers", f"{stats['total_papers']:,}")
    table.add_row("Papers with abstracts", f"{stats['papers_with_abstracts']:,}")
    
    if stats['year_range']:
        table.add_row("Year range", f"{stats['year_range'][0]} - {stats['year_range'][1]}")
    
    # Vector search status
    if agent.paper_search.is_available:
        table.add_row("Vector search", f"[green]✓ Enabled[/green] ({agent.paper_search.count_documents():,} embedded)")
    else:
        table.add_row("Vector search", f"[yellow]✗ Not available[/yellow]")
    
    console.print(table)
    
    if stats['top_journals']:
        console.print("\n[bold]Top Journals[/bold]")
        for name, count in stats['top_journals']:
            console.print(f"  {name}: {count:,}")
    
    console.print()


def main() -> None:
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="RAiner - Research Assistant")
    parser.add_argument("-c", "--config", type=str, help="Path to config file")
    parser.add_argument(
        "-m",
        "--mode",
        choices=["feedback", "writing", "review", "search"],
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

    # Resume or new session
    if args.resume:
        memory = ConversationMemory.load(args.resume)
        if memory is None:
            console.print(f"[red]Session not found: {args.resume}[/red]")
            return
        mode = memory.mode
        console.print(f"[green]Resumed session {args.resume} ({mode} mode)[/green]")
    else:
        # Mode selection
        if args.mode:
            mode = args.mode
        else:
            mode = select_mode()
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
                    if cmd_arg in ("feedback", "writing", "review", "search"):
                        mode = cmd_arg  # type: ignore
                        memory = ConversationMemory.new(mode=mode)
                        agent = ResearchAgent(mode=mode, memory=memory)  # type: ignore
                        console.print(f"[green]Switched to {mode} mode[/green]")
                    else:
                        console.print(
                            "[yellow]Usage: /mode <feedback|writing|review|search>[/yellow]"
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
                            result = agent.load_draft(content, name=Path(cmd_arg).name, metadata=metadata)
                            console.print(f"[green]{result}[/green]")
                    else:
                        console.print("[yellow]Usage: /load <filepath>[/yellow]")
                        console.print("[dim]Supports: .txt, .md, .pdf, .docx (pdf/docx require docling)[/dim]")

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
                        console.print("[dim]Load a PDF/document as a reference paper for writing mode[/dim]")

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

                elif cmd == "/provider":
                    if cmd_arg:
                        valid = ["ollama", "ollama-cloud", "openrouter", "openai", "anthropic", "google"]
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
