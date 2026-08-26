"""Query-time embedding + semantic search (Step 7).

Split out from the ingestion pipeline (app.rag.ingest) because it's the
query-time half of the RAG pipeline: search_manufacturing_knowledge
(tools/rag_tools.py) imports only this module, not the ingestion script.
Reuses the same Qdrant client and embedding model cache as app.rag.ingest
(via get_client()/get_embedding_model()) so a running process never opens a
second client or loads a second copy of the model.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.rag.ingest import get_client, get_embedding_model, load_qdrant_config


def embed_query(text: str) -> list[float]:
    """Embed a single search query.

    fastembed models are typically trained with separate passage/query
    encodings; `query_embed` uses the query-side encoding when the model
    supports one (falls back to the same encoding otherwise).
    """
    return next(iter(get_embedding_model().query_embed([text]))).tolist()


@dataclass(frozen=True)
class SearchResult:
    document_name: str
    document_path: str
    section_title: str
    text: str
    score: float


def search(query: str, *, top_k: int = 5) -> list[SearchResult]:
    """Embed `query` and return the top_k most similar chunks from Qdrant."""
    config = load_qdrant_config()
    client = get_client()

    query_vector = embed_query(query)
    response = client.query_points(
        collection_name=config.collection,
        query=query_vector,
        limit=top_k,
        with_payload=True,
    )

    return [
        SearchResult(
            document_name=point.payload["document_name"],
            document_path=point.payload["document_path"],
            section_title=point.payload["section_title"],
            text=point.payload["text"],
            score=point.score,
        )
        for point in response.points
    ]
