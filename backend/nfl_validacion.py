"""
Validación de integridad de datos NFL.

Como los datos vienen de una API automática (sin ojos humanos como con
FotMob), estos chequeos detectan basura obvia: equipos que no son de los 32
reales de la NFL, marcadores imposibles, duplicados y volúmenes fuera de rango.

NO garantiza que un marcador correcto en formato sea el marcador REAL del
partido — para eso haría falta una segunda fuente o revisión manual. Detecta
lo que se puede detectar por reglas.
"""
from __future__ import annotations
import unicodedata

# Los 32 equipos reales de la NFL (nombres como los da ESPN).
EQUIPOS_NFL_OFICIALES = {
    "arizona cardinals", "atlanta falcons", "baltimore ravens", "buffalo bills",
    "carolina panthers", "chicago bears", "cincinnati bengals", "cleveland browns",
    "dallas cowboys", "denver broncos", "detroit lions", "green bay packers",
    "houston texans", "indianapolis colts", "jacksonville jaguars", "kansas city chiefs",
    "las vegas raiders", "los angeles chargers", "los angeles rams", "miami dolphins",
    "minnesota vikings", "new england patriots", "new orleans saints", "new york giants",
    "new york jets", "philadelphia eagles", "pittsburgh steelers", "san francisco 49ers",
    "seattle seahawks", "tampa bay buccaneers", "tennessee titans", "washington commanders",
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def es_equipo_nfl_real(nombre: str) -> bool:
    """True si el nombre corresponde a uno de los 32 equipos reales."""
    n = _norm(nombre)
    if n in EQUIPOS_NFL_OFICIALES:
        return True
    # match parcial tolerante (por si ESPN abrevia)
    for eq in EQUIPOS_NFL_OFICIALES:
        # coincide la última palabra distintiva (mascota) y la ciudad
        if n and (n in eq or eq in n):
            return True
    return False


def auditar_datos_nfl(db) -> dict:
    """Revisa la salud de los datos NFL guardados y devuelve un reporte
    con banderas rojas. Para el panel de salud de datos."""
    partidos = list(db.partidos.find({"liga": "nfl"}, {"_id": 0}))
    total = len(partidos)
    finalizados = [p for p in partidos if p.get("estado") == "finalizado"]

    problemas = []
    equipos_falsos = set()
    marcadores_raros = 0
    sin_marcador = 0
    duplicados = 0
    vistos = {}
    conteo_equipo = {}

    for p in partidos:
        l, v = p.get("local", ""), p.get("visitante", "")
        # equipos no reales
        for eq in (l, v):
            if eq and not es_equipo_nfl_real(eq):
                equipos_falsos.add(eq)
            elif eq:
                conteo_equipo[eq] = conteo_equipo.get(eq, 0) + 1
        # duplicados (mismo partido dos veces)
        key = f"{_norm(l)}|{_norm(v)}|{str(p.get('inicio',''))[:10]}"
        if key in vistos:
            duplicados += 1
        vistos[key] = True
        # marcadores
        if p.get("estado") == "finalizado":
            gl, gv = p.get("marcadorLocal"), p.get("marcadorVisitante")
            if gl is None or gv is None:
                sin_marcador += 1
            elif not (0 <= gl <= 80 and 0 <= gv <= 80):
                marcadores_raros += 1

    # un equipo no puede jugar más de ~20 partidos en una temporada
    sobrecargados = {e: n for e, n in conteo_equipo.items() if n > 20}

    if equipos_falsos:
        problemas.append(f"{len(equipos_falsos)} equipos NO reales de la NFL: {', '.join(sorted(equipos_falsos)[:6])}")
    if marcadores_raros:
        problemas.append(f"{marcadores_raros} partidos con marcador imposible (fuera de 0-80)")
    if sin_marcador:
        problemas.append(f"{sin_marcador} partidos finalizados SIN marcador")
    if duplicados:
        problemas.append(f"{duplicados} partidos duplicados")
    if sobrecargados:
        problemas.append(f"{len(sobrecargados)} equipos con demasiados partidos (posible mezcla de temporadas)")

    # ¿cuántos de los 32 equipos oficiales están presentes?
    presentes = {e for e in conteo_equipo if es_equipo_nfl_real(e)}

    return {
        "ok": len(problemas) == 0,
        "total_partidos": total,
        "finalizados": len(finalizados),
        "equipos_reales_presentes": len(presentes),
        "equipos_esperados": 32,
        "problemas": problemas,
        "equipos_falsos": sorted(equipos_falsos),
        "veredicto": "Datos limpios ✓" if not problemas else f"{len(problemas)} problema(s) detectado(s)",
    }
