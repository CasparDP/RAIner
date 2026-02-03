"""Script to create ChromaDB embeddings from paper abstracts."""

import argparse
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

console = Console()


def create_embeddings(
    duckdb_path: str,
    chroma_path: str,
    collection_name: str = "paper_abstracts",
    embedding_model: str = "all-MiniLM-L6-v2",
    batch_size: int = 100,
) -> None:
    """
    Create ChromaDB embeddings from paper abstracts.
    
    Args:
        duckdb_path: Path to the DuckDB database
        chroma_path: Path where ChromaDB will be stored
        collection_name: Name for the ChromaDB collection
        embedding_model: Sentence transformer model to use
        batch_size: Number of papers to embed at once
    """
    import chromadb
    from sentence_transformers import SentenceTransformer
    
    from .papers import PaperDB
    
    console.print(f"[bold blue]RAiner Embedding Creator[/bold blue]\n")
    
    # Initialize components
    console.print(f"Loading embedding model: [cyan]{embedding_model}[/cyan]")
    embedder = SentenceTransformer(embedding_model)
    
    console.print(f"Connecting to DuckDB: [cyan]{duckdb_path}[/cyan]")
    paper_db = PaperDB(duckdb_path)
    
    # Get papers with abstracts
    console.print("Fetching papers with abstracts...")
    papers = paper_db.get_papers_with_abstracts()
    console.print(f"Found [green]{len(papers):,}[/green] papers with abstracts")
    
    if not papers:
        console.print("[yellow]No papers with abstracts found. Nothing to embed.[/yellow]")
        return
    
    # Initialize ChromaDB
    chroma_dir = Path(chroma_path).expanduser()
    chroma_dir.mkdir(parents=True, exist_ok=True)
    
    console.print(f"Creating ChromaDB at: [cyan]{chroma_dir}[/cyan]")
    client = chromadb.PersistentClient(path=str(chroma_dir))
    
    # Delete existing collection if it exists
    try:
        client.delete_collection(name=collection_name)
        console.print(f"[yellow]Deleted existing collection: {collection_name}[/yellow]")
    except ValueError:
        pass  # Collection doesn't exist
    
    # Create new collection
    collection = client.create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"}  # Use cosine similarity
    )
    
    # Process in batches
    console.print(f"\nEmbedding abstracts (batch size: {batch_size})...\n")
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Embedding papers...", total=len(papers))
        
        for i in range(0, len(papers), batch_size):
            batch = papers[i:i + batch_size]
            
            # Prepare batch data
            ids = []
            documents = []
            metadatas = []
            
            for paper in batch:
                if not paper.abstract:
                    continue
                    
                ids.append(paper.doi)
                documents.append(paper.abstract)
                metadatas.append({
                    "title": paper.title or "",
                    "authors": paper.authors or "",
                    "year": paper.year or 0,
                    "journal": paper.journal_name or "",
                    "ssrn_id": paper.ssrn_id or "",
                })
            
            if not ids:
                progress.update(task, advance=len(batch))
                continue
            
            # Generate embeddings
            embeddings = embedder.encode(documents, show_progress_bar=False).tolist()
            
            # Add to collection
            collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
            )
            
            progress.update(task, advance=len(batch))
    
    # Final stats
    console.print(f"\n[bold green]✓ Done![/bold green]")
    console.print(f"  Collection: [cyan]{collection_name}[/cyan]")
    console.print(f"  Documents: [cyan]{collection.count():,}[/cyan]")
    console.print(f"  Location: [cyan]{chroma_dir}[/cyan]")


def main():
    """CLI entry point for embedding creation."""
    parser = argparse.ArgumentParser(
        description="Create ChromaDB embeddings from paper abstracts"
    )
    parser.add_argument(
        "--duckdb",
        type=str,
        required=True,
        help="Path to DuckDB database",
    )
    parser.add_argument(
        "--chroma",
        type=str,
        required=True,
        help="Path for ChromaDB storage",
    )
    parser.add_argument(
        "--collection",
        type=str,
        default="paper_abstracts",
        help="ChromaDB collection name (default: paper_abstracts)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="all-MiniLM-L6-v2",
        help="Embedding model (default: all-MiniLM-L6-v2)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Batch size for embedding (default: 100)",
    )
    
    args = parser.parse_args()
    
    create_embeddings(
        duckdb_path=args.duckdb,
        chroma_path=args.chroma,
        collection_name=args.collection,
        embedding_model=args.model,
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    main()
