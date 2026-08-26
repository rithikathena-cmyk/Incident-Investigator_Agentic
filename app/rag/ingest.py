"""RAG pipeline (Step 7): config, document loading, chunking, embeddings,
and Qdrant collection/upsert - plus the ingestion script itself.

Run with:  python -m app.rag.ingest

    Documents -> Document loading -> Text splitting/chunking -> Embeddings
    -> Qdrant

No Claude Agent SDK dependency anywhere in this module - the RAG pipeline is
a plain Python + Qdrant + fastembed stack the SDK only touches through a
tool (tools/rag_tools.py). Query-time embedding + search live in
app.rag.search, which imports the shared client/config helpers from here so
there's only one Qdrant client and one embedding model cache in the process.

Recreates the collection each run, so it's safe to run repeatedly during
development (mirrors app.database.seed's drop_existing behavior).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from fastembed import TextEmbedding
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

load_dotenv()

# --- Config -----------------------------------------------------------

_REQUIRED_VARS = ("QDRANT_HOST", "QDRANT_PORT", "QDRANT_COLLECTION")

# Small (384-dim), fast, no API key required - runs locally via fastembed's
# ONNX runtime. See https://qdrant.github.io/fastembed/ for the supported
# model list (confirmed against the installed package, not guessed).
EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIMENSIONS = 384

DOCUMENTS_DIR = "data/documents"

# Chunking parameters (characters, not tokens - see chunk_document() below).
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150

# rag/ingest.py -> rag/ -> app/ -> project root
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class QdrantConfig:
    host: str
    port: int
    collection: str


def load_qdrant_config() -> QdrantConfig:
    missing = [name for name in _REQUIRED_VARS if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            "Missing required Qdrant environment variable(s): "
            f"{', '.join(missing)}.\n"
            "Copy .env.example to .env and fill in real values, then start "
            "Qdrant with `docker compose up -d`."
        )

    return QdrantConfig(
        host=os.environ["QDRANT_HOST"],
        port=int(os.environ["QDRANT_PORT"]),
        collection=os.environ["QDRANT_COLLECTION"],
    )


# --- Document loading (stage 1 of 3: load -> chunk -> embed) -----------


@dataclass(frozen=True)
class Document:
    name: str
    """File name, e.g. 'motor_failure_sop.md' - what the tool reports as the
    source document."""
    path: str
    """Path relative to the project root, for source information."""
    text: str


def documents_dir() -> Path:
    return _PROJECT_ROOT / DOCUMENTS_DIR


def load_documents() -> list[Document]:
    """Load every .md file in data/documents/, sorted by filename for a
    deterministic, reproducible ingestion order.
    """
    directory = documents_dir()
    if not directory.is_dir():
        raise FileNotFoundError(f"Documents directory not found: {directory}")

    documents = []
    for file_path in sorted(directory.glob("*.md")):
        text = file_path.read_text(encoding="utf-8")
        documents.append(
            Document(
                name=file_path.name,
                path=str(file_path.relative_to(_PROJECT_ROOT)).replace("\\", "/"),
                text=text,
            )
        )
    return documents


# --- Text splitting / chunking (stage 2 of 3) ---------------------------
#
# Splits a markdown document into chunks along its ## section boundaries
# (the documents are written with clear headers), further splitting any
# section that's still too long by paragraph, with a small character
# overlap so a chunk doesn't lose context at its boundary. No external
# chunking library - simple enough to hand-roll and easy to inspect.

_SECTION_SPLIT_RE = re.compile(r"(?m)^(?=## )")


@dataclass(frozen=True)
class Chunk:
    document_name: str
    document_path: str
    chunk_index: int
    section_title: str
    text: str


def _split_paragraphs(text: str, size: int, overlap: int) -> list[str]:
    """Greedily pack paragraphs into chunks of at most `size` characters,
    carrying the last `overlap` characters of a chunk into the next one.
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= size or not current:
            current = candidate
            continue
        chunks.append(current)
        carry = current[-overlap:] if overlap else ""
        current = f"{carry}\n\n{paragraph}" if carry else paragraph
    if current:
        chunks.append(current)
    return chunks


def chunk_document(document: Document, *, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[Chunk]:
    """Split one document into Chunks along ## section boundaries."""
    title_line, _, body = document.text.partition("\n")
    title = title_line.lstrip("#").strip() or document.name

    sections = [s for s in _SECTION_SPLIT_RE.split(body) if s.strip()]
    if not sections:
        sections = [body]

    chunks: list[Chunk] = []
    for section in sections:
        heading_line, _, _ = section.partition("\n")
        section_title = heading_line.lstrip("#").strip() or title

        for piece in _split_paragraphs(section.strip(), size, overlap):
            chunks.append(
                Chunk(
                    document_name=document.name,
                    document_path=document.path,
                    chunk_index=len(chunks),
                    section_title=section_title,
                    text=piece,
                )
            )
    return chunks


def chunk_documents(documents: list[Document], *, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[Chunk]:
    chunks: list[Chunk] = []
    for document in documents:
        chunks.extend(chunk_document(document, size=size, overlap=overlap))
    return chunks


# --- Embeddings (stage 3 of 3) ------------------------------------------
#
# Wraps fastembed (local, ONNX-based, no API key or Claude Agent SDK
# involved) to turn text into vectors. The model is loaded once and reused -
# loading it is the slow part (downloads/caches the ONNX model on first
# use). app.rag.search imports get_embedding_model() to embed queries with
# the same cached model instead of loading a second copy.

_model: TextEmbedding | None = None


def get_embedding_model() -> TextEmbedding:
    global _model
    if _model is None:
        _model = TextEmbedding(model_name=EMBEDDING_MODEL_NAME)
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts (e.g. document chunks)."""
    return [vector.tolist() for vector in get_embedding_model().embed(texts)]


# --- Qdrant vector store --------------------------------------------------
#
# Owns the collection lifecycle (create, upsert). This, together with
# app.rag.search, is the only code that talks to Qdrant - no Claude Agent
# SDK code here.

_client: QdrantClient | None = None


def get_client() -> QdrantClient:
    global _client
    if _client is None:
        config = load_qdrant_config()
        _client = QdrantClient(host=config.host, port=config.port)
    return _client


def ensure_collection(*, recreate: bool = False) -> str:
    """Create the collection if it doesn't exist. recreate=True drops and
    recreates it, for a clean re-ingestion during development.
    """
    config = load_qdrant_config()
    client = get_client()

    if recreate and client.collection_exists(config.collection):
        client.delete_collection(config.collection)

    if not client.collection_exists(config.collection):
        client.create_collection(
            collection_name=config.collection,
            vectors_config=VectorParams(size=EMBEDDING_DIMENSIONS, distance=Distance.COSINE),
        )
    return config.collection


def upsert_chunks(chunks: list[Chunk]) -> int:
    """Embed and upsert a batch of chunks. Returns the number of points written."""
    config = load_qdrant_config()
    client = get_client()

    vectors = embed_texts([c.text for c in chunks])
    points = [
        PointStruct(
            id=i,
            vector=vector,
            payload={
                "document_name": chunk.document_name,
                "document_path": chunk.document_path,
                "chunk_index": chunk.chunk_index,
                "section_title": chunk.section_title,
                "text": chunk.text,
            },
        )
        for i, (chunk, vector) in enumerate(zip(chunks, vectors))
    ]
    client.upsert(collection_name=config.collection, points=points)
    return len(points)


def main() -> None:
    print("[ingest] Loading documents...")
    documents = load_documents()
    for doc in documents:
        print(f"[ingest]   - {doc.name} ({len(doc.text)} chars)")

    print("[ingest] Chunking...")
    chunks = chunk_documents(documents)
    print(f"[ingest]   {len(chunks)} chunks across {len(documents)} documents")

    print("[ingest] Recreating Qdrant collection...")
    collection = ensure_collection(recreate=True)

    print("[ingest] Embedding + upserting into Qdrant...")
    count = upsert_chunks(chunks)

    print(f"[ingest] Done: {count} chunks embedded and stored in collection '{collection}'.")


if __name__ == "__main__":
    main()
