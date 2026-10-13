"""
ingest.py
---------
Carga conocimiento al vector store desde data/conocimiento.json.

Ventaja: editas un JSON normal (sin tocar código Python) y corres esto
cada vez que agregues aprendizajes. Es idempotente: usa upsert, así que
re-correrlo no duplica, solo actualiza.

Ejecutar:  python -m data.ingest
"""

import json
import os

from rag.embeddings import generar_embeddings
from rag.vectorstore import VectorStore

# Ruta al JSON de conocimiento (junto a este archivo).
RUTA_JSON = os.path.join(os.path.dirname(__file__), "conocimiento.json")


def cargar_conocimiento() -> list[dict]:
    with open(RUTA_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def poblar():
    conocimiento = cargar_conocimiento()

    if not conocimiento:
        print("El JSON está vacío. Nada que cargar.")
        return

    store = VectorStore()

    ids = [d["id"] for d in conocimiento]
    textos = [d["texto"] for d in conocimiento]
    metadatas = [d["metadata"] for d in conocimiento]

    print(f"Generando embeddings de {len(textos)} documentos...")
    embeddings = generar_embeddings(textos)

    print("Guardando en el vector store...")
    store.agregar(ids=ids, textos=textos, embeddings=embeddings, metadatas=metadatas)

    print(f"Listo. Total de documentos en memoria: {store.contar()}")


if __name__ == "__main__":
    poblar()
