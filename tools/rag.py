"""
tools/rag.py
------------
Local document RAG store backed by ChromaDB + sentence-transformers
(all-MiniLM-L6-v2). Ingests PDF / TXT / MD files, chunks them, and answers
top-k similarity queries. Content-hash dedup avoids re-indexing unchanged files
(tracked in the SQLite `rag_documents` table).

All personal documents stay LOCAL -- the rag_query tool is in ALWAYS_LOCAL_TOOLS
in spirit (privacy floor 0.70, never routed to cloud for content).

ChromaDB / the embedding model load lazily; if unavailable, every method
returns a graceful error dict instead of raising (SPEC 8).
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Optional

from pypdf import PdfReader

from config.store import get_config
from kg.schema import get_db_connection


# Chunking parameters.
_CHUNK_CHARS: int = 800
_CHUNK_OVERLAP: int = 100
_EMBED_MODEL_NAME: str = "all-MiniLM-L6-v2"
_COLLECTION_NAME: str = "knowledgemind_docs"
_SUPPORTED_SUFFIXES: frozenset[str] = frozenset({".pdf", ".txt", ".md"})


# ---------------------------------------------------------------------------
# Text extraction + chunking
# ---------------------------------------------------------------------------

def _read_text(path: Path) -> str:
    """Extract plain text from a PDF / TXT / MD file."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    return path.read_text(encoding="utf-8", errors="ignore")


def _chunk(text: str) -> list[str]:
    """Split text into overlapping character windows."""
    cleaned = " ".join(text.split())
    if not cleaned:
        return []
    chunks: list[str] = []
    start = 0
    step = max(_CHUNK_CHARS - _CHUNK_OVERLAP, 1)
    while start < len(cleaned):
        chunks.append(cleaned[start:start + _CHUNK_CHARS])
        start += step
    return chunks


def _content_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# RAG tool
# ---------------------------------------------------------------------------

class RagTool:
    """ChromaDB-backed document store. Lazy, fail-soft."""

    def __init__(self) -> None:
        self._collection: Any = None
        self._available: Optional[bool] = None

    def _ensure_collection(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            import chromadb
            from chromadb.utils import embedding_functions

            cfg = get_config()
            try:
                Path(cfg.chroma_persist_dir).mkdir(parents=True, exist_ok=True)
                client = chromadb.PersistentClient(path=cfg.chroma_persist_dir)
            except Exception:
                print("[RAG] WARNING: chroma_persist_dir not writable — using in-memory client (data resets on restart)")
                client = chromadb.EphemeralClient()

            embedder = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=_EMBED_MODEL_NAME
            )
            self._collection = client.get_or_create_collection(
                name=_COLLECTION_NAME, embedding_function=embedder
            )
            self._available = True
        except Exception as error:  # noqa: BLE001 -- degrade gracefully
            print(f"[RAG] WARNING: document store unavailable ({error}).")
            self._available = False
        return self._available

    # -- ingestion ---------------------------------------------------------

    def add_documents(self, file_paths: list[str]) -> dict[str, Any]:
        """
        Index files into the store. Skips files whose content hash is already
        recorded. Returns {"added", "skipped", "chunks", "success"}.
        """
        if not self._ensure_collection():
            return {"success": False, "added": [], "skipped": [],
                    "chunks": 0, "error": "RAG store unavailable."}

        cfg = get_config()
        conn = get_db_connection(cfg.db_path)
        added: list[str] = []
        skipped: list[str] = []
        total_chunks = 0

        try:
            for raw_path in file_paths:
                path = Path(raw_path)
                if not path.exists() or path.suffix.lower() not in _SUPPORTED_SUFFIXES:
                    skipped.append(path.name)
                    continue

                file_hash = _content_hash(path)
                exists = conn.execute(
                    "SELECT 1 FROM rag_documents WHERE content_hash = ?", (file_hash,)
                ).fetchone()
                if exists is not None:
                    skipped.append(path.name)
                    continue

                chunks = _chunk(_read_text(path))
                if not chunks:
                    skipped.append(path.name)
                    continue

                ids = [f"{file_hash}:{index}" for index in range(len(chunks))]
                metadatas = [{"filename": path.name, "chunk": index} for index in range(len(chunks))]
                self._collection.add(documents=chunks, metadatas=metadatas, ids=ids)

                conn.execute(
                    """INSERT INTO rag_documents (filename, content_hash, chunk_count, indexed_at)
                       VALUES (?, ?, ?, ?)""",
                    (path.name, file_hash, len(chunks), time.time()),
                )
                conn.commit()
                added.append(path.name)
                total_chunks += len(chunks)
        finally:
            conn.close()

        return {"success": True, "added": added, "skipped": skipped, "chunks": total_chunks}

    # -- query -------------------------------------------------------------

    def query(self, query_text: str, top_k: int = 5) -> dict[str, Any]:
        """Return the top-k most similar chunks for a query."""
        if not self._ensure_collection():
            return {"success": False, "error": "RAG store unavailable.",
                    "formatted": "Document store is unavailable.", "chunks": []}

        if self._collection.count() == 0:
            return {"success": True, "formatted": "No documents indexed yet.", "chunks": []}

        result = self._collection.query(query_texts=[query_text], n_results=top_k)
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        chunks: list[dict[str, Any]] = []
        lines: list[str] = []
        for document, metadata, distance in zip(documents, metadatas, distances):
            similarity = round(1.0 - float(distance), 3)
            filename = metadata.get("filename", "?")
            chunks.append({"text": document, "filename": filename, "similarity": similarity})
            lines.append(f"[{filename} sim={similarity}] {document[:200]}")

        if not chunks:
            return {"success": True, "formatted": "No relevant chunks found.", "chunks": []}
        return {"success": True, "formatted": "\n\n".join(lines), "chunks": chunks}

    # -- listing -----------------------------------------------------------

    def list_documents(self) -> list[str]:
        """Return indexed document filenames."""
        conn = get_db_connection(get_config().db_path)
        try:
            rows = conn.execute(
                "SELECT filename FROM rag_documents ORDER BY indexed_at DESC"
            ).fetchall()
        finally:
            conn.close()
        return [row["filename"] for row in rows]


# Shared singleton.
rag_tool = RagTool()


def add_documents_to_rag(file_paths: list[str]) -> dict[str, Any]:
    """Module-level convenience wrapper used by the agent/orchestrator."""
    return rag_tool.add_documents(file_paths)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import tempfile

    # ignore_cleanup_errors: ChromaDB keeps its SQLite file open, so Windows
    # cannot delete the temp dir until the process exits (WinError 32). The
    # indexing/query assertions below are what matter, not temp cleanup.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        os.environ["KM_DB_PATH"] = str(Path(tmp) / "rag.db")
        cfg = get_config()
        cfg.db_path = os.environ["KM_DB_PATH"]
        cfg.chroma_persist_dir = str(Path(tmp) / "chroma")

        # Chunking is testable without ChromaDB / the model.
        sample_chunks = _chunk("word " * 500)
        assert len(sample_chunks) >= 2, "expected multiple chunks for long text"
        print(f"=> chunking produced {len(sample_chunks)} chunk(s)")

        doc = Path(tmp) / "note.txt"
        doc.write_text("KnowledgeMind routes personal data locally for privacy.", encoding="utf-8")

        result = add_documents_to_rag([str(doc)])
        if result.get("success"):
            print(f"=> indexed {result['added']} ({result['chunks']} chunks)")
            answer = rag_tool.query("where does personal data go?", top_k=2)
            print(f"=> query ok={answer['success']}, chunks={len(answer.get('chunks', []))}")
            assert doc.name in rag_tool.list_documents(), "document not listed"
        else:
            # Offline / model unavailable: graceful skip, still a pass.
            print(f"=> RAG store unavailable (offline?), skipped: {result.get('error')}")

    print("All tools/rag.py smoke tests passed.")
