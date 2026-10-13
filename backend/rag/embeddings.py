"""Embeddings de bajo costo con cliente inicializado bajo demanda."""
import os
from openai import OpenAI

MODELO_EMBEDDING = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
_cliente = None


def _get_client() -> OpenAI:
    global _cliente
    if _cliente is None:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("Falta OPENAI_API_KEY")
        _cliente = OpenAI(api_key=key)
    return _cliente


def generar_embedding(texto: str) -> list[float]:
    texto = str(texto or "").strip()
    if not texto:
        raise ValueError("No se puede generar embedding de texto vacío")
    respuesta = _get_client().embeddings.create(model=MODELO_EMBEDDING, input=texto)
    return respuesta.data[0].embedding


def generar_embeddings(textos: list[str]) -> list[list[float]]:
    limpios = [str(t or "").strip() for t in textos]
    if not limpios or any(not t for t in limpios):
        raise ValueError("Todos los textos deben contener información")
    respuesta = _get_client().embeddings.create(model=MODELO_EMBEDDING, input=limpios)
    return [dato.embedding for dato in respuesta.data]
