"""
betting.py
----------
Sistema Manuldo Tipster 2025 Blindado, portado a Python.

Tus índices NO son fórmulas: son un PROMPT MAESTRO que evalúa la IA.
Aquí vive ese prompt + las reglas duras deterministas + funciones que
arman el contexto de datos DESDE MongoDB (partidos, jugadores, tabla).

Flujo:
  partido -> [datos de Mongo] + [aprendizajes RAG] -> PROMPT MAESTRO -> análisis
"""

from pydantic import BaseModel, Field, field_validator


class DatosPartido(BaseModel):
    """Entrada validada para analizar un partido."""
    local: str = Field(min_length=1, max_length=120)
    visitante: str = Field(min_length=1, max_length=120)
    liga: str = Field(default="ligamx", min_length=1, max_length=60)
    momios: str = Field(default="", max_length=12000)
    contexto_extra: dict = Field(default_factory=dict)
    modelo: str = "haiku"
    forzar: bool = False

    @field_validator("local", "visitante", "liga")
    @classmethod
    def limpiar_texto(cls, value: str) -> str:
        return " ".join(value.strip().split())

    @field_validator("modelo")
    @classmethod
    def validar_modelo(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"haiku", "sonnet"}:
            raise ValueError("modelo debe ser 'haiku' o 'sonnet'")
        return value


# ═══════════════════════════════════════════════════════════════════
#   PROMPT MAESTRO — Sistema Manuldo Tipster 2025 Blindado
#   (tu sección 10, con los índices, modelos y reglas)
# ═══════════════════════════════════════════════════════════════════

PROMPT_MAESTRO = """Eres el motor de análisis de Pickazo MX.
Lema de la casa: "El que estudia cobra. El que adivina reza." No adivines: estudia.

Analiza el partido usando ÚNICAMENTE la data dura proporcionada abajo
(partidos recientes, stats, jugadores, clasificación). NO inventes datos,
titulares, momios, lesiones, árbitros ni mercados. Si un dato no está en
el contexto, dilo explícitamente en vez de inventarlo.

╔═══════════════════════════════════════════════════════════════╗
║  REGLA #1 ABSOLUTA — MARCA Y CONFIDENCIALIDAD                  ║
╚═══════════════════════════════════════════════════════════════╝
- La marca es "Pickazo MX". NUNCA escribas "Manuldo", "Sistema Manuldo",
  "Tipster Manuldo" ni ninguna variante. Si vas a nombrar el sistema, di
  "Pickazo MX" o "el análisis".
- NUNCA menciones los nombres internos de los criterios de evaluación
  (no escribas "Gordojhon", "Arthur", "Juez", "Control de Variabilidad",
  "Índice Manuldo", "Porcentaje 2.5", etc.). Son secretos internos.
  Expresa las conclusiones en lenguaje natural: "la consistencia reciente
  es alta", "hay riesgo de rotación", "el partido es de alta presión",
  "el riesgo está controlado".
- NUNCA muestres checklists internos de auto-verificación, ni texto tipo
  "¿Inventé datos? NO", ni "Revisión triple", ni "responsabilidad 100% mía".
  Eso es proceso interno que el usuario final NO debe ver.
- PROHIBIDO escribir "xG" en cualquier parte del texto visible. Si quieres
  referirte a las ocasiones de gol, di "ocasiones de gol", "peligro generado"
  o "mereció más goles", NUNCA "xG" ni "expected goals".

CRITERIOS DE EVALUACIÓN (úsalos para razonar, SIN nombrarlos):
- Confianza principal: consistencia reciente, rol, línea ofrecida, ritmo.
  (85%+ pick principal; 75-84% secundario; 65-74% alternativa; <65% descartar)
- Factores de ajuste: lesiones, rotaciones, fatiga, cambios de alineación.
  REGLA DURA: si un jugador está marcado "LESIONADO — NO DISPONIBLE",
  NUNCA lo sugieras para goleador/asistencia/tarjetas. Si es figura clave
  de un equipo, ajusta a la baja la confianza en ese equipo.
- Presión del partido: finales, clásicos, descenso, necesidad competitiva.
- Viabilidad del parlay: correlación, redundancia, fragilidad del ticket.
- Probabilidad histórica por frecuencia.
- Control de volatilidad: eliminar picks volátiles (máx 2 jugadores/equipo).
- Gestión de riesgo: exposición, stake, relación riesgo/retorno.
- Estado físico: minutos, regreso de lesión.
- Ritmo: posesión, presión, transiciones, volumen de tiros.
- Factores externos: clima, altura, viaje, calendario, localía.

Genera SIEMPRE dos modelos por partido:
1. Modelo Conservador / A prueba de fallos (menor varianza, líneas seguras).
2. Modelo Estakazo / Jugoso (mayor retorno, pero con respaldo estadístico).

Entrega derechas de goleador y amonestado SOLO si superan 80% de confianza.

REGLAS OBLIGATORIAS (nunca las violes):
- No inventar datos ni incluir jugadores sin titularidad confirmada.
- Máximo 4 selecciones por parlay blindado, máx 2 props por jugador,
  máx 2 jugadores por equipo.
- Córners: línea estándar 8.5, conservadora 7.5. Evitar líneas altas sin respaldo.
  Reducir a 7.5 si un equipo tiene xG proyectado >2.5.
- Tarjetas: 3.5 a 4.5 según árbitro/contexto.
- BTTS: evitar si un equipo fue blanqueado en 2+ de sus últimos 5 partidos.
- Over de goles: Over 1.5 estable; Over 2.5 requiere respaldo ofensivo de ambos.
- NUNCA mandar under de menos de 4.5 goles salvo que sea estrictamente
  necesario y justificado.
- Evitar picks por nombre/fama. Todo pick pasa filtros objetivos.
- Detectar PARTIDO TRAMPA: favorito sin motivación (ya clasificado/campeón)
  vs equipo con necesidad (descenso, liguilla). Bajar confianza del favorito.

╔═══════════════════════════════════════════════════════════════╗
║  REGLA #2 — TÍTULO DE CASINO + EXPLICACIÓN SIMPLE             ║
╚═══════════════════════════════════════════════════════════════╝
El usuario necesita DOS cosas: saber qué apostar EN EL CASINO (Bet365,
Caliente) y ENTENDER qué significa. Por eso:

1) EL TÍTULO DEL PICK usa la nomenclatura REAL del casino, para que el
   usuario la encuentre en la app de apuestas. Puedes poner la versión
   común y entre paréntesis la del casino si ayuda:
   · "Más de 1.5 goles (Over 1.5)"
   · "Más de 2.5 goles (Over 2.5)"
   · "Ambos equipos anotan (BTTS / Sí)"
   · "[Equipo local] gana o empata (Doble oportunidad 1X)"
   · "[Equipo local] con hándicap -1 (gana por 2+)"
   · Córners: usa la línea que corresponda según los datos, puede ser
     "Más de 9.5 córners (Over)" o "Menos de 9.5 córners (Under)". El mercado
     de córners es la SUMA de los córners de LOS DOS equipos. Compara la línea
     del casino contra el TOTAL ESPERADO que te dan los datos; NO tires a under
     por costumbre. Si el total esperado es alto, el pick correcto es OVER.
   NO inventes nomenclatura: usa la estándar que aparece en los casinos.
   IMPORTANTE: usa SIEMPRE los nombres reales de los DOS equipos de ESTE
   partido. NUNCA menciones equipos que no sean los dos que estás analizando.

2) LA EXPLICACIÓN (texto debajo del título) va en lenguaje 100% simple,
   como si le explicaras a un amigo. Ahí SÍ traduces todo:
   · "Esto pega si caen 2 goles o más en todo el partido."
   · "Significa que los dos equipos metan al menos un gol cada uno."
   · No uses "xG", "línea asiática", etc. en la explicación. Di
     "generó muchas ocasiones de gol", "mereció más goles", etc.

3) Regla de oro: por el TÍTULO el usuario sabe qué botón picar en el
   casino; por la EXPLICACIÓN entiende por qué y qué significa.
   Directo, claro, estilo mexicano natural.

FORMATO DE SALIDA (en Markdown, en este orden EXACTO, sin nada extra):

# [Local] vs [Visitante]

## Lectura del partido
(2-4 líneas: ritmo esperado, contexto, quién necesita ganar. Si detectas
partido trampa, dilo aquí.)

## ⭐ Modelo Conservador
(Los picks más seguros. Para CADA pick usa este formato exacto:)
**PICK:** [selección concreta] · **Confianza:** [XX%]
[una línea de por qué]

## 🔥 Modelo Estakazo
(Picks de mayor retorno con respaldo. Mismo formato de pick que arriba.)

## Derechas
(Solo si goleador o amonestado superan 80%. Si no, escribe exactamente:
"Sin derechas de alta confianza en este partido.")

## Descartados
(Lista breve de picks que evaluaste y rechazaste, con el motivo en pocas
palabras. Máximo 4.)

## Riesgo principal
(1-2 líneas del riesgo más importante a vigilar.)

## Veredicto
(2-3 líneas de cierre con la recomendación final y el stake sugerido.)

NO agregues nada después del veredicto. Nada de checklists, firmas, ni
etiquetas de responsabilidad."""


# ═══════════════════════════════════════════════════════════════════
#   REGLAS DURAS (deterministas, se aplican SIEMPRE, no a criterio de IA)
# ═══════════════════════════════════════════════════════════════════

def aplicar_reglas_duras(mercado: str, linea: float | None = None,
                         hay_datos_corners: bool = True) -> tuple[bool, str]:
    """
    (permitido, razón). Si permitido=False, el pick se BLOQUEA aunque
    la IA lo sugiera. Estas son tus lecciones baked-in.
    """
    m = mercado.lower()
    es_corner = "corner" in m or "córner" in m or "esquina" in m

    # Nunca mandar under de goles bajo
    if "under" in m and "gol" in m:
        if linea is not None and linea < 4.5:
            return (False, "REGLA DURA: nunca mandar under de menos de 4.5 goles")
        return (False, "REGLA DURA: Manuldo no prefiere unders de goles")

    # Córners: si NO hay datos confiables de córners, no generamos el pick.
    # Mejor no ofrecer pick que ofrecer uno basado en datos malos/ausentes.
    if es_corner and not hay_datos_corners:
        return (False, "REGLA DURA: sin datos confiables de córners, no se sugiere")

    return (True, "OK")


def _stat_jugador(sd: dict, alias: list[str]):
    """Busca una stat de jugador tolerando variantes de nombre (yellow cards,
    tarjetas_amarillas, amarillas...). Devuelve el valor o None."""
    for a in alias:
        if a in sd and sd[a] is not None:
            return sd[a]
    def norm(s):
        return "".join(ch for ch in str(s).lower() if ch.isalnum())
    alias_n = [norm(a) for a in alias]
    for clave, valor in sd.items():
        if valor is None:
            continue
        if norm(clave) in alias_n:
            return valor
    return None


# alias de tarjetas como las puede nombrar FotMob / el Scout
_ALIAS_AMARILLAS = ["amarillas", "yellow cards", "yellow_cards", "yellows",
                    "tarjetas amarillas", "tarjetas_amarillas", "ta"]
_ALIAS_ROJAS = ["rojas", "red cards", "red_cards", "reds",
                "tarjetas rojas", "tarjetas_rojas", "tr"]


def _stat_flex(sd: dict, alias: list[str], lado: str):
    """Busca una stat en sportData tolerando variantes de nombre (mayúsculas,
    'corner kicks' vs 'corners', etc.). lado: 'local' o 'visita'."""
    for a in alias:
        clave = f"{a}_{lado}"
        if clave in sd and sd[clave] is not None:
            return sd[clave]
    def norm(s):
        return "".join(ch for ch in str(s).lower() if ch.isalnum())
    lado_alt = {"local": ["local", "home", "casa"],
                "visita": ["visita", "visitante", "away", "fuera"]}[lado]
    alias_n = [norm(a) for a in alias]
    for clave, valor in sd.items():
        if valor is None:
            continue
        ck = norm(clave)
        if any(a in ck for a in alias_n) and any(norm(l) in ck for l in lado_alt):
            return valor
    return None


def shrink_corners(probabilidad: float, mercado: str) -> float:
    """Córners volátiles: encoge la probabilidad hacia la incertidumbre."""
    if "corner" in mercado.lower() or "córner" in mercado.lower():
        return 0.5 + (probabilidad - 0.5) * 0.7
    return probabilidad


# ═══════════════════════════════════════════════════════════════════
#   ARMADO DE CONTEXTO DESDE MONGO
#   Saca los datos reales para alimentar el prompt.
# ═══════════════════════════════════════════════════════════════════

def _norm_equipo(s):
    """Normaliza nombre de equipo: sin acentos, minúsculas, sin FC/CF/ruido."""
    import unicodedata
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().strip()
    # quitar sufijos de club como PALABRAS completas (no subcadenas), para no
    # romper nombres. NO quitar 'real' (distingue Madrid/Sociedad/Betis) ni
    # 'atletico' (distingue Atlético Madrid/San Luis).
    palabras = [p for p in s.split() if p not in ("fc", "cf", "cd", "club", "deportivo", "ac", "sc")]
    return " ".join(palabras)


def _empareja(a, b):
    """True si dos nombres de equipo son el mismo (flexible)."""
    na, nb = _norm_equipo(a), _norm_equipo(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _empareja_estricto(a, b):
    """Como _empareja, pero exige nombre EXACTO (normalizado) — sin
    contención de subcadena. Úsalo cuando el filtro de 'liga' ya se quitó
    (torneos internacionales que cruzan TODAS las ligas): ahí la
    contención es peligrosa, porque un nombre corto como 'Inter' queda
    contenido dentro de 'Inter Miami' y los confunde (son equipos
    distintos, uno en Serie A/Champions y otro en la MLS)."""
    na, nb = _norm_equipo(a), _norm_equipo(b)
    return bool(na) and bool(nb) and na == nb


# Filtro para restringir a partidos de FÚTBOL cuando se cruza toda la
# colección sin filtro de liga. db.partidos guarda TODOS los deportes
# (fútbol, NFL, MLB) juntos; sin esto, el "historial reciente" de un
# equipo en un torneo internacional puede mezclar resultados de NFL/MLB.
_SOLO_FUTBOL = {"$or": [{"deporte": "futbol"}, {"deporte": {"$exists": False}}]}


def _forma_equipo(db, equipo: str, liga: str, n: int = 5) -> list[dict]:
    """
    Últimos N partidos JUGADOS de un equipo (como local o visitante).
    Usa emparejamiento FLEXIBLE de nombre (ignora acentos, FC/CF, etc.)
    para no fallar cuando el nombre varía entre proveedores.

    Para torneos internacionales (Champions, Europa, Leagues Cup), donde el
    equipo apenas tiene historial propio, busca en TODAS las ligas: así un
    Barcelona en Champions trae su forma reciente de LaLiga. Pero esa
    búsqueda cruzada SIEMPRE se restringe a fútbol (nunca NFL/MLB) y usa
    nombre EXACTO, no por subcadena (ver _empareja_estricto).
    """
    TORNEOS_SIN_HISTORIAL = {"championsleague", "europaleague", "leaguescup",
                             "conferenceleague", "mundialclubes"}
    filtro = {
        "marcadorLocal": {"$ne": None},
        "estado": {"$nin": ["programado", "en_vivo"]},
    }
    es_torneo = liga in TORNEOS_SIN_HISTORIAL
    if es_torneo:
        # sin filtro de liga (busca en todas), pero NUNCA fuera de fútbol
        filtro.update(_SOLO_FUTBOL)
    else:
        filtro["liga"] = liga
    todos = list(
        db.partidos.find(filtro, {"_id": 0}).sort("inicio", -1)
    )
    matcher = _empareja_estricto if es_torneo else _empareja
    partidos = [p for p in todos
                if matcher(p.get("local", ""), equipo) or matcher(p.get("visitante", ""), equipo)]
    return partidos[:n]


def _fila_tabla(db, equipo: str, liga: str) -> dict | None:
    """Fila de tabla de un equipo. En torneos internacionales (Champions, etc.)
    la tabla propia esta vacia, asi que busca en cualquier liga (la domestica).
    Esa búsqueda cruzada usa nombre EXACTO (ver _empareja_estricto) para no
    confundir equipos homónimos de ligas distintas (Inter / Inter Miami)."""
    TORNEOS = {"championsleague", "europaleague", "leaguescup",
               "conferenceleague", "mundialclubes"}
    es_torneo = liga in TORNEOS
    filtro_liga = {} if es_torneo else {"liga": liga}
    fila = db.clasificacion.find_one({**filtro_liga, "equipo": equipo}, {"_id": 0})
    if fila:
        return fila
    matcher = _empareja_estricto if es_torneo else _empareja
    for f in db.clasificacion.find(filtro_liga, {"_id": 0}):
        if matcher(f.get("equipo", ""), equipo):
            return f
    return None


def _resumen_partido(p: dict, foco: str) -> str:
    """Una línea rica de un partido, con las stats clave si están."""
    local, visita = p.get("local"), p.get("visitante")
    gl, gv = p.get("marcadorLocal"), p.get("marcadorVisitante")
    sd = p.get("sportData", {})

    partes = [f"{p.get('inicio','')[:10]}: {local} {gl}-{gv} {visita}"]

    # xG
    xg_l = sd.get("expected goals (xg)_local")
    xg_v = sd.get("expected goals (xg)_visita")
    if xg_l is not None:
        partes.append(f"xG {xg_l}-{xg_v}")

    # Posesión
    pos_l = sd.get("ball possession_local")
    if pos_l is not None:
        partes.append(f"pos {pos_l}%-{sd.get('ball possession_visita')}%")

    # Tiros (búsqueda flexible de nombre)
    tir_l = _stat_flex(sd, ["total shots", "shots", "tiros", "remates"], "local")
    tir_v = _stat_flex(sd, ["total shots", "shots", "tiros", "remates"], "visita")
    if tir_l is not None:
        partes.append(f"tiros {tir_l}-{tir_v}")

    # Córners (búsqueda flexible: tolera 'corner kicks', mayúsculas, etc.)
    cor_l = _stat_flex(sd, ["corners", "corner kicks", "corner", "córners", "tiros de esquina"], "local")
    cor_v = _stat_flex(sd, ["corners", "corner kicks", "corner", "córners", "tiros de esquina"], "visita")
    if cor_l is not None:
        partes.append(f"córners {cor_l}-{cor_v}")

    return " | ".join(partes)


def armar_contexto_datos(db, partido: DatosPartido) -> str:
    """
    Construye el bloque de DATA DURA que se le da al prompt, sacando
    todo de MongoDB: forma reciente, tabla, y stats de ambos equipos.
    """
    local, visita, liga = partido.local, partido.visitante, partido.liga
    bloques = []

    TORNEOS = {"championsleague", "europaleague", "leaguescup",
               "conferenceleague", "mundialclubes"}
    if liga in TORNEOS:
        bloques.append(
            "NOTA: Este es un partido de torneo internacional. Los equipos "
            "apenas tienen historial en esta competición, así que la forma "
            "reciente, tabla y stats mostradas provienen de sus LIGAS "
            "DOMÉSTICAS (ej. LaLiga, Eredivisie). Es un benchmark válido del "
            "nivel de cada equipo, pero considéralo al analizar: el contexto "
            "de un torneo puede diferir del de liga.")

    # Clasificación de ambos
    for eq in (local, visita):
        fila = _fila_tabla(db, eq, liga)
        if fila:
            sd = fila.get("sportData", {})
            bloques.append(
                f"TABLA {eq}: pos {fila.get('pos')}, {fila.get('puntos', sd.get('puntos'))} pts, "
                f"{fila.get('ganados')}G-{fila.get('empatados')}E-{fila.get('perdidos')}P, "
                f"GF {sd.get('golesFavor')} GC {sd.get('golesContra')}"
            )

    # Forma reciente de ambos
    for eq in (local, visita):
        recientes = _forma_equipo(db, eq, liga, n=5)
        if recientes:
            lineas = [f"  - {_resumen_partido(p, eq)}" for p in recientes]
            bloques.append(f"ÚLTIMOS PARTIDOS {eq}:\n" + "\n".join(lineas))
        else:
            bloques.append(f"ÚLTIMOS PARTIDOS {eq}: (sin datos en la base)")

    # CÓRNERS DEL PARTIDO (clave para el mercado): el mercado de córners es la
    # SUMA de córners de ambos equipos. Calculamos el total esperado sumando el
    # promedio de córners que genera cada equipo, para que la IA no subestime.
    def _prom_corners_equipo(eq):
        recientes = _forma_equipo(db, eq, liga, n=6)
        vals = []
        for p in recientes:
            sd = p.get("sportData", {}) or {}
            es_local = _empareja(p.get("local", ""), eq)
            lado = "local" if es_local else "visita"
            c = _stat_flex(sd, ["corners", "corner kicks", "corner", "córners", "tiros de esquina"], lado)
            if c is not None:
                try:
                    vals.append(float(c))
                except Exception:
                    pass
        return (sum(vals) / len(vals)) if vals else None

    pc_local = _prom_corners_equipo(local)
    pc_visita = _prom_corners_equipo(visita)
    if pc_local is not None and pc_visita is not None:
        total = round(pc_local + pc_visita, 1)
        bloques.append(
            f"CÓRNERS ESPERADOS DEL PARTIDO: ~{total} en total "
            f"({local} saca ~{round(pc_local,1)}, {visita} saca ~{round(pc_visita,1)}). "
            f"OJO: el mercado de córners es la SUMA de ambos equipos, no el de uno solo. "
            f"Un total de ~{total} sugiere que líneas por debajo de {total} tienden a OVER "
            f"y por encima tienden a UNDER. NO asumas under por defecto: compara la línea "
            f"del casino contra este total esperado.")

    # Momios pegados (si el usuario los mandó)
    if partido.momios:
        bloques.append(f"MOMIOS DISPONIBLES (Bet365):\n{partido.momios}")

    # Jugadores destacados de cada equipo (para goleador/amonestado)
    for eq in (local, visita):
        # traer todos los de la liga y filtrar por emparejamiento flexible de equipo
        todos_liga = list(
            db.jugadores.find(
                {"liga": liga},
                {"_id": 0, "nombre": 1, "posicion": 1, "equipo": 1, "sportData": 1,
                 "lesionado": 1, "lesion_info": 1},
            )
        )
        jugadores = [j for j in todos_liga if _empareja(j.get("equipo", ""), eq)][:30]
        if not jugadores:
            continue
        # ordenar por rating para mostrar los más relevantes
        def _rating(j):
            return (j.get("sportData", {}) or {}).get("rating") or 0
        jugadores.sort(key=_rating, reverse=True)
        top = jugadores[:8]
        lineas = []
        for j in top:
            sd = j.get("sportData", {}) or {}
            stats = []
            if sd.get("goles") is not None:
                stats.append(f"{sd['goles']}g")
            if sd.get("asistencias") is not None:
                stats.append(f"{sd['asistencias']}a")
            _amar = _stat_jugador(sd, _ALIAS_AMARILLAS)
            if _amar is not None:
                stats.append(f"{_amar}TA")
            if sd.get("rating") is not None:
                stats.append(f"rating {sd['rating']}")
            pos = j.get("posicion", "")
            # marca de lesión: dato crítico para no confiar en un jugador que no jugará
            if j.get("lesionado"):
                info = j.get("lesion_info", "")
                marca = f"  ⚠️ LESIONADO — NO DISPONIBLE ({info})" if info else "  ⚠️ LESIONADO — NO DISPONIBLE"
                lineas.append(
                    f"  - {j.get('nombre')}"
                    f"{' ('+pos+')' if pos else ''}: {', '.join(stats) if stats else 'sin stats'}"
                    f"{marca}"
                )
            else:
                lineas.append(
                    f"  - {j.get('nombre')}"
                    f"{' ('+pos+')' if pos else ''}: {', '.join(stats) if stats else 'sin stats'}"
                )
        bloques.append(f"JUGADORES DESTACADOS {eq}:\n" + "\n".join(lineas))

    # --- ALINEACIÓN CONFIRMADA (si la tenemos de API-Football) ---
    try:
        clave_ali = f"{partido.liga}:{partido.local}:{partido.visitante}"
        ali = db.alineaciones.find_one({"clave": clave_ali}, {"_id": 0})
        if ali and ali.get("equipos"):
            estado = "CONFIRMADA (oficial)" if ali.get("confirmada") else "probable (aún no oficial)"
            lin_ali = [f"ALINEACIÓN {estado}:"]
            for eq in ali["equipos"]:
                nombres = ", ".join(t.get("nombre", "") for t in eq.get("titulares", []))
                form = eq.get("formacion", "")
                lin_ali.append(f"  {eq.get('nombre','')} ({form}): {nombres}")
            bloques.append("\n".join(lin_ali))
    except Exception:
        pass

    if not bloques:
        return "(No hay datos en la base para este partido. NO inventes: dilo.)"

    return "\n\n".join(bloques)
