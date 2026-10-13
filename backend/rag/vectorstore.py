"""
vectorstore.py
--------------
El "cerebro de memoria" del sistema. Aquí se GUARDAN y se RECUPERAN
los conocimientos: aprendizajes, post-mortems, perfiles de equipos.

Usamos ChromaDB porque:
- Corre local, sin servidor aparte (como tu node:sqlite)
- Persiste en disco (usaremos un volumen de Railway para no perder datos)
- Es la opción más simple para empezar

CONCEPTO: una "colección" es como una tabla. Cada documento tiene:
- un id (único)
- el texto (el conocimiento)
- su embedding (el vector, calculado aparte)
- metadata (etiquetas para filtrar: tipo, liga, equipo, fecha...)
"""

import os
import chromadb
from chromadb.config import Settings

# Ruta donde ChromaDB guarda todo en disco.
# En Docker/Railway esto será un VOLUMEN persistente.
RUTA_PERSISTENCIA = os.environ.get("CHROMA_PATH", "/data/chroma")
MAX_DISTANCIA = float(os.environ.get("CHROMA_MAX_DISTANCE", "0.45"))


class VectorStore:
    def __init__(self):
        # Cliente persistente: escribe a disco, sobrevive reinicios.
        self._cliente = chromadb.PersistentClient(
            path=RUTA_PERSISTENCIA,
            settings=Settings(anonymized_telemetry=False),
        )
        # get_or_create: si ya existe la colección la usa, si no la crea.
        self._coleccion = self._cliente.get_or_create_collection(
            name="conocimiento_apuestas",
            # cosine = mide similitud por ángulo, estándar para embeddings
            metadata={"hnsw:space": "cosine"},
        )

    def agregar(
        self,
        ids: list[str],
        textos: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict],
    ):
        """Guarda documentos en el vector store."""
        self._coleccion.upsert(
            ids=ids,
            documents=textos,
            embeddings=embeddings,
            metadatas=metadatas,
        )

    def buscar(
        self,
        embedding_consulta: list[float],
        n: int = 5,
        filtro: dict | None = None,
    ) -> list[dict]:
        """
        Recupera los N documentos más parecidos a la consulta.

        filtro: opcional, para restringir por metadata.
            Ej: {"liga": "la_liga"} solo busca en documentos de La Liga.
        """
        resultado = self._coleccion.query(
            query_embeddings=[embedding_consulta],
            n_results=n,
            where=filtro,  # None = sin filtro
        )
        # Chroma devuelve listas anidadas; las aplanamos a algo cómodo.
        documentos = resultado["documents"][0]
        metadatas = resultado["metadatas"][0]
        distancias = resultado["distances"][0]

        return [
            {"texto": doc, "metadata": meta, "distancia": dist}
            for doc, meta, dist in zip(documentos, metadatas, distancias)
            if dist is not None and float(dist) <= MAX_DISTANCIA
        ]

    def contar(self) -> int:
        """Cuántos documentos hay guardados."""
        return self._coleccion.count()
