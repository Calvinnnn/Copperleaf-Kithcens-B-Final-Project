"""Tests for Admin RAG document management."""

from __future__ import annotations

import pytest

import rag.admin_documents as admin_docs
class FakeEmbedding:
    def __init__(self, values):
        self.values = values

    def tolist(self):
        return self.values

class FakeCollection:
    def __init__(self):
        self.records = {}

    def delete(self, where):
        source = where["source"]

        self.records = {
            key: value
            for key, value in self.records.items()
            if value["metadata"]["source"] != source
        }

    def upsert(
        self,
        ids,
        documents,
        metadatas,
        embeddings,
    ):
        for item_id, text, metadata, embedding in zip(
            ids,
            documents,
            metadatas,
            embeddings,
        ):
            self.records[item_id] = {
                "text": text,
                "metadata": metadata,
                "embedding": embedding,
            }

    def get(
        self,
        where=None,
        include=None,
    ):
        rows = list(self.records.items())

        if where:
            source = where["source"]

            rows = [
                row
                for row in rows
                if row[1]["metadata"]["source"] == source
            ]

        return {
            "ids": [row[0] for row in rows],
            "metadatas": [
                row[1]["metadata"]
                for row in rows
            ],
        }


@pytest.fixture()
def fake_rag_environment(
    tmp_path,
    monkeypatch,
):
    documents_dir = tmp_path / "documents"
    documents_dir.mkdir()

    collection = FakeCollection()

    monkeypatch.setattr(
        admin_docs,
        "DOCUMENTS_DIR",
        documents_dir,
    )

    monkeypatch.setattr(
        admin_docs,
        "_get_collection",
        lambda: collection,
    )

    def fake_chunks(pdf_path):
        return [
            {
                "text": "Maintenance policy test content",
                "metadata": {
                    "source": pdf_path.name,
                    "page": 1,
                    "chunk_id": 0,
                },
            }
        ]

    monkeypatch.setattr(
        admin_docs,
        "create_chunks_for_pdf",
        fake_chunks,
    )

    monkeypatch.setattr(
        admin_docs,
        "create_embeddings",
        lambda chunks: [
            FakeEmbedding([0.1, 0.2, 0.3])
            for _ in chunks
        ],
    )

    return documents_dir, collection


def test_upload_document_indexes_it(
    fake_rag_environment,
):
    documents_dir, collection = (
        fake_rag_environment
    )

    result = admin_docs.upload_rag_document(
        "Maintenance_Manual.pdf",
        b"fake pdf bytes",
    )

    assert result["stored"] is True
    assert result["indexed"] is True
    assert result["indexed_chunks"] == 1

    assert (
        documents_dir
        / "Maintenance_Manual.pdf"
    ).exists()

    assert len(collection.records) == 1


def test_list_documents(
    fake_rag_environment,
):
    admin_docs.upload_rag_document(
        "Maintenance_Manual.pdf",
        b"fake pdf bytes",
    )

    documents = (
        admin_docs.list_rag_documents()
    )

    assert len(documents) == 1
    assert (
        documents[0]["name"]
        == "Maintenance_Manual.pdf"
    )
    assert (
        documents[0]["indexed_chunks"]
        == 1
    )


def test_delete_document_removes_file_and_vectors(
    fake_rag_environment,
):
    documents_dir, collection = (
        fake_rag_environment
    )

    admin_docs.upload_rag_document(
        "Maintenance_Manual.pdf",
        b"fake pdf bytes",
    )

    result = admin_docs.delete_rag_document(
        "Maintenance_Manual.pdf"
    )

    assert result["deleted"] is True
    assert result["removed_chunks"] == 1

    assert not (
        documents_dir
        / "Maintenance_Manual.pdf"
    ).exists()

    assert len(collection.records) == 0


def test_only_pdf_allowed(
    fake_rag_environment,
):
    with pytest.raises(
        ValueError,
        match="Only PDF",
    ):
        admin_docs.upload_rag_document(
            "notes.txt",
            b"text",
        )