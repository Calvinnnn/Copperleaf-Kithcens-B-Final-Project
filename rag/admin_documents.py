"""Admin RAG document management for Issue #4.

Uploads and deletes documents against the SAME document directory and
ChromaDB collection used by the existing Copperleaf Kitchens RAG agent.
"""

from __future__ import annotations

import re
from pathlib import Path

import chromadb

from rag.chunking import (
    DOCUMENTS_DIR,
    create_chunks_for_pdf,
)
from rag.embeddings import create_embeddings
from rag.vector_store import (
    COLLECTION_NAME,
    DB_PATH,
    HNSW_CONFIG,
    _derive_doc_type,
)


def _safe_filename(filename: str) -> str:
    """Prevent path traversal and allow PDF files only."""

    name = Path(filename).name

    if not name.lower().endswith(".pdf"):
        raise ValueError(
            "Only PDF documents are supported."
        )

    name = re.sub(
        r"[^A-Za-z0-9._ -]",
        "_",
        name,
    )

    if not name:
        raise ValueError(
            "Invalid document filename."
        )

    return name


def _get_collection():
    """Get or create the existing Copperleaf RAG collection."""

    client = chromadb.PersistentClient(
        path=DB_PATH
    )

    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={
            "description": (
                "Copperleaf Kitchens RAG knowledge base "
                "— operational policy documents"
            ),
            **HNSW_CONFIG,
        },
    )


def list_rag_documents() -> list[dict]:
    """List PDF documents currently available to the RAG system."""

    DOCUMENTS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    collection = _get_collection()

    documents = []

    for pdf_path in sorted(
        DOCUMENTS_DIR.glob("*.pdf")
    ):
        result = collection.get(
            where={
                "source": pdf_path.name,
            },
            include=["metadatas"],
        )

        documents.append(
            {
                "name": pdf_path.name,
                "size_bytes": pdf_path.stat().st_size,
                "indexed_chunks": len(
                    result.get("ids", [])
                ),
            }
        )

    return documents


def upload_rag_document(
    filename: str,
    content: bytes,
) -> dict:
    """Store a PDF and immediately index it in the live RAG store."""

    safe_name = _safe_filename(
        filename
    )

    DOCUMENTS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    destination = (
        DOCUMENTS_DIR / safe_name
    )

    destination.write_bytes(
        content
    )

    chunks = create_chunks_for_pdf(
        destination
    )

    if not chunks:
        destination.unlink(
            missing_ok=True
        )

        raise ValueError(
            "The uploaded PDF contains no extractable text."
        )

    embeddings = create_embeddings(
        chunks
    )

    collection = _get_collection()

    # If the same filename is uploaded again,
    # replace its old vectors.
    collection.delete(
        where={
            "source": safe_name,
        }
    )

    ids = []
    texts = []
    metadatas = []
    vectors = []

    for index, (
        chunk,
        embedding,
    ) in enumerate(
        zip(
            chunks,
            embeddings,
        )
    ):
        metadata = chunk[
            "metadata"
        ]

        chunk_id = (
            f"admin::{safe_name}::"
            f"{metadata['page']}::{index}"
        )

        ids.append(
            chunk_id
        )

        texts.append(
            chunk["text"]
        )

        metadatas.append(
            {
                "source": safe_name,
                "page": metadata[
                    "page"
                ],
                "chunk_id": metadata[
                    "chunk_id"
                ],
                "doc_type": (
                    _derive_doc_type(
                        safe_name
                    )
                ),
            }
        )

        vectors.append(
            embedding.tolist()
        )

    collection.upsert(
        ids=ids,
        documents=texts,
        metadatas=metadatas,
        embeddings=vectors,
    )

    return {
        "name": safe_name,
        "stored": True,
        "indexed": True,
        "indexed_chunks": len(ids),
    }


def delete_rag_document(
    filename: str,
) -> dict:
    """Remove a PDF and all of its vectors from active retrieval."""

    safe_name = _safe_filename(
        filename
    )

    pdf_path = (
        DOCUMENTS_DIR
        / safe_name
    )

    if not pdf_path.exists():
        raise FileNotFoundError(
            f"Document not found: {safe_name}"
        )

    collection = _get_collection()

    existing = collection.get(
        where={
            "source": safe_name,
        },
        include=["metadatas"],
    )

    removed_chunks = len(
        existing.get(
            "ids",
            [],
        )
    )

    collection.delete(
        where={
            "source": safe_name,
        }
    )

    pdf_path.unlink()

    return {
        "name": safe_name,
        "deleted": True,
        "removed_chunks": (
            removed_chunks
        ),
    }