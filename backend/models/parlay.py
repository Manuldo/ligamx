"""Motor de coherencia de parlays y generación validada de picks sugeridos."""
from itertools import combinations
import json
import logging
import re

from models.betting import aplicar_reglas_duras, shrink_corners

logger = logging.getLogger(__name__)


def _es_over_under(m: str) -> bool:
    m = (m or "").lower()
    return any(x in m for x in ["over", "under", "mas de", "más de", "menos de"])


def compatible(a: dict, b: dict) -> bool:
    if a.get("id") and b.get("id") and a["id"] == b["id"]:
        return False
    if a.get("partido") and a.get("partido") == b.get("partido"):
        m1 = (a.get("mercado") or "").lower()
        m2 = (b.get("mercado") or "").lower()
        if _es_over_under(m1) and _es_over_under(m2):
            return False
        if "local" in m1 and "visit" in m2:
            return False
        if "visit" in m1 and "local" in m2:
            return False
        return False
    return True


def calcular_parlay(combo: list[dict]) -> dict:
    """Calcula métricas y marca si el edge es matemáticamente utilizable."""
    momio_comb = 1.0
    prob_conjunta = 1.0
    for p in combo:
        momio_comb *= max(float(p.get("momio", 1.0) or 1.0), 1.0)
        prob_conjunta *= min(max(float(p.get("probEstimada", 0.5) or 0.5), 0.0), 1.0)

    prob_implicita = 1 / momio_comb if momio_comb else 0
    momios_reales = bool(combo) and all(p.get("momioReal") is True for p in combo)
    probabilidades_calibradas = bool(combo) and all(p.get("probCalibrada") is True for p in combo)
    edge_valido = momios_reales and probabilidades_calibradas

    return {
        "momioComb": round(momio_comb, 2),
        "probConjunta": round(prob_conjunta, 4),
        "probImplicita": round(prob_implicita, 4),
        "edge": round(prob_conjunta - prob_implicita, 4) if edge_valido else None,
        "edgeValido": edge_valido,
        "probabilidadCalibrada": probabilidades_calibradas,
        "momiosReales": momios_reales,
    }


def validar_boleto(patas: list[dict]) -> dict:
    avisos = []
    for a, b in combinations(patas, 2):
        if not compatible(a, b):
            avisos.append(
                f"'{a.get('mercado')}' y '{b.get('mercado')}' pueden estar correlacionados."
            )

    for p in patas:
        mercado = (p.get("mercado") or "").lower()
        if "under" in mercado and "gol" in mercado:
            avisos.append(f"'{p.get('mercado')}': bloqueado por la regla de unders de goles.")

    metricas = calcular_parlay(patas)
    if not metricas["edgeValido"]:
        avisos.append(
            "El edge no se calcula hasta usar momios reales de una casa de apuestas y probabilidades calibradas con resultados históricos."
        )
    return {"metricas": metricas, "avisos": avisos, "patas": patas}


def mejores_parlays(picks: list[dict], min_k=2, max_k=3, top=5) -> list[dict]:
    """Rankea solo combinaciones con momios reales y probabilidades calibradas."""
    parlays = []
    for k in range(min_k, max_k + 1):
        for combo in combinations(picks, k):
            ok = all(compatible(combo[i], combo[j])
                     for i in range(len(combo))
                     for j in range(i + 1, len(combo)))
            if not ok:
                continue
            metricas = calcular_parlay(list(combo))
            if not metricas["edgeValido"] or metricas["edge"] is None or metricas["edge"] <= 0:
                continue
            parlays.append({"patas": list(combo), **metricas})
    parlays.sort(key=lambda x: (x["probConjunta"], x["edge"]), reverse=True)
    return parlays[:top]


def mejores_parlays_estimados(picks: list[dict], min_k=2, max_k=3, top=5) -> list[dict]:
    """
    Combinaciones coherentes rankeadas por PROBABILIDAD CONJUNTA estimada,
    SIN exigir momios reales ni calibración. El edge queda en None (N/D):
    honesto, pero permite mostrar parlays como análisis mientras no haya
    momios reales. Cuando existan, usar mejores_parlays() (con edge real).
    """
    parlays = []
    for k in range(min_k, max_k + 1):
        for combo in combinations(picks, k):
            ok = all(compatible(combo[i], combo[j])
                     for i in range(len(combo))
                     for j in range(i + 1, len(combo)))
            if not ok:
                continue
            metricas = calcular_parlay(list(combo))
            # aquí NO filtramos por edge: mostramos como estimación (edge=N/D)
            parlays.append({"patas": list(combo), **metricas})
    # rankear por probabilidad conjunta estimada (lo más "seguro" arriba)
    parlays.sort(key=lambda x: x["probConjunta"], reverse=True)
    return parlays[:top]


def _una_pata_por_partido(picks: list[dict]) -> list[dict]:
    """
    De todo el universo, se queda con la MEJOR pata de cada partido
    (mayor probEstimada). Así garantizamos que un combinado grande nunca
    mezcle dos mercados del mismo partido (incoherente).
    """
    mejor = {}
    for p in picks:
        # el partido es la clave; asumimos que 'partido' identifica el juego
        clave = p.get("partido", "")
        actual = mejor.get(clave)
        if actual is None or (p.get("probEstimada", 0) > actual.get("probEstimada", 0)):
            mejor[clave] = p
    return list(mejor.values())


def parlays_sonadores(picks: list[dict], umbral_verde: float = 0.70,
                      max_patas: int = 12) -> dict:
    """
    Arma dos combinados grandes SIN límite de 3 patas (el gancho gratis):

    - 'sonador_seguro': solo patas VERDES (probEstimada >= umbral_verde),
      una por partido. El más sólido dentro de lo ambicioso.
    - 'sonador_total': las MEJORES patas disponibles (una por partido),
      ordenadas por probabilidad, hasta max_patas. Premio máximo.

    Devuelve {seguro: {...} | None, total: {...} | None}.
    Edge queda N/D igual que el resto (honestidad).
    """
    base = _una_pata_por_partido(picks)
    if not base:
        return {"seguro": None, "total": None}

    base.sort(key=lambda p: p.get("probEstimada", 0), reverse=True)

    def armar(patas, etiqueta):
        patas = patas[:max_patas]
        if len(patas) < 2:
            return None
        m = calcular_parlay(patas)
        return {
            "tipo": etiqueta,
            "patas": patas,
            **m,   # momioComb, probConjunta, edge(None), edgeValido, etc.
        }

    verdes = [p for p in base if p.get("probEstimada", 0) >= umbral_verde]
    seguro = armar(verdes, "sonador_seguro")
    total = armar(base, "sonador_total")

    return {"seguro": seguro, "total": total}


PROMPT_PICKS = """Eres el motor de picks de Pickazo MX. A partir SOLO de la data
proporcionada genera de 2 a 4 opciones concretas. Responde únicamente con un
array JSON válido. Cada elemento debe tener:
- mercado: nomenclatura estándar del casino.
- confianza: estimación heurística de 0 a 100; no la presentes como probabilidad calibrada.
- momio: referencia decimal visual entre 1.01 y 25.00; NO es un momio real de una casa.
No inventes lesiones, alineaciones, estadísticas ni mercados.
REGLA CRÍTICA: usa ÚNICAMENTE los dos equipos del partido indicado. Si un mercado
menciona un equipo por nombre, debe ser uno de esos dos — NUNCA menciones equipos
de otros partidos o ligas. Prohibido usar nombres de equipos de la data de ejemplo.
CÓRNERS: solo sugiere mercados de córners/tiros de esquina si la DATA incluye
promedios reales de córners de ambos equipos. Si la data no trae córners, NO
sugieras ningún mercado de córners.
Formato: [{"mercado":"Más de 1.5 goles (Over 1.5)","confianza":72,"momio":1.45}]"""


def _extraer_linea(mercado: str) -> float | None:
    match = re.search(r"(?:over|under|más de|mas de|menos de)\s*([0-9]+(?:\.[0-9]+)?)", mercado, re.I)
    return float(match.group(1)) if match else None


def _menciona_equipo_ajeno(mercado: str, equipos_partido: list[str]) -> bool:
    """True si el mercado menciona un equipo conocido que NO pertenece al
    partido analizado. Blindaje determinista contra que la IA copie un
    equipo de la data de ejemplo (bug 'Chivas' en análisis de otro partido)."""
    # equipos frecuentes que, si aparecen sin ser del partido, delatan contaminación
    EQUIPOS_CONOCIDOS = [
        "chivas", "guadalajara", "tijuana", "cruz azul", "monterrey", "toluca",
        "pumas", "necaxa", "atlas", "santos", "pachuca", "américa", "america",
        "tigres", "león", "leon", "puebla", "querétaro", "queretaro", "juárez",
        "juarez", "mazatlán", "mazatlan", "san luis",
        "barcelona", "real madrid", "atlético", "atletico", "sevilla", "valencia",
        "villarreal", "athletic", "betis", "osasuna", "celta", "elche",
    ]
    m = mercado.lower()
    for eq in EQUIPOS_CONOCIDOS:
        if eq in m:
            # ¿ese equipo es parte del partido? si sí, está bien
            es_del_partido = any(eq in tok or tok in eq for tok in equipos_partido)
            if not es_del_partido:
                return True
    return False


def generar_picks_sugeridos(anthropic, contexto_datos: str, partido_nombre: str) -> list[dict]:
    """Genera picks exploratorios, valida esquema y aplica reglas deterministas."""
    try:
        resp = anthropic.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            system=PROMPT_PICKS,
            messages=[{
                "role": "user",
                "content": f"Partido: {partido_nombre}\n\nDATA:\n{contexto_datos[:24000]}",
            }],
        )
        texto = resp.content[0].text.strip().replace("```json", "").replace("```", "").strip()
        raw = json.loads(texto)
        if not isinstance(raw, list):
            raise ValueError("La respuesta no es una lista")

        salida = []
        vistos = set()
        # nombres de los dos equipos del partido (para detectar contaminación)
        equipos_partido = partido_nombre.lower().replace(" vs ", " ").split()
        # ¿el contexto trae datos reales de córners? Si no, bloqueamos picks de
        # córners (evita picks basados en datos ausentes o malos del scraper).
        ctx_low = (contexto_datos or "").lower()
        hay_datos_corners = ("córners" in ctx_low or "corners" in ctx_low
                             or "tiros de esquina" in ctx_low)
        for item in raw[:8]:
            if not isinstance(item, dict):
                continue
            mercado = " ".join(str(item.get("mercado", "")).strip().split())[:180]
            if len(mercado) < 3 or mercado.lower() in vistos:
                continue
            vistos.add(mercado.lower())

            linea = _extraer_linea(mercado)
            permitido, razon = aplicar_reglas_duras(mercado, linea, hay_datos_corners)
            if not permitido:
                logger.info("Pick descartado por regla dura: %s (%s)", mercado, razon)
                continue

            # anti-contaminación: si el mercado nombra un equipo conocido que
            # NO es de este partido, se descarta (evita el bug "Chivas" en un
            # análisis de Barcelona).
            if _menciona_equipo_ajeno(mercado, equipos_partido):
                logger.info("Pick descartado por equipo ajeno: %s", mercado)
                continue

            confianza = min(max(float(item.get("confianza", 50)), 0), 100)
            prob = shrink_corners(confianza / 100, mercado)
            momio = min(max(float(item.get("momio", 2.0)), 1.01), 25.0)
            salida.append({
                "mercado": mercado,
                "confianza": round(prob * 100, 1),
                "momio": round(momio, 2),
                "probEstimada": round(prob, 3),
                "partido": partido_nombre,
                "sugerido": True,
                "momioReal": False,
                "probCalibrada": False,
                "advertencia": "Estimación exploratoria; confirma el momio real antes de apostar.",
            })
            if len(salida) == 4:
                break
        return salida
    except Exception as exc:
        logger.exception("No se pudieron generar picks para %s: %s", partido_nombre, exc)
        return []
