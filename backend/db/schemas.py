"""
schemas.py
----------
Estructuras de la data que llega del scraper (validadas con Pydantic).

Sigue TU patrón híbrido de Pickazo: campos comunes arriba + sportData
flexible para lo específico del deporte. Así es consistente con lo que
ya conoces y puedes crecer a NFL igual que en Pickazo.
"""

from pydantic import BaseModel, Field


class PartidoIn(BaseModel):
    """Un partido finalizado con sus stats. Llega del FotMob Scout."""
    # --- Común a todo deporte ---
    deporte: str = "futbol"
    liga: str = "ligamx"
    temporada: str = ""
    local: str
    visitante: str
    inicio: str = ""                 # fecha ISO o texto; se guarda tal cual
    estado: str = "finalizado"
    marcadorLocal: int | None = None
    marcadorVisitante: int | None = None
    fase: str = ""                   # "Jornada 1", "Semana 5"...
    fuenteId: str = ""               # matchId de FotMob

    # --- Específico del deporte ---
    # fútbol: { posesionLocal, xgLocal, tirosLocal, corners, tarjetas... }
    sportData: dict = Field(default_factory=dict)


class JugadorIn(BaseModel):
    """Stats de UN jugador en UN partido. Alimenta el benchmark."""
    jugadorId: str                   # id FotMob (o nombre normalizado)
    nombre: str
    equipo: str
    liga: str = "ligamx"
    partidoId: str = ""              # fuenteId del partido al que pertenece
    posicion: str = ""

    # estado físico (para no confiar en un goleador que no jugará)
    lesionado: bool = False
    lesion_info: str = ""            # ej: "Ligamento cruzado, regreso ene 2027"

    # stats variables según posición -> por eso van en sportData
    # ej: { rating, goles, asistencias, minutos, tiros, pases, ... }
    sportData: dict = Field(default_factory=dict)


class FilaClasificacionIn(BaseModel):
    """Una fila de la tabla de posiciones."""
    deporte: str = "futbol"
    liga: str = "ligamx"
    temporada: str = ""
    equipo: str
    abrev: str = ""
    pos: int = 0
    jugados: int = 0
    ganados: int = 0
    empatados: int = 0
    perdidos: int = 0
    grupo: str = ""

    # fútbol: { golesFavor, golesContra, dif, puntos, forma }
    sportData: dict = Field(default_factory=dict)


class ClasificacionIn(BaseModel):
    """La tabla completa: lista de filas de un jalón."""
    liga: str = "ligamx"
    temporada: str = ""
    filas: list[FilaClasificacionIn]
