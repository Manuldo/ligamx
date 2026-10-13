"""
Scraper de MLB usando la API pública (no-oficial) de ESPN — mismo patrón
que nfl_scraper.py, mismo proveedor, mismo "raw siempre se preserva".

DIFERENCIA CLAVE con NFL: la MLB no se organiza por semana. ESPN la
organiza por FECHA (dates=YYYYMMDD, un día a la vez). La temporada
regular son ~180 días y 2430 partidos — por eso aquí la sync es "por
rango de fechas", no "por semana completa como NFL".

Uso básico:
    from mlb_scraper import obtener_partidos_mlb
    partidos = obtener_partidos_mlb("20261003")           # un día
    partidos = obtener_partidos_mlb_rango("20260401", "20260410")  # rango

Uso de detalle (mismo shape que NFL: boxscore con categorías 'batting' y
'pitching', cada una con labels + athletes[]):
    from mlb_scraper import obtener_detalle_partido
    detalle = obtener_detalle_partido("401815765")

tipo_temporada (igual idea que en NFL, valores de ESPN):
    1 = Preseason (spring training), 2 = Regular Season, 3 = Postseason
"""
from __future__ import annotations
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

ESPN_SCOREBOARD = "https://site.web.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
ESPN_SUMMARY = "https://site.web.api.espn.com/apis/site/v2/sports/baseball/mlb/summary"
ESPN_ROSTER = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams/{team}/roster"
ESPN_LESIONES = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/injuries"

_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
    "Referer": "https://www.espn.com/",
    "Origin": "https://www.espn.com",
    "Connection": "keep-alive",
}

_ESTADO_MAP = {
    "STATUS_SCHEDULED": "programado",
    "STATUS_IN_PROGRESS": "en_vivo",
    "STATUS_END_PERIOD": "en_vivo",
    "STATUS_FINAL": "finalizado",
    "STATUS_POSTPONED": "programado",
    "STATUS_CANCELED": "cancelado",
    "STATUS_SUSPENDED": "programado",
}

# Nombre completo (displayName de ESPN) -> abreviatura para roster/injuries.
# Los 30 equipos de MLB, abreviaturas oficiales de ESPN.
MLB_ABREV = {
    "Arizona Diamondbacks": "ari", "Atlanta Braves": "atl", "Baltimore Orioles": "bal",
    "Boston Red Sox": "bos", "Chicago Cubs": "chc", "Chicago White Sox": "chw",
    "Cincinnati Reds": "cin", "Cleveland Guardians": "cle", "Colorado Rockies": "col",
    "Detroit Tigers": "det", "Houston Astros": "hou", "Kansas City Royals": "kc",
    "Los Angeles Angels": "laa", "Los Angeles Dodgers": "lad", "Miami Marlins": "mia",
    "Milwaukee Brewers": "mil", "Minnesota Twins": "min", "New York Mets": "nym",
    "New York Yankees": "nyy", "Oakland Athletics": "oak", "Athletics": "oak",
    "Philadelphia Phillies": "phi", "Pittsburgh Pirates": "pit", "San Diego Padres": "sd",
    "San Francisco Giants": "sf", "Seattle Mariners": "sea", "St. Louis Cardinals": "stl",
    "Tampa Bay Rays": "tb", "Texas Rangers": "tex", "Toronto Blue Jays": "tor",
    "Washington Nationals": "wsh",
}


def _fetch(url: str, timeout: int = 12) -> dict:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _num(v):
    if v in (None, "", "--"):
        return None
    s = str(v).replace("%", "")
    try:
        return int(s)
    except Exception:
        pass
    try:
        return float(s)
    except Exception:
        return v


def _abrev_equipo(nombre_equipo: str) -> str | None:
    if nombre_equipo in MLB_ABREV:
        return MLB_ABREV[nombre_equipo]
    nl = nombre_equipo.lower()
    for nombre, abrev in MLB_ABREV.items():
        if nl in nombre.lower() or nombre.lower() in nl:
            return abrev
    return None


# ═══════════════════════════════════════════════════════════════════
#  CAPA 1 — CALENDARIO / RESULTADOS, por fecha (no por semana)
# ═══════════════════════════════════════════════════════════════════
def obtener_partidos_mlb(fecha: str, tipo_temporada: int | None = None) -> list[dict]:
    """
    Partidos de MLB de UN día. fecha en formato YYYYMMDD.
    tipo_temporada: si se pasa, se usa para forzar el campo (normalmente
    no hace falta — ESPN ya dice en cada evento si es regular/postseason).
    """
    url = ESPN_SCOREBOARD + "?" + urllib.parse.urlencode({"dates": fecha})
    try:
        data = _fetch(url)
    except Exception as e:
        print(f"[mlb_scraper] error al consultar ESPN ({fecha}): {e}")
        return []

    eventos = data.get("events", []) or []
    partidos: list[dict] = []
    for ev in eventos:
        try:
            comp = (ev.get("competitions") or [{}])[0]
            competidores = comp.get("competitors", []) or []
            if len(competidores) < 2:
                continue
            local = visitante = None
            gl = gv = None
            for c in competidores:
                nombre = (c.get("team", {}) or {}).get("displayName", "")
                puntos = c.get("score")
                puntos = int(puntos.get("value")) if isinstance(puntos, dict) else \
                         (int(puntos) if puntos not in (None, "") else None)
                if c.get("homeAway") == "home":
                    local, gl = nombre, puntos
                else:
                    visitante, gv = nombre, puntos
            if not local or not visitante:
                continue

            estado_espn = (ev.get("status", {}) or {}).get("type", {}).get("name", "")
            estado = _ESTADO_MAP.get(estado_espn, "programado")

            _tipo = tipo_temporada
            try:
                _tipo = (data.get("leagues", [{}])[0].get("season", {}) or {}).get("type", {}).get("type", tipo_temporada)
            except Exception:
                pass

            partidos.append({
                "deporte": "beisbol",
                "liga": "mlb",
                "temporada": str((data.get("leagues", [{}])[0].get("season", {}) or {}).get("year", "")),
                "tipo_temporada": _tipo,  # 1=spring, 2=regular, 3=postseason
                "local": local,
                "visitante": visitante,
                "inicio": ev.get("date", ""),
                "estado": estado,
                "marcadorLocal": gl,
                "marcadorVisitante": gv,
                "fase": fecha,   # aquí 'fase' es la fecha YYYYMMDD, no una jornada
                "fuenteId": str(ev.get("id", "")),
            })
        except Exception as e:
            print(f"[mlb_scraper] evento saltado ({fecha}): {e}")
            continue
    return partidos


def obtener_partidos_mlb_rango(fecha_inicio: str, fecha_fin: str,
                               pausa_seg: float = 0.3) -> list[dict]:
    """Recorre un rango de fechas (YYYYMMDD a YYYYMMDD, inclusive) día por
    día — ESPN no soporta rango en una sola llamada para MLB de forma
    confiable, así que se pide un día a la vez con una pausa chica."""
    d0 = datetime.strptime(fecha_inicio, "%Y%m%d")
    d1 = datetime.strptime(fecha_fin, "%Y%m%d")
    if d1 < d0:
        d0, d1 = d1, d0
    partidos = []
    dias = (d1 - d0).days + 1
    for i in range(dias):
        dia = (d0 + timedelta(days=i)).strftime("%Y%m%d")
        partidos.extend(obtener_partidos_mlb(dia))
        if i < dias - 1:
            time.sleep(pausa_seg)
    return partidos


# ═══════════════════════════════════════════════════════════════════
#  CAPA 2 — DETALLE DE UN PARTIDO (mismo shape que NFL: boxscore con
#  categorías 'batting' y 'pitching', cada una labels[] + athletes[])
# ═══════════════════════════════════════════════════════════════════
def _parse_equipo_stats(boxscore: dict) -> list[dict]:
    salida = []
    for t in (boxscore.get("teams") or []):
        team = t.get("team", {}) or {}
        stats = {}
        for s in (t.get("statistics") or []):
            etiqueta = s.get("label") or s.get("name") or ""
            valor = s.get("displayValue")
            if etiqueta:
                stats[etiqueta] = _num(valor)
        salida.append({
            "equipo": team.get("displayName", ""),
            "abrev": team.get("abbreviation", ""),
            "casa_visita": t.get("homeAway", ""),
            "stats": stats,
        })
    return salida


def _parse_jugadores(boxscore: dict) -> list[dict]:
    """Igual lógica que nfl_scraper._parse_jugadores: aplana
    players[].statistics[].athletes[] a {jugador, jugadorId, equipo,
    categoria, stats:{label:val}}. Las categorías en MLB son
    'batting' y 'pitching' (en vez de passing/rushing/receiving)."""
    salida = []
    for grupo in (boxscore.get("players") or []):
        team = grupo.get("team", {}) or {}
        equipo = team.get("displayName", "")
        for cat in (grupo.get("statistics") or []):
            categoria = cat.get("name", "") or cat.get("text", "")
            labels = cat.get("labels") or []
            for atl in (cat.get("athletes") or []):
                ath = atl.get("athlete", {}) or {}
                vals = atl.get("stats") or []
                stats = {}
                for i, lab in enumerate(labels):
                    if i < len(vals):
                        stats[lab] = _num(vals[i])
                if not stats:
                    continue
                salida.append({
                    "jugador": ath.get("displayName", ""),
                    "jugadorId": str(ath.get("id", "")),
                    "posicion": (ath.get("position", {}) or {}).get("abbreviation", ""),
                    "equipo": equipo,
                    "categoria": categoria,
                    "stats": stats,
                })
    return salida


def _parse_resumen(data: dict) -> dict:
    res = {}
    try:
        header = (data.get("header", {}) or {})
        comp = (header.get("competitions") or [{}])[0]
        competidores = comp.get("competitors", []) or []
        for c in competidores:
            team = c.get("team", {}) or {}
            if c.get("homeAway") == "home":
                res["local"] = team.get("displayName", "")
                res["marcadorLocal"] = _num(c.get("score"))
            else:
                res["visitante"] = team.get("displayName", "")
                res["marcadorVisitante"] = _num(c.get("score"))
        res["estado"] = (comp.get("status", {}) or {}).get("type", {}).get("name", "")
    except Exception:
        pass
    return res


def obtener_detalle_partido(game_id: str) -> dict | None:
    """Data completa de un partido de MLB: equipo_stats + jugadores
    (bateo/pitcheo) + raw. No hay 'drives' en beisbol (eso es de NFL)."""
    try:
        data = _fetch(ESPN_SUMMARY + "?" + urllib.parse.urlencode({"event": game_id}), timeout=15)
    except Exception as e:
        print(f"[mlb_scraper] summary {game_id} falló: {e}")
        return None
    boxscore = data.get("boxscore", {}) or {}
    return {
        "fuenteId": str(game_id),
        "liga": "mlb",
        "deporte": "beisbol",
        "capturado": datetime.now(timezone.utc).isoformat(),
        "resumen": _parse_resumen(data),
        "equipo_stats": _parse_equipo_stats(boxscore),
        "jugadores": _parse_jugadores(boxscore),
        "raw": data,
    }


# ═══════════════════════════════════════════════════════════════════
#  CAPA 3 — ROSTER ACTIVO Y LESIONES (igual patrón que NFL)
# ═══════════════════════════════════════════════════════════════════
def obtener_roster_equipo(nombre_equipo: str) -> dict:
    abrev = _abrev_equipo(nombre_equipo)
    if not abrev:
        return {"equipo": nombre_equipo, "jugadores": [],
                "error": f"No reconozco el equipo '{nombre_equipo}' (no está en MLB_ABREV)"}
    try:
        data = _fetch(ESPN_ROSTER.format(team=abrev), timeout=15)
    except Exception as e:
        print(f"[mlb_scraper] roster {nombre_equipo} falló: {e}")
        return {"equipo": nombre_equipo, "jugadores": [],
                "error": f"ESPN no respondió ({type(e).__name__}: {e})"}
    jugadores = []
    for grupo in (data.get("athletes") or []):
        for atl in (grupo.get("items") or []):
            lesiones = atl.get("injuries") or []
            jugadores.append({
                "nombre": atl.get("displayName", ""),
                "posicion": (atl.get("position", {}) or {}).get("abbreviation", ""),
                "dorsal": atl.get("jersey", ""),
                "estado": atl.get("status", {}).get("name") if isinstance(atl.get("status"), dict)
                          else atl.get("status", "Active"),
                "lesion": (lesiones[0].get("status") if lesiones else None),
            })
    return {"equipo": nombre_equipo, "jugadores": jugadores, "error": None}


def obtener_lesiones_liga() -> list[dict]:
    try:
        data = _fetch(ESPN_LESIONES, timeout=15)
    except Exception as e:
        print(f"[mlb_scraper] lesiones falló: {e}")
        return []
    salida = []
    for t in (data.get("injuries") or []):
        lesiones = []
        for inj in (t.get("injuries") or []):
            ath = inj.get("athlete", {}) or {}
            det = inj.get("details", {}) or {}
            lesiones.append({
                "jugador": f"{ath.get('firstName','')} {ath.get('lastName','')}".strip(),
                "posicion": (ath.get("position", {}) or {}).get("abbreviation", ""),
                "estado": inj.get("status", ""),
                "detalle": inj.get("shortComment", "") or det.get("type", ""),
                "fecha": inj.get("date", ""),
            })
        salida.append({"equipo": t.get("displayName", ""), "lesiones": lesiones})
    return salida


def obtener_lesiones_equipo(nombre_equipo: str) -> list[dict]:
    todas = obtener_lesiones_liga()
    for t in todas:
        if nombre_equipo.lower() in t["equipo"].lower() or t["equipo"].lower() in nombre_equipo.lower():
            return t["lesiones"]
    return []


if __name__ == "__main__":
    hoy = datetime.now(timezone.utc).strftime("%Y%m%d")
    ps = obtener_partidos_mlb(hoy)
    print(f"{len(ps)} partidos MLB hoy ({hoy}):")
    for p in ps:
        print(f"  {p['visitante']} @ {p['local']} · {p['estado']} · "
              f"{p['marcadorVisitante']}-{p['marcadorLocal']}")
