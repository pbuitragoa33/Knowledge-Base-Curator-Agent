from __future__ import annotations

import logging
from pathlib import Path
from typing import TypedDict

from docx import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader


logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 120
DEFAULT_SEPARATORS = ["\n\n", "\n", " ", ""]


class ChunkRecord(TypedDict):
    chunk_id: str
    text: str
    document_id: int
    doc_hash: str
    upload_hash: str
    course: str
    upload_date: str
    filename: str
    file_hash: str
    chunk_index: int


def extract_plain_text(filepath: str) -> str:
    """Extract plain text from supported source files."""

    extension = Path(filepath).suffix.lower()

    if extension in {".txt", ".md"}:
        return Path(filepath).read_text(encoding="utf-8", errors="ignore").strip()

    if extension == ".docx":
        document = Document(filepath)
        paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
        return "\n".join(paragraphs).strip()

    if extension == ".pdf":
        reader = PdfReader(filepath)
        pages = []

        for page in reader.pages:
            page_text = (page.extract_text() or "").strip()
            if page_text:
                pages.append(page_text)

        return "\n\n".join(pages).strip()

    raise ValueError(f"Unsupported file format for plain-text extraction: {extension or 'unknown'}")


def split_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """Split text into overlapping chunks ready for embeddings."""

    if not text or not text.strip():
        return []

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=DEFAULT_SEPARATORS,
        length_function=len,
    )

    chunks = splitter.split_text(text)
    return [chunk.strip() for chunk in chunks if chunk and chunk.strip()]


def build_chunk_records(
    chunks: list[str],
    *,
    document_id: int,
    doc_hash: str,
    upload_hash: str,
    course: str,
    upload_date: str,
    filename: str,
    file_hash: str,
) -> list[ChunkRecord]:
    """Build deterministic chunk records for downstream indexing."""

    records: list[ChunkRecord] = []

    for chunk_index, chunk_text in enumerate(chunks):
        records.append(
            {
                "chunk_id": f"{upload_hash}:{document_id}:{chunk_index}",
                "text": chunk_text,
                "document_id": document_id,
                "doc_hash": doc_hash,
                "upload_hash": upload_hash,
                "course": course,
                "upload_date": upload_date,
                "filename": filename,
                "file_hash": file_hash,
                "chunk_index": chunk_index,
            }
        )

    return records


def process_uploaded_file(
    filepath: str,
    *,
    document_id: int,
    doc_hash: str,
    upload_hash: str,
    course: str,
    upload_date: str,
    filename: str,
    file_hash: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[ChunkRecord]:
    """Extract and chunk a newly uploaded file without failing the whole upload."""

    try:
        text = extract_plain_text(filepath)
        chunks = split_text(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    except Exception:
        logger.exception("Failed to process uploaded file for chunking: %s", filepath)
        return []

    if not chunks:
        logger.warning("No chunks generated for file: %s", filepath)
        return []

    return build_chunk_records(
        chunks,
        document_id=document_id,
        doc_hash=doc_hash,
        upload_hash=upload_hash,
        course=course,
        upload_date=upload_date,
        filename=filename,
        file_hash=file_hash,
    )
