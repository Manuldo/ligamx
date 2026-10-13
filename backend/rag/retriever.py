"""
retriever.py
------------
Junta las dos piezas anteriores: dado un texto de consulta,
lo convierte en vector y recupera el conocimiento relevante.

Esta es la "R" de RAG: Retrieval (recuperación).
"""

from rag.embeddings import generar_embedding
from rag.vectorstore import VectorStore


class Retriever:
    def __init__(self):
        self.store = VectorStore()

    def recuperar_contexto(
        self,
        consulta: str,
        n: int = 5,
        filtro: dict | None = None,
    ) -> str:
        """
        Dado el texto de una consulta (ej: descripción de un partido),
        recupera los N conocimientos más relevantes y los devuelve
        formateados como un solo bloque de texto listo para el prompt.
        """
        # 1. Convertimos la consulta en vector
        vector = generar_embedding(consulta)

        # 2. Buscamos lo más parecido
        resultados = self.store.buscar(vector, n=n, filtro=filtro)

        if not resultados:
            return "(No hay conocimiento previo relevante.)"

        # 3. Formateamos para el prompt
        bloques = []
        for r in resultados:
            tipo = r["metadata"].get("tipo", "nota")
            bloques.append(f"[{tipo}] {r['texto']}")

        return "\n".join(bloques)
