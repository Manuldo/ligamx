"""
Análisis de NFL (fútbol americano) para Pickazo MX.

El análisis de fútbol (goles, córners, BTTS) no aplica al americano. Aquí se
arma el contexto con los datos que el scraper de NFL ya guardó
(nfl_partidos, nfl_jugadores) y se define el prompt de mercados de americano:
spread, over/under de puntos, moneyline, totales de equipo.
"""
from __future__ import annotations


def _forma_nfl(db, equipo: str, n: int = 5) -> list[dict]:
    """Últimos partidos finalizados del equipo (como local o visitante),
    con su marcador. Usa la colección ligera db.partidos (liga=nfl)."""
    cur = db.partidos.find(
        {"liga": "nfl", "$or": [{"local": equipo}, {"visitante": equipo}],
         "estado": "finalizado"},
        {"_id": 0},
    ).sort("inicio", -1).limit(n)
    filas = []
    for p in cur:
        es_local = p.get("local") == equipo
        pf = p.get("marcadorLocal") if es_local else p.get("marcadorVisitante")
        pc = p.get("marcadorVisitante") if es_local else p.get("marcadorLocal")
        rival = p.get("visitante") if es_local else p.get("local")
        filas.append({
            "rival": rival,
            "casa": "local" if es_local else "visita",
            "puntos_favor": pf,
            "puntos_contra": pc,
            "fecha": (p.get("inicio", "") or "")[:10],
        })
    return filas


def _stats_equipo_nfl(db, equipo: str, n: int = 4) -> dict:
    """Promedios de stats de equipo (yardas, etc.) de los últimos partidos
    detallados en nfl_partidos."""
    # busca partidos donde el equipo aparece en equipo_stats
    docs = list(db.nfl_partidos.find({"liga": "nfl"}, {"_id": 0}).sort("capturado", -1).limit(40))
    ptos, yardas, muestras = [], [], 0
    for d in docs:
        for e in (d.get("equipo_stats") or []):
            nom = (e.get("equipo") or "")
            if equipo.lower() in nom.lower() or nom.lower() in equipo.lower():
                st = e.get("stats", {}) or {}
                # yardas totales: la etiqueta puede variar
                for k, v in st.items():
                    kl = k.lower()
                    if "total yards" in kl or "yardas totales" in kl:
                        try:
                            yardas.append(float(v))
                        except Exception:
                            pass
                muestras += 1
    prom_yardas = round(sum(yardas) / len(yardas), 1) if yardas else None
    return {"muestras": muestras, "prom_yardas_totales": prom_yardas}


def _record_temporada_nfl(db, equipo: str, n: int = 25) -> dict:
    """Récord G-P-E de la temporada actual. No hay colección de tabla de
    posiciones para NFL en Mongo (a diferencia de fútbol), así que se calcula
    sobre la marcha de los partidos finalizados ya guardados."""
    partidos = _forma_nfl(db, equipo, n=n)
    g = sum(1 for p in partidos if p["puntos_favor"] is not None and p["puntos_favor"] > p["puntos_contra"])
    pe = sum(1 for p in partidos if p["puntos_favor"] is not None and p["puntos_favor"] < p["puntos_contra"])
    e = sum(1 for p in partidos if p["puntos_favor"] is not None and p["puntos_favor"] == p["puntos_contra"])
    return {"ganados": g, "perdidos": pe, "empates": e, "jugados": g + pe + e}


def _lesiones_relevantes_nfl(equipo: str, top: int = 6) -> list[dict]:
    """Jugadores lesionados/en duda del equipo, en vivo desde ESPN (mismo
    reporte que ya usa el panel admin, pero antes nunca llegaba al análisis
    del partido). Si ESPN falla no truena el análisis, solo se omite el
    bloque — igual que el resto del contexto duro."""
    try:
        from nfl_scraper import obtener_lesiones_equipo
        todas = obtener_lesiones_equipo(equipo)
    except Exception:
        return []
    # "Active" sin más es jugador disponible; lo que importa es lo demás
    # (Out, Doubtful, Questionable, IR, etc.)
    relevantes = [j for j in todas if (j.get("estado") or "").strip().lower() not in ("", "active")]
    return relevantes[:top]


def armar_contexto_nfl(db, partido) -> str:
    """Arma el contexto de datos para analizar un partido de NFL."""
    local, visitante = partido.local, partido.visitante
    partes = [f"PARTIDO NFL: {local} (local) vs {visitante} (visitante)"]

    for equipo, rol in ((local, "LOCAL"), (visitante, "VISITANTE")):
        forma = _forma_nfl(db, equipo)
        st = _stats_equipo_nfl(db, equipo)
        rec = _record_temporada_nfl(db, equipo)
        lesiones = _lesiones_relevantes_nfl(equipo)
        partes.append(f"\n── {rol}: {equipo} ──")
        if rec["jugados"]:
            empate_txt = f"-{rec['empates']}E" if rec["empates"] else ""
            partes.append(f"Récord de temporada: {rec['ganados']}G-{rec['perdidos']}P{empate_txt} ({rec['jugados']} juegos jugados)")
        if forma:
            resumen = ", ".join(
                f"{f['puntos_favor']}-{f['puntos_contra']} vs {f['rival']} ({f['casa']})"
                for f in forma if f["puntos_favor"] is not None
            )
            partes.append(f"Últimos juegos: {resumen or 'sin datos'}")
            # promedio de puntos a favor / en contra
            pf = [f["puntos_favor"] for f in forma if f["puntos_favor"] is not None]
            pc = [f["puntos_contra"] for f in forma if f["puntos_contra"] is not None]
            if pf:
                partes.append(f"Promedio puntos anotados: {round(sum(pf)/len(pf),1)}")
            if pc:
                partes.append(f"Promedio puntos recibidos: {round(sum(pc)/len(pc),1)}")
        else:
            partes.append("Sin partidos previos cargados.")
        if st.get("prom_yardas_totales") is not None:
            partes.append(f"Promedio yardas totales: {st['prom_yardas_totales']} ({st['muestras']} muestras)")
        if lesiones:
            lineas_les = []
            for j in lesiones:
                detalle = f" — {j.get('detalle')}" if j.get("detalle") else ""
                lineas_les.append(f"  - {j.get('jugador','?')} ({j.get('posicion','')}): {j.get('estado','')}{detalle}")
            partes.append(f"⚠️ Reporte de lesiones {equipo}:\n" + "\n".join(lineas_les))
        else:
            partes.append(f"Reporte de lesiones {equipo}: sin bajas relevantes reportadas por ESPN ahora mismo.")

    return "\n".join(partes)


PROMPT_SISTEMA_NFL = """Eres un analista experto de la NFL (fútbol americano) para Pickazo MX.
Lema de la casa: "El que estudia cobra. El que adivina reza." No adivines: estudia.
Analizas partidos con datos, no corazonadas. Vendemos INFORMACIÓN, no apuestas:
das la mejor lectura posible, pero la decisión es del usuario.

╔═══════════════════════════════════════════════════════════════╗
║  REGLA #1 ABSOLUTA — MARCA Y CONFIDENCIALIDAD                  ║
╚═══════════════════════════════════════════════════════════════╝
- La marca es "Pickazo MX". NUNCA escribas "Manuldo", "Sistema Manuldo",
  "Tipster Manuldo" ni ninguna variante.
- NUNCA menciones nombres internos de criterios de evaluación (nada de
  "Índice", "Gordojhon", "Arthur", "Juez", "Control de Variabilidad",
  "Porcentaje 2.5", etc.). Son secretos internos de la casa. Exprésalo en
  lenguaje natural: "la consistencia reciente es alta", "hay riesgo por el
  viaje", "es un partido de alta presión", "el riesgo está controlado".
- NUNCA muestres checklists internos de auto-verificación, ni texto tipo
  "¿Inventé datos? NO". Eso es proceso interno que el usuario no debe ver.

MERCADOS de la NFL que puedes sugerir (nomenclatura estándar de casinos):
· Spread / Hándicap de puntos (ej. "Local -3.5")
· Total de puntos del partido (Over/Under, ej. "Más de 44.5 puntos")
· Moneyline (gana local / gana visitante)
· Total de puntos de un equipo (ej. "Local más de 24.5")

CRITERIOS DE EVALUACIÓN (úsalos para razonar el pick, SIN nombrarlos —
es la versión NFL de los mismos criterios que ya usa el motor en fútbol):
- Confianza principal: consistencia reciente, récord de temporada, ventaja
  de campo real (no solo "jugar en casa"). Guía de rangos: 85%+ es un pick
  principal; 75-84% secundario; 65-74% alternativa especulativa; menos de
  65% dilo como especulativo o mejor ni lo sugieras — no lo disfraces de
  "seguro" solo por llenar la cuota de 2-4 picks.
- Factores de ajuste — lesiones y rotación: usa el reporte de lesiones.
  REGLA DURA: si un jugador clave aparece "Out", "Doubtful" o en "Injured
  Reserve", NUNCA construyas un pick que dependa de que él produzca; si es
  figura clave del equipo (QB titular, as del ataque, pilar defensivo),
  ajusta la confianza de ESE equipo a la baja y dilo explícitamente, no
  solo lo menciones de pasada.
- Presión del partido: playoffs, rivalidad divisional, necesidad real de
  clasificar, revancha de una paliza reciente — sube o baja la confianza
  según qué tanto está en juego para cada equipo.
- Viabilidad del parlay (si el usuario pide combinar varios mercados del
  mismo partido): no trates selecciones muy correlacionadas (ej. "gana
  local" + "local cubre spread") como si fueran dos apuestas independientes.
- Probabilidad histórica por frecuencia: apóyate en el récord REAL de
  temporada (G-P-E), no en la sensación de "se ve bien".
- Control de volatilidad: si el promedio de puntos de un equipo está
  inflado por UN partido atípico (ej. un 41-31 aislado), dilo — no dejes
  que ese dato distorsione la lectura del resto de la muestra.
- Gestión de riesgo: menciona la relación riesgo/retorno del momio, no
  solo si "gusta" el pick.
- Estado físico/rotación: un jugador que regresa de lesión suele rendir
  menos en su primer juego de vuelta aunque ya esté "Active" — adviértelo
  si el reporte de lesiones lo sugiere.
- Ritmo: tendencia pase vs. carrera, qué tan rápido juega cada ataque,
  si eso favorece un total alto o bajo.
- Factores externos: clima (viento/nieve castiga el pase y los goles de
  campo), viaje largo costa a costa (resta rendimiento ofensivo visitante),
  semana corta (partido de jueves con poco descanso).

REGLAS:
- Usa SOLO los dos equipos del partido. Nunca menciones otros equipos.
- Si no hay datos suficientes de un equipo, dilo con honestidad; no inventes.
- Si hay "APRENDIZAJES RECUPERADOS" en los datos, úsalos para afinar el pick
  (son post-mortems de análisis anteriores de la NFL, reales, no genéricos).
- Da 2-4 picks sugeridos con su nivel de confianza, explicando el porqué con datos.
- No garantices resultados. El que estudia cobra; el que adivina reza.

Estructura tu respuesta EXACTAMENTE en este formato markdown:

## Lectura del partido
(2-3 párrafos con el análisis de datos)

## Picks sugeridos
(Para CADA pick usa EXACTAMENTE este formato, sin tablas, sin ### , sin > :)
**PICK:** <mercado> · **Confianza:** <número>%
<una línea explicando el porqué con datos>

(Ejemplo:)
**PICK:** Seahawks -5.5 (Spread) · **Confianza:** 72%
El precedente 29-13 y la defensa local sostienen el margen.

## Riesgo principal
(2-3 riesgos concretos)"""


def prompt_partido_nfl(local: str, visitante: str, contexto: str, momios: str = "",
                        aprendizajes: str = "") -> str:
    extra = f"\n\nMomios de referencia:\n{momios}" if momios else ""
    bloque_aprendizajes = (
        f"\n\nAPRENDIZAJES RECUPERADOS (post-mortems previos de NFL):\n{aprendizajes}"
        if aprendizajes else ""
    )
    return f"""PARTIDO A ANALIZAR: {local} vs {visitante} (NFL)

DATOS DISPONIBLES:
{contexto}{extra}{bloque_aprendizajes}

Analiza el partido y entrega tu lectura, picks sugeridos de mercados de NFL
(spread, total de puntos, moneyline) y el riesgo principal."""


def _lideres_categoria(db, equipo: str, categoria: str, label_clave: str, top: int = 3,
                       tipo_temporada: int = 2):
    """Top jugadores de un equipo en una categoría (passing/rushing/receiving)
    por el label indicado (ej. YDS), sumando/promediando sus partidos recientes.

    SOLO temporada regular (tipo_temporada=2) por defecto: antes se mezclaba
    con pretemporada, lo que inflaba/diluía el promedio y hacía que anotadores
    reales de temporada regular ni siquiera aparecieran en el top."""
    # busca jugadores de ese equipo y categoría
    _filtro = {"liga": "nfl", "categoria": {"$regex": f"^{categoria}$", "$options": "i"}}
    if tipo_temporada is not None:
        _filtro["tipo_temporada"] = tipo_temporada
    docs = list(db.nfl_jugadores.find(_filtro, {"_id": 0}).limit(500))
    acum = {}
    for d in docs:
        nom_eq = d.get("equipo", "")
        if equipo.lower() not in nom_eq.lower() and nom_eq.lower() not in equipo.lower():
            continue
        st = d.get("stats", {}) or {}
        # el label puede venir como 'YDS', 'yds', etc.
        val = None
        for k, v in st.items():
            if k.lower() == label_clave.lower():
                val = v
                break
        if val is None:
            continue
        nombre = d.get("jugador", "")
        pos = d.get("posicion", "")
        if nombre not in acum:
            acum[nombre] = {"nombre": nombre, "posicion": pos, "valor": 0, "juegos": 0}
        try:
            acum[nombre]["valor"] += float(val)
            acum[nombre]["juegos"] += 1
        except Exception:
            pass
    lista = list(acum.values())
    for x in lista:
        # promedio por juego, redondeado
        x["valor"] = round(x["valor"] / x["juegos"], 1) if x["juegos"] else 0
    lista.sort(key=lambda x: x["valor"], reverse=True)
    return lista[:top]


def _lideres_touchdowns(db, equipo: str, top: int = 3, tipo_temporada: int = 2):
    """Anotadores REALES de touchdown: solo cuenta TD de carrera (rushing) y
    de recepción (receiving). NO cuenta passing.TD, porque ese es un pase de
    TD LANZADO por el QB, no un touchdown que él anotó. El que anota es quien
    corre o atrapa el balón en la zona de anotación.

    SOLO temporada regular (tipo_temporada=2) por defecto: ver nota en
    _lideres_categoria."""
    acum = {}
    # OJO: passing NO entra — un QB que lanza 5 pases de TD no anotó 5 TD.
    # Sí entran defensive/kickReturns/puntReturns/interceptions: son TD
    # reales (pick-six, fumble return, retorno de patada) que antes no se
    # contaban — por eso un equipo con TDs de defensiva/especiales salía
    # con menos anotadores de los que de verdad tuvo en el partido.
    for cat in ("rushing", "receiving", "defensive", "kickReturns", "puntReturns", "interceptions"):
        _filtro = {"liga": "nfl", "categoria": {"$regex": f"^{cat}$", "$options": "i"}}
        if tipo_temporada is not None:
            _filtro["tipo_temporada"] = tipo_temporada
        docs = db.nfl_jugadores.find(_filtro, {"_id": 0}).limit(500)
        for d in docs:
            nom_eq = d.get("equipo", "")
            if equipo.lower() not in nom_eq.lower() and nom_eq.lower() not in equipo.lower():
                continue
            st = d.get("stats", {}) or {}
            td = None
            for k, v in st.items():
                if k.lower() == "td":
                    td = v; break
            if td is None:
                continue
            nombre = d.get("jugador", "")
            pos = d.get("posicion", "")
            a = acum.setdefault(nombre, {"nombre": nombre, "posicion": pos, "valor": 0})
            try:
                a["valor"] += float(td)
            except Exception:
                pass
    lista = [x for x in acum.values() if x["valor"] > 0]
    for x in lista:
        x["valor"] = int(x["valor"]) if x["valor"] == int(x["valor"]) else round(x["valor"], 1)
    lista.sort(key=lambda x: x["valor"], reverse=True)
    return lista[:top]


def stats_partido_nfl(db, local: str, visitante: str, top: int = 3,
                      tipo_temporada: int = 2) -> dict:
    """Números del partido para NFL: líderes en pase, carrera y recepción
    de cada equipo (promedio por juego de los partidos cargados).

    Por defecto SOLO cuenta temporada regular (tipo_temporada=2). Si un
    equipo no tiene nada guardado de temporada regular todavía (apenas
    arrancó la temporada o falta re-sincronizar), cae a TODO lo que haya
    (incluida pretemporada) para no dejar el bloque vacío, y lo marca con
    'solo_pretemporada' para que no se confunda con un dato confiable."""
    def bloque(equipo):
        anot = _lideres_touchdowns(db, equipo, top, tipo_temporada)
        pas = _lideres_categoria(db, equipo, "passing", "YDS", top, tipo_temporada)
        cor = _lideres_categoria(db, equipo, "rushing", "YDS", top, tipo_temporada)
        rec = _lideres_categoria(db, equipo, "receiving", "YDS", top, tipo_temporada)
        solo_pre = False
        if not (anot or pas or cor or rec):
            # nada en temporada regular para este equipo: usar lo que haya
            # (normalmente pretemporada) en vez de devolver todo vacío
            anot = _lideres_touchdowns(db, equipo, top, tipo_temporada=None)
            pas = _lideres_categoria(db, equipo, "passing", "YDS", top, tipo_temporada=None)
            cor = _lideres_categoria(db, equipo, "rushing", "YDS", top, tipo_temporada=None)
            rec = _lideres_categoria(db, equipo, "receiving", "YDS", top, tipo_temporada=None)
            solo_pre = bool(anot or pas or cor or rec)
        return {
            "equipo": equipo,
            "anotadores": anot, "pasadores": pas, "corredores": cor, "receptores": rec,
            "solo_pretemporada": solo_pre,
        }
    return {"ok": True, "deporte": "americano",
            "local": bloque(local), "visitante": bloque(visitante)}
