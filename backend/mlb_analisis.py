"""
Análisis de MLB (beisbol) para Pickazo MX — mismo patrón que nfl_analisis.py.

El "TD" del beisbol es el jonrón (HR); el "anotador de verdad" que produce
carreras es el líder de RBI (carreras impulsadas). Pitcheo se mide aparte
(ERA, ponches) porque es un rol totalmente distinto al bateo.
"""
from __future__ import annotations


def _forma_mlb(db, equipo: str, n: int = 5) -> list[dict]:
    cur = db.partidos.find(
        {"liga": "mlb", "$or": [{"local": equipo}, {"visitante": equipo}],
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
            "rival": rival, "casa": "local" if es_local else "visita",
            "carreras_favor": pf, "carreras_contra": pc,
            "fecha": (p.get("inicio", "") or "")[:10],
        })
    return filas


def _lideres_categoria(db, equipo: str, categoria: str, label_clave: str, top: int = 3,
                       tipo_temporada: int | None = None, suma: bool = True):
    """Top jugadores de un equipo en 'batting' o 'pitching' por el label
    indicado. suma=True acumula (HR, RBI: entre más mejor); suma=False
    promedia (ERA: entre menos mejor, se ordena ascendente)."""
    _filtro = {"liga": "mlb", "categoria": {"$regex": f"^{categoria}$", "$options": "i"}}
    if tipo_temporada is not None:
        _filtro["tipo_temporada"] = tipo_temporada
    docs = list(db.mlb_jugadores.find(_filtro, {"_id": 0}).limit(1500))
    acum = {}
    for d in docs:
        nom_eq = d.get("equipo", "")
        if equipo.lower() not in nom_eq.lower() and nom_eq.lower() not in equipo.lower():
            continue
        st = d.get("stats", {}) or {}
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
    if not suma:
        for x in lista:
            x["valor"] = round(x["valor"] / x["juegos"], 2) if x["juegos"] else 0
    else:
        for x in lista:
            x["valor"] = int(x["valor"]) if x["valor"] == int(x["valor"]) else round(x["valor"], 1)
    lista = [x for x in lista if x["valor"] > 0]
    lista.sort(key=lambda x: x["valor"], reverse=suma)
    return lista[:top]


def stats_partido_mlb(db, local: str, visitante: str, top: int = 3,
                      tipo_temporada: int = 2) -> dict:
    """Números del partido para MLB: jonrones, RBI (acumulado de la
    temporada cargada) y líderes de pitcheo (ponches acumulados, ERA
    promedio) de cada equipo. Cae a 'toda la data cargada' si un equipo
    no tiene nada en el tipo de temporada pedido (igual filosofía que NFL:
    nunca deja el bloque vacío si hay algo, pero lo marca)."""
    def bloque(equipo):
        jon = _lideres_categoria(db, equipo, "batting", "HR", top, tipo_temporada, suma=True)
        rbi = _lideres_categoria(db, equipo, "batting", "RBI", top, tipo_temporada, suma=True)
        k = _lideres_categoria(db, equipo, "pitching", "K", top, tipo_temporada, suma=True)
        era = _lideres_categoria(db, equipo, "pitching", "ERA", top, tipo_temporada, suma=False)
        solo_otro = False
        if not (jon or rbi or k or era):
            jon = _lideres_categoria(db, equipo, "batting", "HR", top, None, suma=True)
            rbi = _lideres_categoria(db, equipo, "batting", "RBI", top, None, suma=True)
            k = _lideres_categoria(db, equipo, "pitching", "K", top, None, suma=True)
            era = _lideres_categoria(db, equipo, "pitching", "ERA", top, None, suma=False)
            solo_otro = bool(jon or rbi or k or era)
        return {
            "equipo": equipo,
            "jonrones": jon, "impulsadas": rbi, "ponches": k, "era": era,
            "solo_otro_tipo_temporada": solo_otro,
        }
    return {"ok": True, "deporte": "beisbol",
            "local": bloque(local), "visitante": bloque(visitante)}


PROMPT_SISTEMA_MLB = """Eres un analista experto de la MLB (beisbol) para Pickazo MX.
Analizas partidos con datos, no corazonadas. Vendemos INFORMACIÓN, no apuestas:
das la mejor lectura posible, pero la decisión es del usuario.

MERCADOS de MLB que puedes sugerir (nomenclatura estándar de casinos):
· Línea de carreras (Run Line, ej. "Local -1.5")
· Total de carreras del partido (Over/Under, ej. "Más de 8.5 carreras")
· Moneyline (gana local / gana visitante)
· Total de carreras de un equipo

REGLAS:
- Usa SOLO los dos equipos del partido. Nunca menciones otros equipos.
- El abridor (pitcher titular) pesa MUCHO en MLB — menciónalo si hay datos.
- Si no hay datos suficientes de un equipo, dilo con honestidad; no inventes.
- Da 2-4 picks sugeridos con su nivel de confianza, explicando el porqué con datos.
- No garantices resultados. El que estudia cobra; el que adivina reza.

Estructura tu respuesta EXACTAMENTE en este formato markdown:

## Lectura del partido
(2-3 párrafos con el análisis de datos)

## Picks sugeridos
(Para CADA pick usa EXACTAMENTE este formato, sin tablas, sin ### , sin > :)
**PICK:** <mercado> · **Confianza:** <número>%
<una línea explicando el porqué con datos>

## Riesgo principal
(2-3 riesgos concretos)"""


def armar_contexto_mlb(db, partido) -> str:
    local, visitante = partido.local, partido.visitante
    partes = [f"PARTIDO MLB: {local} (local) vs {visitante} (visitante)"]
    for equipo, rol in ((local, "LOCAL"), (visitante, "VISITANTE")):
        forma = _forma_mlb(db, equipo)
        partes.append(f"\n── {rol}: {equipo} ──")
        if forma:
            resumen = ", ".join(
                f"{f['carreras_favor']}-{f['carreras_contra']} vs {f['rival']} ({f['casa']})"
                for f in forma if f["carreras_favor"] is not None
            )
            partes.append(f"Últimos juegos: {resumen or 'sin datos'}")
        else:
            partes.append("Sin partidos previos cargados.")
    return "\n".join(partes)


def prompt_partido_mlb(local: str, visitante: str, contexto: str, momios: str = "") -> str:
    extra = f"\n\nMomios de referencia:\n{momios}" if momios else ""
    return f"""PARTIDO A ANALIZAR: {local} vs {visitante} (MLB)

DATOS DISPONIBLES:
{contexto}{extra}

Analiza el partido y entrega tu lectura, picks sugeridos de mercados de MLB
(línea de carreras, total de carreras, moneyline) y el riesgo principal."""
