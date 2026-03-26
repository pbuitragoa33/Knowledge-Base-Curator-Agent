from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Sequence

from embedding_processing import EmbeddingPayload


DEFAULT_CHROMA_PERSIST_DIR = str(Path(__file__).resolve().parent / ".chroma")
COLLECTION_NAME_PREFIX = "kbca-course-"

VECTOR_STORE_CLIENT = None


class VectorStoreError(RuntimeError):
    """Raised when the vector store cannot be accessed safely."""


def get_chroma_persist_dir() -> str:
    return os.environ.get("CHROMA_PERSIST_DIR", DEFAULT_CHROMA_PERSIST_DIR)


def reset_vector_store_client() -> None:
    global VECTOR_STORE_CLIENT
    client = VECTOR_STORE_CLIENT
    VECTOR_STORE_CLIENT = None

    if client is None:
        return

    server = getattr(client, "_server", None)

    if server is not None:
        stop = getattr(server, "stop", None)

        if callable(stop):
            try:
                stop()
            except Exception:
                pass

    clear_system_cache = getattr(client, "clear_system_cache", None)

    if callable(clear_system_cache):
        try:
            clear_system_cache()
        except Exception:
            pass


def get_vector_store_client():
    """Return a lazy persistent Chroma client."""

    global VECTOR_STORE_CLIENT

    if VECTOR_STORE_CLIENT is None:
        persist_dir = get_chroma_persist_dir()
        os.makedirs(persist_dir, exist_ok=True)

        try:
            import chromadb
        except ImportError as exc:
            raise VectorStoreError(
                "chromadb is not installed. Run 'pip install -r requirements.txt'."
            ) from exc

        try:
            VECTOR_STORE_CLIENT = chromadb.PersistentClient(path=persist_dir)
        except Exception as exc:
            raise VectorStoreError("Failed to initialize the Chroma persistent client.") from exc

    return VECTOR_STORE_CLIENT


def build_course_collection_name(course_code: str) -> str:
    """Build a deterministic Chroma-safe collection name from the course code."""

    normalized_code = re.sub(r"[^a-z0-9]+", "-", str(course_code or "").strip().lower()).strip("-")

    if not normalized_code:
        normalized_code = "course"

    collection_name = f"{COLLECTION_NAME_PREFIX}{normalized_code}"

    if len(collection_name) < 3:
        return f"{COLLECTION_NAME_PREFIX}course"

    return collection_name


def get_course_collection(course_code: str):
    """Get or create the collection assigned to a course."""

    normalized_course_code = str(course_code or "").strip().upper()
    collection_name = build_course_collection_name(normalized_course_code)
    client = get_vector_store_client()

    try:
        return client.get_or_create_collection(
            name=collection_name,
            metadata={"course_code": normalized_course_code},
        )
    except Exception as exc:
        raise VectorStoreError(
            f"Failed to get or create the Chroma collection for course '{normalized_course_code}'."
        ) from exc


def _build_vector_store_metadatas(
    course_code: str,
    embedding_payloads: Sequence[EmbeddingPayload],
) -> list[dict[str, object]]:
    normalized_course_code = str(course_code or "").strip().upper()
    metadatas: list[dict[str, object]] = []

    for payload in embedding_payloads:
        metadata = dict(payload["metadata"])
        metadata["course_code"] = normalized_course_code
        metadatas.append(metadata)

    return metadatas


def _normalize_sequence(value) -> list:
    if value is None:
        return []

    if hasattr(value, "tolist"):
        converted = value.tolist()
        return list(converted) if isinstance(converted, (list, tuple)) else [converted]

    return list(value)


def upsert_course_embeddings(
    course_code: str,
    embedding_payloads: Sequence[EmbeddingPayload],
) -> list[str]:
    """Persist embeddings and chunk text inside the course collection."""

    if not embedding_payloads:
        return []

    collection = get_course_collection(course_code)
    chunk_ids = [payload["chunk_id"] for payload in embedding_payloads]
    documents = [payload["text"] for payload in embedding_payloads]
    embeddings = [payload["embedding"] for payload in embedding_payloads]
    metadatas = _build_vector_store_metadatas(course_code, embedding_payloads)

    try:
        collection.upsert(
            ids=chunk_ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )
    except Exception as exc:
        raise VectorStoreError("Failed to persist embeddings in the Chroma vector store.") from exc

    return chunk_ids


def delete_course_embeddings(course_code: str, chunk_ids: Sequence[str]) -> None:
    """Delete a known set of chunk ids from the course collection."""

    if not chunk_ids:
        return

    collection = get_course_collection(course_code)

    try:
        collection.delete(ids=list(chunk_ids))
    except Exception as exc:
        raise VectorStoreError("Failed to delete embeddings from the Chroma vector store.") from exc


def get_course_embeddings_by_metadata(
    course_code: str,
    where: dict[str, object],
) -> list[EmbeddingPayload]:
    """Fetch embeddings for a course using a metadata filter."""

    if not where:
        return []

    collection = get_course_collection(course_code)

    try:
        records = collection.get(
            where=where,
            include=["documents", "embeddings", "metadatas"],
        )
    except Exception as exc:
        raise VectorStoreError("Failed to fetch embeddings from the Chroma vector store.") from exc

    ids = _normalize_sequence(records.get("ids"))
    documents = _normalize_sequence(records.get("documents"))
    embeddings = _normalize_sequence(records.get("embeddings"))
    metadatas = _normalize_sequence(records.get("metadatas"))
    payloads: list[EmbeddingPayload] = []

    for chunk_id, text, embedding, metadata in zip(ids, documents, embeddings, metadatas):
        if embedding is None:
            embedding_values: list[float] = []
        elif hasattr(embedding, "tolist"):
            embedding_values = [float(value) for value in embedding.tolist()]
        else:
            embedding_values = [float(value) for value in embedding]

        metadata_values = dict(metadata) if isinstance(metadata, dict) else {}
        payloads.append(
            {
                "chunk_id": str(chunk_id),
                "text": "" if text is None else str(text),
                "embedding": embedding_values,
                "metadata": metadata_values,
            }
        )

    return payloads


def delete_course_embeddings_by_metadata(
    course_code: str,
    where: dict[str, object],
) -> list[str]:
    """Delete embeddings for a course using a metadata filter."""

    if not where:
        return []

    collection = get_course_collection(course_code)

    try:
        records = collection.get(where=where, include=[])
        chunk_ids = [str(chunk_id) for chunk_id in _normalize_sequence(records.get("ids"))]

        if chunk_ids:
            collection.delete(ids=chunk_ids)
    except Exception as exc:
        raise VectorStoreError("Failed to delete embeddings from the Chroma vector store by metadata filter.") from exc

    return chunk_ids


def query_course_embeddings(
    course_code: str,
    query_embedding: Sequence[float],
    *,
    top_n: int,
) -> list[dict[str, object]]:
    """Run similarity search against a single course collection."""

    if top_n < 1:
        return []

    collection = get_course_collection(course_code)

    try:
        results = collection.query(
            query_embeddings=[list(query_embedding)],
            n_results=top_n,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:
        raise VectorStoreError("Failed to query embeddings from the Chroma vector store.") from exc

    ids = _normalize_sequence(results.get("ids"))
    documents = _normalize_sequence(results.get("documents"))
    metadatas = _normalize_sequence(results.get("metadatas"))
    distances = _normalize_sequence(results.get("distances"))

    first_ids = _normalize_sequence(ids[0]) if ids else []
    first_documents = _normalize_sequence(documents[0]) if documents else []
    first_metadatas = _normalize_sequence(metadatas[0]) if metadatas else []
    first_distances = _normalize_sequence(distances[0]) if distances else []
    ranked_results: list[dict[str, object]] = []

    for chunk_id, text, metadata, distance in zip(first_ids, first_documents, first_metadatas, first_distances):
        numeric_distance = float(distance) if distance is not None else 0.0
        similarity_score = 1.0 / (1.0 + max(0.0, numeric_distance))
        ranked_results.append(
            {
                "chunk_id": str(chunk_id),
                "text": str(text or ""),
                "score": similarity_score,
                "distance": numeric_distance,
                "metadata": dict(metadata or {}),
            }
        )

    return ranked_results
