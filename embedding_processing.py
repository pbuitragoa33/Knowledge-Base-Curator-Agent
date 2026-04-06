from __future__ import annotations

from typing import Protocol, TypedDict

from document_processing import ChunkRecord

DEFAULT_EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_EMBEDDING_DIMENSION = 384
DEFAULT_EMBEDDING_BATCH_SIZE = 32
DEFAULT_EMBEDDING_DEVICE = "cpu"


class EmbeddingMetadata(TypedDict):
    document_id: int
    doc_hash: str
    upload_hash: str
    course: str
    upload_date: str
    filename: str
    file_hash: str
    chunk_index: int


class EmbeddingPayload(TypedDict):
    chunk_id: str
    text: str
    embedding: list[float]
    metadata: EmbeddingMetadata


class EmbeddingProvider(Protocol):
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per provided text."""


class EmbeddingGenerationError(RuntimeError):
    """Raised when embedding generation cannot be completed safely."""


class LocalSentenceTransformerProvider:
    """Lazy sentence-transformers provider for local embeddings."""

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_EMBEDDING_MODEL_NAME,
        batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE,
        device: str = DEFAULT_EMBEDDING_DEVICE,
        embedding_dimension: int = DEFAULT_EMBEDDING_DIMENSION,
    ) -> None:
        self.model_name = model_name
        self.batch_size = max(1, int(batch_size))
        self.device = device
        self.embedding_dimension = embedding_dimension
        self._model = None

    def _get_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise EmbeddingGenerationError(
                    "sentence-transformers is not installed. Run 'pip install -r requirements.txt'."
                ) from exc

            try:
                self._model = SentenceTransformer(self.model_name, device=self.device)
            except Exception as exc:
                raise EmbeddingGenerationError(
                    f"Failed to load embedding model '{self.model_name}'."
                ) from exc

        return self._model

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        model = self._get_model()

        try:
            embeddings = model.encode(
                texts,
                batch_size=self.batch_size,
                convert_to_numpy=False,
                show_progress_bar=False,
                normalize_embeddings=False,
            )
        except Exception as exc:
            raise EmbeddingGenerationError("Failed to generate embeddings for uploaded chunks.") from exc

        normalized_embeddings: list[list[float]] = []

        for embedding in embeddings:
            vector = embedding.tolist() if hasattr(embedding, "tolist") else list(embedding)
            normalized_vector = [float(value) for value in vector]

            if len(normalized_vector) != self.embedding_dimension:
                raise EmbeddingGenerationError(
                    f"Unexpected embedding dimension {len(normalized_vector)}; expected {self.embedding_dimension}."
                )

            normalized_embeddings.append(normalized_vector)

        if len(normalized_embeddings) != len(texts):
            raise EmbeddingGenerationError("Embedding provider returned an unexpected number of vectors.")

        return normalized_embeddings


class DeterministicEmbeddingProvider:
    """Simple provider for tests that avoids external model downloads."""

    def __init__(self, *, embedding_dimension: int = DEFAULT_EMBEDDING_DIMENSION) -> None:
        self.embedding_dimension = embedding_dimension

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        embeddings: list[list[float]] = []

        for index, _ in enumerate(texts):
            embeddings.append([float(index + 1)] * self.embedding_dimension)

        return embeddings


def build_embedding_payloads(
    chunk_records: list[ChunkRecord],
    *,
    provider: EmbeddingProvider,
    embedding_dimension: int = DEFAULT_EMBEDDING_DIMENSION,
) -> list[EmbeddingPayload]:
    """Build chunk payloads enriched with embeddings and selected metadata."""

    if not chunk_records:
        return []

    chunk_texts = [record["text"] for record in chunk_records]
    embeddings = provider.embed_texts(chunk_texts)

    if len(embeddings) != len(chunk_records):
        raise EmbeddingGenerationError("Embedding payload count does not match the generated chunks.")

    payloads: list[EmbeddingPayload] = []

    for record, embedding in zip(chunk_records, embeddings):
        if len(embedding) != embedding_dimension:
            raise EmbeddingGenerationError(
                f"Unexpected embedding dimension {len(embedding)}; expected {embedding_dimension}."
            )

        payloads.append(
            {
                "chunk_id": record["chunk_id"],
                "text": record["text"],
                "embedding": [float(value) for value in embedding],
                "metadata": {
                    "document_id": record["document_id"],
                    "doc_hash": record["doc_hash"],
                    "upload_hash": record["upload_hash"],
                    "course": record["course"],
                    "upload_date": record["upload_date"],
                    "filename": record["filename"],
                    "file_hash": record["file_hash"],
                    "chunk_index": record["chunk_index"],
                },
            }
        )

    return payloads
