"""ChromaDB vector search for paper abstracts."""

import os
from pathlib import Path
from typing import Any

# Disable ChromaDB telemetry before importing chromadb
os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["CHROMA_TELEMETRY"] = "False"
os.environ["POSTHOG_DISABLED"] = "True"

from .config import get_config
from .papers import Paper, PaperDB


class SearchResult:
    """A search result with paper and relevance score."""

    def __init__(self, paper: Paper, score: float, matched_text: str | None = None):
        self.paper = paper
        self.score = score
        self.matched_text = matched_text

    def __repr__(self) -> str:
        return f"SearchResult({self.paper.short_cite()}, score={self.score:.3f})"


class PaperSearch:
    """Vector search over paper abstracts using ChromaDB."""

    def __init__(
        self,
        chroma_path: str | Path | None = None,
        collection_name: str | None = None,
        embedding_model: str | None = None,
        paper_db: PaperDB | None = None,
    ):
        config = get_config()

        if chroma_path is None:
            chroma_path = config.data.chroma_path
        if collection_name is None:
            collection_name = config.data.chroma_collection
        if embedding_model is None:
            embedding_model = config.embeddings.model

        self.chroma_path = Path(chroma_path).expanduser()
        self.collection_name = collection_name
        self.embedding_model_name = embedding_model
        self.paper_db = paper_db or PaperDB()

        # Lazy initialization
        self._client = None
        self._collection = None
        self._embedder = None
        self._initialized = False
        self._init_error: str | None = None

    def _initialize(self) -> bool:
        """Lazy initialization of ChromaDB and embedding model."""
        if self._initialized:
            return self._init_error is None

        self._initialized = True

        try:
            import chromadb
            from chromadb.config import Settings
            from sentence_transformers import SentenceTransformer

            # Check if ChromaDB path exists
            if not self.chroma_path.exists():
                self._init_error = (
                    f"ChromaDB not found at {self.chroma_path}. "
                    f"Run 'rainer-embed' first to create embeddings."
                )
                return False

            # Initialize ChromaDB with telemetry disabled
            self._client = chromadb.PersistentClient(
                path=str(self.chroma_path),
                settings=Settings(anonymized_telemetry=False),
            )

            # Try to get collection
            try:
                self._collection = self._client.get_collection(name=self.collection_name)
            except ValueError:
                self._init_error = (
                    f"Collection '{self.collection_name}' not found. "
                    f"Run 'rainer-embed' first to create embeddings."
                )
                return False

            # Initialize embedding model
            self._embedder = SentenceTransformer(self.embedding_model_name)

            return True

        except Exception as e:
            self._init_error = f"Failed to initialize search: {e}"
            return False

    @property
    def is_available(self) -> bool:
        """Check if vector search is available."""
        return self._initialize()

    @property
    def init_error(self) -> str | None:
        """Get initialization error message if any."""
        self._initialize()
        return self._init_error

    def embed_query(self, query: str) -> list[float]:
        """Embed a query string."""
        if not self._initialize():
            raise RuntimeError(self._init_error)
        return self._embedder.encode(query).tolist()

    def search(
        self,
        query: str,
        top_k: int | None = None,
        min_similarity: float | None = None,
        year_min: int | None = None,
        year_max: int | None = None,
    ) -> list[SearchResult]:
        """
        Search for papers relevant to a query.

        Args:
            query: Natural language search query
            top_k: Number of results to return
            min_similarity: Minimum similarity threshold (0-1)
            year_min: Filter to papers from this year or later
            year_max: Filter to papers from this year or earlier

        Returns:
            List of SearchResult objects
        """
        if not self._initialize():
            # Fall back to DuckDB fulltext search
            return self._fallback_search(query, top_k or 10)

        config = get_config()
        if top_k is None:
            top_k = config.search.top_k
        if min_similarity is None:
            min_similarity = config.search.min_similarity

        # Build where clause for filtering
        where_clause: dict[str, Any] | None = None
        if year_min is not None or year_max is not None:
            conditions = []
            if year_min is not None:
                conditions.append({"year": {"$gte": year_min}})
            if year_max is not None:
                conditions.append({"year": {"$lte": year_max}})
            if len(conditions) == 1:
                where_clause = conditions[0]
            else:
                where_clause = {"$and": conditions}

        # Query ChromaDB
        query_embedding = self.embed_query(query)

        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where_clause,
            include=["documents", "distances", "metadatas"],
        )

        # Process results
        search_results = []

        if not results["ids"] or not results["ids"][0]:
            return []

        ids = results["ids"][0]
        distances = results["distances"][0] if results["distances"] else [0] * len(ids)
        documents = results["documents"][0] if results["documents"] else [None] * len(ids)
        metadatas = results["metadatas"][0] if results["metadatas"] else [{}] * len(ids)

        for idx, (paper_id, distance, doc, meta) in enumerate(
            zip(ids, distances, documents, metadatas)
        ):
            # ChromaDB with cosine returns distance where 0 = identical
            # Convert to similarity: 1 - distance for cosine
            similarity = max(0, 1 - distance)

            if similarity < min_similarity:
                continue

            # Get full paper from DuckDB
            paper = self.paper_db.get_paper(paper_id)
            if paper is None:
                # Fall back to metadata if paper not in DuckDB
                paper = Paper(
                    id=paper_id,
                    title=meta.get("title", "Unknown"),
                    authors=meta.get("authors"),
                    year=meta.get("year"),
                    abstract=doc,
                )

            search_results.append(SearchResult(paper=paper, score=similarity, matched_text=doc))

        return search_results

    def _fallback_search(self, query: str, limit: int) -> list[SearchResult]:
        """Fallback to DuckDB fulltext/title search when ChromaDB unavailable."""
        papers = self.paper_db.fulltext_search(query, limit=limit)
        return [SearchResult(paper=p, score=0.5, matched_text=p.abstract) for p in papers]

    def search_multiple(
        self,
        queries: list[str],
        top_k_per_query: int = 5,
        deduplicate: bool = True,
    ) -> list[SearchResult]:
        """
        Search with multiple queries and combine results.

        Useful for analyzing a document with multiple key concepts.
        """
        all_results: dict[str, SearchResult] = {}

        for query in queries:
            results = self.search(query, top_k=top_k_per_query)
            for result in results:
                paper_id = result.paper.id
                if paper_id not in all_results or result.score > all_results[paper_id].score:
                    all_results[paper_id] = result

        # Sort by score
        sorted_results = sorted(all_results.values(), key=lambda r: r.score, reverse=True)

        if deduplicate:
            return sorted_results
        return sorted_results

    def find_similar_to_paper(self, paper_id: str, top_k: int = 10) -> list[SearchResult]:
        """Find papers similar to a given paper."""
        paper = self.paper_db.get_paper(paper_id)
        if paper is None or not paper.abstract:
            return []
        return self.search(paper.abstract, top_k=top_k + 1)[1:]  # Exclude self

    def count_documents(self) -> int:
        """Count documents in the collection."""
        if not self._initialize():
            return 0
        return self._collection.count()
