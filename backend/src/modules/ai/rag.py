"""RAG: indexación y búsqueda semántica con pgvector (opcional)."""

from __future__ import annotations

# Nota: requiere extensión pgvector en Postgres y un proveedor de embeddings.
# Aquí dejamos la interfaz; la implementación concreta depende del proveedor.


class VectorStore:
    def __init__(self, table: str = "documents"):
        self.table = table

    def add(
        self, text: str, embedding: list[float], metadata: dict | None = None
    ) -> None:
        raise NotImplementedError("Implementar con pgvector insert")

    def search(self, embedding: list[float], k: int = 5) -> list[dict]:
        raise NotImplementedError("Implementar con pgvector cosine similarity")
