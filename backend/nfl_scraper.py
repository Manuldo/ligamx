"""
Scraper de NFL usando la API pública (no-oficial) de ESPN.

ESPN expone endpoints JSON que no requieren API key. Los usamos para
traer el calendario, resultados y estados de los partidos de la NFL, y
—capa nueva v2— el detalle completo de cada partido: estadísticas de
equipo, box score de jugadores, drives y jugada por jugada (play-by-play).

Uso básico (igual que antes, NO cambió):
    from nfl_scraper import obtener_partidos_nfl
    partidos = obtener_partidos_nfl()          # semana actual
    partidos = obtener_partidos_nfl(semana=5)  # semana específica

Uso nuevo (data profunda de un partido):
    from nfl_scraper import obtener_detalle_partido
    detalle = obtener_detalle_partido("401671789")
    # -> {raw, resumen, equipo_stats, jugadores, drives, plays}

Filosofía: es una API no-oficial, puede cambiar sin aviso. Todo es
defensivo: si algo falta, se salta en vez de romper. La capa 'raw'
(JSON crudo de ESPN) siempre se preserva, así que aunque un parser
fino falle, nunca pierdes la data — se puede re-parsear después.

Buen ciudadano: cachea/espacia las llamadas. summary es 1 request por
partido; hay una pausa configurable entre llamadas en el helper de lote.
"""
from __future__ import annotations
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

ESPN_SCOREBOARD = "https://site.web.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
ESPN_SUMMARY = "https://site.web.api.espn.com/apis/site/v2/sports/football/nfl/summary"
_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
    "Referer": "https://www.espn.com/",
    "Origin": "https://www.espn.com",
    "Connection": "keep-alive",
}

# ESPN usa estos estados; los mapeamos a los del motor
_ESTADO_MAP = {
    "STATUS_SCHEDULED": "programado",
    "STATUS_IN_PROGRESS": "en_vivo",
    "STATUS_HALFTIME": "en_vivo",
    "STATUS_END_PERIOD": "en_vivo",
    "STATUS_FINAL": "finalizado",
    "STATUS_FINAL_OVERTIME": "finalizado",
    "STATUS_POSTPONED": "programado",
    "STATUS_CANCELED": "cancelado",
}


def _fetch(url: str, timeout: int = 12) -> dict:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ═══════════════════════════════════════════════════════════════════
#  CAPA 1 — CALENDARIO / RESULTADOS (tu función original, SIN CAMBIOS)
# ═══════════════════════════════════════════════════════════════════
def obtener_partidos_nfl(semana: int | None = None,
                         temporada: int | None = None,
                         tipo_temporada: int = 2) -> list[dict]:
    """
    Trae los partidos de la NFL desde ESPN.

    semana: número de semana (1-18 regular). None = semana actual.
    temporada: año (ej. 2026). None = temporada actual de ESPN.
    tipo_temporada: 1=pretemporada, 2=temporada regular, 3=playoffs.

    Devuelve lista de partidos en formato PartidoIn del motor.
    """
    from datetime import datetime as _dt
    if temporada is None:
        temporada = _dt.now(timezone.utc).year

    params = {}
    if semana is not None:
        # Para pedir una SEMANA específica, ESPN usa year+seasontype+week.
        # NO se manda 'dates' con solo el año, porque eso hace que ESPN
        # ignore 'week' y devuelva otra semana (bug: todo salía Semana 18).
        params["seasontype"] = tipo_temporada
        params["year"] = temporada
        params["week"] = semana
    # Sin semana: NO se manda nada. ESPN devuelve por sí solo la semana
    # ACTUAL y nos dice en qué tipo de temporada estamos (season.type).
    # Antes se mandaba dates=año, que hacía que ESPN devolviera otra semana
    # (la misma confusión que producía el "Semana 18" en todos los partidos).
    url = ESPN_SCOREBOARD + ("?" + urllib.parse.urlencode(params) if params else "")

    try:
        data = _fetch(url)
    except Exception as e:
        print(f"[nfl_scraper] error al consultar ESPN: {e}")
        return []

    eventos = data.get("events", []) or []
    # Sin semana pedida, el tipo real de temporada lo dice ESPN (1/2/3);
    # así 'tipo_temporada' nunca queda mal aunque el motor pase otro valor.
    if semana is None:
        try:
            _t = (data.get("season", {}) or {}).get("type")
            if _t in (1, 2, 3):
                tipo_temporada = _t
        except Exception:
            pass
    fase_semana = ""
    try:
        wk = data.get("week", {}).get("number")
        if wk:
            fase_semana = f"Semana {wk}"
    except Exception:
        pass

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
                puntos = int(puntos) if (puntos not in (None, "")) else None
                if c.get("homeAway") == "home":
                    local, gl = nombre, puntos
                else:
                    visitante, gv = nombre, puntos
            if not local or not visitante:
                continue

            estado_espn = (ev.get("status", {}) or {}).get("type", {}).get("name", "")
            estado = _ESTADO_MAP.get(estado_espn, "programado")

            # Semana del partido:
            # - Si se PIDIÓ una semana específica, esa manda (es la correcta,
            #   porque ESPN filtró por ella). Esto arregla el sync por semana.
            # - Si NO se pidió (sync automático de la semana actual), se deduce
            #   de cada evento, o cae a la global.
            if semana is not None:
                fase_partido = f"Semana {semana}"
            else:
                fase_partido = fase_semana
                try:
                    wk_ev = ev.get("week", {}).get("number")
                    if wk_ev:
                        fase_partido = f"Semana {wk_ev}"
                except Exception:
                    pass

            partidos.append({
                "deporte": "americano",
                "liga": "nfl",
                "temporada": str(data.get("season", {}).get("year", temporada or "")),
                "tipo_temporada": tipo_temporada,  # 1=pre, 2=regular, 3=playoffs
                "local": local,
                "visitante": visitante,
                "inicio": ev.get("date", ""),
                "estado": estado,
                "marcadorLocal": gl,
                "marcadorVisitante": gv,
                "fase": fase_partido,
                "fuenteId": str(ev.get("id", "")),
            })
        except Exception as e:
            print(f"[nfl_scraper] evento saltado: {e}")
            continue

    return partidos


# ═══════════════════════════════════════════════════════════════════
#  CAPA 2 — DETALLE PROFUNDO DE UN PARTIDO (nuevo)
#  summary?event={id} trae en UN request: boxscore (equipo+jugadores),
#  drives, y plays (play-by-play). Parseamos cada bloque de forma
#  defensiva y SIEMPRE guardamos el crudo.
# ═══════════════════════════════════════════════════════════════════

def _num(v):
    """Convierte a número si se puede; si no, devuelve el string tal cual."""
    if v is None or v == "":
        return None
    try:
        if isinstance(v, (int, float)):
            return v
        s = str(v).strip()
        if s.replace(".", "", 1).replace("-", "", 1).isdigit():
            return float(s) if "." in s else int(s)
        return s
    except Exception:
        return v


def obtener_summary(game_id: str, timeout: int = 15) -> dict | None:
    """Trae el summary crudo de un partido. None si falla."""
    url = ESPN_SUMMARY + "?" + urllib.parse.urlencode({"event": game_id})
    try:
        return _fetch(url, timeout=timeout)
    except Exception as e:
        print(f"[nfl_scraper] summary {game_id} falló: {e}")
        return None


def _parse_equipo_stats(boxscore: dict) -> list[dict]:
    """
    boxscore.teams[] -> stats de equipo (yardas, posesión, 3er down, etc.)
    Cada equipo: {equipo, abrev, casa_visita, stats:{label: value}}
    """
    salida = []
    for t in (boxscore.get("teams") or []):
        team = t.get("team", {}) or {}
        stats = {}
        for s in (t.get("statistics") or []):
            # ESPN: {name, displayValue, label} — usamos label legible
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
    """
    boxscore.players[] -> por equipo, varias categorías (passing, rushing,
    receiving, defensive, etc.), cada una con 'labels' + 'athletes[]'.
    Aplanamos a: {jugador, jugadorId, equipo, categoria, stats:{label:val}}
    """
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
                # emparejar labels con valores por posición
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


def _parse_drives(data: dict) -> list[dict]:
    """
    data.drives.previous[] -> cada drive con equipo, resultado, jugadas,
    yardas, tiempo. Defensivo ante variaciones de estructura.
    """
    salida = []
    drives_root = data.get("drives") or {}
    lista = drives_root.get("previous") or []
    # a veces vienen en 'current' cuando el partido está en vivo
    if drives_root.get("current"):
        lista = lista + [drives_root["current"]]
    for d in lista:
        try:
            team = d.get("team", {}) or {}
            salida.append({
                "equipo": team.get("displayName", "") or team.get("shortDisplayName", ""),
                "abrev": team.get("abbreviation", ""),
                "resultado": d.get("displayResult", "") or d.get("result", ""),
                "jugadas": _num((d.get("plays") if isinstance(d.get("plays"), int)
                                 else len(d.get("plays", []) or []))),
                "yardas": _num(d.get("yards")),
                "descripcion": d.get("description", ""),
                "puntos_marca": bool(d.get("isScore", False)),
                "periodo": (d.get("start", {}) or {}).get("period", {}).get("number"),
                "tiempo_inicio": (d.get("start", {}) or {}).get("clock", {}).get("displayValue", ""),
                "tiempo_fin": (d.get("end", {}) or {}).get("clock", {}).get("displayValue", ""),
            })
        except Exception:
            continue
    return salida


def _parse_plays(data: dict, limite: int | None = None) -> list[dict]:
    """
    Jugada por jugada. En site.web.api las plays NO vienen en data.plays
    (raíz), sino anidadas dentro de cada drive (drives.previous[].plays[]).
    Buscamos en la raíz primero (por compatibilidad) y si no, las juntamos
    recorriendo los drives. Cada play: texto, tipo, down/distance, reloj, etc.
    """
    plays = data.get("plays") or []
    if not plays:
        # reunir las plays desde los drives
        drives_root = data.get("drives") or {}
        lista_drives = drives_root.get("previous") or []
        if drives_root.get("current"):
            lista_drives = lista_drives + [drives_root["current"]]
        for d in lista_drives:
            if isinstance(d, dict) and isinstance(d.get("plays"), list):
                plays.extend(d["plays"])
    if limite:
        plays = plays[:limite]
    salida = []
    for p in plays:
        try:
            tipo = (p.get("type", {}) or {}).get("text", "")
            salida.append({
                "id": str(p.get("id", "")),
                "texto": p.get("text", ""),
                "tipo": tipo,
                "periodo": (p.get("period", {}) or {}).get("number"),
                "reloj": (p.get("clock", {}) or {}).get("displayValue", ""),
                "down": p.get("start", {}).get("down") if p.get("start") else None,
                "distancia": p.get("start", {}).get("distance") if p.get("start") else None,
                "yarda": p.get("start", {}).get("yardLine") if p.get("start") else None,
                "scoring": bool(p.get("scoringPlay", False)),
                "puntos": _num(p.get("scoreValue")),
                "equipoId": str((p.get("team", {}) or {}).get("id", "")) if p.get("team") else "",
            })
        except Exception:
            continue
    return salida


def _parse_resumen(data: dict) -> dict:
    """Cabecera compacta del partido desde el summary (equipos, marcador)."""
    header = data.get("header", {}) or {}
    comp = ((header.get("competitions") or [{}])[0]) if header else {}
    competidores = comp.get("competitors", []) or []
    res = {"fuenteId": str(header.get("id", "")), "liga": "nfl", "deporte": "americano"}
    for c in competidores:
        team = c.get("team", {}) or {}
        lado = c.get("homeAway", "")
        if lado == "home":
            res["local"] = team.get("displayName", "")
            res["abrevLocal"] = team.get("abbreviation", "")
            res["marcadorLocal"] = _num(c.get("score"))
        elif lado == "away":
            res["visitante"] = team.get("displayName", "")
            res["abrevVisitante"] = team.get("abbreviation", "")
            res["marcadorVisitante"] = _num(c.get("score"))
    # línea por cuarto si está
    try:
        res["por_cuarto"] = {
            c.get("homeAway"): [ln.get("displayValue") for ln in (c.get("linescores") or [])]
            for c in competidores
        }
    except Exception:
        pass
    return res


def obtener_detalle_partido(game_id: str, incluir_plays: bool = True,
                            limite_plays: int | None = None) -> dict | None:
    """
    Data COMPLETA de un partido: crudo + equipo + jugadores + drives + plays.

    game_id: id de ESPN (el 'fuenteId').
    incluir_plays: si False, omite el play-by-play (ahorra tamaño).
    limite_plays: cap opcional de jugadas (None = todas).

    Devuelve dict o None si el summary no cargó.
    La clave 'raw' preserva el JSON de ESPN tal cual (red de seguridad).
    """
    data = obtener_summary(game_id)
    if not data:
        return None

    boxscore = data.get("boxscore", {}) or {}
    detalle = {
        "fuenteId": str(game_id),
        "liga": "nfl",
        "deporte": "americano",
        "capturado": datetime.now(timezone.utc).isoformat(),
        "resumen": _parse_resumen(data),
        "equipo_stats": _parse_equipo_stats(boxscore),
        "jugadores": _parse_jugadores(boxscore),
        "drives": _parse_drives(data),
        "plays": _parse_plays(data, limite_plays) if incluir_plays else [],
        "raw": data,  # crudo completo — nunca perdemos nada
    }
    return detalle


def obtener_detalles_semana(semana: int | None = None,
                            temporada: int | None = None,
                            tipo_temporada: int = 2,
                            pausa_seg: float = 1.0,
                            incluir_plays: bool = True) -> list[dict]:
    """
    Combina las dos capas: lista los partidos de la semana y, para cada
    uno, trae el detalle profundo. Mete una pausa entre summaries para
    ser buen ciudadano con la API no-oficial.

    Devuelve lista de detalles (los mismos dicts de obtener_detalle_partido),
    saltando los que fallen.
    """
    partidos = obtener_partidos_nfl(semana=semana, temporada=temporada,
                                    tipo_temporada=tipo_temporada)
    detalles = []
    for i, p in enumerate(partidos):
        gid = p.get("fuenteId")
        if not gid:
            continue
        det = obtener_detalle_partido(gid, incluir_plays=incluir_plays)
        if det:
            # arrastramos algunos campos del scoreboard que el summary no da igual
            det["resumen"].setdefault("inicio", p.get("inicio", ""))
            det["resumen"].setdefault("estado", p.get("estado", ""))
            det["resumen"].setdefault("fase", p.get("fase", ""))
            det["resumen"].setdefault("temporada", p.get("temporada", ""))
            detalles.append(det)
        if i < len(partidos) - 1:
            time.sleep(pausa_seg)
    return detalles


# ═══════════════════════════════════════════════════════════════════
#  CAPA 3 — ROSTER ACTIVO Y REPORTE DE LESIONES
#  Esto NO existe en el summary del partido (boxscore solo trae a los
#  que jugaron ese día). Son dos endpoints públicos aparte de ESPN.
#  NO es la misma idea que "alineaciones" de soccer (titulares probables
#  confirmados ~1h antes): la NFL no publica eso así. Lo útil aquí es
#  "quién está disponible" (roster + estado de lesión).
# ═══════════════════════════════════════════════════════════════════

ESPN_ROSTER = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{team}/roster"
ESPN_LESIONES = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"

# Nombre completo (como lo guarda nfl_scraper/db.partidos vía displayName
# de ESPN) -> abreviatura de equipo que usa la URL del roster. Estático,
# los 32 equipos no cambian de abreviatura.
NFL_ABREV = {
    "Arizona Cardinals": "ari", "Atlanta Falcons": "atl", "Baltimore Ravens": "bal",
    "Buffalo Bills": "buf", "Carolina Panthers": "car", "Chicago Bears": "chi",
    "Cincinnati Bengals": "cin", "Cleveland Browns": "cle", "Dallas Cowboys": "dal",
    "Denver Broncos": "den", "Detroit Lions": "det", "Green Bay Packers": "gb",
    "Houston Texans": "hou", "Indianapolis Colts": "ind", "Jacksonville Jaguars": "jax",
    "Kansas City Chiefs": "kc", "Las Vegas Raiders": "lv", "Los Angeles Chargers": "lac",
    "Los Angeles Rams": "lar", "Miami Dolphins": "mia", "Minnesota Vikings": "min",
    "New England Patriots": "ne", "New Orleans Saints": "no", "New York Giants": "nyg",
    "New York Jets": "nyj", "Philadelphia Eagles": "phi", "Pittsburgh Steelers": "pit",
    "San Francisco 49ers": "sf", "Seattle Seahawks": "sea", "Tampa Bay Buccaneers": "tb",
    "Tennessee Titans": "ten", "Washington Commanders": "wsh",
}


def _abrev_equipo(nombre_equipo: str) -> str | None:
    """Resuelve el nombre de un equipo (como lo tenemos guardado) a su
    abreviatura de ESPN, tolerando coincidencia parcial (ej. 'Steelers')."""
    if nombre_equipo in NFL_ABREV:
        return NFL_ABREV[nombre_equipo]
    nl = nombre_equipo.lower()
    for nombre, abrev in NFL_ABREV.items():
        if nl in nombre.lower() or nombre.lower() in nl:
            return abrev
    return None


def obtener_roster_equipo(nombre_equipo: str) -> dict:
    """Roster activo de un equipo: {equipo, jugadores:[...], error: str|None}.
    Antes regresaba None en cualquier falla y eso se perdía silenciosamente
    (el panel solo mostraba '(0)' sin decir por qué) — ahora siempre
    regresa un dict con el motivo exacto si algo falló, para poder
    diagnosticar sin tener que entrar a los logs de Railway."""
    abrev = _abrev_equipo(nombre_equipo)
    if not abrev:
        return {"equipo": nombre_equipo, "jugadores": [],
                "error": f"No reconozco el equipo '{nombre_equipo}' (no está en NFL_ABREV)"}
    try:
        data = _fetch(ESPN_ROSTER.format(team=abrev), timeout=15)
    except Exception as e:
        print(f"[nfl_scraper] roster {nombre_equipo} falló: {e}")
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
    """Reporte de lesiones de TODA la liga en una sola llamada:
    [{equipo, lesiones:[{jugador, posicion, estado, detalle, fecha}]}]."""
    try:
        data = _fetch(ESPN_LESIONES, timeout=15)
    except Exception as e:
        print(f"[nfl_scraper] lesiones falló: {e}")
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
    """Filtra el reporte de lesiones de toda la liga a un solo equipo."""
    todas = obtener_lesiones_liga()
    for t in todas:
        if nombre_equipo.lower() in t["equipo"].lower() or t["equipo"].lower() in nombre_equipo.lower():
            return t["lesiones"]
    return []


if __name__ == "__main__":
    # prueba manual: python nfl_scraper.py
    ps = obtener_partidos_nfl(tipo_temporada=1)  # pretemporada
    print(f"{len(ps)} partidos NFL (pretemporada):")
    for p in ps[:6]:
        print(f"  {p['visitante']} @ {p['local']} · {p['estado']} · "
              f"{p['marcadorVisitante']}-{p['marcadorLocal']} · {p['inicio']}")
    if ps:
        gid = ps[0]["fuenteId"]
        print(f"\nDetalle del primer partido (id {gid}):")
        det = obtener_detalle_partido(gid, limite_plays=5)
        if det:
            print(f"  equipo_stats: {len(det['equipo_stats'])} equipos")
            print(f"  jugadores:    {len(det['jugadores'])} líneas")
            print(f"  drives:       {len(det['drives'])}")
            print(f"  plays:        {len(det['plays'])} (limitado a 5)")
