"""
alineaciones.py — Trae alineaciones (titulares) desde API-Football.

Por qué API-Football y no FotMob:
  - FotMob bloquea llamadas desde servidor (403). API-Football sí las permite.
  - API-Football marca si la alineación es CONFIRMADA (oficial, ~1h antes)
    o si aún no está publicada.

Estrategia (para cuidar el límite de 100 req/día del plan gratis):
  - 1 llamada trae los fixtures de un día de una liga.
  - Se cruzan con los partidos de tu base por nombre de equipo (normalizado).
  - Se cachea en Mongo: si ya tenemos la alineación, no se re-pide.

Requiere la variable de entorno APIFOOTBALL_KEY.
"""

import os
import unicodedata
import httpx
from datetime import datetime, timezone

API_BASE = "https://v3.football.api-sports.io"
# Key PRINCIPAL (marcadores en vivo, fixtures). La de siempre.
API_KEY = os.environ.get("APIFOOTBALL_KEY", "").strip()

# Key DEDICADA a alineaciones (segunda cuenta gratis de API-Football, para no
# competir por la cuota con marcadores/stats). Si no está configurada, usa la
# key principal como respaldo. Se configura en Railway, NUNCA en el código.
API_KEY_ALINEACIONES = (os.environ.get("APIFOOTBALL_KEY_ALINEACIONES", "").strip()
                        or API_KEY)

# IDs de liga en API-Football (se confirman al registrarse; estos son los oficiales)
LIGA_IDS = {
    "ligamx": 262,       # Liga MX
    "premier": 39,       # Premier League
    "laliga": 140,       # La Liga
    "seriea": 135,       # Serie A
    "bundesliga": 78,    # Bundesliga
    "ligue1": 61,        # Ligue 1
    "championsleague": 2,
    "europaleague": 3,
    "mls": 253,
}


def _norm(s: str) -> str:
    """Normaliza un nombre de equipo para comparar: sin acentos, minúsculas, sin ruido."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower().strip()
    # quitar palabras/ruido común que difiere entre proveedores
    for ruido in [" fc", "fc ", " cf", "cf ", "club ", " club", "deportivo ",
                  "atletico", "atl.", "cd ", " cd", "real "]:
        s = s.replace(ruido, " ")
    return " ".join(s.split())


def _headers(para_alineaciones: bool = True):
    # Por defecto usa la key dedicada de alineaciones (la mayoría de funciones
    # aquí son de alineaciones). Los marcadores pasan para_alineaciones=False.
    key = API_KEY_ALINEACIONES if para_alineaciones else API_KEY
    return {"x-apisports-key": key}


def hay_api_key() -> bool:
    return bool(API_KEY_ALINEACIONES)


def traer_fixtures_dia(liga_slug: str, fecha_iso: str) -> list[dict]:
    """
    Trae los fixtures de una liga en una fecha (YYYY-MM-DD).
    1 sola request. Devuelve lista de fixtures con sus IDs.
    """
    if not API_KEY:
        return []
    liga_id = LIGA_IDS.get(liga_slug)
    if not liga_id:
        return []
    # la temporada suele ser el año de inicio; API-Football la infiere por fecha
    anio = fecha_iso[:4]
    try:
        r = httpx.get(
            f"{API_BASE}/fixtures",
            headers=_headers(),
            params={"league": liga_id, "season": anio, "date": fecha_iso},
            timeout=15,
        )
        if r.status_code != 200:
            return []
        return r.json().get("response", [])
    except Exception:
        return []


def traer_alineacion_fixture(fixture_id: int) -> dict | None:
    """
    Trae la alineación de un fixture concreto.
    Devuelve {confirmada: bool, equipos: [{nombre, formacion, titulares:[...], suplentes:[...]}]}
    """
    if not API_KEY:
        return None
    try:
        r = httpx.get(
            f"{API_BASE}/fixtures/lineups",
            headers=_headers(),
            params={"fixture": fixture_id},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        resp = r.json().get("response", [])
        if not resp:
            # sin alineación publicada aún
            return {"confirmada": False, "equipos": []}

        equipos = []
        for eq in resp:
            titulares = [
                {
                    "nombre": (j.get("player") or {}).get("name", ""),
                    "numero": (j.get("player") or {}).get("number"),
                    "posicion": (j.get("player") or {}).get("pos", ""),
                }
                for j in (eq.get("startXI") or [])
            ]
            suplentes = [
                {"nombre": (j.get("player") or {}).get("name", "")}
                for j in (eq.get("substitutes") or [])
            ]
            equipos.append({
                "nombre": (eq.get("team") or {}).get("name", ""),
                "formacion": eq.get("formation", ""),
                "titulares": titulares,
                "suplentes": suplentes,
            })
        # si hay startXI, la consideramos confirmada (API-Football solo publica
        # la oficial; la predicha es un add-on aparte que no usamos)
        confirmada = any(e["titulares"] for e in equipos)
        return {"confirmada": confirmada, "equipos": equipos}
    except Exception:
        return None


def emparejar_equipo(nombre_base: str, nombre_api: str) -> bool:
    """Compara dos nombres de equipo de proveedores distintos."""
    a, b = _norm(nombre_base), _norm(nombre_api)
    if not a or not b:
        return False
    # match exacto normalizado, o uno contenido en el otro (para abreviaciones)
    return a == b or a in b or b in a


def actualizar_alineaciones_liga(db, liga_slug: str, fecha_iso: str) -> dict:
    """
    Flujo completo para una liga en una fecha:
      1. Trae fixtures del día (1 request)
      2. Para cada fixture con partido tuyo, trae su alineación
      3. Cruza por nombre de equipo y guarda en tu base (colección 'alineaciones')
    Devuelve resumen de lo actualizado.
    """
    if not API_KEY:
        return {"ok": False, "error": "Falta APIFOOTBALL_KEY en las variables de entorno"}

    fixtures = traer_fixtures_dia(liga_slug, fecha_iso)
    if not fixtures:
        return {"ok": True, "actualizados": 0, "nota": "Sin fixtures ese día (o liga no disponible en tu plan)"}

    # tus partidos de esa liga (para cruzar)
    mis_partidos = list(db.partidos.find(
        {"liga": liga_slug}, {"_id": 0, "local": 1, "visitante": 1, "fuenteId": 1}
    ))

    actualizados = 0
    detalles = []
    for fx in fixtures:
        info = fx.get("fixture", {})
        teams = fx.get("teams", {})
        fixture_id = info.get("id")
        home = (teams.get("home") or {}).get("name", "")
        away = (teams.get("away") or {}).get("name", "")

        # ¿este fixture corresponde a un partido tuyo?
        mio = None
        for p in mis_partidos:
            if emparejar_equipo(p["local"], home) and emparejar_equipo(p["visitante"], away):
                mio = p
                break
        if not mio:
            continue

        ali = traer_alineacion_fixture(fixture_id)
        if not ali or not ali.get("equipos"):
            continue

        # guardar (cache) ligado a tu partido
        clave = f"{liga_slug}:{mio['local']}:{mio['visitante']}"
        db.alineaciones.update_one(
            {"clave": clave},
            {"$set": {
                "clave": clave,
                "liga": liga_slug,
                "local": mio["local"],
                "visitante": mio["visitante"],
                "confirmada": ali["confirmada"],
                "equipos": ali["equipos"],
                "fixture_id": fixture_id,
                "actualizado": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )
        actualizados += 1
        detalles.append({
            "partido": f"{mio['local']} vs {mio['visitante']}",
            "confirmada": ali["confirmada"],
        })

    return {"ok": True, "actualizados": actualizados, "detalles": detalles}


# ═══════════════════════════════════════════════════════════════════
#   VERSIÓN EFICIENTE: solo partidos que empiezan pronto
#   1 request trae TODOS los fixtures del día (todas las ligas)
#   Solo se pide alineación de los que arrancan en la ventana horaria
# ═══════════════════════════════════════════════════════════════════

# slug por id de liga (inverso de LIGA_IDS) para etiquetar
_ID_A_SLUG = {v: k for k, v in LIGA_IDS.items()}


def traer_fixtures_dia_todos(fecha_iso: str) -> list[dict]:
    """
    1 SOLA request: trae TODOS los fixtures de una fecha (todas las ligas).
    Filtramos después por las ligas que nos interesan.
    """
    if not API_KEY:
        return []
    try:
        r = httpx.get(
            f"{API_BASE}/fixtures",
            headers=_headers(),
            params={"date": fecha_iso},
            timeout=20,
        )
        if r.status_code != 200:
            return []
        return r.json().get("response", [])
    except Exception:
        return []


def actualizar_alineaciones_proximas(db, ventana_horas: float = 3.0, margen_retro_horas: float = 0.0) -> dict:
    """
    Trae alineaciones de partidos que empiezan en las próximas
    'ventana_horas' horas (donde ya hay titulares confirmados, ~1h antes).

    margen_retro_horas: además de los que faltan por arrancar, también
    reintenta partidos que YA arrancaron hace hasta ese margen. Sin esto,
    un partido que se pasó de la ventana (API lento en publicar, el server
    se reinició justo en esos minutos, etc.) se queda SIN alineación para
    siempre, porque una vez que el kickoff pasa, el filtro normal lo
    descarta y nadie vuelve a pedirla.

    Eficiencia:
      - 1 request para todos los fixtures de hoy (y mañana si la ventana cruza medianoche)
      - Solo pide alineación de los partidos dentro de la ventana → ahorra requests
      - Cachea: si ya tenemos alineación confirmada de un partido, no la re-pide
    """
    if not API_KEY:
        return {"ok": False, "error": "Falta APIFOOTBALL_KEY"}

    ahora = datetime.now(timezone.utc)
    limite = ahora.timestamp() + ventana_horas * 3600
    desde = ahora.timestamp() - margen_retro_horas * 3600

    # fechas a consultar (hoy; y mañana si la ventana cruza la medianoche UTC)
    fechas = [ahora.strftime("%Y-%m-%d")]
    fin_ventana = datetime.fromtimestamp(limite, timezone.utc)
    if fin_ventana.strftime("%Y-%m-%d") != fechas[0]:
        fechas.append(fin_ventana.strftime("%Y-%m-%d"))

    # 1 request por fecha (normalmente solo 1)
    fixtures = []
    for f in fechas:
        fixtures.extend(traer_fixtures_dia_todos(f))

    if not fixtures:
        return {"ok": True, "actualizados": 0, "revisados": 0,
                "nota": "Sin partidos en la ventana (o límite de API alcanzado)"}

    # solo ligas que seguimos, y solo dentro de la ventana horaria
    ligas_seguidas = set(LIGA_IDS.values())
    candidatos = []
    for fx in fixtures:
        liga_id = (fx.get("league") or {}).get("id")
        if liga_id not in ligas_seguidas:
            continue
        ts = (fx.get("fixture") or {}).get("timestamp")
        if not ts:
            continue
        # dentro de la ventana hacia adelante, o recién arrancado dentro
        # del margen retroactivo (para no perder el partido para siempre
        # si se nos pasó el momento justo antes del kickoff)
        if desde <= ts <= limite:
            candidatos.append(fx)

    if not candidatos:
        return {"ok": True, "actualizados": 0, "revisados": len(fixtures),
                "nota": f"Ningún partido empieza en las próximas {ventana_horas:.0f}h"}

    actualizados = 0
    detalles = []
    requests_usados = len(fechas)  # los fixtures ya gastados

    for fx in candidatos:
        liga_id = (fx.get("league") or {}).get("id")
        liga_slug = _ID_A_SLUG.get(liga_id, str(liga_id))
        teams = fx.get("teams", {})
        home = (teams.get("home") or {}).get("name", "")
        away = (teams.get("away") or {}).get("name", "")
        fixture_id = (fx.get("fixture") or {}).get("id")

        # ¿corresponde a un partido tuyo?
        mis = list(db.partidos.find({"liga": liga_slug}, {"_id": 0, "local": 1, "visitante": 1}))
        mio = None
        for p in mis:
            if emparejar_equipo(p["local"], home) and emparejar_equipo(p["visitante"], away):
                mio = p
                break
        if not mio:
            continue

        # cache: ¿ya tenemos alineación CONFIRMADA? entonces no re-pedir
        clave = f"{liga_slug}:{mio['local']}:{mio['visitante']}"
        previo = db.alineaciones.find_one({"clave": clave}, {"_id": 0})
        if previo and previo.get("confirmada"):
            continue

        # pedir alineación (1 request)
        ali = traer_alineacion_fixture(fixture_id)
        requests_usados += 1
        if not ali or not ali.get("equipos"):
            continue

        db.alineaciones.update_one(
            {"clave": clave},
            {"$set": {
                "clave": clave, "liga": liga_slug,
                "local": mio["local"], "visitante": mio["visitante"],
                "confirmada": ali["confirmada"], "equipos": ali["equipos"],
                "fixture_id": fixture_id,
                "actualizado": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )
        actualizados += 1
        detalles.append({
            "partido": f"{mio['local']} vs {mio['visitante']}",
            "liga": liga_slug,
            "confirmada": ali["confirmada"],
        })

    return {
        "ok": True,
        "actualizados": actualizados,
        "candidatos": len(candidatos),
        "requests_usados": requests_usados,
        "detalles": detalles,
    }


def actualizar_alineaciones_jornada(db, liga: str, jornada: str) -> dict:
    """
    Trae alineaciones de los partidos de UNA jornada específica.
    Solo funciona para partidos cercanos (API-Football tiene confirmadas ~1h antes;
    para partidos lejanos puede no haber datos aún).
    """
    if not API_KEY:
        return {"ok": False, "error": "Falta APIFOOTBALL_KEY"}

    # partidos de esa jornada
    mis = list(db.partidos.find(
        {"liga": liga, "fase": jornada},
        {"_id": 0, "local": 1, "visitante": 1, "inicio": 1}
    ))
    if not mis:
        return {"ok": True, "actualizados": 0, "nota": f"Sin partidos en {jornada}"}

    # fechas únicas de esa jornada
    fechas = sorted(set((p.get("inicio", "") or "")[:10] for p in mis if p.get("inicio")))
    fixtures = []
    requests_usados = 0
    for f in fechas:
        fixtures.extend(traer_fixtures_dia_todos(f))
        requests_usados += 1

    actualizados = 0
    detalles = []
    for fx in fixtures:
        liga_id = (fx.get("league") or {}).get("id")
        liga_slug = _ID_A_SLUG.get(liga_id, str(liga_id))
        if liga_slug != liga:
            continue
        teams = fx.get("teams", {})
        home = (teams.get("home") or {}).get("name", "")
        away = (teams.get("away") or {}).get("name", "")
        fixture_id = (fx.get("fixture") or {}).get("id")

        # ¿es un partido de esta jornada?
        mio = None
        for p in mis:
            if emparejar_equipo(p["local"], home) and emparejar_equipo(p["visitante"], away):
                mio = p
                break
        if not mio:
            continue

        clave = f"{liga}:{mio['local']}:{mio['visitante']}"
        previo = db.alineaciones.find_one({"clave": clave}, {"_id": 0})
        if previo and previo.get("confirmada"):
            continue

        ali = traer_alineacion_fixture(fixture_id)
        requests_usados += 1
        if not ali or not ali.get("equipos"):
            continue

        db.alineaciones.update_one(
            {"clave": clave},
            {"$set": {
                "clave": clave, "liga": liga,
                "local": mio["local"], "visitante": mio["visitante"],
                "confirmada": ali["confirmada"], "equipos": ali["equipos"],
                "fixture_id": fixture_id,
                "actualizado": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )
        actualizados += 1
        detalles.append({"partido": f"{mio['local']} vs {mio['visitante']}",
                         "confirmada": ali["confirmada"]})

    conf = sum(1 for d in detalles if d["confirmada"])
    return {"ok": True, "actualizados": actualizados, "confirmadas": conf,
            "requests_usados": requests_usados,
            "resumen": f"{jornada}: {actualizados} alineaciones ({conf} confirmadas)",
            "detalles": detalles}


def actualizar_marcadores_vivo(db) -> dict:
    """
    Trae los partidos EN VIVO de API-Football (1 request con live=all) y
    actualiza marcador + minuto + estado. IMPORTANTE: los partidos que
    estaban en vivo y YA NO aparecen (terminaron) se marcan como finalizados
    con su resultado final (evita que se queden 'EN VIVO' congelados).
    """
    if not API_KEY:
        return {"ok": False, "error": "Falta APIFOOTBALL_KEY"}

    import httpx
    ligas_seguidas = set(LIGA_IDS.values())

    # 1) recordar qué partidos tenía marcados como en vivo ANTES
    estaban_vivos = {
        f"{p['liga']}:{p['local']}:{p['visitante']}"
        for p in db.partidos.find({"estado": "en_vivo"},
                                  {"_id": 0, "liga": 1, "local": 1, "visitante": 1})
    }

    try:
        with httpx.Client(timeout=20) as client:
            r = client.get(f"{API_BASE}/fixtures", headers=_headers(para_alineaciones=False), params={"live": "all"})
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return {"ok": False, "error": "No se pudieron traer las alineaciones ahora"}

    fixtures = data.get("response", [])
    actualizados = 0
    detalles = []
    siguen_vivos = set()   # claves que API confirma que SIGUEN en vivo

    for fx in fixtures:
        liga_id = (fx.get("league") or {}).get("id")
        if liga_id not in ligas_seguidas:
            continue
        liga_slug = _ID_A_SLUG.get(liga_id, str(liga_id))
        teams = fx.get("teams", {})
        home = (teams.get("home") or {}).get("name", "")
        away = (teams.get("away") or {}).get("name", "")
        goals = fx.get("goals", {})
        gl = goals.get("home")
        gv = goals.get("away")
        status = (fx.get("fixture") or {}).get("status", {})
        minuto = status.get("elapsed")
        corto = status.get("short", "")

        mis = list(db.partidos.find({"liga": liga_slug}, {"_id": 0, "local": 1, "visitante": 1}))
        mio = None
        for p in mis:
            if emparejar_equipo(p["local"], home) and emparejar_equipo(p["visitante"], away):
                mio = p
                break
        if not mio:
            continue

        if corto in ("1H", "2H", "HT", "ET", "BT", "P", "LIVE"):
            estado = "en_vivo"
        elif corto in ("FT", "AET", "PEN"):
            estado = "finalizado"
        else:
            estado = "programado"

        clave = f"{liga_slug}:{mio['local']}:{mio['visitante']}"
        if estado == "en_vivo":
            siguen_vivos.add(clave)

        db.partidos.update_one(
            {"liga": liga_slug, "local": mio["local"], "visitante": mio["visitante"]},
            {"$set": {
                "marcadorLocal": gl, "marcadorVisitante": gv,
                "estado": estado, "minuto": minuto if estado == "en_vivo" else None,
            }},
        )
        actualizados += 1
        detalles.append({"partido": f"{mio['local']} {gl}-{gv} {mio['visitante']}",
                         "minuto": minuto, "estado": estado})

    # 2) los que ESTABAN en vivo y ya NO aparecen → terminaron.
    #    Traer su resultado final (por fecha) y marcarlos finalizados.
    terminaron = estaban_vivos - siguen_vivos
    cerrados = 0
    for clave in terminaron:
        try:
            liga_slug, loc, vis = clave.split(":", 2)
        except ValueError:
            continue
        # buscar su fecha para traer el resultado final
        p = db.partidos.find_one({"liga": liga_slug, "local": loc, "visitante": vis},
                                 {"_id": 0, "inicio": 1})
        marcado = False
        if p and p.get("inicio"):
            try:
                fixtures_dia = traer_fixtures_dia_todos(p["inicio"][:10])
                for fx in fixtures_dia:
                    lid = (fx.get("league") or {}).get("id")
                    if _ID_A_SLUG.get(lid) != liga_slug:
                        continue
                    t = fx.get("teams", {})
                    h = (t.get("home") or {}).get("name", "")
                    a = (t.get("away") or {}).get("name", "")
                    if emparejar_equipo(loc, h) and emparejar_equipo(vis, a):
                        g = fx.get("goals", {})
                        st = (fx.get("fixture") or {}).get("status", {}).get("short", "")
                        est = "finalizado" if st in ("FT", "AET", "PEN") else "en_vivo"
                        db.partidos.update_one(
                            {"liga": liga_slug, "local": loc, "visitante": vis},
                            {"$set": {"marcadorLocal": g.get("home"),
                                      "marcadorVisitante": g.get("away"),
                                      "estado": est, "minuto": None}})
                        marcado = True
                        cerrados += 1
                        break
            except Exception:
                pass
        # si no se pudo confirmar, igual lo sacamos de "en vivo" (a finalizado)
        if not marcado:
            db.partidos.update_one(
                {"liga": liga_slug, "local": loc, "visitante": vis},
                {"$set": {"estado": "finalizado", "minuto": None}})
            cerrados += 1

    # 3) RED DE SEGURIDAD: un partido no dura más de ~3h. Si algo quedó
    #    "en_vivo" y empezó hace más de 3.5h, lo cerramos (por si API falló).
    from datetime import datetime, timezone, timedelta
    limite = (datetime.now(timezone.utc) - timedelta(hours=3.5)).isoformat()
    r_viejos = db.partidos.update_many(
        {"estado": "en_vivo", "inicio": {"$lt": limite}},
        {"$set": {"estado": "finalizado", "minuto": None}})
    viejos_cerrados = r_viejos.modified_count

    return {"ok": True, "en_vivo": len(fixtures), "actualizados": actualizados,
            "cerrados": cerrados + viejos_cerrados,
            "requests_usados": 1 + (1 if terminaron else 0), "detalles": detalles,
            "resumen": f"{actualizados} en vivo, {cerrados + viejos_cerrados} finalizados"}


def actualizar_alineacion_partido(db, liga: str, local: str, visitante: str) -> dict:
    """
    Trae la alineación de UN partido específico. Eficiente: busca el fixture
    del día de ese partido y pide solo su alineación. Ideal para el botón
    'Traer alineación' de cada juego.
    """
    if not API_KEY:
        return {"ok": False, "error": "Falta APIFOOTBALL_KEY"}

    # buscar el partido en la base para saber su fecha
    p = db.partidos.find_one(
        {"liga": liga, "local": local, "visitante": visitante},
        {"_id": 0, "inicio": 1}
    )
    if not p:
        # intento flexible por si el nombre varía
        for cand in db.partidos.find({"liga": liga}, {"_id": 0, "local": 1, "visitante": 1, "inicio": 1}):
            if emparejar_equipo(cand.get("local",""), local) and emparejar_equipo(cand.get("visitante",""), visitante):
                p = cand
                break
    if not p or not p.get("inicio"):
        return {"ok": False, "error": "Partido no encontrado"}

    fecha = p["inicio"][:10]
    fixtures = traer_fixtures_dia_todos(fecha)  # 1 request
    requests_usados = 1

    for fx in fixtures:
        liga_id = (fx.get("league") or {}).get("id")
        if _ID_A_SLUG.get(liga_id) != liga:
            continue
        teams = fx.get("teams", {})
        home = (teams.get("home") or {}).get("name", "")
        away = (teams.get("away") or {}).get("name", "")
        if not (emparejar_equipo(local, home) and emparejar_equipo(visitante, away)):
            continue

        fixture_id = (fx.get("fixture") or {}).get("id")
        ali = traer_alineacion_fixture(fixture_id)  # 1 request
        requests_usados += 1
        if not ali or not ali.get("equipos"):
            return {"ok": True, "actualizados": 0, "requests_usados": requests_usados,
                    "nota": "Aún no hay alineación (se confirma ~1h antes)"}

        clave = f"{liga}:{local}:{visitante}"
        db.alineaciones.update_one(
            {"clave": clave},
            {"$set": {
                "clave": clave, "liga": liga, "local": local, "visitante": visitante,
                "confirmada": ali["confirmada"], "equipos": ali["equipos"],
                "fixture_id": fixture_id,
                "actualizado": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )
        return {"ok": True, "actualizados": 1, "confirmada": ali["confirmada"],
                "requests_usados": requests_usados,
                "resumen": f"{local} vs {visitante}: alineación {'confirmada' if ali['confirmada'] else 'probable'}"}

    return {"ok": True, "actualizados": 0, "requests_usados": requests_usados,
            "nota": "Las alineaciones aún no están disponibles"}


def estado_cuenta_api() -> dict:
    """
    Consulta tu plan y cuota de API-Football. Usa el endpoint /status
    (no gasta cuota de datos). Te dice cuántos requests tienes y usas.
    """
    if not API_KEY:
        return {"ok": False, "error": "Falta APIFOOTBALL_KEY"}
    import httpx
    try:
        with httpx.Client(timeout=15) as client:
            r = client.get(f"{API_BASE}/status", headers=_headers())
            r.raise_for_status()
            data = r.json().get("response", {})
    except Exception as e:
        return {"ok": False, "error": "No se pudieron traer las alineaciones ahora"}

    sub = data.get("subscription", {})
    req = data.get("requests", {})
    return {
        "ok": True,
        "plan": sub.get("plan", "?"),
        "activo": sub.get("active", False),
        "fin": sub.get("end", ""),
        "requests_usados_hoy": req.get("current", 0),
        "requests_limite_dia": req.get("limit_day", 0),
    }
