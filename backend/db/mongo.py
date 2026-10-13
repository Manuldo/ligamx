"""Conexión centralizada a MongoDB Atlas con timeouts e índices operativos."""
import os
from pymongo import MongoClient, ASCENDING, DESCENDING

_uri = os.environ.get("MONGO_URI")
_cliente = MongoClient(
    _uri,
    appname="pickazo-rag-engine",
    serverSelectionTimeoutMS=int(os.environ.get("MONGO_SERVER_SELECTION_TIMEOUT_MS", "5000")),
    connectTimeoutMS=int(os.environ.get("MONGO_CONNECT_TIMEOUT_MS", "5000")),
    socketTimeoutMS=int(os.environ.get("MONGO_SOCKET_TIMEOUT_MS", "15000")),
    maxPoolSize=int(os.environ.get("MONGO_MAX_POOL_SIZE", "20")),
    retryWrites=True,
) if _uri else None
_db = _cliente[os.environ.get("MONGO_DB_NAME", "apuestas_rag")] if _cliente else None


def get_db():
    if _db is None:
        raise RuntimeError("No hay conexión a Mongo. Define MONGO_URI.")
    return _db


def close_db():
    if _cliente is not None:
        _cliente.close()


def crear_indices():
    """Índices idempotentes para integridad y consultas frecuentes."""
    db = get_db()

    db.partidos.create_index(
        [("liga", ASCENDING), ("local", ASCENDING), ("visitante", ASCENDING), ("inicio", ASCENDING)],
        unique=True, name="partido_unico",
    )
    db.partidos.create_index(
        [("liga", ASCENDING), ("estado", ASCENDING), ("inicio", ASCENDING)],
        name="partidos_liga_estado_inicio",
    )
    db.partidos.create_index(
        [("liga", ASCENDING), ("fuenteId", ASCENDING)],
        unique=True, sparse=True, name="partido_fuente_unico",
    )

    db.jugadores.create_index(
        [("jugadorId", ASCENDING), ("partidoId", ASCENDING)],
        unique=True, name="jugador_partido_unico",
    )
    db.jugadores.create_index(
        [("liga", ASCENDING), ("nombre", ASCENDING)], name="jugador_liga_nombre"
    )
    db.jugadores.create_index(
        [("liga", ASCENDING), ("equipo", ASCENDING), ("rating", DESCENDING)],
        name="jugador_equipo_rating",
    )

    db.clasificacion.create_index(
        [("liga", ASCENDING), ("equipo", ASCENDING), ("temporada", ASCENDING)],
        unique=True, name="equipo_unico",
    )
    db.clasificacion.create_index(
        [("liga", ASCENDING), ("pos", ASCENDING)], name="tabla_liga_pos"
    )

    db.analisis.create_index([("clave", ASCENDING)], unique=True, name="analisis_clave")
    db.analisis.create_index(
        [("liga", ASCENDING), ("creado", DESCENDING)], name="analisis_liga_creado"
    )
    db.picks_sugeridos.create_index([("clave", ASCENDING)], unique=True, name="picks_clave")
    db.picks_sugeridos.create_index([("liga", ASCENDING)], name="picks_liga")
    db.alineaciones.create_index([("clave", ASCENDING)], unique=True, name="alineacion_clave")
    db.boletos.create_index(
        [("liga", ASCENDING), ("likes", DESCENDING), ("creado", DESCENDING)],
        name="boletos_comunidad",
    )

    # ── NFL (data profunda desde ESPN) ──
    db.nfl_raw.create_index([("fuenteId", ASCENDING)], unique=True, name="nfl_raw_unico")
    db.nfl_partidos.create_index([("fuenteId", ASCENDING)], unique=True, name="nfl_partido_unico")
    db.nfl_jugadores.create_index(
        [("fuenteId", ASCENDING), ("jugadorId", ASCENDING), ("categoria", ASCENDING)],
        unique=True, name="nfl_jugador_unico",
    )
    db.nfl_jugadores.create_index([("jugador", ASCENDING)], name="nfl_jugador_nombre")
    db.nfl_plays.create_index([("fuenteId", ASCENDING)], name="nfl_plays_fuente")
