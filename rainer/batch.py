"""Batch processing for RAiner workflows."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table


@dataclass
class BatchResult:
    """Result from processing a single file."""

    filepath: Path
    success: bool
    output_path: Path | None = None
    error: str | None = None
    session_id: str | None = None


def process_single_file(
    filepath: str,
    workflow: str,
    extra_prompt: str,
    config_path: str | None,
    provider: str | None,
    model: str | None,
    output_dir: str | None,
) -> BatchResult:
    """Process a single file through the feedback/review workflow.

    Runs in a worker process: constructs its own config, agent, and memory.
    """
    from .agent import ResearchAgent
    from .cli import LoadError, load_file_raw
    from .config import load_config, set_config
    from .memory import ConversationMemory
    from .output import MarkdownWriter, _parse_draft_name

    path = Path(filepath)

    try:
        # Init config in worker
        config = load_config(config_path) if config_path else load_config()
        if provider:
            config.provider.name = provider  # type: ignore
        if model:
            config.provider.model = model
        set_config(config)

        # Load document
        content, metadata = load_file_raw(str(path))

        # Create session and agent
        memory = ConversationMemory.new(mode=workflow)
        agent = ResearchAgent(mode=workflow, memory=memory)  # type: ignore

        # Load draft into agent
        agent.load_draft(content, name=path.name, metadata=metadata)

        # Build prompt
        base_prompt = {
            "feedback": "Run a structured student-facing feedback report on the loaded draft.",
            "feedback_hyp_rd": "Run a structured hypothesis and research design feedback report on the loaded draft.",
            "review": "Run a structured review report on the loaded draft.",
        }.get(workflow, "Run a structured report on the loaded draft.")

        prompt = f"{base_prompt} {extra_prompt}".strip() if extra_prompt else base_prompt

        # Run the workflow
        response = agent.chat(prompt)

        # Auto-save output
        resolved_output_dir = output_dir or config.output.directory
        writer = MarkdownWriter(
            output_dir=resolved_output_dir,
            citation_formatter=agent.citation_formatter,
        )

        draft_name = path.stem
        student_name, draft_title = _parse_draft_name(draft_name)

        writer.set_title(f"Feedback on: {draft_name}")
        writer.add_metadata("date", datetime.now().strftime("%Y-%m-%d"))
        writer.add_metadata("type", workflow)
        writer.set_draft_info(title=draft_title or draft_name, student=student_name)
        writer.add_text(response)

        output_path = writer.write()

        return BatchResult(
            filepath=path,
            success=True,
            output_path=output_path,
            session_id=memory.session_id,
        )

    except Exception as e:
        return BatchResult(
            filepath=path,
            success=False,
            error=f"{type(e).__name__}: {e}",
        )


def run_batch(
    workflow: str,
    files: list[str],
    extra_prompt: str = "",
    workers: int = 2,
    config_path: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    output_dir: str | None = None,
) -> list[BatchResult]:
    """Run batch processing across multiple files."""
    console = Console()

    # Validate files exist
    valid_files: list[str] = []
    for f in files:
        p = Path(f).expanduser()
        if p.exists():
            valid_files.append(str(p))
        else:
            console.print(f"[yellow]Skipping (not found): {f}[/yellow]")

    if not valid_files:
        console.print("[red]No valid files to process.[/red]")
        return []

    console.print(
        f"[bold]Batch {workflow}[/bold]: {len(valid_files)} file(s), {workers} worker(s)\n"
    )

    results: list[BatchResult] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        overall = progress.add_task("Processing files", total=len(valid_files))

        if workers <= 1:
            # Sequential
            for filepath in valid_files:
                name = Path(filepath).name
                progress.update(overall, description=f"Processing: {name}")
                result = process_single_file(
                    filepath, workflow, extra_prompt, config_path, provider, model, output_dir
                )
                results.append(result)
                progress.advance(overall)
        else:
            # Parallel
            with ProcessPoolExecutor(max_workers=workers) as executor:
                future_to_file = {}
                for filepath in valid_files:
                    future = executor.submit(
                        process_single_file,
                        filepath,
                        workflow,
                        extra_prompt,
                        config_path,
                        provider,
                        model,
                        output_dir,
                    )
                    future_to_file[future] = filepath

                for future in as_completed(future_to_file):
                    filepath = future_to_file[future]
                    try:
                        result = future.result()
                    except Exception as e:
                        result = BatchResult(
                            filepath=Path(filepath),
                            success=False,
                            error=f"Worker error: {e}",
                        )
                    results.append(result)
                    progress.advance(overall)

    # Print summary
    console.print()
    _print_summary(console, results)
    return results


def _print_summary(console: Console, results: list[BatchResult]) -> None:
    """Print a summary table of batch results."""
    table = Table(title="Batch Results", show_header=True)
    table.add_column("File", style="cyan")
    table.add_column("Status")
    table.add_column("Output")

    succeeded = 0
    for r in results:
        if r.success:
            succeeded += 1
            table.add_row(r.filepath.name, "[green]OK[/green]", str(r.output_path))
        else:
            table.add_row(r.filepath.name, "[red]FAILED[/red]", r.error or "Unknown error")

    console.print(table)
    console.print(f"\n[bold]{succeeded}/{len(results)} files processed successfully.[/bold]")
