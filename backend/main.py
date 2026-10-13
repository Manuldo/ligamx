"""
main.py
-------
El backend FastAPI. Junta las tres capas:

  1. Lógica dura (models/betting.py)  -> calcula índices, aplica reglas
  2. Retrieval (rag/retriever.py)     -> recupera conocimiento relevante
  3. Generación (Anthropic)           -> produce el análisis final

FLUJO RAG completo:
  partido -> [calcular índices] + [recuperar contexto] -> [LLM] -> análisis

Levantar en local:
  uvicorn main:app --reload --port 3131
"""

import os
import hmac
import re
import hashlib
import logging
import time
from uuid import uuid4
from fastapi import FastAPI, Header, HTTPException, Depends, Request, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import os as _os
from pydantic import BaseModel, Field
from anthropic import Anthropic

from models.betting import (
    DatosPartido, PROMPT_MAESTRO, aplicar_reglas_duras,
    armar_contexto_datos, _forma_equipo, _empareja,
)
from models.parlay import (
    validar_boleto, mejores_parlays, mejores_parlays_estimados,
    parlays_sonadores, generar_picks_sugeridos,
)
from models.alineaciones import (
    actualizar_alineaciones_liga, hay_api_key,
    actualizar_alineaciones_proximas,
    actualizar_alineaciones_jornada,
    actualizar_marcadores_vivo,
    actualizar_alineacion_partido,
    estado_cuenta_api,
)
from rag.embeddings import generar_embedding
from rag.retriever import Retriever
from db.mongo import get_db, crear_indices
from db.schemas import PartidoIn, JugadorIn, ClasificacionIn


class NuevoAprendizaje(BaseModel):
    """Aprendizaje curado para la memoria vectorial."""
    id: str = Field(min_length=1, max_length=160)
    texto: str = Field(min_length=10, max_length=12000)
    tipo: str = Field(default="aprendizaje", max_length=40)
    liga: str = Field(default="general", max_length=60)

# Se sube a mano cada vez que se entrega un zip nuevo. Sirve para confirmar
# en /salud (público, sin token) si el deploy de Railway REALMENTE agarró el
# código nuevo, en vez de adivinar por la hora del "Success" en Deployments.
MOTOR_VERSION = "9.56"

ENVIRONMENT = os.environ.get("ENVIRONMENT", "development").strip().lower()
IS_PRODUCTION = ENVIRONMENT == "production"
ENABLE_INTERNAL_UI = os.environ.get("ENABLE_INTERNAL_UI", "false").lower() == "true"
ENABLE_INTERNAL_SCHEDULER = os.environ.get("ENABLE_INTERNAL_SCHEDULER", "false").lower() == "true"
API_TOKEN = os.environ.get("API_TOKEN", "")
PICKAZO_BRIDGE_KEY = os.environ.get("PICKAZO_BRIDGE_KEY", "")

_required = []
if IS_PRODUCTION:
    _required = ["MONGO_URI", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "API_TOKEN", "PICKAZO_BRIDGE_KEY"]
_missing = [name for name in _required if not os.environ.get(name)]
if _missing:
    raise RuntimeError(f"Faltan variables críticas: {', '.join(_missing)}")
if IS_PRODUCTION:
    _weak = [name for name in ("API_TOKEN", "PICKAZO_BRIDGE_KEY")
             if len(os.environ.get(name, "")) < 32]
    if _weak:
        raise RuntimeError(f"Secretos demasiado cortos (mínimo 32 caracteres): {', '.join(_weak)}")

app = FastAPI(
    title="Pickazo MX Engine",
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None if IS_PRODUCTION else "/redoc",
)

from fastapi.middleware.cors import CORSMiddleware
_origins_env = os.environ.get("CORS_ORIGINS", "")
_origins = [o.strip().rstrip("/") for o in _origins_env.split(",") if o.strip()]
if not _origins and not IS_PRODUCTION:
    _origins = ["http://localhost:3000", "http://localhost:3131"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
    max_age=86400,
)

logger = logging.getLogger("pickazo.engine")


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid4())
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    logger.info("request method=%s path=%s status=%s duration_ms=%s request_id=%s",
                request.method, request.url.path, response.status_code, elapsed_ms, request_id)
    return response

_retriever = None
_anthropic = None


def get_retriever() -> Retriever:
    global _retriever
    if _retriever is None:
        _retriever = Retriever()
    return _retriever


def get_anthropic() -> Anthropic:
    global _anthropic
    if _anthropic is None:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise HTTPException(status_code=503, detail="Anthropic no configurado")
        _anthropic = Anthropic(api_key=key)
    return _anthropic


def _validar_bearer(authorization: str, secreto: str, nombre: str):
    if not secreto:
        if IS_PRODUCTION:
            raise HTTPException(status_code=503, detail=f"{nombre} no configurado")
        return True
    esperado = f"Bearer {secreto}"
    if not hmac.compare_digest(authorization or "", esperado):
        raise HTTPException(status_code=401, detail=f"{nombre} inválido o ausente")
    return True


def verificar_token(authorization: str = Header(default="")):
    return _validar_bearer(authorization, API_TOKEN, "Token")


def verificar_bridge(authorization: str = Header(default="")):
    """Valida la llamada privada de pickazoapp al motor (y paneles admin)."""
    return _validar_bearer(authorization, PICKAZO_BRIDGE_KEY, "Llave de puente")


def _parse_inicio(p):
    """Devuelve datetime UTC del inicio del partido, o None si no se puede."""
    from datetime import datetime, timezone
    raw = p.get("inicio", "") or ""
    if not raw:
        return None
    try:
        s = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _es_realmente_vivo(p) -> bool:
    """Un partido está EN VIVO de verdad solo si su estado lo dice Y la hora
    lo respalda: ya empezó y no han pasado más de 2.5h desde el inicio.
    Evita 'EN VIVO' congelado en partidos ya terminados o no empezados."""
    from datetime import datetime, timezone, timedelta
    estado = p.get("estado", "programado")
    if estado not in ("vivo", "en_vivo", "en_juego"):
        return False
    dt = _parse_inicio(p)
    if dt is None:
        # sin hora fiable: confiamos en el estado (mejor que nada)
        return True
    ahora = datetime.now(timezone.utc)
    # debe haber empezado (con 5 min de tolerancia) y no llevar más de 2.5h
    if ahora < dt - timedelta(minutes=5):
        return False   # aún no empieza
    if ahora > dt + timedelta(hours=2.5):
        return False   # ya debería haber terminado
    return True


def _estado_por_hora(p) -> str:
    """Estado 'seguro' cuando NO está en vivo: si ya pasó tiempo de sobra desde
    el inicio, es finalizado (sin importar lo que diga 'estado' guardado); si
    no ha empezado, programado.

    Antes, si 'estado' se había quedado en 'programado' (porque el scheduler
    de en vivo nunca lo tocó: típico en MLB, que no tiene ese scheduler, o en
    NFL/fútbol si el sync se saltó ese partido), el partido se quedaba
    "próximo" para siempre aunque hubiera pasado días — por eso aparecían
    partidos de hace una semana en 'Próximos partidos'. Ahora, pasado el
    margen, SIEMPRE se cierra a 'finalizado' sin importar el estado guardado."""
    from datetime import datetime, timezone, timedelta
    estado = p.get("estado", "programado")
    if estado == "finalizado":
        return "finalizado"
    dt = _parse_inicio(p)
    if dt is None:
        return estado if estado in ("programado", "finalizado") else "programado"
    ahora = datetime.now(timezone.utc)
    if ahora > dt + timedelta(hours=2.5):
        # ya pasó tiempo de sobra: el partido ya terminó en la realidad,
        # tenga o no marcador sincronizado y sin importar qué decía 'estado'.
        return "finalizado"
    if ahora < dt:
        return "programado"
    return estado if estado in ("programado", "finalizado") else "programado"


_STATIC_DIR = _os.path.join(_os.path.dirname(__file__), "static")


@app.get("/")
def home():
    """En producción el motor es privado y la cara pública vive en pickazoapp.com."""
    if IS_PRODUCTION and not ENABLE_INTERNAL_UI:
        return {"estado": "vivo", "servicio": "Pickazo MX Engine"}
    index = _os.path.join(_STATIC_DIR, "index.html")
    if _os.path.exists(index):
        return FileResponse(index)
    return {"estado": "vivo"}


@app.get("/salud")
def salud():
    """Readiness real: responde 503 cuando Mongo no está disponible."""
    mongo = False
    try:
        get_db().command("ping")
        mongo = True
    except Exception:
        pass
    return JSONResponse(
        status_code=200 if mongo else 503,
        content={"estado": "vivo" if mongo else "degradado", "mongo": mongo,
                  "motor_version": MOTOR_VERSION},
    )


@app.post("/analizar")
def analizar(partido: DatosPartido, _=Depends(verificar_token)):
    """
    Analiza un partido con el Sistema Manuldo Tipster 2025 Blindado.

    OPTIMIZADO PARA COSTO:
    - Modelo Haiku por defecto (10x más barato); Sonnet solo si se pide.
    - Prompt caching del sistema fijo (se cobra fracción en repeticiones).
    - Guarda el análisis en Mongo; si ya existe y no se fuerza, lo devuelve
      SIN volver a pagar la llamada a la IA.
    """
    db = None
    try:
        db = get_db()
    except Exception:
        pass

    # Corrige la liga: busca en qué liga existen REALMENTE estos dos equipos.
    # Evita que se muestre "Liga MX" en un partido de otra liga por un error
    # del front al no seleccionar la liga correcta.
    if db is not None:
        try:
            real = db.partidos.find_one(
                {"local": partido.local, "visitante": partido.visitante},
                {"_id": 0, "liga": 1},
                sort=[("inicio", -1)],
            )
            if real and real.get("liga") and real["liga"] != partido.liga:
                partido.liga = real["liga"]
        except Exception:
            pass

    # La clave incluye partido/fecha, modelo, versión del prompt y momios.
    # Evita reciclar por error un análisis de un enfrentamiento anterior.
    referencia = "sin-fecha"
    if db is not None:
        proximo = db.partidos.find_one(
            {"liga": partido.liga, "local": partido.local, "visitante": partido.visitante},
            {"_id": 0, "fuenteId": 1, "inicio": 1},
            sort=[("inicio", 1)],
        )
        if proximo:
            referencia = str(proximo.get("fuenteId") or proximo.get("inicio") or referencia)
    version = os.environ.get("ANALYSIS_CACHE_VERSION", "v2")
    momios_hash = hashlib.sha256(partido.momios.encode("utf-8")).hexdigest()[:10] if partido.momios else "sin-momios"
    clave_analisis = f"{version}:{partido.liga}:{referencia}:{partido.local}:{partido.visitante}:{partido.modelo}:{momios_hash}"

    # --- CACHE DE ANÁLISIS: si ya lo analizamos y no se fuerza, devolver ---
    if db is not None and not partido.forzar:
        previo = db.analisis.find_one({"clave": clave_analisis}, {"_id": 0})
        if previo:
            previo["_desde_cache"] = True  # marca que no se pagó IA
            return previo

    # --- Capa 1: DATA DURA desde Mongo ---
    es_nfl = (partido.liga or "").lower() == "nfl"
    es_mlb = (partido.liga or "").lower() == "mlb"

    if es_nfl:
        # --- Rama NFL: contexto y prompt de fútbol americano ---
        from nfl_analisis import armar_contexto_nfl, PROMPT_SISTEMA_NFL, prompt_partido_nfl
        try:
            contexto_datos = armar_contexto_nfl(db, partido)
        except Exception as e:
            contexto_datos = f"(No se pudo leer datos NFL: {e})"
        # Antes se dejaba vacío a propósito. Ahora sí jala la misma capa de
        # aprendizajes (post-mortems reales) que usa fútbol, filtrada a NFL
        # — así el motor también "aprende" en NFL con el tiempo.
        try:
            consulta_nfl = (
                f"{partido.local} vs {partido.visitante} en NFL. "
                f"aprendizajes reglas partido trampa {partido.contexto_extra}"
            )
            aprendizajes = get_retriever().recuperar_contexto(consulta_nfl, n=5, filtro={"liga": "nfl"})
        except Exception:
            aprendizajes = ""
        prompt_partido = prompt_partido_nfl(
            partido.local, partido.visitante, contexto_datos, partido.momios or "", aprendizajes)
        sistema = PROMPT_SISTEMA_NFL
    elif es_mlb:
        # --- Rama MLB: contexto y prompt de beisbol ---
        from mlb_analisis import armar_contexto_mlb, PROMPT_SISTEMA_MLB, prompt_partido_mlb
        try:
            contexto_datos = armar_contexto_mlb(db, partido)
        except Exception as e:
            contexto_datos = f"(No se pudo leer datos MLB: {e})"
        aprendizajes = ""
        prompt_partido = prompt_partido_mlb(
            partido.local, partido.visitante, contexto_datos, partido.momios or "")
        sistema = PROMPT_SISTEMA_MLB
    else:
        try:
            contexto_datos = armar_contexto_datos(db, partido)
        except Exception as e:
            contexto_datos = f"(No se pudo leer la base de datos: {e})"

        # --- Capa 2: aprendizajes del RAG ---
        consulta = (
            f"{partido.local} vs {partido.visitante} en {partido.liga}. "
            f"aprendizajes reglas partido trampa {partido.contexto_extra}"
        )
        aprendizajes = get_retriever().recuperar_contexto(consulta, n=5, filtro={"liga": partido.liga})

        # --- Capa 3: prompt del partido (lo VARIABLE) ---
        prompt_partido = f"""═══════════════════════════════════════
PARTIDO A ANALIZAR: {partido.local} vs {partido.visitante} ({partido.liga})
═══════════════════════════════════════

DATA DURA (desde la base de datos, real):
{contexto_datos}

APRENDIZAJES Y REGLAS RECUPERADAS (post-mortems previos):
{aprendizajes}

Ahora analiza el partido siguiendo TODA la metodología del Sistema Manuldo
Tipster 2025 Blindado. Fundaméntate SOLO en la data dura y los aprendizajes
proporcionados. Si falta algún dato, dilo, no lo inventes."""
        sistema = PROMPT_MAESTRO

    # Elegir modelo: haiku (barato, ~10x menos que sonnet) o sonnet (profundo)
    modelo_api = (
        "claude-sonnet-4-6" if partido.modelo == "sonnet"
        else "claude-haiku-4-5-20251001"
    )

    # Nota: el prompt maestro (~800 tokens) es más chico que el mínimo de
    # cache de Haiku (4096 tokens), así que el caching no aplica aquí.
    # El ahorro real viene de usar Haiku (10x más barato) + no re-analizar
    # (cache de análisis en Mongo, arriba).
    respuesta = get_anthropic().messages.create(
        model=modelo_api,
        max_tokens=4096,
        system=sistema,
        messages=[{"role": "user", "content": prompt_partido}],
    )

    analisis = respuesta.content[0].text

    resultado = {
        "partido": f"{partido.local} vs {partido.visitante}",
        "clave": clave_analisis,
        "liga": partido.liga,
        "modelo_usado": modelo_api,
        "data_usada": contexto_datos,
        "aprendizajes_usados": aprendizajes,
        "analisis": analisis,
        "_desde_cache": False,
    }

    # --- GUARDAR el análisis (tu punto #5: aprender de errores) ---
    if db is not None:
        try:
            from datetime import datetime, timezone
            doc = dict(resultado)
            doc["creado"] = datetime.now(timezone.utc).isoformat()
            doc["resultado_real"] = "pendiente"  # luego marcas acierto/fallo
            db.analisis.update_one(
                {"clave": clave_analisis}, {"$set": doc}, upsert=True
            )
        except Exception:
            pass

    return resultado


class DerechasExperimental(BaseModel):
    local: str
    visitante: str
    liga: str = "ligamx"


def _derechas_markdown(body: "DerechasExperimental") -> dict:
    """Fútbol: derechas de goleador/amonestado. NFL: candidatos a anotar
    touchdown. Misma lógica para el panel del motor y el puente de
    pickazoapp — lo único que cambia es quién puede llamarla."""
    db = get_db()
    es_nfl = (body.liga or "").lower() == "nfl"

    if es_nfl:
        from nfl_analisis import stats_partido_nfl, _lesiones_relevantes_nfl
        datos = stats_partido_nfl(db, body.local, body.visitante)
        bloques = []
        for lado in ("local", "visitante"):
            b = datos.get(lado, {})
            eq = b.get("equipo", "") or (body.local if lado == "local" else body.visitante)
            anot = b.get("anotadores", [])
            les = _lesiones_relevantes_nfl(eq)
            txt_anot = (", ".join(f"{a['nombre']} ({a['posicion']}): {a['valor']} TD en temporada" for a in anot)
                        or "sin anotadores con datos suficientes")
            txt_les = (", ".join(f"{l.get('jugador')} ({l.get('estado')})" for l in les)
                       or "sin bajas reportadas por ESPN ahora mismo")
            bloques.append(f"{eq}:\nAnotadores de TD recientes: {txt_anot}\n⚠️ Lesiones: {txt_les}")
        contexto = "\n\n".join(bloques)
        sistema = (
            "Eres un analista de props de touchdown de NFL para Pickazo MX (EXPERIMENTAL). "
            "SOLO sugiere anotadores de touchdown (rushing o receiving) con respaldo REAL "
            "en los datos de anotadores recientes. REGLA DURA: nunca sugieras un jugador "
            "que aparezca en el reporte de lesiones como Out/Doubtful/IR. No sugieras a "
            "nadie solo por fama o posición — si no hay nadie con respaldo real de TDs "
            "recientes, dilo honestamente en vez de rellenar con nombres."
        )
        prompt = (
            f"PARTIDO: {body.local} vs {body.visitante} (NFL)\n\nDATOS:\n{contexto}\n\n"
            "Da 2-4 candidatos a anotar touchdown con su nivel de confianza, EXACTAMENTE "
            "en este formato (sin tablas, sin ### , sin >):\n"
            "**PICK:** <jugador> anota touchdown · **Confianza:** <número>%\n"
            "<una línea explicando el porqué con datos>\n\n"
            "Si no hay candidatos con respaldo real, responde solo:\n"
            "Sin anotadores de alta confianza en este partido."
        )
    else:
        from models.betting import armar_contexto_datos
        dp = DatosPartido(local=body.local, visitante=body.visitante, liga=body.liga)
        contexto = armar_contexto_datos(db, dp)
        sistema = (
            "Eres un analista de derechas (goleador y amonestado) para Pickazo MX "
            "(EXPERIMENTAL). SOLO sugiere jugadores con respaldo real de goles o "
            "tarjetas recientes en los datos. REGLA DURA: nunca sugieras un jugador "
            "marcado 'LESIONADO — NO DISPONIBLE'. Si no hay nadie con respaldo fuerte, "
            "dilo honestamente en vez de rellenar con nombres por fama."
        )
        prompt = (
            f"PARTIDO: {body.local} vs {body.visitante} ({body.liga})\n\nDATOS:\n{contexto}\n\n"
            "Da 2-4 derechas de goleador y/o amonestado con su nivel de confianza, "
            "EXACTAMENTE en este formato (sin tablas, sin ### , sin >):\n"
            "**PICK:** <jugador> anota gol (Goleador) · **Confianza:** <número>%\n"
            "<una línea explicando el porqué con datos>\n\n"
            "(Para amonestado usa: **PICK:** <jugador> recibe tarjeta (Amonestado) · **Confianza:** <número>%)\n\n"
            "Si no hay candidatos con respaldo real, responde solo:\n"
            "Sin derechas de alta confianza en este partido."
        )

    respuesta = get_anthropic().messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=sistema,
        messages=[{"role": "user", "content": prompt}],
    )
    return {"ok": True, "liga": body.liga, "derechas_markdown": respuesta.content[0].text}


@app.post("/pickazo/derechas_experimental")
def derechas_experimental(body: DerechasExperimental, _=Depends(verificar_token)):
    """Panel del motor — token de admin."""
    return _derechas_markdown(body)


@app.post("/pickazo/derechas")
def derechas_puente(body: DerechasExperimental, _=Depends(verificar_bridge)):
    """Puente — esta es la que llama pickazoapp (Node) con la llave de
    puente. Mismo resultado que la del panel, ya no tan 'experimental':
    Manuldo ya la probó y la quiere en la app real."""
    return _derechas_markdown(body)


@app.post("/aprender")
def aprender(nuevo: NuevoAprendizaje, _=Depends(verificar_token)):
    """
    Mete un aprendizaje nuevo al vector store SIN reiniciar nada.
    Úsalo después de cada jornada: post-mortem de un pick que falló/pegó.
    La memoria del sistema crece sola.
    """
    vector = generar_embedding(nuevo.texto)
    get_retriever().store.agregar(
        ids=[nuevo.id],
        textos=[nuevo.texto],
        embeddings=[vector],
        metadatas=[{"tipo": nuevo.tipo, "liga": nuevo.liga}],
    )
    return {"guardado": nuevo.id, "total_en_memoria": get_retriever().store.contar()}


@app.get("/memoria")
def ver_memoria(consulta: str = "aprendizajes de apuestas", n: int = 10, _=Depends(verificar_token)):
    """
    Ver qué hay en la memoria. Útil para revisar que la data se cargó bien.
    Recupera los N documentos más parecidos a la consulta.
    """
    contexto = get_retriever().recuperar_contexto(consulta, n=min(max(n, 1), 25))
    return {
        "total_en_memoria": get_retriever().store.contar(),
        "muestra": contexto,
    }


# ==========================================================
#   CAPA DE DATOS DURA (MongoDB Atlas)
#   Partidos, jugadores, clasificación, benchmark.
#   Esto NO va a RAG: es data estructurada y masiva.
# ==========================================================

@app.post("/setup-db")
def setup_db(_=Depends(verificar_token)):
    """Crea los índices de Mongo. Córrelo UNA vez tras el primer deploy."""
    crear_indices()
    return {"ok": True, "mensaje": "Índices de Mongo creados."}


from fastapi import Depends


def _intentar_tomar_lock_nfl(db):
    """Adquiere el candado de sincronización NFL de forma atómica.

    Antes, cada endpoint hacía find_one() y LUEGO, en una llamada aparte,
    dejaba que el background task pusiera corriendo=True. Entre esas dos
    operaciones hay un hueco (race condition): si el botón se clickea dos
    veces rápido (doble clic, doble tab, doble submit), ambas peticiones
    pueden leer corriendo=false antes de que cualquiera alcance a marcarlo,
    y las dos arrancan su propio _ejecutar_sync_nfl_todas en paralelo —
    cada una con su propio contador desde 0, peleándose por escribir el
    mismo documento de estado. Eso es lo que hacía que el reporte final
    bajara en vez de subir: la sincronización que iba más atrás pisaba a
    la que ya llevaba más avance.

    find_one_and_update con filtro corriendo != True es una sola operación
    atómica en Mongo: solo una de las dos peticiones puede "ganar" el
    documento, la otra ve que ya no cumple el filtro y no lo toca.
    Devuelve True si se tomó el candado (se puede arrancar), False si ya
    hay una corriendo."""
    from datetime import datetime, timezone
    from pymongo.errors import DuplicateKeyError
    try:
        r = db.nfl_estado.update_one(
            {"_id": "sync", "corriendo": {"$ne": True}},
            {"$set": {"corriendo": True, "fase": "Arrancando...",
                       "actualizado": datetime.now(timezone.utc).isoformat()}},
            upsert=True)
        return r.matched_count > 0 or r.upserted_id is not None
    except DuplicateKeyError:
        # Dos peticiones llegaron al mismo tiempo: el doc "sync" ya existe
        # con corriendo=True (lo tomó la otra), así que esta pierde la carrera.
        return False


def _ejecutar_sync_nfl(semana, temporada, tipo, profundo, incluir_plays, acumulado=None):
    """Trabajo pesado de la sync NFL. Corre en segundo plano (BackgroundTasks).
    Guarda su progreso en db.nfl_estado para poder consultarlo desde el panel.

    `acumulado`, si se pasa, es un dict {partidos, detalle_profundo, jugadores,
    plays} con los totales acumulados de corridas previas (lo usa
    _ejecutar_sync_nfl_todas al recorrer varias semanas). Cuando está presente,
    el progreso y el reporte final que se guardan en db.nfl_estado son el
    acumulado + lo de esta semana, en vez de resetear a 0 en cada semana (que
    es lo que hacía que el panel reportara "0 jugadores" al terminar: cada
    llamada pisaba el conteo de la semana anterior con el de la última)."""
    from nfl_scraper import obtener_partidos_nfl, obtener_detalle_partido
    from datetime import datetime, timezone
    db = get_db()

    base = dict(acumulado) if acumulado is not None else {
        "partidos": 0, "detalle_profundo": 0, "jugadores": 0, "plays": 0}

    def _estado(patch):
        patch["actualizado"] = datetime.now(timezone.utc).isoformat()
        db.nfl_estado.update_one({"_id": "sync"}, {"$set": patch}, upsert=True)

    _estado({"corriendo": True, "fase": "Listando partidos...",
             "partidos": base["partidos"], "detalle_profundo": base["detalle_profundo"],
             "jugadores": base["jugadores"], "plays": base["plays"], "error": None})

    try:
        if semana is None and tipo == 1:
            semanas_a_jalar = [1, 2, 3, 4]   # pretemporada: HOF + Pre 1,2,3
        elif semana is None:
            semanas_a_jalar = [None]
        else:
            semanas_a_jalar = [semana]

        partidos = []
        _vistos = set()
        for _wk in semanas_a_jalar:
            lote = obtener_partidos_nfl(semana=_wk, temporada=temporada, tipo_temporada=tipo)
            for _p in lote:
                _fid = _p.get("fuenteId")
                if _fid and _fid in _vistos:
                    continue
                if _fid:
                    _vistos.add(_fid)
                partidos.append(_p)

        guardados = 0
        for doc in partidos:
            filtro = {"liga": "nfl", "fuenteId": doc["fuenteId"]} if doc.get("fuenteId") else {
                "liga": "nfl", "local": doc["local"], "visitante": doc["visitante"], "inicio": doc["inicio"]}
            db.partidos.update_one(filtro, {"$set": doc}, upsert=True)
            guardados += 1
        _estado({"partidos": base["partidos"] + guardados,
                 "fase": f"{guardados} partidos guardados. Bajando detalle..."})

        detalle_ok = jugadores_tot = plays_tot = 0
        if profundo:
            import time as _t
            for i, p in enumerate(partidos):
                gid = p.get("fuenteId")
                if not gid or p.get("estado") == "programado":
                    continue
                det = obtener_detalle_partido(gid, incluir_plays=incluir_plays)
                if not det:
                    continue
                db.nfl_raw.update_one({"fuenteId": gid},
                    {"$set": {"fuenteId": gid, "capturado": det["capturado"], "raw": det["raw"]}}, upsert=True)
                db.nfl_partidos.update_one({"fuenteId": gid},
                    {"$set": {"fuenteId": gid, "liga": "nfl", "deporte": "americano",
                              "capturado": det["capturado"], "resumen": det["resumen"],
                              "equipo_stats": det["equipo_stats"], "drives": det["drives"]}}, upsert=True)
                # tipo_temporada va en cada registro de jugador: sin esto no se
                # puede separar pretemporada de temporada regular al armar los
                # "Números del partido" (antes se promediaba todo junto).
                for j in det["jugadores"]:
                    db.nfl_jugadores.update_one(
                        {"fuenteId": gid, "jugadorId": j["jugadorId"], "categoria": j["categoria"]},
                        {"$set": {**j, "fuenteId": gid, "liga": "nfl",
                                  "tipo_temporada": p.get("tipo_temporada")}}, upsert=True)
                jugadores_tot += len(det["jugadores"])
                if incluir_plays and det["plays"]:
                    db.nfl_plays.delete_many({"fuenteId": gid})
                    db.nfl_plays.insert_many([{**pl, "fuenteId": gid, "liga": "nfl"} for pl in det["plays"]])
                    plays_tot += len(det["plays"])
                detalle_ok += 1
                _estado({"detalle_profundo": base["detalle_profundo"] + detalle_ok,
                         "jugadores": base["jugadores"] + jugadores_tot,
                         "plays": base["plays"] + plays_tot,
                         "fase": f"Detalle {detalle_ok}/{len(partidos)}..."})
                if i < len(partidos) - 1:
                    _t.sleep(1.0)

        try:
            _resolver_track(db)
        except Exception:
            pass

        total_partidos = base["partidos"] + guardados
        total_detalle = base["detalle_profundo"] + detalle_ok
        total_jugadores = base["jugadores"] + jugadores_tot
        total_plays = base["plays"] + plays_tot

        if acumulado is not None:
            acumulado["partidos"] = total_partidos
            acumulado["detalle_profundo"] = total_detalle
            acumulado["jugadores"] = total_jugadores
            acumulado["plays"] = total_plays

        _estado({"corriendo": False, "fase": "✅ Completado",
                 "partidos": total_partidos, "detalle_profundo": total_detalle,
                 "jugadores": total_jugadores, "plays": total_plays})
    except Exception as e:
        _estado({"corriendo": False, "fase": "❌ Error", "error": f"{type(e).__name__}: {e}"})


def _ejecutar_sync_nfl_todas(temporada, tipo, profundo, incluir_plays):
    """Sincroniza TODAS las semanas (1-18) de la temporada, una por una, para
    re-etiquetar correctamente los partidos que quedaron con semana equivocada.

    Lleva un `acumulado` que se pasa a cada llamada de _ejecutar_sync_nfl para
    que el conteo final (partidos/jugadores/plays) sea la suma de las 18
    semanas y no solo el de la última — antes cada semana reseteaba el estado
    a 0 y el panel terminaba mostrando "0 jugadores" aunque las semanas
    anteriores sí se hubieran guardado bien en la base."""
    db = get_db()
    total_semanas = 18 if tipo == 2 else (4 if tipo == 1 else 5)
    acumulado = {"partidos": 0, "detalle_profundo": 0, "jugadores": 0, "plays": 0}
    for wk in range(1, total_semanas + 1):
        try:
            db.nfl_estado.update_one({"_id": "sync"},
                {"$set": {"corriendo": True, "fase": f"Semana {wk}/{total_semanas}…"}}, upsert=True)
            _ejecutar_sync_nfl(wk, temporada, tipo, profundo, incluir_plays, acumulado=acumulado)
        except Exception as e:
            print(f"[nfl sync todas] semana {wk}: {e}")
    db.nfl_estado.update_one({"_id": "sync"},
        {"$set": {"corriendo": False, "fase": f"✅ {total_semanas} semanas sincronizadas",
                  "partidos": acumulado["partidos"], "detalle_profundo": acumulado["detalle_profundo"],
                  "jugadores": acumulado["jugadores"], "plays": acumulado["plays"]}}, upsert=True)


@app.post("/admin/nfl_arreglar_semanas")
def admin_nfl_arreglar_semanas(background: BackgroundTasks,
                               temporada: int | None = None, tipo: int = 2,
                               _=Depends(verificar_bridge)):
    """Versión para el panel (usa PICKAZO_BRIDGE_KEY): re-sincroniza todas las
    semanas de la NFL para arreglar las que quedaron mal etiquetadas."""
    db = get_db()
    if not _intentar_tomar_lock_nfl(db):
        return {"ok": False, "error": "Ya hay una sincronización en curso."}
    background.add_task(_ejecutar_sync_nfl_todas, temporada, tipo, False, False)
    return {"ok": True, "mensaje": "Re-sincronizando todas las semanas NFL en segundo plano."}


@app.post("/nfl/sincronizar_todas")
def nfl_sincronizar_todas(background: BackgroundTasks,
                          temporada: int | None = None, tipo: int = 2,
                          profundo: bool = False, incluir_plays: bool = False,
                          _=Depends(verificar_token)):
    """Sincroniza TODAS las semanas de la temporada (para arreglar semanas mal
    etiquetadas). Corre en segundo plano. profundo=False por defecto para que
    sea más rápido (solo marcadores/semana, sin bajar todos los detalles)."""
    db = get_db()
    if not _intentar_tomar_lock_nfl(db):
        return {"ok": False, "error": "Ya hay una sincronización en curso."}
    background.add_task(_ejecutar_sync_nfl_todas, temporada, tipo, profundo, incluir_plays)
    return {"ok": True, "mensaje": "🏈 Sincronizando TODAS las semanas en segundo plano. "
            "Esto re-etiqueta los partidos con su semana correcta. Consulta /nfl/estado."}


@app.post("/nfl/sincronizar")
def nfl_sincronizar(background: BackgroundTasks,
                    semana: int | None = None, temporada: int | None = None,
                    tipo: int = 2, profundo: bool = True,
                    incluir_plays: bool = True, _=Depends(verificar_token)):
    """
    Dispara la sincronización NFL EN SEGUNDO PLANO y responde al instante
    (así el navegador no da timeout aunque bajar 49 partidos tarde minutos).
    Consulta el avance con GET /nfl/estado.

    tipo: 1=pretemporada,2=regular,3=playoffs. semana: 1-18 (None=actual/todas).
    """
    db = get_db()
    if not _intentar_tomar_lock_nfl(db):
        est = db.nfl_estado.find_one({"_id": "sync"}) or {}
        return {"ok": False, "error": "Ya hay una sincronización en curso. Espera a que termine.",
                "fase": est.get("fase", "")}
    background.add_task(_ejecutar_sync_nfl, semana, temporada, tipo, profundo, incluir_plays)
    return {"ok": True, "mensaje": "🏈 Sincronización NFL arrancada en segundo plano. "
            "Consulta el avance en /nfl/estado o recarga en un rato."}


@app.get("/nfl/estado")
def nfl_estado(_=Depends(verificar_token)):
    """Muestra cómo va (o cómo terminó) la última sincronización NFL."""
    db = get_db()
    est = db.nfl_estado.find_one({"_id": "sync"}, {"_id": 0})
    return est or {"fase": "Nunca se ha sincronizado", "corriendo": False}


# ══════════════════════════════════════════════════════════════════
#  MLB — mismo patrón que NFL arriba (candado atómico, acumulado entre
#  días en vez de resetear, estado consultable), pero por RANGO DE
#  FECHAS en vez de por semana — la MLB no tiene "semanas".
#  Colección de candado separada (db.mlb_estado) para que un sync de
#  MLB y uno de NFL puedan correr al mismo tiempo sin bloquearse entre sí.
# ══════════════════════════════════════════════════════════════════

def _intentar_tomar_lock_mlb(db):
    """Mismo candado atómico que _intentar_tomar_lock_nfl, para MLB."""
    from datetime import datetime, timezone
    from pymongo.errors import DuplicateKeyError
    try:
        r = db.mlb_estado.update_one(
            {"_id": "sync", "corriendo": {"$ne": True}},
            {"$set": {"corriendo": True, "fase": "Arrancando...",
                       "actualizado": datetime.now(timezone.utc).isoformat()}},
            upsert=True)
        return r.matched_count > 0 or r.upserted_id is not None
    except DuplicateKeyError:
        return False


def _ejecutar_sync_mlb(fecha_inicio, fecha_fin, profundo, acumulado=None):
    """Trabajo pesado de la sync MLB para un rango de fechas. Guarda
    progreso en db.mlb_estado. 'acumulado' funciona igual que en NFL:
    si se pasa, suma sobre los totales previos en vez de resetear —
    así _ejecutar_sync_mlb_rango puede recorrer varios días sin que el
    reporte final se quede solo con el último día."""
    from mlb_scraper import obtener_partidos_mlb, obtener_detalle_partido
    from datetime import datetime, timedelta, timezone
    db = get_db()

    base = dict(acumulado) if acumulado is not None else {
        "partidos": 0, "detalle_profundo": 0, "jugadores": 0}

    def _estado(patch):
        patch["actualizado"] = datetime.now(timezone.utc).isoformat()
        db.mlb_estado.update_one({"_id": "sync"}, {"$set": patch}, upsert=True)

    _estado({"corriendo": True, "fase": f"Listando partidos {fecha_inicio}–{fecha_fin}...",
             "partidos": base["partidos"], "detalle_profundo": base["detalle_profundo"],
             "jugadores": base["jugadores"], "error": None})

    try:
        d0 = datetime.strptime(fecha_inicio, "%Y%m%d")
        d1 = datetime.strptime(fecha_fin, "%Y%m%d")
        if d1 < d0:
            d0, d1 = d1, d0
        dias = (d1 - d0).days + 1

        partidos = []
        _vistos = set()
        for i in range(dias):
            dia = (d0 + timedelta(days=i)).strftime("%Y%m%d")
            for p in obtener_partidos_mlb(dia):
                fid = p.get("fuenteId")
                if fid and fid in _vistos:
                    continue
                if fid:
                    _vistos.add(fid)
                partidos.append(p)

        guardados = 0
        for doc in partidos:
            filtro = {"liga": "mlb", "fuenteId": doc["fuenteId"]} if doc.get("fuenteId") else {
                "liga": "mlb", "local": doc["local"], "visitante": doc["visitante"], "inicio": doc["inicio"]}
            db.partidos.update_one(filtro, {"$set": doc}, upsert=True)
            guardados += 1
        _estado({"partidos": base["partidos"] + guardados,
                 "fase": f"{guardados} partidos guardados. Bajando detalle..."})

        detalle_ok = jugadores_tot = 0
        if profundo:
            import time as _t
            for i, p in enumerate(partidos):
                gid = p.get("fuenteId")
                if not gid or p.get("estado") == "programado":
                    continue
                det = obtener_detalle_partido(gid)
                if not det:
                    continue
                db.mlb_raw.update_one({"fuenteId": gid},
                    {"$set": {"fuenteId": gid, "capturado": det["capturado"], "raw": det["raw"]}}, upsert=True)
                db.mlb_partidos.update_one({"fuenteId": gid},
                    {"$set": {"fuenteId": gid, "liga": "mlb", "deporte": "beisbol",
                              "capturado": det["capturado"], "resumen": det["resumen"],
                              "equipo_stats": det["equipo_stats"]}}, upsert=True)
                for j in det["jugadores"]:
                    db.mlb_jugadores.update_one(
                        {"fuenteId": gid, "jugadorId": j["jugadorId"], "categoria": j["categoria"]},
                        {"$set": {**j, "fuenteId": gid, "liga": "mlb",
                                  "tipo_temporada": p.get("tipo_temporada")}}, upsert=True)
                jugadores_tot += len(det["jugadores"])
                detalle_ok += 1
                _estado({"detalle_profundo": base["detalle_profundo"] + detalle_ok,
                         "jugadores": base["jugadores"] + jugadores_tot,
                         "fase": f"Detalle {detalle_ok}/{len(partidos)}..."})
                if i < len(partidos) - 1:
                    _t.sleep(0.6)

        total_partidos = base["partidos"] + guardados
        total_detalle = base["detalle_profundo"] + detalle_ok
        total_jugadores = base["jugadores"] + jugadores_tot

        if acumulado is not None:
            acumulado["partidos"] = total_partidos
            acumulado["detalle_profundo"] = total_detalle
            acumulado["jugadores"] = total_jugadores

        _estado({"corriendo": False, "fase": "✅ Completado",
                 "partidos": total_partidos, "detalle_profundo": total_detalle,
                 "jugadores": total_jugadores})
    except Exception as e:
        _estado({"corriendo": False, "fase": "❌ Error", "error": f"{type(e).__name__}: {e}"})


@app.post("/mlb/sincronizar")
def mlb_sincronizar(background: BackgroundTasks,
                    fecha_inicio: str = "", fecha_fin: str = "",
                    dias_atras: int = 7, profundo: bool = True,
                    _=Depends(verificar_token)):
    """
    Sincroniza MLB por rango de fechas (YYYYMMDD). Si no se dan fechas,
    usa los últimos 'dias_atras' días hasta hoy — para traer lo más
    reciente (playoffs en curso, por ejemplo) sin tener que acordarte
    de fechas exactas.
    """
    from datetime import datetime, timedelta, timezone
    db = get_db()
    if not fecha_inicio or not fecha_fin:
        hoy = datetime.now(timezone.utc)
        fecha_fin = hoy.strftime("%Y%m%d")
        fecha_inicio = (hoy - timedelta(days=dias_atras)).strftime("%Y%m%d")
    if not _intentar_tomar_lock_mlb(db):
        est = db.mlb_estado.find_one({"_id": "sync"}) or {}
        return {"ok": False, "error": "Ya hay una sincronización de MLB en curso.",
                "fase": est.get("fase", "")}
    background.add_task(_ejecutar_sync_mlb, fecha_inicio, fecha_fin, profundo)
    return {"ok": True, "mensaje": f"⚾ Sincronizando MLB del {fecha_inicio} al {fecha_fin} "
            "en segundo plano. Consulta /mlb/estado."}


@app.get("/mlb/estado")
def mlb_estado(_=Depends(verificar_token)):
    """Muestra cómo va (o cómo terminó) la última sincronización MLB."""
    db = get_db()
    est = db.mlb_estado.find_one({"_id": "sync"}, {"_id": 0})
    return est or {"fase": "Nunca se ha sincronizado", "corriendo": False}


@app.get("/mlb/lesiones/{equipo}")
def mlb_lesiones_equipo(equipo: str, _=Depends(verificar_token)):
    """Reporte de lesiones de un equipo de MLB, en vivo desde ESPN."""
    from mlb_scraper import obtener_lesiones_equipo
    return {"equipo": equipo, "lesiones": obtener_lesiones_equipo(equipo)}


@app.get("/mlb/roster/{equipo}")
def mlb_roster_equipo(equipo: str, _=Depends(verificar_token)):
    """Roster activo de un equipo de MLB, en vivo desde ESPN."""
    from mlb_scraper import obtener_roster_equipo
    return obtener_roster_equipo(equipo)


@app.post("/admin/registrar_picks")
def admin_registrar_picks(_=Depends(verificar_bridge)):
    """Fuerza el registro de los picks del día de todas las ligas (por si el
    job no ha corrido o quieres asegurar que queden guardados). Manual."""
    db = get_db()
    n = _registrar_picks_todas_ligas(db)
    return {"ok": True, "picks_registrados": n}


@app.get("/admin/estado_track")
def admin_estado_track(_=Depends(verificar_bridge)):
    """Estado del track record: cuántos picks hay, pendientes, resueltos."""
    db = get_db()
    total = db.picks_track.count_documents({})
    pendientes = db.picks_track.count_documents({"resultado": "pendiente"})
    aciertos = db.picks_track.count_documents({"resultado": "acierto"})
    fallos = db.picks_track.count_documents({"resultado": "fallo"})
    nulos = db.picks_track.count_documents({"resultado": "nulo"})
    return {
        "ok": True, "total": total, "pendientes": pendientes,
        "aciertos": aciertos, "fallos": fallos, "nulos": nulos,
        "resueltos": aciertos + fallos + nulos,
    }


@app.get("/admin/auditoria_jugadores")
def admin_auditoria_jugadores(liga: str = "laliga", _=Depends(verificar_bridge)):
    """Audita qué equipos de una liga tienen jugadores cargados y cuáles no.
    Cruza los equipos de la clasificación (los que deberían existir) contra los
    que tienen jugadores en la base. Detecta huecos como 'Real Madrid sin datos'."""
    import unicodedata
    def norm(s):
        s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
        return " ".join(p for p in s.split() if p not in ("fc", "cf", "cd", "club", "deportivo", "ac", "sc"))

    db = get_db()
    # equipos "oficiales" de la liga: los de la clasificación
    equipos_tabla = [f.get("equipo", "") for f in db.clasificacion.find({"liga": liga}, {"_id": 0, "equipo": 1})]
    # si no hay tabla, usar los equipos que aparecen en los partidos
    if not equipos_tabla:
        eqs = set()
        for p in db.partidos.find({"liga": liga}, {"_id": 0, "local": 1, "visitante": 1}):
            if p.get("local"): eqs.add(p["local"])
            if p.get("visitante"): eqs.add(p["visitante"])
        equipos_tabla = sorted(eqs)

    # contar jugadores por equipo (normalizado)
    conteo = {}
    for j in db.jugadores.find({"liga": liga}, {"_id": 0, "equipo": 1}):
        k = norm(j.get("equipo", ""))
        if k:
            conteo[k] = conteo.get(k, 0) + 1

    reporte = []
    sin_datos = []
    for eq in sorted(set(equipos_tabla)):
        n = conteo.get(norm(eq), 0)
        reporte.append({"equipo": eq, "jugadores": n, "ok": n > 0})
        if n == 0:
            sin_datos.append(eq)

    total_jugadores = sum(conteo.values())
    return {
        "ok": True, "liga": liga,
        "equipos_totales": len(reporte),
        "equipos_con_datos": len([r for r in reporte if r["ok"]]),
        "equipos_sin_datos": len(sin_datos),
        "sin_datos": sin_datos,
        "total_jugadores": total_jugadores,
        "detalle": reporte,
    }


@app.get("/admin/auditoria_todas_ligas")
def admin_auditoria_todas_ligas(_=Depends(verificar_bridge)):
    """Corre la misma auditoría de /admin/auditoria_jugadores pero en TODAS
    las ligas activas de un jalón, para detectar de golpe qué equipos de qué
    ligas se quedaron sin goleadores/amonestados por un scrape incompleto
    (en vez de tener que revisar liga por liga a mano). Solo lectura, no
    borra ni cambia nada. Excluye NFL/MLB: usan sus propias colecciones de
    jugadores (nfl_jugadores / mlb_jugadores), no db.jugadores."""
    import unicodedata
    def norm(s):
        s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
        return " ".join(p for p in s.split() if p not in ("fc", "cf", "cd", "club", "deportivo", "ac", "sc"))

    db = get_db()
    EXCLUIR = {"nfl", "mlb"}
    ligas = sorted(
        {l for l in db.clasificacion.distinct("liga") if l and l not in EXCLUIR}
        | {l for l in db.partidos.distinct("liga") if l and l not in EXCLUIR}
    )

    TORNEOS = {"championsleague", "europaleague", "leaguescup", "conferenceleague", "mundialclubes"}
    reporte = []
    for liga in ligas:
        equipos_tabla = [f.get("equipo", "") for f in db.clasificacion.find({"liga": liga}, {"_id": 0, "equipo": 1})]
        if not equipos_tabla:
            eqs = set()
            for p in db.partidos.find({"liga": liga}, {"_id": 0, "local": 1, "visitante": 1}):
                if p.get("local"): eqs.add(p["local"])
                if p.get("visitante"): eqs.add(p["visitante"])
            equipos_tabla = sorted(eqs)
        if not equipos_tabla:
            continue

        filtro_jug = {} if liga in TORNEOS else {"liga": liga}
        conteo = {}
        for j in db.jugadores.find(filtro_jug, {"_id": 0, "equipo": 1}):
            k = norm(j.get("equipo", ""))
            if k:
                conteo[k] = conteo.get(k, 0) + 1

        sin_datos = sorted({eq for eq in equipos_tabla if conteo.get(norm(eq), 0) == 0})
        reporte.append({
            "liga": liga,
            "equipos_totales": len(set(equipos_tabla)),
            "equipos_sin_datos": len(sin_datos),
            "sin_datos": sin_datos,
        })

    reporte.sort(key=lambda r: -r["equipos_sin_datos"])
    return {
        "ok": True,
        "ligas_revisadas": len(reporte),
        "ligas_con_huecos": len([r for r in reporte if r["equipos_sin_datos"] > 0]),
        "total_equipos_faltantes": sum(r["equipos_sin_datos"] for r in reporte),
        "detalle": reporte,
    }


@app.get("/admin/diagnostico_equipo")
def admin_diagnostico_equipo(equipo: str, _=Depends(verificar_bridge)):
    """SOLO LECTURA. Rastrea dónde quedó la data de un equipo (ej. 'Real Madrid'):
    jugadores por liga, partidos por liga/estado y filas de clasificación.
    Sirve para ver si la data no llegó, llegó con otro nombre o cayó en otra liga."""
    import unicodedata
    def norm(s):
        s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
        return " ".join(p for p in s.split() if p not in ("fc", "cf", "cd", "club", "deportivo", "ac", "sc"))
    obj = norm(equipo)
    if not obj:
        return {"ok": False, "error": "equipo vacío"}

    def coincide(nom):
        n = norm(nom)
        return bool(n) and (n == obj or n in obj or obj in n)

    db = get_db()

    # jugadores: por (liga, nombre de equipo tal como quedó guardado)
    jug = []
    for g in db.jugadores.aggregate([
        {"$group": {"_id": {"liga": "$liga", "equipo": "$equipo"},
                    "jugadores": {"$sum": 1}, "partidos": {"$addToSet": "$partidoId"}}}
    ]):
        if coincide(g["_id"].get("equipo", "")):
            parts = g.get("partidos") or []
            jug.append({"liga": g["_id"].get("liga"), "equipo_guardado": g["_id"].get("equipo"),
                        "registros": g["jugadores"],
                        "partidos_distintos": len([x for x in parts if x]),
                        "sin_partidoId": "" in parts or None in parts})

    # listado real de jugadores (nombre, goles, amarillas) para ver quién
    # sí quedó cargado y a ojo notar si falta plantilla (ej. un equipo con
    # solo 3-4 jugadores en vez de los ~20-25 de una plantilla completa)
    #
    # BUG encontrado 2026-10-10 (tercera vuelta): este 'for' leía goles/
    # amarillas/rating como campos de NIVEL SUPERIOR del documento
    # ({"goles": 1, ...} en el projection + j.get("goles")). Esos campos
    # NUNCA existen ahí — JugadorIn (el modelo que arma el doc al guardar)
    # solo los mete dentro de 'sportData'. Por eso ESTE endpoint mostraba
    # 'null' para TODOS los jugadores de TODOS los equipos, siempre, sin
    # importar si el dato de verdad estaba guardado — llevó a sospechar de
    # un índice viejo en Mongo que nunca fue el problema real. Ahora se lee
    # igual que la tarjeta del partido (_top_por_stat): directo primero,
    # sportData como respaldo.
    jugadores_detalle = []
    for j in db.jugadores.find({}, {"_id": 0, "liga": 1, "equipo": 1, "nombre": 1,
                                     "goles": 1, "amarillas": 1, "rating": 1,
                                     "posicion": 1, "sportData": 1}):
        if coincide(j.get("equipo", "")):
            sd = j.get("sportData") or {}
            jugadores_detalle.append({
                "liga": j.get("liga"), "equipo_guardado": j.get("equipo"),
                "nombre": j.get("nombre"), "posicion": j.get("posicion"),
                "goles": j.get("goles") if j.get("goles") is not None else sd.get("goles"),
                "amarillas": j.get("amarillas") if j.get("amarillas") is not None else sd.get("amarillas"),
                "rating": j.get("rating") if j.get("rating") is not None else sd.get("rating"),
            })
    jugadores_detalle.sort(key=lambda x: (x.get("liga") or "", x.get("nombre") or ""))

    # partidos donde juega ese equipo, por liga y estado
    part = {}
    nombres_partido = set()
    for p in db.partidos.find({}, {"_id": 0, "liga": 1, "estado": 1, "local": 1, "visitante": 1}):
        for lado in ("local", "visitante"):
            if coincide(p.get(lado, "")):
                nombres_partido.add(p.get(lado, ""))
                k = f"{p.get('liga')}|{p.get('estado')}"
                part[k] = part.get(k, 0) + 1
    partidos = [{"liga": k.split("|")[0], "estado": k.split("|")[1], "partidos": v}
                for k, v in sorted(part.items())]

    # clasificación: aquí se ven duplicados / temporadas mezcladas
    clasif = [f for f in db.clasificacion.find({}, {"_id": 0, "liga": 1, "equipo": 1, "temporada": 1, "pos": 1, "jugados": 1})
              if coincide(f.get("equipo", ""))]

    ingesta = [i for i in db.ingesta_jugadores.find({}, {"_id": 0})
               if coincide(i.get("equipo", ""))]

    return {
        "ok": True, "buscado": equipo,
        "version_motor": MOTOR_VERSION,
        "ultima_carga_recibida": ingesta,
        "jugadores_por_liga": jug,
        "total_jugadores": sum(x["registros"] for x in jug),
        "jugadores_detalle": jugadores_detalle,
        "partidos": partidos,
        "nombres_en_partidos": sorted(nombres_partido),
        "clasificacion": clasif,
    }


@app.post("/admin/nfl_backfill_tipo_temporada")
def admin_nfl_backfill_tipo_temporada(_=Depends(verificar_bridge)):
    """Corrige datos YA guardados: etiqueta cada registro de nfl_jugadores que
    no tenga 'tipo_temporada' con el de su partido (por fuenteId). Necesario
    porque antes de este cambio esos registros se guardaban sin esa etiqueta,
    y por eso 'Números del partido' mezclaba pretemporada y temporada regular.
    Solo lectura/escritura sobre lo ya existente, no vuelve a bajar nada de ESPN."""
    db = get_db()
    mapa = {}
    for p in db.partidos.find({"liga": "nfl", "tipo_temporada": {"$exists": True}},
                               {"_id": 0, "fuenteId": 1, "tipo_temporada": 1}):
        if p.get("fuenteId"):
            mapa[p["fuenteId"]] = p["tipo_temporada"]

    from pymongo import UpdateOne
    ops = []
    sin_match = 0
    for d in db.nfl_jugadores.find(
            {"liga": "nfl", "tipo_temporada": {"$exists": False}},
            {"_id": 1, "fuenteId": 1}):
        tt = mapa.get(d.get("fuenteId"))
        if tt is None:
            sin_match += 1
            continue
        ops.append(UpdateOne({"_id": d["_id"]}, {"$set": {"tipo_temporada": tt}}))

    actualizados = 0
    for i in range(0, len(ops), 500):
        res = db.nfl_jugadores.bulk_write(ops[i:i + 500], ordered=False)
        actualizados += res.modified_count

    return {"ok": True, "actualizados": actualizados, "sin_match": sin_match,
            "nota": "sin_match = registros de jugador cuyo partido (fuenteId) ya no "
                    "está en db.partidos o nunca tuvo tipo_temporada; esos quedan sin "
                    "tocar y 'Números del partido' los ignora hasta que tengan la etiqueta."}


@app.get("/nfl/auditoria")
def nfl_auditoria():
    """Salud de los datos NFL: detecta equipos falsos, marcadores imposibles,
    duplicados y mezcla de temporadas. Ojos automáticos sobre la data."""
    from nfl_validacion import auditar_datos_nfl
    db = get_db()
    return auditar_datos_nfl(db)


@app.get("/nfl/resumen")
def nfl_resumen(_=Depends(verificar_token)):
    """Cuánta data NFL hay guardada (para verificar de un vistazo)."""
    db = get_db()
    return {
        "partidos_ligero": db.partidos.count_documents({"liga": "nfl"}),
        "nfl_partidos_procesados": db.nfl_partidos.count_documents({}),
        "nfl_raw": db.nfl_raw.count_documents({}),
        "lineas_jugador": db.nfl_jugadores.count_documents({}),
        "jugadores_unicos": len(db.nfl_jugadores.distinct("jugadorId")),
        "plays": db.nfl_plays.count_documents({}),
    }


@app.get("/nfl/equipo/{nombre}")
def nfl_equipo(nombre: str, _=Depends(verificar_token)):
    """Data acumulada de un equipo: sus partidos y sus jugadores con stats.
    'nombre' puede ser parcial (ej. 'Cardinals' o 'Arizona')."""
    db = get_db()
    import re as _re
    rx = {"$regex": _re.escape(nombre), "$options": "i"}
    partidos = list(db.nfl_partidos.find(
        {"$or": [{"resumen.local": rx}, {"resumen.visitante": rx}]},
        {"_id": 0, "raw": 0}))
    jugadores = list(db.nfl_jugadores.find({"equipo": rx}, {"_id": 0}))
    return {
        "equipo_buscado": nombre,
        "partidos_encontrados": len(partidos),
        "partidos": partidos,
        "jugadores_encontrados": len(jugadores),
        "jugadores": jugadores,
    }


@app.get("/nfl/partido/{fuente_id}")
def nfl_ver_partido(fuente_id: str, con_plays: bool = False, _=Depends(verificar_token)):
    """Devuelve el partido NFL procesado (resumen + stats + drives + jugadores)."""
    db = get_db()
    part = db.nfl_partidos.find_one({"fuenteId": fuente_id}, {"_id": 0})
    if not part:
        raise HTTPException(status_code=404, detail="Partido NFL no encontrado")
    part["jugadores"] = list(db.nfl_jugadores.find({"fuenteId": fuente_id}, {"_id": 0}))
    if con_plays:
        part["plays"] = list(db.nfl_plays.find({"fuenteId": fuente_id}, {"_id": 0}))
    return part


@app.post("/partido")
def guardar_partido(partido: PartidoIn, _=Depends(verificar_token)):
    """
    Guarda un partido (programado o finalizado).
    - Si trae fuenteId (matchId de FotMob), esa es la clave única: así un
      partido pasa de 'programado' a 'finalizado' SIN duplicarse.
    - Si no, cae al upsert por (liga, local, visitante, inicio).
    """
    db = get_db()
    doc = partido.model_dump()

    if doc.get("fuenteId"):
        filtro = {"liga": doc["liga"], "fuenteId": doc["fuenteId"]}
    else:
        filtro = {
            "liga": doc["liga"],
            "local": doc["local"],
            "visitante": doc["visitante"],
            "inicio": doc["inicio"],
        }

    db.partidos.update_one(filtro, {"$set": doc}, upsert=True)
    return {
        "ok": True,
        "partido": f"{doc['local']} vs {doc['visitante']}",
        "estado": doc.get("estado", "finalizado"),
    }


def _texto_flexible(v) -> str:
    """Convierte lo que mande el scraper (texto, número, o un objeto de FotMob
    tipo {fallback:..} / {fullName:..}) en texto simple. Nunca lanza error."""
    if v is None:
        return ""
    if isinstance(v, dict):
        for k in ("fullName", "name", "fallback", "label", "key", "text"):
            if v.get(k) not in (None, ""):
                return _texto_flexible(v[k])
        if v.get("firstName") or v.get("lastName"):
            return f"{v.get('firstName', '')} {v.get('lastName', '')}".strip()
        return ""
    if isinstance(v, (list, tuple, set)):
        return ""
    return str(v).strip()


def _limpiar_jugador(raw: dict) -> dict:
    """Normaliza UN jugador crudo del scraper al esquema JugadorIn.
    Lanza ValueError con el motivo si de plano no sirve (sin nombre/equipo)."""
    if not isinstance(raw, dict):
        raise ValueError("no es un objeto")
    nombre = _texto_flexible(raw.get("nombre"))
    equipo = _texto_flexible(raw.get("equipo"))
    if not nombre:
        raise ValueError("sin nombre")
    if not equipo:
        raise ValueError("sin equipo")
    sd = raw.get("sportData")
    return JugadorIn(
        jugadorId=_texto_flexible(raw.get("jugadorId")) or nombre,
        nombre=nombre,
        equipo=equipo,
        liga=_texto_flexible(raw.get("liga")) or "ligamx",
        partidoId=_texto_flexible(raw.get("partidoId")),
        posicion=_texto_flexible(raw.get("posicion")),
        lesionado=bool(raw.get("lesionado", False)),
        lesion_info=_texto_flexible(raw.get("lesion_info")),
        sportData=sd if isinstance(sd, dict) else {},
    ).model_dump()


@app.post("/jugadores")
def guardar_jugadores(jugadores: list[dict], _=Depends(verificar_token)):
    """
    Guarda stats de varios jugadores (lista). upsert por (jugadorId, partidoId, liga, equipo).
    Usa bulk_write (escritura en lote) para no tardar tanto que Railway corte con
    502 en cargas grandes (ligas enteras).

    TOLERANTE: cada jugador se limpia y valida POR SEPARADO. Antes, un solo jugador
    con un campo raro (ej. posición como objeto) hacía que se rechazara la lista
    COMPLETA del equipo (422) y el equipo entero se quedaba sin datos. Ahora solo
    se descarta ese jugador y la respuesta dice cuáles y por qué ('rechazados').
    """
    from pymongo import UpdateOne
    db = get_db()
    if not jugadores:
        return {"ok": True, "jugadores_guardados": 0, "rechazados_total": 0, "rechazados": []}

    docs, rechazados = [], []
    for raw in jugadores:
        try:
            docs.append(_limpiar_jugador(raw))
        except Exception as e:
            nom = _texto_flexible(raw.get("nombre")) if isinstance(raw, dict) else ""
            rechazados.append({"nombre": nom or "?", "motivo": str(e)[:120]})

    ops = []
    for doc in docs:
        # La 'liga' va en la llave: un mismo jugador (ej. de Real Madrid) juega
        # LaLiga y Champions; sin 'liga' aquí, cada carga sobrescribía el
        # registro de la otra competencia y el jugador "saltaba" de liga.
        #
        # El 'equipo' TAMBIÉN va en la llave (bug encontrado 2026-10-10): el
        # Scout no siempre manda un fotmob_id numérico al scrapear plantillas
        # completas (paso "EQUIPOS"), y ahí jugadorId cae al nombre del
        # jugador (ver _limpiar_jugador). Además ese paso no manda partidoId
        # (queda ""). Sin 'equipo' en la llave, dos jugadores de EQUIPOS
        # DISTINTOS de la misma liga con el mismo nombre (o sin fotmob_id)
        # compartían la llave {jugadorId, "", liga} y cada carga pisaba
        # ($set sobre TODO el doc, incluyendo 'equipo') el registro del
        # jugador anterior — así un equipo scrapeado completo (ej. 28
        # jugadores) se quedaba con solo un puñado tras scrapear el resto
        # de la liga (confirmado con Barcelona: 28 enviados, 4 sobrevivieron
        # tras cargar el resto de LaLiga tras él). Con 'equipo' en la llave
        # esto ya no puede pasar entre equipos distintos.
        ops.append(UpdateOne(
            {"jugadorId": doc["jugadorId"], "partidoId": doc["partidoId"],
             "liga": doc["liga"], "equipo": doc["equipo"]},
            {"$set": doc}, upsert=True,
        ))
    # Bitácora de lo recibido por (liga, equipo): sirve para saber si el Scout
    # realmente manda a los jugadores de un equipo. Nunca debe romper el guardado.
    try:
        from datetime import datetime, timezone
        _rec = {}
        for d in docs:
            _k = (d["liga"], d["equipo"])
            _r = _rec.setdefault(_k, {"n": 0, "sin_partidoId": 0, "ids": set(), "nombres": set()})
            _r["n"] += 1
            if not d["partidoId"]:
                _r["sin_partidoId"] += 1
            _r["ids"].add(d["jugadorId"])
            _r["nombres"].add(d["nombre"])
        _ahora = datetime.now(timezone.utc).isoformat()
        for (_lg, _eq), _r in _rec.items():
            # 'jugadorIds_distintos' < 'n' significa que el Scout mandó el
            # MISMO jugadorId para varios jugadores de ese lote (ej. porque
            # no trajo el fotmob_id y cayó al nombre, o dos nombres distintos
            # cayeron en el mismo valor) — eso los pisa entre ELLOS MISMOS,
            # ni siquiera hace falta otro equipo para que se pierdan.
            db.ingesta_jugadores.update_one(
                {"liga": _lg, "equipo": _eq},
                {"$set": {"ultima": _ahora, "ultimo_lote": _r["n"],
                          "sin_partidoId": _r["sin_partidoId"],
                          "jugadorIds_distintos": len(_r["ids"]),
                          "nombres_distintos": len(_r["nombres"]),
                          "rechazados_ultimo_lote": len(rechazados)}}, upsert=True)
    except Exception:
        pass
    # ejecutar en lotes de 500 para no saturar memoria ni la conexión
    #
    # IMPORTANTE (encontrado 2026-10-10): antes esto no tenía try/except.
    # Si Mongo tiene un ÍNDICE ÚNICO viejo (creado a mano, de antes de que
    # 'equipo' se agregara a la llave) sobre {jugadorId, partidoId, liga}
    # SIN 'equipo', un upsert con la llave nueva (que SÍ incluye equipo) no
    # encuentra el doc viejo de otro equipo con esos mismos jugadorId/liga/
    # partidoId="" para hacer $set, así que Mongo intenta INSERTAR uno
    # nuevo — y el índice viejo lo rechaza por duplicado (E11000), aunque
    # para nosotros es un jugador distinto. bulk_write(ordered=False) sigue
    # con el resto del lote, pero esos jugadores en particular NUNCA se
    # guardan — y antes esto fallaba en silencio (el error ni se veía).
    # Esto explicaría por qué LaLiga-Barcelona seguía perdiendo jugadores
    # después del fix de la llave: no es que el fix no sirva, es que el
    # índice viejo en Mongo lo sigue bloqueando por debajo.
    # Ahora: se captura el error, se cuenta cuántos fallaron por duplicado,
    # y se manda en la respuesta (sin tronar el guardado) para que se vea
    # YA, sin tener que pedir otro scrape aparte solo para probarlo.
    from pymongo.errors import BulkWriteError
    errores_duplicado = 0
    detalle_errores = []
    for i in range(0, len(ops), 500):
        lote = ops[i:i + 500]
        try:
            db.jugadores.bulk_write(lote, ordered=False)
        except BulkWriteError as bwe:
            werrs = bwe.details.get("writeErrors", [])
            errores_duplicado += len(werrs)
            for we in werrs[:5]:
                detalle_errores.append({
                    "codigo": we.get("code"),
                    "mensaje": str(we.get("errmsg", ""))[:200],
                })
    resultado = {"ok": True, "jugadores_guardados": len(ops) - errores_duplicado,
            "rechazados_total": len(rechazados), "rechazados": rechazados[:10]}
    if errores_duplicado:
        resultado["errores_bulk_write"] = errores_duplicado
        resultado["detalle_errores_bulk_write"] = detalle_errores
        resultado["aviso"] = ("%d jugadores NO se guardaron por un choque de índice "
            "en Mongo (probablemente un índice único viejo que no incluye 'equipo'). "
            "Llama a POST /admin/arreglar_indices_jugadores para corregirlo "
            "sin necesidad de volver a scrapear." % errores_duplicado)
    return resultado


@app.post("/admin/arreglar_indices_jugadores")
def admin_arreglar_indices_jugadores(_=Depends(verificar_bridge)):
    """
    NO necesita scrape para usarse — corre sola, de inmediato.

    Busca en db.jugadores un índice único VIEJO (de antes de que 'equipo'
    se agregara a la llave) que cubra jugadorId/partidoId/liga pero NO
    'equipo'. Si existe, lo borra y crea el índice único correcto con
    'equipo' incluido. Esto es lo que probablemente está bloqueando por
    debajo que jugadores de distintos equipos (incluso ya con el fix del
    2026-10-10 en el código) se guarden bien, aunque el código ya mande
    la llave correcta — porque Mongo exige unicidad sobre el índice VIEJO
    sin importar qué llave use el filtro del upsert.

    Seguro de correr aunque no haya ningún índice viejo (no hace nada y
    responde 'sin_cambios': true).
    """
    db = get_db()
    antes = db.jugadores.index_information()
    campos_llave_correcta = {"jugadorId", "partidoId", "liga", "equipo"}
    borrados = []
    for nombre_idx, info in antes.items():
        if nombre_idx == "_id_":
            continue
        campos_idx = {campo for campo, _ in info.get("key", [])}
        es_unico = info.get("unique", False)
        # Antes solo buscaba índices sobre jugadorId/partidoId/liga sin
        # 'equipo'. Pero el real (encontrado 2026-10-10, segunda vuelta) fue
        # 'jugador_nombre' — un índice ÚNICO sobre 'nombre' SOLO. Con eso,
        # Mongo exige que 'nombre' sea único en TODA la colección: dos
        # jugadores de equipos/ligas distintos con el mismo nombre (o el
        # segundo que llega con ese nombre, aunque sea jugador distinto)
        # se pisan — así se explica por qué solo sobrevivían un puñado de
        # cada equipo sin importar el fix de la llave jugadorId/partidoId/
        # liga/equipo: ese índice ni se fija en esos campos.
        # Ahora: se borra CUALQUIER índice único que no sea el correcto
        # (jugadorId+partidoId+liga+equipo), sea sobre los campos que sea.
        if es_unico and campos_idx != campos_llave_correcta:
            db.jugadores.drop_index(nombre_idx)
            borrados.append({"nombre": nombre_idx, "campos": sorted(campos_idx)})
    # Asegurar que exista el índice único correcto (no truena si ya existe).
    creado_correcto = False
    tiene_correcto = any(
        {campo for campo, _ in info.get("key", [])} == campos_llave_correcta and info.get("unique", False)
        for info in db.jugadores.index_information().values()
    )
    if not tiene_correcto:
        db.jugadores.create_index(
            [("jugadorId", 1), ("partidoId", 1), ("liga", 1), ("equipo", 1)],
            unique=True, name="jugadorId_partidoId_liga_equipo_unico",
        )
        creado_correcto = True
    despues = db.jugadores.index_information()
    return {
        "ok": True,
        "sin_cambios": not borrados and not creado_correcto,
        "indices_viejos_borrados": borrados,
        "indice_correcto_creado": creado_correcto,
        "indices_antes": {k: v.get("key") for k, v in antes.items()},
        "indices_despues": {k: v.get("key") for k, v in despues.items()},
    }


@app.post("/admin/dedup_clasificacion")
def admin_dedup_clasificacion(payload: dict, _=Depends(verificar_bridge)):
    """Detecta y fusiona equipos duplicados en la tabla (mismo equipo con
    acentos o nombres distintos: 'Leon' vs 'León', 'FC Juarez' vs 'FC Juárez').
    Se queda con el registro más completo (más partidos jugados) y borra el otro."""
    import unicodedata
    liga = payload.get("liga", "ligamx")
    solo_ver = payload.get("solo_ver", False)  # si True, solo reporta, no borra

    def norm(s):
        s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
        # quitar ruido común
        for r in [" fc", "fc ", " cf", "cf ", "club ", " club", "deportivo ", "cd ", " cd"]:
            s = s.replace(r, " ")
        return " ".join(s.split())

    db = get_db()
    filas = list(db.clasificacion.find({"liga": liga}))
    grupos = {}
    for f in filas:
        k = norm(f.get("equipo", ""))
        grupos.setdefault(k, []).append(f)

    duplicados = {k: v for k, v in grupos.items() if len(v) > 1}
    reporte = []
    borrados = 0

    for k, docs in duplicados.items():
        # elegir el "bueno": el que tenga más partidos jugados (dato más completo)
        def jugados(d):
            return d.get("jugados", 0) or (d.get("sportData", {}) or {}).get("jugados", 0) or 0
        docs_ord = sorted(docs, key=jugados, reverse=True)
        bueno = docs_ord[0]
        malos = docs_ord[1:]
        nombres = [d.get("equipo", "") for d in docs]
        reporte.append({
            "equipo": bueno.get("equipo", ""),
            "duplicados": nombres,
            "se_queda": bueno.get("equipo", ""),
            "se_borran": [d.get("equipo", "") for d in malos],
        })
        if not solo_ver:
            for m in malos:
                db.clasificacion.delete_one({"_id": m["_id"]})
                borrados += 1

    return {
        "ok": True, "liga": liga,
        "grupos_duplicados": len(duplicados),
        "borrados": borrados,
        "solo_ver": solo_ver,
        "detalle": reporte,
    }


@app.post("/clasificacion")
def guardar_clasificacion(clasif: ClasificacionIn, _=Depends(verificar_token)):
    """Reemplaza la tabla de posiciones de la liga (upsert por equipo)."""
    db = get_db()
    for fila in clasif.filas:
        doc = fila.model_dump()
        doc["liga"] = clasif.liga
        doc["temporada"] = clasif.temporada
        db.clasificacion.update_one(
            {"liga": doc["liga"], "equipo": doc["equipo"],
             "temporada": doc["temporada"]},
            {"$set": doc},
            upsert=True,
        )
    return {"ok": True, "filas": len(clasif.filas)}


@app.get("/clasificacion")
def ver_clasificacion(liga: str = "ligamx", ):
    """Devuelve la tabla ordenada por posición.
    Para NFL, la calcula desde los partidos finalizados (récord W-L-T)."""
    db = get_db()
    if liga == "nfl":
        from nfl_validacion import es_equipo_nfl_real
        rec = {}
        # ¿hay partidos marcados como temporada regular? si sí, usar solo esos
        hay_regular = db.partidos.count_documents(
            {"liga": "nfl", "estado": "finalizado", "tipo_temporada": 2}) > 0
        filtro_t = {"liga": "nfl", "estado": "finalizado"}
        if hay_regular:
            filtro_t["tipo_temporada"] = 2  # solo temporada regular
        for p in db.partidos.find(filtro_t, {"_id": 0}):
            gl, gv = p.get("marcadorLocal"), p.get("marcadorVisitante")
            l, v = p.get("local"), p.get("visitante")
            if gl is None or gv is None or not l or not v:
                continue
            # solo equipos reales de la NFL (filtra Pro Bowl, conferencias, basura)
            if not es_equipo_nfl_real(l) or not es_equipo_nfl_real(v):
                continue
            # marcadores imposibles fuera
            if not (0 <= gl <= 80 and 0 <= gv <= 80):
                continue
            for eq in (l, v):
                rec.setdefault(eq, {"equipo": eq, "g": 0, "p": 0, "e": 0, "pf": 0, "pc": 0})
            if gl > gv:
                rec[l]["g"] += 1; rec[v]["p"] += 1
            elif gv > gl:
                rec[v]["g"] += 1; rec[l]["p"] += 1
            else:
                rec[l]["e"] += 1; rec[v]["e"] += 1
            rec[l]["pf"] += gl; rec[l]["pc"] += gv
            rec[v]["pf"] += gv; rec[v]["pc"] += gl
        filas = list(rec.values())
        # ordenar por victorias, luego diferencia de puntos
        filas.sort(key=lambda x: (x["g"], x["pf"] - x["pc"]), reverse=True)
        tabla = []
        for i, f in enumerate(filas):
            jugados = f["g"] + f["p"] + f["e"]
            tabla.append({
                "pos": i + 1, "equipo": f["equipo"], "jugados": jugados,
                "sportData": {"puntos": f["g"] * 1,  # en NFL "puntos" = victorias
                              "dif": f["pf"] - f["pc"],
                              "record": f"{f['g']}-{f['p']}" + (f"-{f['e']}" if f["e"] else "")},
            })
        return {"liga": liga, "tabla": tabla}
    if liga == "mlb":
        from mlb_scraper import MLB_ABREV
        rec = {}
        hay_regular = db.partidos.count_documents(
            {"liga": "mlb", "estado": "finalizado", "tipo_temporada": 2}) > 0
        filtro_t = {"liga": "mlb", "estado": "finalizado"}
        if hay_regular:
            filtro_t["tipo_temporada"] = 2
        for p in db.partidos.find(filtro_t, {"_id": 0}):
            gl, gv = p.get("marcadorLocal"), p.get("marcadorVisitante")
            l, v = p.get("local"), p.get("visitante")
            if gl is None or gv is None or not l or not v:
                continue
            # solo los 30 equipos reales de MLB (filtra All-Star/exhibición)
            if l not in MLB_ABREV or v not in MLB_ABREV:
                continue
            for eq in (l, v):
                rec.setdefault(eq, {"equipo": eq, "g": 0, "p": 0, "cf": 0, "cc": 0})
            if gl > gv:
                rec[l]["g"] += 1; rec[v]["p"] += 1
            elif gv > gl:
                rec[v]["g"] += 1; rec[l]["p"] += 1
            rec[l]["cf"] += gl; rec[l]["cc"] += gv
            rec[v]["cf"] += gv; rec[v]["cc"] += gl
        filas = list(rec.values())
        filas.sort(key=lambda x: (x["g"], x["cf"] - x["cc"]), reverse=True)
        tabla = []
        for i, f in enumerate(filas):
            jugados = f["g"] + f["p"]
            tabla.append({
                "pos": i + 1, "equipo": f["equipo"], "jugados": jugados,
                "sportData": {"puntos": f["g"],
                              "dif": f["cf"] - f["cc"],
                              "record": f"{f['g']}-{f['p']}"},
            })
        return {"liga": liga, "tabla": tabla}
    filas = list(
        db.clasificacion.find({"liga": liga}, {"_id": 0}).sort("pos", 1)
    )
    return {"liga": liga, "tabla": filas}


@app.get("/benchmark/{nombre}")
def benchmark_jugador(nombre: str, _=Depends(verificar_token)):
    """
    Promedia las stats de un jugador sobre todos sus partidos guardados.
    Base para comparar un jugador contra su propio promedio.
    """
    db = get_db()
    registros = list(db.jugadores.find({"nombre": nombre}, {"_id": 0}))
    if not registros:
        return {"jugador": nombre, "partidos": 0, "promedios": {}}

    # Junta todas las claves numéricas de sportData y saca promedios.
    sumas: dict[str, float] = {}
    conteos: dict[str, int] = {}
    for r in registros:
        for clave, valor in (r.get("sportData") or {}).items():
            if isinstance(valor, (int, float)):
                sumas[clave] = sumas.get(clave, 0) + valor
                conteos[clave] = conteos.get(clave, 0) + 1

    promedios = {k: round(sumas[k] / conteos[k], 2) for k in sumas}
    return {
        "jugador": nombre,
        "equipo": registros[-1].get("equipo", ""),
        "partidos": len(registros),
        "promedios": promedios,
    }


@app.get("/partidos")
def listar_partidos(
    liga: str = "ligamx", limite: int = 200, estado: str = "", jornada: str = "",
):
    """
    Lista partidos de una liga.
    - estado: "" todos | "programado" | "finalizado"
    - jornada: "" todas | "Jornada 3" filtra esa jornada
    """
    db = get_db()
    filtro = {"liga": liga}
    if estado:
        filtro["estado"] = estado
    if jornada:
        filtro["fase"] = jornada

    # Traer todo lo que matchea (sin cortar) y separar; así los programados
    # futuros nunca quedan fuera por culpa de finalizados viejos que llenan
    # el límite. Luego limitamos solo los finalizados.
    todos = list(db.partidos.find(filtro, {"_id": 0}).sort("inicio", 1))

    from datetime import datetime, timezone, timedelta
    _ahora = datetime.now(timezone.utc)
    def _ya_paso(p):
        dt = _parse_inicio(p)
        if dt is None:
            return False
        return _ahora > dt + timedelta(hours=4)

    programados = [p for p in todos
                   if p.get("estado") in ("programado", "en_vivo") and not _ya_paso(p)]
    finalizados = [p for p in todos
                   if p.get("estado") not in ("programado", "en_vivo") or _ya_paso(p)]
    # finalizados: más recientes primero, y solo los últimos 'limite'
    finalizados.sort(key=lambda p: p.get("inicio", ""), reverse=True)
    finalizados = finalizados[:limite]
    partidos = programados + finalizados

    return {
        "liga": liga,
        "total": len(partidos),
        "programados": programados,
        "finalizados": finalizados,
        "partidos": partidos,
    }


@app.get("/jornadas")
def listar_jornadas(liga: str = "ligamx", _=Depends(verificar_token)):
    """
    Devuelve las jornadas que existen en la base, con su estado
    (jugada/actual/próxima) para el selector de la web.
    """
    if liga == "mlb":
        # MLB no tiene "jornadas": se sincroniza por rango de fechas, no por
        # semana. El campo 'fase' de cada partido MLB guarda su fecha
        # (YYYYMMDD) en vez de una jornada real — si se tratara como jornada
        # normal, cada día sincronizado aparecía como un chip suelto en el
        # selector (20260903, 20260904, ...) y el front elegía uno "actual"
        # al azar, filtrando todo lo demás a cero. Sin jornadas, pickazoapp
        # muestra todos los próximos de MLB agrupados por día, sin selector
        # (igual que la pestaña "Todas las ligas").
        return {"liga": liga, "jornadas": []}
    db = get_db()
    fases = db.partidos.distinct("fase", {"liga": liga})
    # ordenar por número de jornada
    def _num(f):
        import re
        m = re.search(r"\d+", f or "")
        return int(m.group()) if m else 999
    fases = sorted([f for f in fases if f], key=_num)

    resultado = []
    for f in fases:
        total = db.partidos.count_documents({"liga": liga, "fase": f})
        jugados = db.partidos.count_documents(
            {"liga": liga, "fase": f, "estado": "finalizado"}
        )
        if jugados == 0:
            estado = "proxima"
        elif jugados < total:
            estado = "actual"
        else:
            estado = "jugada"
        resultado.append(
            {"jornada": f, "numero": _num(f), "total": total,
             "jugados": jugados, "estado": estado}
        )
    return {"liga": liga, "jornadas": resultado}


@app.get("/partidos_deporte")
def partidos_deporte(deporte: str = "futbol", limite: int = 300, ):
    """
    Todos los partidos de TODAS las ligas de un deporte, mezclados.
    Para el modo 'ver todo el fútbol'. Cada partido trae su liga.
    """
    db = get_db()
    NOMBRES = {
        "ligamx": "Liga MX", "premier": "Premier League", "laliga": "LaLiga",
        "seriea": "Serie A", "bundesliga": "Bundesliga", "ligue1": "Ligue 1",
        "nfl": "NFL", "mlb": "MLB",
        "championsleague": "Champions League", "europaleague": "Europa League",
        "leaguescup": "Leagues Cup",
        "mls": "MLS", "canada": "Canadian Premier League",
        # UEFA Nations League: FotMob la divide en 4 grupos, cada uno con su
        # propio ID — el Scout puede haber usado cualquiera de estas dos
        # convenciones de slug según cómo se haya nombrado al cargarla.
        "nationsleaguea": "UEFA Nations League A",
        "nationsleagueb": "UEFA Nations League B",
        "nationsleaguec": "UEFA Nations League C",
        "nationsleagued": "UEFA Nations League D",
        "uefanationsleaguea": "UEFA Nations League A",
        "uefanationsleagueb": "UEFA Nations League B",
        "uefanationsleaguec": "UEFA Nations League C",
        "uefanationsleagued": "UEFA Nations League D",
    }

    # ligas que son de este deporte
    filtro_base = {"deporte": deporte} if deporte != "futbol" else {
        "$or": [{"deporte": "futbol"}, {"deporte": {"$exists": False}}]
    }
    partidos = list(
        db.partidos.find(filtro_base, {"_id": 0}).sort("inicio", 1)
    )

    # etiquetar cada partido con el nombre de su liga
    for p in partidos:
        p["liga_nombre"] = NOMBRES.get(p.get("liga", ""), p.get("liga", "").title())

    # Un partido es "próximo" solo si su estado lo dice Y su hora no pasó hace
    # mucho. Los que ya se jugaron pero siguen marcados 'programado' (el scraper
    # no los actualizó) van a resultados, no a próximos.
    from datetime import datetime, timezone, timedelta
    _ahora = datetime.now(timezone.utc)
    def _ya_paso(p):
        dt = _parse_inicio(p)
        if dt is None:
            return False
        return _ahora > dt + timedelta(hours=4)

    programados = [p for p in partidos
                   if p.get("estado") in ("programado", "en_vivo") and not _ya_paso(p)]
    finalizados = [p for p in partidos
                   if p.get("estado") not in ("programado", "en_vivo") or _ya_paso(p)]
    finalizados.sort(key=lambda p: p.get("inicio", ""), reverse=True)
    finalizados = finalizados[:limite]  # limitar solo finalizados, no los programados

    # lista de ligas presentes (para el selector de filtrado)
    ligas_presentes = sorted(set(p.get("liga", "") for p in partidos if p.get("liga")))
    ligas_info = [{"slug": s, "nombre": NOMBRES.get(s, s.title())} for s in ligas_presentes]

    return {
        "deporte": deporte,
        "programados": programados,
        "finalizados": finalizados,
        "ligas": ligas_info,
    }


@app.get("/jugadores")
def buscar_jugadores(q: str = "", liga: str = "ligamx", limite: int = 30, ):
    """
    Busca jugadores por nombre (para el buscador de la web).
    Devuelve nombre, equipo, posición y stats de temporada.
    """
    db = get_db()
    if liga == "nfl":
        # Jugadores NFL: agregamos por jugador sus yardas de pase/carrera/recepción
        termino = str(q or "").strip()[:80]
        filtro_nfl = {"liga": "nfl"}
        if termino:
            filtro_nfl["jugador"] = {"$regex": re.escape(termino), "$options": "i"}
        agg = {}
        for d in db.nfl_jugadores.find(filtro_nfl, {"_id": 0}).limit(2000):
            nom = d.get("jugador", "")
            if not nom:
                continue
            cat = (d.get("categoria", "") or "").lower()
            st = d.get("stats", {}) or {}
            yds = None
            for k, v in st.items():
                if k.lower() == "yds":
                    yds = v; break
            a = agg.setdefault(nom, {"nombre": nom, "equipo": d.get("equipo", ""),
                                     "posicion": d.get("posicion", ""),
                                     "sportData": {}})
            if yds is not None:
                try:
                    key = {"passing": "yardas_pase", "rushing": "yardas_carrera",
                           "receiving": "yardas_recepcion"}.get(cat)
                    if key:
                        a["sportData"][key] = a["sportData"].get(key, 0) + float(yds)
                except Exception:
                    pass
        jugadores = list(agg.values())[:min(max(limite, 1), 100)]
        return {"total": len(jugadores), "jugadores": jugadores}
    if liga == "mlb":
        # Jugadores MLB: agregamos HR/RBI (bateo) y K (pitcheo) por jugador
        termino = str(q or "").strip()[:80]
        filtro_mlb = {"liga": "mlb"}
        if termino:
            filtro_mlb["jugador"] = {"$regex": re.escape(termino), "$options": "i"}
        agg = {}
        for d in db.mlb_jugadores.find(filtro_mlb, {"_id": 0}).limit(2000):
            nom = d.get("jugador", "")
            if not nom:
                continue
            cat = (d.get("categoria", "") or "").lower()
            st = d.get("stats", {}) or {}
            a = agg.setdefault(nom, {"nombre": nom, "equipo": d.get("equipo", ""),
                                     "posicion": d.get("posicion", ""),
                                     "sportData": {}})
            for label, key in (("HR", "jonrones"), ("RBI", "impulsadas"), ("K", "ponches")):
                val = None
                for k, v in st.items():
                    if k.lower() == label.lower():
                        val = v; break
                if val is not None:
                    try:
                        a["sportData"][key] = a["sportData"].get(key, 0) + float(val)
                    except Exception:
                        pass
        jugadores = list(agg.values())[:min(max(limite, 1), 100)]
        return {"total": len(jugadores), "jugadores": jugadores}
    filtro = {"liga": liga}
    termino = str(q or "").strip()[:80]
    if termino:
        filtro["nombre"] = {"$regex": re.escape(termino), "$options": "i"}
    jugadores = list(
        db.jugadores.find(filtro, {"_id": 0}).limit(min(max(limite, 1), 100))
    )
    return {"total": len(jugadores), "jugadores": jugadores}


@app.get("/rankings")
def rankings_jugadores(liga: str = "ligamx", top: int = 10, ):
    """
    Rankings de jugadores por categoría (goleadores, asistencias, etc.).
    Para la vista de Jugadores: top expandible de cada stat.
    Para NFL: líderes por yardas de pase, carrera y recepción.
    """
    db = get_db()

    if liga == "nfl":
        # agregamos yardas por jugador y categoría desde nfl_jugadores
        cats = {"passing": "Líderes de pase (yardas)",
                "rushing": "Líderes de carrera (yardas)",
                "receiving": "Líderes de recepción (yardas)"}
        acumulados = {c: {} for c in cats}
        for d in db.nfl_jugadores.find({"liga": "nfl"}, {"_id": 0}).limit(4000):
            cat = (d.get("categoria", "") or "").lower()
            if cat not in cats:
                continue
            st = d.get("stats", {}) or {}
            yds = None
            for k, v in st.items():
                if k.lower() == "yds":
                    yds = v; break
            if yds is None:
                continue
            nom = d.get("jugador", "")
            a = acumulados[cat].setdefault(nom, {"nombre": nom, "equipo": d.get("equipo", ""),
                                                 "posicion": d.get("posicion", ""), "valor": 0, "j": 0})
            try:
                a["valor"] += float(yds); a["j"] += 1
            except Exception:
                pass
        rankings = []
        for cat, etiqueta in cats.items():
            lista = list(acumulados[cat].values())
            for x in lista:
                x["valor"] = round(x["valor"] / x["j"], 1) if x["j"] else 0
                x.pop("j", None)
            lista.sort(key=lambda x: x["valor"], reverse=True)
            if lista:
                rankings.append({"clave": cat, "etiqueta": etiqueta,
                                 "unidad": " yd", "lideres": lista[:top]})
        return {"liga": liga, "rankings": rankings}

    if liga == "mlb":
        # bateo: HR y RBI se ACUMULAN (entre más mejor). pitcheo: K se
        # acumula, ERA se PROMEDIA (entre menos mejor, orden ascendente).
        cats = {
            "bat_hr": ("batting", "HR", "Líderes de jonrones", "", True),
            "bat_rbi": ("batting", "RBI", "Líderes de impulsadas", "", True),
            "pit_k": ("pitching", "K", "Líderes de ponches", "", True),
            "pit_era": ("pitching", "ERA", "Mejor ERA", "", False),
        }
        rankings = []
        for clave, (categoria, label, etiqueta, unidad, suma) in cats.items():
            acumulados = {}
            for d in db.mlb_jugadores.find(
                    {"liga": "mlb", "categoria": {"$regex": f"^{categoria}$", "$options": "i"}},
                    {"_id": 0}).limit(4000):
                st = d.get("stats", {}) or {}
                val = None
                for k, v in st.items():
                    if k.lower() == label.lower():
                        val = v; break
                if val is None:
                    continue
                nom = d.get("jugador", "")
                a = acumulados.setdefault(nom, {"nombre": nom, "equipo": d.get("equipo", ""),
                                                 "posicion": d.get("posicion", ""), "valor": 0, "j": 0})
                try:
                    a["valor"] += float(val); a["j"] += 1
                except Exception:
                    pass
            lista = list(acumulados.values())
            for x in lista:
                if not suma:
                    x["valor"] = round(x["valor"] / x["j"], 2) if x["j"] else 0
                x.pop("j", None)
            lista.sort(key=lambda x: x["valor"], reverse=suma)
            if lista:
                rankings.append({"clave": clave, "etiqueta": etiqueta,
                                 "unidad": unidad, "lideres": lista[:top]})
        return {"liga": liga, "rankings": rankings}

    jugadores = list(
        db.jugadores.find({"liga": liga}, {"_id": 0})
    )

    # categorías: (clave en sportData, etiqueta). Rating va primero (métrica más completa).
    categorias = [
        ("rating", "Mejor rating"),
        ("goles", "Goleadores"),
        ("asistencias", "Asistencias"),
        ("remates_arco_pp", "Remates a puerta"),
        ("remates_pp", "Remates totales"),
        ("faltas_pp", "Faltas cometidas"),
        ("amarillas", "Tarjetas amarillas"),
        ("rojas", "Tarjetas rojas"),
        ("minutos", "Más minutos"),
    ]

    # alias flexibles para las claves que el scraper puede nombrar distinto
    from models.betting import _stat_jugador, _ALIAS_AMARILLAS, _ALIAS_ROJAS
    _ALIAS_CAT = {"amarillas": _ALIAS_AMARILLAS, "rojas": _ALIAS_ROJAS}

    resultado = []
    for clave, etiqueta in categorias:
        con_stat = []
        for j in jugadores:
            sd = j.get("sportData", {}) or {}
            # tarjetas: búsqueda flexible; el resto: directo
            if clave in _ALIAS_CAT:
                val = _stat_jugador(sd, _ALIAS_CAT[clave])
            else:
                val = sd.get(clave)
            if val is not None and val != 0:
                con_stat.append({
                    "nombre": j.get("nombre"),
                    "equipo": j.get("equipo", ""),
                    "posicion": j.get("posicion", ""),
                    "valor": val,
                    "lesionado": j.get("lesionado", False),
                    "lesion_info": j.get("lesion_info", ""),
                })
        con_stat.sort(key=lambda x: x["valor"], reverse=True)
        # solo incluir categorías que tengan al menos un jugador con datos
        if con_stat:
            resultado.append({
                "clave": clave,
                "etiqueta": etiqueta,
                "lideres": con_stat[:top],
            })

    return {"liga": liga, "rankings": resultado}


@app.get("/ligas")
def listar_ligas():
    """
    Devuelve las ligas que realmente tienen data en la base, con conteos.
    Alimenta el selector de ligas de la web (pagina madre).
    """
    db = get_db()
    NOMBRES = {
        "ligamx": "Liga MX", "premier": "Premier League", "laliga": "LaLiga",
        "seriea": "Serie A", "bundesliga": "Bundesliga", "ligue1": "Ligue 1",
        "nfl": "NFL", "mlb": "MLB",
        "championsleague": "Champions League", "europaleague": "Europa League",
        "leaguescup": "Leagues Cup",
        "mls": "MLS", "canada": "Canadian Premier League",
        # UEFA Nations League: FotMob la divide en 4 grupos, cada uno con su
        # propio ID — el Scout puede haber usado cualquiera de estas dos
        # convenciones de slug según cómo se haya nombrado al cargarla.
        "nationsleaguea": "UEFA Nations League A",
        "nationsleagueb": "UEFA Nations League B",
        "nationsleaguec": "UEFA Nations League C",
        "nationsleagued": "UEFA Nations League D",
        "uefanationsleaguea": "UEFA Nations League A",
        "uefanationsleagueb": "UEFA Nations League B",
        "uefanationsleaguec": "UEFA Nations League C",
        "uefanationsleagued": "UEFA Nations League D",
    }
    slugs = db.partidos.distinct("liga")
    slugs = sorted(set(slugs) | set(db.clasificacion.distinct("liga"))
                   | set(db.jugadores.distinct("liga")))
    ligas = []
    for sl in slugs:
        if not sl:
            continue
        total = db.partidos.count_documents({"liga": sl})
        jugados = db.partidos.count_documents({"liga": sl, "estado": "finalizado"})
        restantes = total - jugados
        # detectar el deporte de la liga (desde un partido, o desde clasificación)
        muestra = db.partidos.find_one({"liga": sl}, {"deporte": 1}) \
            or db.clasificacion.find_one({"liga": sl}, {"deporte": 1}) or {}
        deporte = muestra.get("deporte", "futbol")
        ligas.append({
            "slug": sl,
            "nombre": NOMBRES.get(sl, sl.replace("_", " ").title()),
            "deporte": deporte,
            "partidos": total,
            "jugados": jugados,
            "restantes": restantes,
            "equipos": db.clasificacion.count_documents({"liga": sl}),
            "jugadores": db.jugadores.count_documents({"liga": sl}),
        })
    ligas.sort(key=lambda x: x["partidos"] + x["equipos"], reverse=True)
    return {"ligas": ligas}


@app.get("/equipos")
def listar_equipos(liga: str = "ligamx", ):
    """Lista los equipos de la liga (desde la clasificación).
    Para NFL (que no tiene tabla), saca los equipos de sus partidos."""
    db = get_db()
    if liga == "nfl":
        equipos = set()
        for p in db.partidos.find({"liga": "nfl"}, {"_id": 0, "local": 1, "visitante": 1}):
            if p.get("local"):
                equipos.add(p["local"])
            if p.get("visitante"):
                equipos.add(p["visitante"])
        return {"equipos": sorted(equipos)}
    if liga == "mlb":
        equipos = set()
        for p in db.partidos.find({"liga": "mlb"}, {"_id": 0, "local": 1, "visitante": 1}):
            if p.get("local"):
                equipos.add(p["local"])
            if p.get("visitante"):
                equipos.add(p["visitante"])
        return {"equipos": sorted(equipos)}
    # Clasificación (ordenada por posición): la fuente normal.
    filas = list(
        db.clasificacion.find({"liga": liga}, {"_id": 0, "equipo": 1, "pos": 1})
        .sort("pos", 1)
    )
    equipos = [f["equipo"] for f in filas]

    # PARCHE: en competencias sin tabla única (ej. UEFA Nations League, que en
    # FotMob se divide en Liga A/B/C/D, cada una con su propio ID) o cuando la
    # clasificación no se cargó para todos los equipos, un equipo puede tener
    # partidos guardados y NO aparecer aquí -> el selector de "Analizar
    # partido" se queda sin ese equipo aunque el partido sí exista.
    # Se completa con los equipos que sí tienen partido en esta liga, sin
    # tocar el orden de los que ya vienen de la clasificación.
    vistos = set(equipos)
    faltantes = set()
    for p in db.partidos.find({"liga": liga}, {"_id": 0, "local": 1, "visitante": 1}):
        for lado in ("local", "visitante"):
            eq = p.get(lado)
            if eq and eq not in vistos:
                faltantes.add(eq)
    if faltantes:
        equipos = equipos + sorted(faltantes)

    return {"equipos": equipos}


@app.get("/analisis")
def listar_analisis(liga: str = "ligamx", limite: int = 30, ):
    """
    Lista los análisis guardados (tu historial para aprender de errores).
    Muestra cada análisis con su resultado_real (pendiente/acierto/fallo).
    """
    db = get_db()
    analisis = list(
        db.analisis.find(
            {"liga": liga},
            {"_id": 0, "analisis": 0, "data_usada": 0, "aprendizajes_usados": 0},
        ).sort("creado", -1).limit(min(max(limite, 1), 100))
    )
    return {"total": len(analisis), "analisis": analisis}


@app.get("/analisis/{clave}")
def ver_analisis(clave: str, _=Depends(verificar_token)):
    """Devuelve un análisis guardado completo por su clave."""
    db = get_db()
    doc = db.analisis.find_one({"clave": clave}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Análisis no encontrado")
    return doc


class ResultadoReal(BaseModel):
    clave: str
    resultado: str  # "acierto" | "fallo" | "nulo"


@app.post("/analisis/resultado")
def marcar_resultado(r: ResultadoReal, _=Depends(verificar_token)):
    """
    Marca cómo salió un análisis (acierto/fallo). Base para el aprendizaje:
    con esto sabes qué funcionó y qué no, y puedes generar post-mortems.
    """
    db = get_db()
    res = db.analisis.update_one(
        {"clave": r.clave}, {"$set": {"resultado_real": r.resultado}}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Análisis no encontrado")
    return {"ok": True, "clave": r.clave, "resultado": r.resultado}


# ═══════════════════════════════════════════════════════════════════
#   PARLAY BUILDER
# ═══════════════════════════════════════════════════════════════════

def _picks_de_liga(db, liga: str, limite: int) -> list[dict]:
    """
    Helper: genera picks sugeridos de los próximos partidos de UNA liga.
    Reutilizado por /parlay/sugeridos y /parlay/maestro.
    """
    proximos = list(
        db.partidos.find(
            {"liga": liga, "estado": {"$in": ["programado", "en_vivo"]}},
            {"_id": 0},
        ).sort("inicio", 1).limit(limite)
    )

    resultado = []
    for p in proximos:
        nombre = f"{p['local']} vs {p['visitante']}"
        clave = f"picks:v2:{liga}:{p.get('fuenteId') or p.get('inicio', '')}:{p['local']}:{p['visitante']}"

        previo = db.picks_sugeridos.find_one({"clave": clave}, {"_id": 0})
        if previo:
            resultado.append({
                "partido": nombre, "local": p["local"], "visitante": p["visitante"],
                "inicio": p.get("inicio", ""), "liga": liga,
                "deporte": p.get("deporte", "futbol"), "picks": previo["picks"],
            })
            continue

        dp = DatosPartido(local=p["local"], visitante=p["visitante"], liga=liga)
        try:
            contexto = armar_contexto_datos(db, dp)
        except Exception:
            contexto = ""
        picks = generar_picks_sugeridos(get_anthropic(), contexto, nombre)

        if picks:
            db.picks_sugeridos.update_one(
                {"clave": clave},
                {"$set": {"clave": clave, "liga": liga, "partido": nombre, "picks": picks}},
                upsert=True,
            )
        resultado.append({
            "partido": nombre, "local": p["local"], "visitante": p["visitante"],
            "inicio": p.get("inicio", ""), "liga": liga,
            "deporte": p.get("deporte", "futbol"), "picks": picks,
        })
    return resultado


@app.get("/parlay/sugeridos")
def parlay_sugeridos(liga: str = "ligamx", limite: int = 6, _=Depends(verificar_token)):
    """Parlay Builder normal: picks de UNA liga."""
    db = get_db()
    return {"liga": liga, "partidos": _picks_de_liga(db, liga, limite)}


@app.get("/parlay/maestro")
def parlay_maestro(ligas: str = "", limite_por_liga: int = 4, _=Depends(verificar_token)):
    """
    Parlay Maestro cross-deporte: picks de VARIAS ligas a la vez,
    agrupados por deporte. 'ligas' es una lista separada por comas
    (ej. "ligamx,canada"). El usuario elige qué ligas entran.
    """
    db = get_db()
    slugs = [s.strip() for s in ligas.split(",") if s.strip()]
    if not slugs:
        return {"deportes": [], "total_partidos": 0}

    # nombres bonitos de liga (mismo mapa que /ligas)
    NOMBRES = {
        "ligamx": "Liga MX", "premier": "Premier League", "laliga": "LaLiga",
        "seriea": "Serie A", "bundesliga": "Bundesliga", "ligue1": "Ligue 1",
        "nfl": "NFL", "mlb": "MLB",
        "championsleague": "Champions League", "europaleague": "Europa League",
        "leaguescup": "Leagues Cup",
        "mls": "MLS", "canada": "Canadian Premier League",
        # UEFA Nations League: FotMob la divide en 4 grupos, cada uno con su
        # propio ID — el Scout puede haber usado cualquiera de estas dos
        # convenciones de slug según cómo se haya nombrado al cargarla.
        "nationsleaguea": "UEFA Nations League A",
        "nationsleagueb": "UEFA Nations League B",
        "nationsleaguec": "UEFA Nations League C",
        "nationsleagued": "UEFA Nations League D",
        "uefanationsleaguea": "UEFA Nations League A",
        "uefanationsleagueb": "UEFA Nations League B",
        "uefanationsleaguec": "UEFA Nations League C",
        "uefanationsleagued": "UEFA Nations League D",
    }

    # juntar partidos de todas las ligas, agrupar por deporte
    por_deporte = {}
    for slug in slugs:
        partidos = _picks_de_liga(db, slug, limite_por_liga)
        for p in partidos:
            p["liga_nombre"] = NOMBRES.get(slug, slug.title())
            dep = p.get("deporte", "futbol")
            por_deporte.setdefault(dep, []).append(p)

    # emojis por deporte
    DEP_EMOJI = {"futbol": "⚽", "basquet": "🏀", "beisbol": "⚾",
                 "americano": "🏈", "tenis": "🎾", "mma": "🥊"}
    DEP_NOMBRE = {"futbol": "Fútbol", "basquet": "Básquetbol", "beisbol": "Béisbol",
                  "americano": "Fútbol Americano", "tenis": "Tenis", "mma": "MMA / Boxeo"}

    deportes = []
    total = 0
    for dep, partidos in por_deporte.items():
        total += len(partidos)
        deportes.append({
            "deporte": dep,
            "nombre": DEP_NOMBRE.get(dep, dep.title()),
            "emoji": DEP_EMOJI.get(dep, "🏅"),
            "partidos": partidos,
        })

    return {"deportes": deportes, "total_partidos": total, "ligas": slugs}


class BoletoIn(BaseModel):
    nombre: str = "Mi boleto"
    autor: str = "Anónimo"
    patas: list[dict]
    liga: str = "ligamx"


@app.post("/parlay/validar")
def parlay_validar(boleto: BoletoIn, _=Depends(verificar_token)):
    """Valida un boleto en vivo: métricas + avisos (sin bloquear)."""
    return validar_boleto(boleto.patas)


@app.post("/parlay/guardar")
def parlay_guardar(boleto: BoletoIn, _=Depends(verificar_token)):
    """Guarda un boleto para verlo después."""
    from datetime import datetime, timezone
    db = get_db()
    val = validar_boleto(boleto.patas)
    doc = {
        "nombre": boleto.nombre,
        "autor": boleto.autor,
        "liga": boleto.liga,
        "patas": boleto.patas,
        "metricas": val["metricas"],
        "likes": 0,
        "creado": datetime.now(timezone.utc).isoformat(),
    }
    res = db.boletos.insert_one(doc)
    return {"ok": True, "id": str(res.inserted_id)}


@app.get("/parlay/comunidad")
def parlay_comunidad(liga: str = "ligamx", top: int = 5, _=Depends(verificar_token)):
    """
    Top de parlays más gustados por la comunidad.
    Por ahora incluye datos de ejemplo (hasta que haya usuarios/likes).
    """
    db = get_db()
    # boletos reales guardados, ordenados por likes
    reales = list(
        db.boletos.find({"liga": liga}, {"_id": 0}).sort("likes", -1).limit(top)
    )

    # si no hay suficientes reales, rellenar con ejemplos
    ejemplos = [
        {"nombre": "El Bombazo del Finde", "autor": "@ElCompaTipster", "likes": 342,
         "patas": [{"mercado": "Victoria Tijuana"}, {"mercado": "América BTTS"},
                   {"mercado": "Over 2.5 Toluca"}],
         "metricas": {"momioComb": 5.42}},
        {"nombre": "Seguro Conservador", "autor": "@picksMX", "likes": 218,
         "patas": [{"mercado": "Tijuana -1.5"}, {"mercado": "Cruz Azul gana"}],
         "metricas": {"momioComb": 2.85}},
        {"nombre": "Locura de Goles", "autor": "@golazos", "likes": 156,
         "patas": [{"mercado": "Over 2.5 América"}, {"mercado": "Over 2.5 Monterrey"},
                   {"mercado": "BTTS Chivas"}, {"mercado": "Over 9.5 córners"}],
         "metricas": {"momioComb": 8.90}},
    ]

    usar_demo = os.environ.get("ENABLE_DEMO_DATA", "false").lower() == "true"
    combinado = reales + (ejemplos if usar_demo else [])
    return {"liga": liga, "parlays": combinado[:min(max(top, 1), 20)], "hay_reales": len(reales) > 0, "demo": usar_demo}


# ═══════════════════════════════════════════════════════════════════
#   ADMIN: limpieza de data (protegido)
# ═══════════════════════════════════════════════════════════════════

class LimpiarIn(BaseModel):
    liga: str
    confirmar: bool = False


@app.get("/admin/detectar_intrusos")
def admin_detectar_intrusos(liga: str, col: str = "clasificacion",
                            _=Depends(verificar_bridge)):
    """Detecta equipos que NO pertenecen a la liga (el scraper los metió mal).
    Revisa la colección indicada (clasificacion, partidos, jugadores).
    Devuelve los documentos sospechosos con su _id para revisarlos/borrarlos."""
    from equipos_liga import equipo_pertenece
    if col not in ("clasificacion", "partidos", "jugadores"):
        return {"ok": False, "error": "Colección no soportada"}
    db = get_db()
    docs = list(db[col].find({"liga": liga}))
    intrusos = []
    for d in docs:
        # nombres de equipo según la colección
        nombres = []
        if col == "clasificacion":
            nombres = [d.get("equipo", "")]
        elif col == "partidos":
            nombres = [d.get("local", ""), d.get("visitante", "")]
        elif col == "jugadores":
            nombres = [d.get("equipo", "")]
        ajeno = any(nom and not equipo_pertenece(nom, liga) for nom in nombres)
        if ajeno:
            intrusos.append({
                "id": str(d["_id"]),
                "equipo": " / ".join(n for n in nombres if n),
                "liga": d.get("liga", ""),
            })
    return {"ok": True, "liga": liga, "col": col,
            "total_revisados": len(docs), "intrusos": intrusos}


@app.post("/admin/limpiar_intrusos")
def admin_limpiar_intrusos(payload: dict, _=Depends(verificar_bridge)):
    """Borra los equipos intrusos detectados en una liga/colección.
    Si 'ids' viene, borra esos; si no, borra TODOS los intrusos detectados."""
    from bson import ObjectId
    from equipos_liga import equipo_pertenece
    liga = payload.get("liga", "")
    col = payload.get("col", "clasificacion")
    if col not in ("clasificacion", "partidos", "jugadores"):
        return {"ok": False, "error": "Colección no soportada"}
    db = get_db()
    ids = payload.get("ids")
    if ids:  # borrar los seleccionados
        try:
            oids = [ObjectId(i) for i in ids]
            r = db[col].delete_many({"_id": {"$in": oids}})
            return {"ok": True, "borrados": r.deleted_count}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    # borrar TODOS los intrusos de la liga
    docs = list(db[col].find({"liga": liga}))
    a_borrar = []
    for d in docs:
        if col == "clasificacion":
            nombres = [d.get("equipo", "")]
        elif col == "partidos":
            nombres = [d.get("local", ""), d.get("visitante", "")]
        else:
            nombres = [d.get("equipo", "")]
        if any(nom and not equipo_pertenece(nom, liga) for nom in nombres):
            a_borrar.append(d["_id"])
    if not a_borrar:
        return {"ok": True, "borrados": 0}
    r = db[col].delete_many({"_id": {"$in": a_borrar}})
    return {"ok": True, "borrados": r.deleted_count}


@app.get("/panel/jugadores")
def panel_jugadores():
    """Panel visual: qué equipos tienen jugadores cargados y cuáles no."""
    p = _os.path.join(_STATIC_DIR, "panel-jugadores.html")
    if _os.path.exists(p):
        return FileResponse(p)
    return {"error": "panel no encontrado"}


@app.get("/panel/partidos")
def panel_partidos():
    """Sirve el panel visual de gestión de partidos (herramienta interna
    del motor). La autorización real la hacen los endpoints /admin/* con
    el token; esta página solo es la interfaz."""
    p = _os.path.join(_STATIC_DIR, "admin-partidos.html")
    if _os.path.exists(p):
        return FileResponse(p)
    return {"error": "panel no encontrado"}


@app.get("/panel")
def panel_completo():
    """Panel de administración completo del motor (todas las colecciones)."""
    p = _os.path.join(_STATIC_DIR, "panel.html")
    if _os.path.exists(p):
        return FileResponse(p)
    return {"error": "panel no encontrado"}


# Colecciones que el panel puede administrar, con sus campos editables y
# un resumen legible. Lista blanca por seguridad (no exponemos todo Mongo).
_COLS_ADMIN = {
    "partidos":       {"label": "Partidos",       "orden": "inicio",  "buscar": ["local", "visitante", "liga"]},
    "jugadores":      {"label": "Jugadores",       "orden": "nombre",  "buscar": ["nombre", "equipo", "liga"]},
    "clasificacion":  {"label": "Clasificación",   "orden": "posicion","buscar": ["equipo", "liga"]},
    "analisis":       {"label": "Análisis (cache)","orden": None,       "buscar": ["local", "visitante", "liga"]},
    "picks_sugeridos":{"label": "Picks sugeridos", "orden": None,       "buscar": ["partido", "liga"]},
    "picks_track":    {"label": "Track record",    "orden": "fecha",    "buscar": ["partido", "mercado", "liga"]},
    "boletos":        {"label": "Boletos comunidad","orden": None,      "buscar": ["nombre", "autor"]},
    "alineaciones":   {"label": "Alineaciones",    "orden": None,       "buscar": ["local", "visitante", "liga"]},
}


@app.get("/admin/colecciones")
def admin_colecciones(_=Depends(verificar_bridge)):
    """Lista las colecciones administrables y cuántos documentos tiene cada una."""
    db = get_db()
    out = []
    for col, meta in _COLS_ADMIN.items():
        try:
            n = db[col].count_documents({})
        except Exception:
            n = 0
        out.append({"col": col, "label": meta["label"], "total": n})
    return {"ok": True, "colecciones": out}


@app.get("/admin/col")
def admin_col_listar(col: str, q: str = "", liga: str = "", limite: int = 100,
                     _=Depends(verificar_bridge)):
    """Lista documentos de una colección (con búsqueda opcional).
    q busca en los campos definidos; liga filtra por liga si aplica."""
    if col not in _COLS_ADMIN:
        return {"ok": False, "error": "Colección no permitida"}
    db = get_db()
    meta = _COLS_ADMIN[col]
    filtro = {}
    if liga:
        filtro["liga"] = liga
    if q:
        campos = meta["buscar"]
        filtro["$or"] = [{c: {"$regex": q, "$options": "i"}} for c in campos]
    cursor = db[col].find(filtro)
    if meta["orden"]:
        cursor = cursor.sort(meta["orden"], 1)
    docs = list(cursor.limit(min(max(limite, 1), 300)))
    salida = []
    for d in docs:
        d["_id"] = str(d["_id"])
        salida.append(d)
    return {"ok": True, "col": col, "label": meta["label"], "total": len(salida), "docs": salida}


@app.post("/admin/col/borrar")
def admin_col_borrar(payload: dict, _=Depends(verificar_bridge)):
    """Borra un documento por _id de la colección indicada."""
    from bson import ObjectId
    col = payload.get("col", "")
    if col not in _COLS_ADMIN:
        return {"ok": False, "error": "Colección no permitida"}
    db = get_db()
    try:
        r = db[col].delete_one({"_id": ObjectId(payload.get("id", ""))})
        return {"ok": True, "borrados": r.deleted_count}
    except Exception as e:
        return {"ok": False, "error": f"id inválido: {e}"}


@app.post("/admin/col/editar")
def admin_col_editar(payload: dict, _=Depends(verificar_bridge)):
    """Edita campos de un documento por _id. 'campos' es un dict con lo que cambia."""
    from bson import ObjectId
    col = payload.get("col", "")
    if col not in _COLS_ADMIN:
        return {"ok": False, "error": "Colección no permitida"}
    campos = payload.get("campos", {})
    if not isinstance(campos, dict) or not campos:
        return {"ok": False, "error": "Nada que actualizar"}
    campos.pop("_id", None)  # nunca tocar el _id
    db = get_db()
    try:
        r = db[col].update_one({"_id": ObjectId(payload.get("id", ""))}, {"$set": campos})
        return {"ok": True, "modificados": r.modified_count}
    except Exception as e:
        return {"ok": False, "error": f"id inválido: {e}"}


@app.post("/admin/col/borrar_varios")
def admin_col_borrar_varios(payload: dict, _=Depends(verificar_bridge)):
    """Borra varios documentos por lista de _ids (borrado en lote)."""
    from bson import ObjectId
    col = payload.get("col", "")
    if col not in _COLS_ADMIN:
        return {"ok": False, "error": "Colección no permitida"}
    ids = payload.get("ids", [])
    if not ids:
        return {"ok": False, "error": "Sin ids"}
    db = get_db()
    try:
        oids = [ObjectId(i) for i in ids]
        r = db[col].delete_many({"_id": {"$in": oids}})
        return {"ok": True, "borrados": r.deleted_count}
    except Exception as e:
        return {"ok": False, "error": f"error: {e}"}


@app.get("/admin/partidos_liga")
def admin_partidos_liga(liga: str, _=Depends(verificar_bridge)):
    """Lista los partidos de una liga para el panel de administración.
    Incluye _id (como string) para poder editar/borrar uno individual,
    y marca posibles duplicados (mismo local+visitante+fecha)."""
    db = get_db()
    docs = list(db.partidos.find({"liga": liga}).sort("inicio", 1))
    vistos = {}
    salida = []
    for d in docs:
        key = f"{d.get('local','')}|{d.get('visitante','')}|{str(d.get('inicio',''))[:10]}"
        dup = key in vistos
        vistos[key] = True
        salida.append({
            "id": str(d["_id"]),
            "local": d.get("local", ""),
            "visitante": d.get("visitante", ""),
            "inicio": d.get("inicio", ""),
            "estado": d.get("estado", ""),
            "marcadorLocal": d.get("marcadorLocal"),
            "marcadorVisitante": d.get("marcadorVisitante"),
            "fase": d.get("fase", ""),
            "fuenteId": d.get("fuenteId", ""),
            "posible_duplicado": dup,
        })
    return {"ok": True, "liga": liga, "total": len(salida), "partidos": salida}


@app.post("/admin/borrar_partido")
def admin_borrar_partido(payload: dict, _=Depends(verificar_bridge)):
    """Borra UN solo partido por su _id (sin tocar el resto de la liga)."""
    from bson import ObjectId
    db = get_db()
    pid = payload.get("id", "")
    try:
        r = db.partidos.delete_one({"_id": ObjectId(pid)})
        return {"ok": True, "borrados": r.deleted_count}
    except Exception as e:
        return {"ok": False, "error": f"id inválido: {e}"}


@app.post("/admin/editar_partido")
def admin_editar_partido(payload: dict, _=Depends(verificar_bridge)):
    """Corrige campos de UN partido por su _id: equipos, marcador, hora,
    estado, fase. Solo actualiza lo que mandes."""
    from bson import ObjectId
    db = get_db()
    pid = payload.get("id", "")
    campos = {}
    for k in ("local", "visitante", "inicio", "estado", "fase", "marcadorLocal", "marcadorVisitante"):
        if k in payload and payload[k] is not None:
            campos[k] = payload[k]
    if not campos:
        return {"ok": False, "error": "Nada que actualizar"}
    try:
        r = db.partidos.update_one({"_id": ObjectId(pid)}, {"$set": campos})
        return {"ok": True, "modificados": r.modified_count}
    except Exception as e:
        return {"ok": False, "error": f"id inválido: {e}"}


@app.post("/admin/limpiar_analisis")
def admin_limpiar_analisis(payload: dict, _=Depends(verificar_bridge)):
    """Borra SOLO los análisis y picks cacheados de una liga (no los partidos).
    Útil cuando la data ya está bien pero quieres regenerar análisis limpios."""
    db = get_db()
    liga = payload.get("liga", "")
    if not liga:
        return {"ok": False, "error": "Falta liga"}
    return {"ok": True, "liga": liga, "borrados": {
        "analisis": db.analisis.delete_many({"liga": liga}).deleted_count,
        "picks_sugeridos": db.picks_sugeridos.delete_many({"liga": liga}).deleted_count,
    }}


@app.post("/admin/limpiar_liga")
def admin_limpiar_liga(body: LimpiarIn, _=Depends(verificar_token)):
    """
    Borra TODA la data de una liga (partidos, jugadores, clasificación,
    análisis, picks, boletos). Úsalo para re-jalar limpio.
    Protegido con API_TOKEN si está configurado.
    """
    if not body.confirmar:
        return {"ok": False, "error": "Falta confirmar=true"}

    db = get_db()
    liga = body.liga
    borrados = {
        "partidos": db.partidos.delete_many({"liga": liga}).deleted_count,
        "jugadores": db.jugadores.delete_many({"liga": liga}).deleted_count,
        "clasificacion": db.clasificacion.delete_many({"liga": liga}).deleted_count,
        "analisis": db.analisis.delete_many({"liga": liga}).deleted_count,
        "picks_sugeridos": db.picks_sugeridos.delete_many({"liga": liga}).deleted_count,
        "boletos": db.boletos.delete_many({"liga": liga}).deleted_count,
    }
    return {"ok": True, "liga": liga, "borrados": borrados}


# ═══════════════════════════════════════════════════════════════════
#   ALINEACIONES (titulares) vía API-Football
# ═══════════════════════════════════════════════════════════════════

@app.get("/alineaciones/estado")
def alineaciones_estado(_=Depends(verificar_token)):
    """¿Está configurada la API key? Y prueba la conexión."""
    if not hay_api_key():
        return {"configurada": False}
    # probar conexión con el endpoint /status (no gasta cuota)
    import httpx, os
    try:
        r = httpx.get(
            "https://v3.football.api-sports.io/status",
            headers={"x-apisports-key": os.environ.get("APIFOOTBALL_KEY", "")},
            timeout=15,
        )
        data = r.json().get("response", {})
        return {
            "configurada": True,
            "conexion": r.status_code == 200,
            "cuenta": data.get("account", {}),
            "suscripcion": data.get("subscription", {}),
            "requests": data.get("requests", {}),
        }
    except Exception as e:
        return {"configurada": True, "conexion": False, "error": str(e)}


@app.get("/alineaciones/{liga}/{local}/{visitante}")
def obtener_alineacion(liga: str, local: str, visitante: str, _=Depends(verificar_token)):
    """Devuelve la alineación cacheada de un partido (si existe)."""
    db = get_db()
    clave = f"{liga}:{local}:{visitante}"
    ali = db.alineaciones.find_one({"clave": clave}, {"_id": 0})
    if not ali:
        return {"hay": False}
    return {"hay": True, **ali}


@app.get("/nfl/lesiones/{equipo}")
def nfl_lesiones_equipo(equipo: str, _=Depends(verificar_token)):
    """Reporte de lesiones de un equipo de NFL, en vivo desde ESPN (no se
    cachea: es una sola llamada barata y sin límite de cuota, a diferencia
    de API-Football). No es lo mismo que 'alineaciones' de soccer — la NFL
    no publica titulares confirmados 1h antes; lo que importa aquí es
    quién está disponible para jugar."""
    from nfl_scraper import obtener_lesiones_equipo
    return {"equipo": equipo, "lesiones": obtener_lesiones_equipo(equipo)}


@app.get("/nfl/roster/{equipo}")
def nfl_roster_equipo(equipo: str, _=Depends(verificar_token)):
    """Roster activo de un equipo de NFL, en vivo desde ESPN."""
    from nfl_scraper import obtener_roster_equipo
    return obtener_roster_equipo(equipo)


class ActualizarAliIn(BaseModel):
    liga: str
    fecha: str = ""   # YYYY-MM-DD; si vacío, usa hoy


@app.post("/alineaciones/actualizar")
def actualizar_alineaciones(body: ActualizarAliIn, _=Depends(verificar_token)):
    """
    Trae las alineaciones de los partidos de una liga en una fecha
    desde API-Football y las guarda. Cuida el límite: 1 request por liga/día.
    """
    from datetime import date
    fecha = body.fecha or date.today().isoformat()
    db = get_db()
    return actualizar_alineaciones_liga(db, body.liga, fecha)


@app.post("/alineaciones/proximas")
def alineaciones_proximas(ventana: float = 3.0, _=Depends(verificar_token)):
    """
    VERSIÓN EFICIENTE: trae alineaciones SOLO de partidos que empiezan
    en las próximas 'ventana' horas (donde ya hay titulares confirmados).
    Gasta el mínimo de requests: 1 para todos los fixtures + 1 por partido próximo.
    Esta es la que conviene usar (no gastar tokens en partidos lejanos).
    """
    db = get_db()
    return actualizar_alineaciones_proximas(db, ventana)


# ═══════════════════════════════════════════════════════════════════
#   PUENTE PARA PICKAZOAPP.COM
#   Endpoint dedicado para que pickazoapp (Node) consuma este motor.
#   Protegido con token: solo quien tenga PICKAZO_BRIDGE_KEY puede usarlo.
# ═══════════════════════════════════════════════════════════════════

class AnalisisPickazoIn(BaseModel):
    local: str
    visitante: str
    liga: str = "ligamx"
    momios: str = ""
    profundo: bool = False        # True = análisis avanzado (Sonnet)
    forzar: bool = False          # True = regenera aunque haya cache (datos frescos)


@app.post("/pickazo/analizar")
def pickazo_analizar(body: AnalisisPickazoIn, _=Depends(verificar_bridge)):
    """
    Endpoint que pickazoapp.com consume para obtener un análisis.
    Recibe dos equipos + liga, devuelve el análisis en Markdown listo
    para mostrar. Reutiliza el mismo motor y cache que la app interna.
    Si forzar=True, regenera con los datos actuales (ignora el cache).
    """
    dp = DatosPartido(
        local=body.local,
        visitante=body.visitante,
        liga=body.liga,
        momios=body.momios,
        modelo="sonnet" if body.profundo else "haiku",
        forzar=body.forzar,
    )
    resultado = analizar(dp, True)
    # devolver formato limpio para pickazoapp
    return {
        "ok": True,
        "partido": resultado.get("partido"),
        "liga": body.liga,
        "analisis_markdown": resultado.get("analisis", ""),
        "nivel": "premium" if body.profundo else "basico",
        "desde_cache": resultado.get("_desde_cache", False),
    }


@app.get("/pickazo/picks_dia")
def _track_registrar(db, partido: str, mercado: str, momio: float,
                     liga: str, tipo: str = "pick", prob=None, jornada: str = ""):
    """Registra un pick en picks_track. La clave de dedupe es
    partido+mercado+liga+jornada (NO la fecha del día), así el pick se
    'congela' al generarse: se registra UNA vez y no se duplica ni se pierde
    aunque se regenere en otro día o la IA sugiera algo distinto después.
    Se llama automáticamente al generar picks/análisis/parlays."""
    if not partido or not mercado:
        return
    fecha = _fecha_hoy_utc()
    # clave estable: si hay jornada la usamos; si no, caemos a la fecha (compat)
    ancla = jornada.strip() if jornada else fecha
    try:
        db.picks_track.update_one(
            {"partido": partido, "mercado": mercado, "liga": liga, "ancla": ancla},
            {"$setOnInsert": {
                "partido": partido, "mercado": mercado,
                "momio": momio or 2.0, "liga": liga, "tipo": tipo,
                "probEstimada": prob, "fecha": fecha, "ancla": ancla,
                "jornada": jornada, "resultado": "pendiente",
            }}, upsert=True)
    except Exception:
        pass  # el track nunca debe romper la generación de picks


def pickazo_picks_dia(liga: str = "ligamx", limite: int = 8, _=Depends(verificar_bridge)):
    """
    Para la sección 'Picks de hoy' de pickazoapp: devuelve los picks
    sugeridos de los próximos partidos de una liga.
    """
    db = get_db()
    partidos = _picks_de_liga(db, liga, limite)
    # registrar automáticamente cada pick para el track record (congelado
    # por partido+jornada, no por fecha, para que no se dupliquen ni pierdan)
    for pt in partidos:
        jornada = pt.get("jornada", "") or pt.get("fase", "")
        for pk in (pt.get("picks") or []):
            _track_registrar(db, pt.get("partido", ""), pk.get("mercado", ""),
                             pk.get("momio"), liga, "pick", pk.get("probEstimada"),
                             jornada)
    return {"ok": True, "liga": liga, "partidos": partidos}


def _analizar_parlay_ia(patas: list[dict], momio_comb: float,
                        prob_conjunta: float) -> dict:
    """
    Redacta el análisis del parlay con Haiku (barato). Honesto: habla de
    estimación, no de ventaja garantizada (no hay momios reales todavía).
    Devuelve {analisisIA, veredicto}. Si algo falla, cadenas vacías.
    """
    import json
    patas_txt = "\n".join(
        f"{i + 1}. {p.get('partido','')} — {p.get('mercado','')} "
        f"(momio estimado {p.get('momio')})"
        for i, p in enumerate(patas)
    )
    prompt = (
        "Eres un analista de apuestas de Liga MX, directo y honesto, sin humo.\n"
        f"Analiza este combinado de {len(patas)} patas:\n\n{patas_txt}\n\n"
        f"Momio combinado estimado: {momio_comb}\n"
        f"Probabilidad conjunta estimada: {prob_conjunta * 100:.1f}%\n\n"
        "IMPORTANTE: los momios son estimaciones del análisis, NO momios reales "
        "de una casa. No hables de 'ventaja' ni 'valor garantizado'.\n"
        'Responde SOLO con un JSON valido, sin markdown ni backticks:\n'
        '{"analisis": "2-3 frases en espanol de Mexico: por que estas patas '
        'juntas tienen sentido y cual es el riesgo real", "veredicto": "una de: '
        'SÓLIDO / CON RESERVAS / SOLO SI TE GUSTA EL RIESGO"}'
    )
    try:
        resp = get_anthropic().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        txt = resp.content[0].text.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(txt)
        return {
            "analisisIA": parsed.get("analisis", ""),
            "veredicto": parsed.get("veredicto", ""),
        }
    except Exception:
        return {"analisisIA": "", "veredicto": ""}


def _fecha_hoy_utc() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


_LIGAS_TODAS = ["ligamx", "premier", "laliga", "seriea", "bundesliga", "ligue1"]


def _picks_por_dia_todas(db, limite_por_liga: int = 10) -> dict:
    """
    Recoge picks sugeridos de los próximos partidos de TODAS las ligas y los
    agrupa por día (YYYY-MM-DD según 'inicio'). Devuelve:
      { "2026-08-15": [pick, pick, ...], "2026-08-16": [...], ... }
    Cada pick trae su liga, para poder mezclarlas en un mismo parlay.
    """
    por_dia = {}
    for liga in _LIGAS_TODAS:
        try:
            partidos = _picks_de_liga(db, liga, limite_por_liga)
        except Exception:
            continue
        for pt in partidos:
            dia = (pt.get("inicio", "") or "")[:10]
            if not dia:
                continue
            for pk in (pt.get("picks") or []):
                por_dia.setdefault(dia, []).append({
                    "partido": pt.get("partido", ""),
                    "mercado": pk.get("mercado", ""),
                    "momio": pk.get("momio", 2.0),
                    "probEstimada": pk.get("probEstimada", 0.5),
                    "liga": liga,
                    "id": f"{liga}|{pt.get('partido','')}|{pk.get('mercado','')}",
                })
    return por_dia


def _dia_objetivo(por_dia: dict) -> str | None:
    """
    Elige el día a mostrar: el próximo día (>= hoy) que tenga al menos 2 picks.
    Si ninguno futuro califica, toma el día más cercano con material.
    """
    if not por_dia:
        return None
    hoy = _fecha_hoy_utc()
    futuros = sorted(d for d in por_dia if d >= hoy and len(por_dia[d]) >= 2)
    if futuros:
        return futuros[0]
    # fallback: cualquier día con >=2, el más cercano a hoy
    con_material = sorted(d for d in por_dia if len(por_dia[d]) >= 2)
    return con_material[0] if con_material else None


def _pick_lock(universo: list[dict], momio_min: float = 1.5, momio_max: float = 3.2) -> dict | None:
    """
    La JUGADA MÁS VIABLE del día: una sola pata (no un parlay), la de mejor
    valor dentro de un rango de momio 'jugoso' (ni tan cerrada que no rinde,
    ni tan abierta que es pura apuesta). Útil como ancla del análisis —
    "si solo vas a jugar una, que sea esta".

    Mientras no haya momios reales (ver HONESTIDAD arriba), esto usa el
    momio ESTIMADO igual que el resto del motor — por eso 'edge' aquí
    también puede ser una estimación, no ventaja garantizada. En cuanto
    haya momios reales (Caliente/Novibet), conectarlos aquí sin tocar el
    resto: esta función ya está lista para recibir momio real en el mismo
    campo 'momio' de cada pata.
    """
    candidatos = [p for p in universo if momio_min <= (p.get("momio") or 0) <= momio_max
                  and p.get("probEstimada") is not None]
    if not candidatos:
        return None
    for p in candidatos:
        p["_edge"] = p["probEstimada"] - (1 / p["momio"])
    candidatos.sort(key=lambda p: (p["_edge"], p["probEstimada"]), reverse=True)
    mejor = candidatos[0]
    return {
        "partido": mejor.get("partido", ""),
        "mercado": mejor.get("mercado", ""),
        "momio": mejor.get("momio"),
        "probEstimada": mejor.get("probEstimada"),
        "edge": round(mejor["_edge"], 4),
        "liga": mejor.get("liga", ""),
        "momioReal": False,  # True el día que esto entre con momios scrapeados de verdad
    }


@app.get("/pickazo/parlays")
def pickazo_parlays(
    liga: str = "ligamx",
    limite_partidos: int = 8,
    top: int = 4,
    forzar: bool = False,
    _=Depends(verificar_bridge),
):
    """
    Parlays del día para pickazoapp (Camino B: el motor es la fuente única).
    Combina los picks sugeridos de los próximos partidos en boletos coherentes
    de 2-3 patas, rankeados por probabilidad conjunta ESTIMADA.

    HONESTIDAD: mientras no haya momios reales ni probabilidades calibradas,
    el 'edge' se entrega como None (N/D) y cada parlay trae flags claros
    (momiosReales, probabilidadCalibrada). El front lo muestra como análisis,
    no como ventaja garantizada. Cuando existan momios reales, cambiar a
    mejores_parlays() para edge real sin tocar el resto del flujo.

    Cachea por fecha+liga en db.parlays_dia; forzar=True regenera.
    """
    db = get_db()
    fecha = _fecha_hoy_utc()
    clave = f"parlays:{liga}:{fecha}"

    if not forzar:
        cache = db.parlays_dia.find_one({"clave": clave}, {"_id": 0})
        if cache:
            return {"ok": True, "liga": liga, "fecha": fecha,
                    "diaMostrado": cache.get("diaMostrado", fecha),
                    "edge_disponible": False, "desde_cache": True,
                    "parlays": cache.get("parlays", []),
                    "sonadores": cache.get("sonadores", []),
                    "pickLock": cache.get("pickLock")}

    # 1) Reunir picks. Modo normal (una liga) o TODAS las ligas por día.
    dia_mostrado = fecha
    if liga == "__todas__":
        por_dia = _picks_por_dia_todas(db, limite_por_liga=10)
        dia_mostrado = _dia_objetivo(por_dia)
        universo = por_dia.get(dia_mostrado, []) if dia_mostrado else []
    else:
        partidos = _picks_de_liga(db, liga, limite_partidos)
        universo = []
        for pt in partidos:
            for pk in (pt.get("picks") or []):
                universo.append({
                    "partido": pt.get("partido", ""),
                    "mercado": pk.get("mercado", ""),
                    "momio": pk.get("momio", 2.0),
                    "probEstimada": pk.get("probEstimada", 0.5),
                    "liga": liga,
                    "id": f"{liga}|{pt.get('partido','')}|{pk.get('mercado','')}",
                })

    # 2) Combinar por probabilidad conjunta estimada (edge queda N/D)
    combos = mejores_parlays_estimados(universo, min_k=2, max_k=3, top=top)

    # 3) Validar + análisis IA. El de menor prob conjunta (más arriesgado)
    #    NO; liberamos como gratis el MÁS seguro (mayor prob), resto PRO.
    parlays = []
    for i, c in enumerate(combos):
        val = validar_boleto(c["patas"])
        ia = _analizar_parlay_ia(c["patas"], c["momioComb"], c["probConjunta"])
        parlays.append({
            # el primero (más seguro) es el gancho gratis; el resto PRO
            "tier": "public" if i == 0 else "pro",
            "patas": [{
                "partido": p.get("partido", ""),
                "mercado": p.get("mercado", ""),
                "momio": p.get("momio"),
                "probEstimada": p.get("probEstimada"),
                "liga": p.get("liga", liga),
            } for p in c["patas"]],
            "momioComb": c["momioComb"],
            "probConjunta": c["probConjunta"],
            "edge": c["edge"],                       # None = N/D
            "edgeValido": c["edgeValido"],
            "momiosReales": c["momiosReales"],
            "probabilidadCalibrada": c["probabilidadCalibrada"],
            "avisos": val.get("avisos", []),
            **ia,
        })

    # 4) SOÑADORES (gancho gratis, sin límite de patas):
    #    uno solo con patas verdes, uno con las mejores. Ambos públicos.
    son = parlays_sonadores(universo, umbral_verde=0.70, max_patas=12)
    sonadores = []
    _NOMBRES = {
        "sonador_seguro": ("🟢 Soñador Seguro", "Solo patas de alta probabilidad"),
        "sonador_total": ("🔥 Soñador Total", "Las mejores patas, premio máximo"),
    }
    for clave_s in ("sonador_seguro", "sonador_total"):
        s = son.get("seguro" if clave_s == "sonador_seguro" else "total")
        if not s:
            continue
        nombre, sub = _NOMBRES[clave_s]
        ia = _analizar_parlay_ia(s["patas"], s["momioComb"], s["probConjunta"])
        sonadores.append({
            "tipo": clave_s,
            "nombre": nombre,
            "subtitulo": sub,
            "tier": "public",           # gratis para todos
            "patas": [{
                "partido": p.get("partido", ""),
                "mercado": p.get("mercado", ""),
                "momio": p.get("momio"),
                "probEstimada": p.get("probEstimada"),
                "liga": p.get("liga", liga),
            } for p in s["patas"]],
            "momioComb": s["momioComb"],
            "probConjunta": s["probConjunta"],
            "edge": s["edge"],
            "edgeValido": s["edgeValido"],
            "momiosReales": s["momiosReales"],
            "probabilidadCalibrada": s["probabilidadCalibrada"],
            **ia,
        })

    pick_lock = _pick_lock(universo)

    db.parlays_dia.update_one(
        {"clave": clave},
        {"$set": {"clave": clave, "liga": liga, "fecha": fecha,
                  "diaMostrado": dia_mostrado,
                  "parlays": parlays, "sonadores": sonadores,
                  "pickLock": pick_lock}},
        upsert=True,
    )
    return {"ok": True, "liga": liga, "fecha": fecha,
            "diaMostrado": dia_mostrado,
            "edge_disponible": False, "desde_cache": False,
            "parlays": parlays, "sonadores": sonadores,
            "pickLock": pick_lock}


def _top_por_stat(jugadores: list[dict], clave: str, top: int = 3) -> list[dict]:
    """
    Top jugadores de un equipo por una stat (goles, amarillas...).
    Lee el campo DIRECTO del jugador (igual que el endpoint oficial de top
    jugadores), con sportData solo como respaldo. Esto evita el desajuste
    donde sportData traía otra métrica y los números no cuadraban.
    """
    con = []
    for j in jugadores:
        val = j.get(clave)
        if val is None:
            sd = j.get("sportData", {}) or {}
            val = sd.get(clave)
        if val is not None and val != 0:
            con.append({
                "nombre": j.get("nombre"),
                "posicion": j.get("posicion", ""),
                "valor": val,
            })
    con.sort(key=lambda x: x["valor"], reverse=True)
    return con[:top]


def _jugadores_equipo(db, liga: str, equipo: str) -> list[dict]:
    """Jugadores de un equipo, con emparejamiento tolerante de nombre
    (acentos, FC/CF, 'club', etc.) para que los números coincidan entre
    pantallas y no se pierdan equipos como 'Real Madrid' vs 'Real Madrid CF'."""
    import unicodedata
    def norm(s):
        s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
        # quitar sufijos/prefijos de club como PALABRAS completas (no subcadenas),
        # para no romper nombres. NO quitamos 'real' porque distingue Madrid/Sociedad/Betis.
        palabras = [p for p in s.split() if p not in ("fc", "cf", "cd", "club", "deportivo", "ac", "sc")]
        return " ".join(palabras)
    objetivo = norm(equipo)
    TORNEOS = {"championsleague", "europaleague", "leaguescup",
               "conferenceleague", "mundialclubes"}
    es_torneo = liga in TORNEOS
    filtro = {} if es_torneo else {"liga": liga}
    todos = list(db.jugadores.find(filtro, {"_id": 0}))
    def coincide(nom_eq):
        n = norm(nom_eq)
        if not n or not objetivo:
            return False
        if es_torneo:
            # Sin filtro de liga (cruza TODAS): aquí "contiene" es peligroso
            # — "inter" queda contenido en "inter miami" y los confunde
            # (son equipos distintos, uno en Serie A/Champions, otro en la
            # MLS). Se exige nombre exacto normalizado.
            return n == objetivo
        # match exacto, o uno contiene al otro (para "madrid" vs "real madrid")
        return n == objetivo or n in objetivo or objetivo in n
    res = [j for j in todos if coincide(j.get("equipo", ""))]
    if liga in TORNEOS:
        # En un torneo se mezclan registros de la liga local y del torneo para
        # el mismo jugador: se deja UNO por jugador (prefiere el del torneo).
        vistos = {}
        for j in res:
            k = j.get("jugadorId") or (j.get("nombre"), j.get("equipo"))
            if k not in vistos or (j.get("liga") == liga and vistos[k].get("liga") != liga):
                vistos[k] = j
        res = list(vistos.values())
    return res


def _stat_flexible(sd: dict, nombres: list[str], lado: str):
    """Busca una estadística en sportData tolerando variantes de nombre.
    'nombres' son alias posibles (ej. corners, corner kicks); 'lado' es
    'local' o 'visita'. Prueba: '<alias>_<lado>' en minúsculas, y también
    compara claves normalizadas (sin importar mayúsculas/espacios extra).
    Esto evita que un córner de FotMob se pierda por llamarse distinto."""
    # 1) intento directo con los alias conocidos
    for alias in nombres:
        clave = f"{alias}_{lado}"
        if clave in sd and sd[clave] is not None:
            return sd[clave]
    # 2) búsqueda tolerante: normaliza las claves reales y compara
    def norm(s):
        return "".join(ch for ch in str(s).lower() if ch.isalnum())
    lado_alt = {"local": ["local", "home", "casa"], "visita": ["visita", "visitante", "away", "fuera"]}[lado]
    alias_norm = [norm(a) for a in nombres]
    for clave, valor in sd.items():
        if valor is None:
            continue
        ck = norm(clave)
        # ¿la clave contiene algún alias Y algún indicador de lado?
        if any(a in ck for a in alias_norm) and any(norm(l) in ck for l in lado_alt):
            return valor
    return None


# alias de córners y tiros como los puede nombrar FotMob / el Scout
_ALIAS_CORNERS = ["corners", "corner kicks", "corner", "tiros de esquina", "córners", "saques de esquina"]
_ALIAS_TIROS = ["total shots", "shots", "tiros", "tiros totales", "remates"]


def _corners_recientes(partidos: list[dict], equipo: str) -> dict:
    """Córners y tiros del equipo en sus últimos partidos (como local o visita).
    Usa búsqueda flexible: tolera variantes de nombre del scraper/FotMob."""
    filas = []
    for p in partidos:
        sd = p.get("sportData", {}) or {}
        es_local = _empareja(p.get("local", ""), equipo)
        lado = "local" if es_local else "visita"
        cor = _stat_flexible(sd, _ALIAS_CORNERS, lado)
        tir = _stat_flexible(sd, _ALIAS_TIROS, lado)
        rival = p.get("visitante") if es_local else p.get("local")
        if cor is not None or tir is not None:
            filas.append({
                "fecha": (p.get("inicio", "") or "")[:10],
                "rival": rival,
                "corners": cor,
                "tiros": tir,
            })
    prom_cor = [f["corners"] for f in filas if f["corners"] is not None]
    return {
        "partidos": filas,
        "promedioCorners": round(sum(prom_cor) / len(prom_cor), 1) if prom_cor else None,
    }


@app.get("/stats_partido")
def stats_partido_interno(liga: str = "ligamx", local: str = "", visitante: str = "",
                          top: int = 3):
    """Versión interna (sin token) para que la UI del motor muestre goleadores,
    amonestados y córners en el análisis, igual que pickazoapp."""
    if not local or not visitante:
        return {"ok": False, "error": "Faltan equipos"}
    db = get_db()

    # NFL: stats de americano (anotadores, pasadores, corredores, receptores)
    if liga == "nfl":
        from nfl_analisis import stats_partido_nfl
        return stats_partido_nfl(db, local, visitante, top)
    # MLB: stats de beisbol (jonrones, impulsadas, ponches, ERA)
    if liga == "mlb":
        from mlb_analisis import stats_partido_mlb
        return stats_partido_mlb(db, local, visitante, top)

    def bloque(equipo):
        jug = _jugadores_equipo(db, liga, equipo)
        forma = _forma_equipo(db, equipo, liga, n=5)
        return {
            "equipo": equipo,
            "goleadores": _top_por_stat(jug, "goles", top),
            "amonestados": _top_por_stat(jug, "amarillas", top),
            "corners": _corners_recientes(forma, equipo),
        }

    return {"ok": True, "liga": liga, "local": bloque(local), "visitante": bloque(visitante)}


@app.get("/pickazo/stats_partido")
def pickazo_stats_partido(
    liga: str = "ligamx",
    local: str = "",
    visitante: str = "",
    top: int = 3,
    _=Depends(verificar_bridge),
):
    """
    Stats por partido para el detalle de pickazoapp: por cada equipo,
    top goleadores, top amonestados (amarillas), y córners/tiros de sus
    últimos partidos. Todo sale de datos que el motor ya tiene.
    """
    if not local or not visitante:
        return {"ok": False, "error": "Faltan equipos"}
    db = get_db()
    # NFL: stats de americano (pasadores, corredores, receptores)
    if liga == "nfl":
        from nfl_analisis import stats_partido_nfl
        return stats_partido_nfl(db, local, visitante, top)
    # MLB: stats de beisbol (jonrones, impulsadas, ponches, ERA)
    if liga == "mlb":
        from mlb_analisis import stats_partido_mlb
        return stats_partido_mlb(db, local, visitante, top)


    def bloque(equipo):
        jug = _jugadores_equipo(db, liga, equipo)
        forma = _forma_equipo(db, equipo, liga, n=5)
        return {
            "equipo": equipo,
            "goleadores": _top_por_stat(jug, "goles", top),
            "amonestados": _top_por_stat(jug, "amarillas", top),
            "corners": _corners_recientes(forma, equipo),
        }

    return {
        "ok": True, "liga": liga,
        "local": bloque(local),
        "visitante": bloque(visitante),
    }


def evaluar_pick(mercado: str, gl: int, gv: int,
                 local: str = "", visitante: str = "") -> str:
    """
    Compara un pick contra el marcador final y devuelve:
    'acierto' | 'fallo' | 'nulo' (nulo = no se pudo evaluar).

    Cubre los mercados que genera el motor. Es el CORAZÓN del track record:
    si esta lógica se equivoca, el historial miente. Por eso ante cualquier
    mercado que no reconocemos con certeza, devolvemos 'nulo' (no inventamos).
    """
    if gl is None or gv is None:
        return "nulo"
    m = (mercado or "").lower()
    total = gl + gv

    # --- OVER / UNDER de goles ---
    import re
    mo = re.search(r"m[áa]s de (\d+\.?\d*)\s*goles|over\s*(\d+\.?\d*)", m)
    if mo:
        linea = float(mo.group(1) or mo.group(2))
        return "acierto" if total > linea else "fallo"
    mu = re.search(r"menos de (\d+\.?\d*)\s*goles|under\s*(\d+\.?\d*)", m)
    if mu and "corner" not in m and "córner" not in m and "esquina" not in m:
        linea = float(mu.group(1) or mu.group(2))
        return "acierto" if total < linea else "fallo"

    # --- AMBOS ANOTAN (BTTS) ---
    if "ambos" in m and "anotan" in m or "btts" in m:
        both = gl > 0 and gv > 0
        # si dice "no", se invierte
        if " no" in m or "/no" in m:
            return "acierto" if not both else "fallo"
        return "acierto" if both else "fallo"

    # --- DOBLE OPORTUNIDAD ---
    # "X gana o empata (Doble oportunidad 1X)" / "... (Doble oportunidad X2)"
    if "doble oportunidad" in m or "gana o empata" in m:
        loc = local.lower() in m
        vis = visitante.lower() in m
        if "1x" in m or (loc and "empata" in m):
            return "acierto" if gl >= gv else "fallo"       # local no pierde
        if "x2" in m or (vis and "empata" in m):
            return "acierto" if gv >= gl else "fallo"       # visitante no pierde
        if "12" in m:
            return "acierto" if gl != gv else "fallo"       # no empate
        # fallback por nombre de equipo
        if loc:  return "acierto" if gl >= gv else "fallo"
        if vis:  return "acierto" if gv >= gl else "fallo"
        return "nulo"

    # --- HÁNDICAP -1 (gana por 2+) ---
    mh = re.search(r"h[áa]ndicap\s*-?1|gana por 2", m)
    if mh:
        if local.lower() in m:  return "acierto" if (gl - gv) >= 2 else "fallo"
        if visitante.lower() in m: return "acierto" if (gv - gl) >= 2 else "fallo"
        return "nulo"

    # --- GANA (1X2 simple) ---
    if "gana" in m and "empata" not in m and "o " not in m:
        if local.lower() in m:  return "acierto" if gl > gv else "fallo"
        if visitante.lower() in m: return "acierto" if gv > gl else "fallo"
        if "local" in m:  return "acierto" if gl > gv else "fallo"
        if "visitante" in m: return "acierto" if gv > gl else "fallo"
        return "nulo"

    # --- NFL / MLB: mercados de PUNTOS (spread, total, moneyline) ---
    # Antes nada de esto se reconocía (solo había patrones de "goles"), así
    # que todo pick de americano/béisbol se quedaba "nulo" para siempre: el
    # track record y el aprendizaje automático nunca los veían.
    if "puntos" in m or "moneyline" in m or "spread" in m or "hándicap" in m or "handicap" in m:
        es_total_partido = ("total" in m or "partido" in m) and "equipo" not in m

        if es_total_partido:
            mo2 = re.search(r"m[áa]s de (\d+\.?\d*)\s*puntos|over\s*(\d+\.?\d*)", m)
            if mo2:
                linea = float(mo2.group(1) or mo2.group(2))
                return "acierto" if total > linea else "fallo"
            mu2 = re.search(r"menos de (\d+\.?\d*)\s*puntos|under\s*(\d+\.?\d*)", m)
            if mu2:
                linea = float(mu2.group(1) or mu2.group(2))
                return "acierto" if total < linea else "fallo"

        # Total de puntos de UN equipo (ej. "Bills menos de 24.5 puntos")
        mo3 = re.search(r"m[áa]s de (\d+\.?\d*)\s*puntos", m)
        mu3 = re.search(r"menos de (\d+\.?\d*)\s*puntos", m)
        if (mo3 or mu3) and not es_total_partido:
            linea = float((mo3 or mu3).group(1))
            es_mas = mo3 is not None
            if local.lower() in m:
                return "acierto" if ((gl > linea) if es_mas else (gl < linea)) else "fallo"
            if visitante.lower() in m:
                return "acierto" if ((gv > linea) if es_mas else (gv < linea)) else "fallo"
            return "nulo"

        # Spread / Hándicap de puntos: "Equipo -3.5" cubre si gana por MÁS
        # del número; "Equipo +3.5" cubre si pierde por MENOS o gana.
        me = re.search(r"([+-]\d+\.?\d*)", m)
        if ("spread" in m or "hándicap" in m or "handicap" in m) and me:
            linea = float(me.group(1))
            if local.lower() in m:
                return "acierto" if (gl - gv) > -linea else "fallo"
            if visitante.lower() in m:
                return "acierto" if (gv - gl) > -linea else "fallo"
            return "nulo"

        # Moneyline: gana directo, sin margen de por medio.
        if "moneyline" in m:
            if local.lower() in m: return "acierto" if gl > gv else "fallo"
            if visitante.lower() in m: return "acierto" if gv > gl else "fallo"
            return "nulo"

    # Córners y otros mercados sin marcador de goles: no evaluables aquí.
    return "nulo"


def _texto_post_mortem(pk: dict, res: str, marcador: str) -> str:
    """Arma el texto del aprendizaje automático de UN pick ya resuelto.
    Es puramente informativo (data + veredicto), nunca una instrucción de
    'sube/baja este pick' — el modelo lo lee como referencia al analizar
    partidos parecidos, igual que un aprendizaje escrito a mano en /aprender."""
    prob = pk.get("probEstimada")
    partes = [
        f"POST-MORTEM AUTOMÁTICO ({pk.get('liga','')})",
        f"Partido: {pk.get('partido','')}",
        f"Mercado: {pk.get('mercado','')}",
        f"Momio: {pk.get('momio')}",
        f"Marcador final: {marcador}",
        f"Resultado: {'ACIERTO' if res == 'acierto' else 'FALLO'}",
    ]
    if prob is not None:
        try:
            pct = round(float(prob) * 100)
            partes.append(f"Confianza que le dio el modelo en su momento: {pct}%")
            # señal de calibración: solo informativa, no cambia picks futuros
            # por sí sola — el modelo la lee y decide.
            if res == "fallo" and pct >= 75:
                partes.append(
                    "CALIBRACIÓN: el modelo tuvo confianza ALTA (75%+) y falló. "
                    "Vale la pena revisar con más cuidado mercados/escenarios "
                    "parecidos antes de volver a darles confianza alta.")
            elif res == "acierto" and pct < 60:
                partes.append(
                    "CALIBRACIÓN: el modelo tuvo confianza BAJA (<60%) y acertó. "
                    "No es señal de subir la confianza a ciegas, pero vale "
                    "revisar si había un dato fuerte que se subestimó.")
        except Exception:
            pass
    return "\n".join(partes)


def _registrar_aprendizaje_auto(db, pk: dict, res: str, marcador: str) -> None:
    """Guarda el post-mortem automático en el mismo vector store que usa
    /aprender (a mano), para que el análisis futuro de esa liga lo recupere
    como 'aprendizajes'. Nunca debe tronar la resolución del track record."""
    try:
        texto = _texto_post_mortem(pk, res, marcador)
        # id estable por partido+mercado+liga+ancla: si se vuelve a resolver
        # (no debería, pero por si acaso) actualiza el mismo doc, no duplica.
        base = f"{pk.get('partido','')}|{pk.get('mercado','')}|{pk.get('liga','')}|{pk.get('ancla','')}"
        doc_id = "postmortem_auto_" + hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]
        vector = generar_embedding(texto)
        get_retriever().store.agregar(
            ids=[doc_id],
            textos=[texto],
            embeddings=[vector],
            metadatas=[{"tipo": "post_mortem_auto", "liga": pk.get("liga", ""),
                        "resultado": res}],
        )
    except Exception as e:
        print(f"[aprendizaje auto] no se pudo guardar: {e}")


def _resolver_track(db) -> int:
    """Marca acierto/fallo los picks pendientes de partidos finalizados.
    Reutilizable: la llama el endpoint y el scheduler tras cada actualización
    de marcadores. Devuelve cuántos resolvió.

    Desde aquí también se cierra el ciclo de aprendizaje: cada pick que se
    resuelve con acierto/fallo (no nulo) genera un post-mortem automático
    que se guarda en el RAG — antes esto solo pasaba si alguien lo escribía
    a mano en /aprender, así que en la práctica casi nunca pasaba."""
    finalizados = list(db.partidos.find(
        {"estado": {"$in": ["finalizado"]}},
        {"_id": 0, "local": 1, "visitante": 1, "liga": 1,
         "marcadorLocal": 1, "marcadorVisitante": 1}))
    idx = {}
    for p in finalizados:
        clave = f"{p.get('local','')} vs {p.get('visitante','')}".lower()
        idx[clave] = p

    resueltos = 0
    pendientes = db.picks_track.find({"resultado": "pendiente"})
    for pk in pendientes:
        clave = (pk.get("partido", "") or "").lower()
        p = idx.get(clave)
        if not p:
            continue
        res = evaluar_pick(pk.get("mercado", ""),
                           p.get("marcadorLocal"), p.get("marcadorVisitante"),
                           p.get("local", ""), p.get("visitante", ""))
        if res != "nulo" or p.get("marcadorLocal") is not None:
            marcador = f"{p.get('marcadorLocal')}-{p.get('marcadorVisitante')}"
            db.picks_track.update_one(
                {"_id": pk["_id"]},
                {"$set": {"resultado": res, "marcador": marcador}})
            resueltos += 1
            if res in ("acierto", "fallo"):
                _registrar_aprendizaje_auto(db, pk, res, marcador)
    return resueltos


@app.post("/pickazo/resolver")
def pickazo_resolver(_=Depends(verificar_bridge)):
    """
    Resuelve automáticamente los picks pendientes cuyos partidos ya
    terminaron. Compara cada pick con el marcador real y lo marca
    acierto/fallo/nulo. Devuelve cuántos resolvió.
    """
    db = get_db()
    return {"ok": True, "resueltos": _resolver_track(db)}


@app.get("/pickazo/trackrecord")
def pickazo_trackrecord(liga: str = "", limite: int = 50, _=Depends(verificar_bridge)):
    """
    Resumen del track record + historial. Para el registro público de
    credibilidad y el detalle PRO.
    """
    db = get_db()
    filtro = {"resultado": {"$in": ["acierto", "fallo"]}}
    if liga:
        filtro["liga"] = liga
    cerrados = list(db.picks_track.find(filtro, {"_id": 0}).sort("fecha", -1))

    aciertos = sum(1 for r in cerrados if r["resultado"] == "acierto")
    fallos = len(cerrados) - aciertos
    unidades = 0.0
    for r in cerrados:
        if r["resultado"] == "acierto":
            unidades += (r.get("momio", 2.0) - 1)
        else:
            unidades -= 1
    # racha actual
    racha = 0
    for r in cerrados:  # ya vienen más recientes primero
        if r["resultado"] == "acierto":
            racha += 1
        else:
            break

    return {
        "ok": True,
        "total": len(cerrados),
        "aciertos": aciertos,
        "fallos": fallos,
        "tasaAcierto": round(aciertos / len(cerrados), 3) if cerrados else None,
        "unidades": round(unidades, 2),
        "roi": round(unidades / len(cerrados) * 100, 1) if cerrados else None,
        "rachaActual": racha,
        "historial": cerrados[:limite],
    }


@app.post("/pickazo/track_add")
def pickazo_track_add(payload: dict, _=Depends(verificar_bridge)):
    """
    Registra un pick/análisis/parlay generado, para luego resolverlo.
    payload: {partido, mercado, momio, liga, tipo, probEstimada, fecha}
    Dedupe por (partido, mercado, fecha) para no duplicar.
    """
    db = get_db()
    doc = {
        "partido": payload.get("partido", ""),
        "mercado": payload.get("mercado", ""),
        "momio": payload.get("momio", 2.0),
        "liga": payload.get("liga", ""),
        "tipo": payload.get("tipo", "pick"),   # pick | analisis | parlay
        "probEstimada": payload.get("probEstimada"),
        "fecha": payload.get("fecha") or _fecha_hoy_utc(),
        "resultado": "pendiente",
    }
    db.picks_track.update_one(
        {"partido": doc["partido"], "mercado": doc["mercado"], "fecha": doc["fecha"]},
        {"$setOnInsert": doc}, upsert=True)
    return {"ok": True}


@app.get("/pickazo/estado")
def pickazo_estado():
    """Health check del puente (sin token, para verificar que vive)."""
    return {"ok": True, "motor": "Pickazo MX Engine", "listo": True}


@app.get("/pickazo/maestro")
def pickazo_maestro(ligas: str = "", limite_por_liga: int = 4, _=Depends(verificar_bridge)):
    """Puente para pickazoapp: Parlay Maestro cross-liga/deporte.
    'ligas' separadas por coma. Reutiliza la lógica del maestro del motor."""
    slugs = [s.strip() for s in ligas.split(",") if s.strip()]
    if not slugs:
        return {"deportes": [], "total_partidos": 0, "ligas": []}
    db = get_db()
    NOMBRES = {"ligamx": "Liga MX", "premier": "Premier League", "laliga": "LaLiga",
               "seriea": "Serie A", "bundesliga": "Bundesliga", "ligue1": "Ligue 1"}
    por_deporte = {}
    for slug in slugs:
        for p in _picks_de_liga(db, slug, limite_por_liga):
            p["liga_nombre"] = NOMBRES.get(slug, slug.title())
            p["liga_slug"] = slug
            dep = p.get("deporte", "futbol")
            por_deporte.setdefault(dep, []).append(p)
    DEP_EMOJI = {"futbol": "⚽", "basquet": "🏀", "beisbol": "⚾", "americano": "🏈"}
    DEP_NOMBRE = {"futbol": "Fútbol", "basquet": "Básquetbol", "beisbol": "Béisbol", "americano": "Fútbol Americano"}
    deportes, total = [], 0
    for dep, partidos in por_deporte.items():
        total += len(partidos)
        deportes.append({"deporte": dep, "nombre": DEP_NOMBRE.get(dep, dep.title()),
                         "emoji": DEP_EMOJI.get(dep, "🏅"), "partidos": partidos})
    return {"deportes": deportes, "total_partidos": total, "ligas": slugs}


@app.get("/pickazo/comunidad")
def pickazo_comunidad(liga: str = "ligamx", top: int = 8, dias_vigente: int = 1, _=Depends(verificar_bridge)):
    """Puente: parlays de la comunidad. Separa los VIGENTES (publicados
    hoy, o dentro de la ventana de 'dias_vigente') de los ARCHIVADOS —
    boletos viejos cuyos partidos ya pasaron no deben mezclarse con los
    del día y confundir a la banda sobre qué sigue en pie.
    'dias_vigente' cuenta días completos hacia atrás incluyendo hoy
    (default 1 = solo lo publicado hoy cuenta como vigente)."""
    from datetime import datetime, timezone, timedelta
    db = get_db()
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dv = max(1, dias_vigente)
    corte = (datetime.now(timezone.utc) - timedelta(days=dv - 1)).strftime("%Y-%m-%d")
    todos = list(db.boletos.find({}).sort("likes", -1).limit(300))
    for b in todos:
        b["id"] = str(b.pop("_id"))
        if not b.get("fecha"):
            b["fecha"] = (b.get("creado") or "")[:10]
    vigentes = [b for b in todos if b.get("fecha", "") >= corte][: min(max(top, 1), 30)]
    archivados = [b for b in todos if b.get("fecha", "") < corte][:30]
    return {
        "parlays": vigentes,
        "archivados": archivados,
        "hoy": hoy,
        "hay_reales": len(todos) > 0,
    }


@app.post("/pickazo/guardar_boleto")
def pickazo_guardar_boleto(payload: dict, _=Depends(verificar_bridge)):
    """Puente: guarda un boleto de la comunidad (con nombre y autor)."""
    from datetime import datetime, timezone
    db = get_db()
    patas = payload.get("patas", [])
    if not patas:
        return {"ok": False, "error": "Sin patas"}
    val = validar_boleto(patas)
    ahora = datetime.now(timezone.utc)
    doc = {
        "nombre": (payload.get("nombre") or "Mi boleto")[:60],
        "autor": (payload.get("autor") or "Anónimo")[:40],
        "liga": payload.get("liga", "varias"),
        "patas": patas,
        "metricas": val["metricas"],
        "likes": 0,
        "creado": ahora.isoformat(),
        "fecha": ahora.strftime("%Y-%m-%d"),  # día (UTC) — define cuándo se archiva
    }
    res = db.boletos.insert_one(doc)
    return {"ok": True, "id": str(res.inserted_id)}


@app.post("/pickazo/like_boleto")
def pickazo_like_boleto(payload: dict, _=Depends(verificar_bridge)):
    """Puente: dar like a un boleto de la comunidad."""
    from bson import ObjectId
    db = get_db()
    bid = payload.get("id", "")
    try:
        db.boletos.update_one({"_id": ObjectId(bid)}, {"$inc": {"likes": 1}})
        return {"ok": True}
    except Exception:
        return {"ok": False, "error": "id inválido"}


# ═══════════════════════════════════════════════════════════════════
#   PUENTE — DATOS PARA PICKAZOAPP (partidos, tabla, jugadores)
#   pickazoapp le pide estos datos al motor (Camino B: una sola fuente)
# ═══════════════════════════════════════════════════════════════════

@app.get("/pickazo/partidos")
def pickazo_partidos(liga: str = "ligamx", limite: int = 400, _=Depends(verificar_bridge)):
    """
    Lista de partidos de una liga para mostrar en Jornadas de pickazoapp.
    Devuelve el estado real y una bandera 'yaPaso' (informativa). NO fuerza
    'finalizado' por hora, para que la vista de Jornadas siga mostrando TODOS
    los partidos (pasados y futuros); pickozapp usa 'yaPaso' solo en Picks de hoy.
    """
    db = get_db()
    partidos = list(
        db.partidos.find({"liga": liga}, {"_id": 0}).sort("inicio", 1).limit(limite)
    )
    from datetime import datetime, timezone, timedelta
    _ahora = datetime.now(timezone.utc)
    salida = []
    for p in partidos:
        en_vivo = _es_realmente_vivo(p)
        estado_calc = "en_vivo" if en_vivo else _estado_por_hora(p)
        dt = _parse_inicio(p)
        ya_paso = bool(dt) and (_ahora > dt + timedelta(hours=4)) and not en_vivo
        salida.append({
            "id": f"{liga}:{p.get('local','')}:{p.get('visitante','')}",
            "local": p.get("local", ""),
            "visitante": p.get("visitante", ""),
            "liga": liga,
            "inicio": p.get("inicio", ""),
            "estado": estado_calc,
            "yaPaso": ya_paso,
            "enVivo": en_vivo,
            "minuto": p.get("minuto") if en_vivo else None,
            "golesLocal": p.get("marcadorLocal"),
            "golesVisitante": p.get("marcadorVisitante"),
            "jornada": p.get("fase", "") or p.get("jornada", ""),
        })
    return {"ok": True, "liga": liga, "partidos": salida}


@app.get("/pickazo/tabla")
def pickazo_tabla(liga: str = "ligamx", _=Depends(verificar_bridge)):
    """Tabla de posiciones de una liga para pickazoapp."""
    db = get_db()
    filas = list(db.clasificacion.find({"liga": liga}, {"_id": 0}).sort("pos", 1))
    return {"ok": True, "liga": liga, "tabla": filas}


@app.get("/pickazo/jugadores")
def pickazo_jugadores(liga: str = "ligamx", top: int = 50, _=Depends(verificar_bridge)):
    """Rankings de jugadores de una liga para pickazoapp."""
    db = get_db()
    jugadores = list(
        db.jugadores.find({"liga": liga}, {"_id": 0}).limit(top)
    )
    return {"ok": True, "liga": liga, "jugadores": jugadores}


@app.get("/pickazo/jugadores_equipo")
def pickazo_jugadores_equipo(liga: str, equipo: str, top: int = 5, _=Depends(verificar_bridge)):
    """
    Top jugadores de UN equipo (para la pantalla dedicada del partido).
    Ordenados por rating/goles. Para mostrar junto al análisis.
    """
    db = get_db()
    # buscar jugadores del equipo (emparejando nombre de equipo)
    import unicodedata
    def norm(s):
        s = unicodedata.normalize("NFKD", s or "").encode("ascii","ignore").decode().lower()
        return " ".join(s.replace("fc","").replace("cf","").split())
    objetivo = norm(equipo)
    todos = list(db.jugadores.find({"liga": liga}, {"_id": 0}))
    # filtrar por equipo con emparejamiento flexible
    delequipo = [j for j in todos if norm(j.get("equipo","")) == objetivo
                 or objetivo in norm(j.get("equipo","")) or norm(j.get("equipo","")) in objetivo]
    # ordenar por rating (o goles si no hay rating)
    def clave(j):
        return (j.get("rating") or 0, j.get("goles") or 0)
    delequipo.sort(key=clave, reverse=True)
    return {"ok": True, "equipo": equipo, "jugadores": delequipo[:top]}


# ═══════════════════════════════════════════════════════════════════
#   SCHEDULER AUTOMÁTICO DE ALINEACIONES
#   Corre solo cada 30 min. Trae las alineaciones titulares de los
#   partidos que arrancan pronto (~1.5h antes ya están confirmadas).
#   Así pickazoapp siempre tiene las alineaciones sin que nadie las dispare.
# ═══════════════════════════════════════════════════════════════════
import asyncio

# Cada cuánto revisa (minutos) y a cuántos minutos antes del partido se
# consultan las alineaciones.
#
# OJO: API-Football normalmente confirma la alineación oficial ~60 min
# antes del partido (así lo dicen los mensajes de este mismo archivo),
# así que una ventana de 25 min era DEMASIADO angosta — el scheduler
# ni siquiera empezaba a buscar hasta 25 min antes, y si en esos pocos
# intentos (cada 5 min) la API aún no la había publicado, se quedaba sin
# ella. Se amplía a 70 min para empezar a buscar ANTES de que se confirme.
ALINEACIONES_INTERVALO_MIN = float(os.environ.get("ALINEACIONES_INTERVALO_MIN", "5"))
ALINEACIONES_MIN_ANTES = float(os.environ.get("ALINEACIONES_MIN_ANTES", "70"))
ALINEACIONES_VENTANA_H = ALINEACIONES_MIN_ANTES / 60.0

# Margen retroactivo: además de lo que falta por arrancar, reintenta
# partidos que YA arrancaron hace hasta esto. Antes, si se pasaba el
# kickoff sin alineación (API lenta, el server reiniciando justo en ese
# momento, etc.), el partido quedaba sin alineación PARA SIEMPRE — el
# filtro original solo miraba partidos futuros. Con esto, el motor sigue
# intentando un rato después de que arrancó.
ALINEACIONES_MARGEN_RETRO_MIN = float(os.environ.get("ALINEACIONES_MARGEN_RETRO_MIN", "20"))


async def _loop_alineaciones():
    """Tarea de fondo: revisa y trae alineaciones próximas periódicamente."""
    # espera un poco al arranque para no chocar con el boot
    await asyncio.sleep(20)
    while True:
        try:
            if hay_api_key():
                db = get_db()
                # corre la función bloqueante en un hilo para no trabar el server
                res = await asyncio.to_thread(
                    actualizar_alineaciones_proximas, db, ALINEACIONES_VENTANA_H,
                    ALINEACIONES_MARGEN_RETRO_MIN / 60.0
                )
                print(f"[alineaciones auto] {res.get('resumen', res)}")
        except Exception as e:
            print(f"[alineaciones auto] error: {e}")
        # esperar hasta la próxima revisión
        await asyncio.sleep(ALINEACIONES_INTERVALO_MIN * 60)


@app.on_event("startup")
async def _arrancar_scheduler():
    """Solo para desarrollo con una réplica. En producción usa un cron externo."""
    if not ENABLE_INTERNAL_SCHEDULER:
        print("[alineaciones auto] scheduler interno desactivado")
        return
    asyncio.create_task(_loop_alineaciones())
    print(f"[alineaciones auto] scheduler activo: cada {ALINEACIONES_INTERVALO_MIN} min, ventana {ALINEACIONES_VENTANA_H}h")


@app.get("/pickazo/alineaciones")
def pickazo_alineaciones(liga: str, local: str, visitante: str, _=Depends(verificar_bridge)):
    """
    Alineaciones de un partido para pickazoapp.
    Devuelve las de ambos equipos. El motor las trae automáticamente ~1h antes;
    si aún no hay confirmada, devuelve lo que haya (o mensaje de espera).
    """
    db = get_db()
    import unicodedata
    def _n(s):
        s = unicodedata.normalize("NFKD", s or "").encode("ascii","ignore").decode().lower().strip()
        for r in [" fc","fc "," cf","cf ","club "," club","deportivo ","atletico","atl.","cd "," cd","real ","uanl"]:
            s = s.replace(r, " ")
        return " ".join(s.split())
    def coincide(a, b):
        na, nb = _n(a), _n(b)
        return bool(na) and bool(nb) and (na == nb or na in nb or nb in na)

    # 1) intento por clave exacta (rápido)
    clave = f"{liga}:{local}:{visitante}"
    ali = db.alineaciones.find_one({"clave": clave}, {"_id": 0})

    # 2) si no hay match exacto, buscar flexible entre las alineaciones de la liga
    if not ali:
        for cand in db.alineaciones.find({"liga": liga}, {"_id": 0}):
            eqs = cand.get("equipos", [])
            nombres = [e.get("nombre", "") for e in eqs]
            hay_local = any(coincide(local, n) for n in nombres)
            hay_visita = any(coincide(visitante, n) for n in nombres)
            if hay_local and hay_visita:
                ali = cand
                break

    if not ali:
        return {"ok": True, "hay": False,
                "mensaje": "Las alineaciones se publican aproximadamente 1 hora antes del partido."}
    # los datos vienen como array 'equipos': [{nombre, formacion, titulares, suplentes}]
    equipos = ali.get("equipos", [])
    def buscar(nombre):
        for e in equipos:
            if coincide(nombre, e.get("nombre", "")):
                return e
        return {}
    el = buscar(local)
    ev = buscar(visitante)
    return {
        "ok": True,
        "hay": True,
        "confirmada": ali.get("confirmada", False),
        "local": {
            "equipo": local,
            "formacion": el.get("formacion", ""),
            "titulares": el.get("titulares", []),
            "suplentes": el.get("suplentes", []),
        },
        "visitante": {
            "equipo": visitante,
            "formacion": ev.get("formacion", ""),
            "titulares": ev.get("titulares", []),
            "suplentes": ev.get("suplentes", []),
        },
        "actualizado": ali.get("actualizado", ""),
    }


@app.get("/pickazo/jornadas")
def pickazo_jornadas(liga: str = "ligamx", _=Depends(verificar_bridge)):
    """Lista de jornadas de una liga (para el selector de pickazoapp)."""
    return listar_jornadas(liga)


@app.get("/pickazo/todas_ligas")
def pickazo_todas_ligas(limite: int = 300, _=Depends(verificar_bridge)):
    """
    Partidos de TODAS las ligas de fútbol juntos, mezclados por hora.
    Para la pestaña 'Todas las ligas' de pickazoapp.
    """
    NOMBRES = {
        "ligamx": "Liga MX", "premier": "Premier League", "laliga": "LaLiga",
        "seriea": "Serie A", "bundesliga": "Bundesliga", "ligue1": "Ligue 1",
    }
    db = get_db()
    from datetime import datetime, timezone, timedelta
    filtro = {"$or": [{"deporte": "futbol"}, {"deporte": {"$exists": False}}]}
    # Traer más partidos y separar en código: TODOS los futuros/próximos + los
    # finalizados más recientes. Evita que partidos viejos llenen el límite y
    # dejen fuera los próximos.
    todos = list(db.partidos.find(filtro, {"_id": 0}).sort("inicio", 1).limit(1000))
    ahora = datetime.now(timezone.utc)

    def es_futuro_o_vivo(p):
        dt = _parse_inicio(p)
        if dt is None:
            return p.get("estado") != "finalizado"
        # futuro, o empezó hace poco (en juego), o no finalizado
        return dt >= ahora or p.get("estado") in ("vivo", "en_vivo", "en_juego") or p.get("estado") != "finalizado"

    futuros = [p for p in todos if es_futuro_o_vivo(p)]
    pasados = [p for p in todos if not es_futuro_o_vivo(p)]
    # ordenar pasados por más reciente y quedarnos con los últimos 30
    pasados.sort(key=lambda p: (p.get("inicio", "") or ""), reverse=True)
    partidos = futuros + pasados[:30]
    salida = []
    for p in partidos:
        liga = p.get("liga", "")
        en_vivo = _es_realmente_vivo(p)
        estado_calc = "en_vivo" if en_vivo else _estado_por_hora(p)
        _dt = _parse_inicio(p)
        ya_paso = bool(_dt) and (ahora > _dt + timedelta(hours=4)) and not en_vivo
        salida.append({
            "id": f"{liga}:{p.get('local','')}:{p.get('visitante','')}",
            "yaPaso": ya_paso,
            "local": p.get("local", ""),
            "visitante": p.get("visitante", ""),
            "liga": liga,
            "ligaNombre": NOMBRES.get(liga, liga.title()),
            "inicio": p.get("inicio", ""),
            "estado": estado_calc,
            "enVivo": en_vivo,
            "minuto": p.get("minuto") if en_vivo else None,
            "golesLocal": p.get("marcadorLocal"),
            "golesVisitante": p.get("marcadorVisitante"),
            "jornada": p.get("fase", "") or p.get("jornada", ""),
        })
    return {"ok": True, "partidos": salida}


@app.post("/alineaciones/jornada")
def alineaciones_por_jornada(liga: str = "ligamx", jornada: str = "", _=Depends(verificar_token)):
    """Trae alineaciones de una jornada específica (para el selector del motor)."""
    if not jornada:
        return {"ok": False, "error": "Falta jornada"}
    db = get_db()
    return actualizar_alineaciones_jornada(db, liga, jornada)


@app.post("/alineaciones/marcadores_vivo")
def endpoint_marcadores_vivo(_=Depends(verificar_token)):
    """Botón manual: trae marcadores en vivo de API-Football (1 request)."""
    db = get_db()
    return actualizar_marcadores_vivo(db)


# ═══════════════════════════════════════════════════════════════════
#   SCHEDULER DE MARCADORES EN VIVO
#   Cada 2 min revisa si hay partidos que PODRÍAN estar en vivo (su hora
#   ya empezó, menos de ~3h). Solo entonces consulta API-Football (1 request).
#   Así no gasta cuota cuando no hay nada en vivo.
# ═══════════════════════════════════════════════════════════════════
MARCADORES_INTERVALO_MIN = float(os.environ.get("MARCADORES_INTERVALO_MIN", "5"))


def _hay_partidos_en_ventana_vivo(db) -> bool:
    """True si algún partido pudo haber empezado y no lleva más de ~3h."""
    from datetime import datetime, timezone, timedelta
    ahora = datetime.now(timezone.utc)
    inicio_min = (ahora - timedelta(hours=3)).isoformat()
    inicio_max = ahora.isoformat()
    # partidos cuyo inicio está entre hace 3h y ahora, y no finalizados
    n = db.partidos.count_documents({
        "inicio": {"$gte": inicio_min, "$lte": inicio_max},
        "estado": {"$ne": "finalizado"},
    })
    # también los que ya están marcados en vivo (para seguir actualizándolos)
    n_vivo = db.partidos.count_documents({"estado": "en_vivo"})
    return (n + n_vivo) > 0


async def _loop_marcadores():
    """Tarea de fondo: actualiza marcadores en vivo si hay partidos jugándose."""
    await asyncio.sleep(30)  # espera al arranque
    while True:
        try:
            if hay_api_key():
                db = get_db()
                if _hay_partidos_en_ventana_vivo(db):
                    res = await asyncio.to_thread(actualizar_marcadores_vivo, db)
                    print(f"[marcadores vivo] {res.get('resumen', res)}")
                    # tras actualizar marcadores, resolver picks de partidos que terminaron
                    try:
                        n = await asyncio.to_thread(_resolver_track, db)
                        if n:
                            print(f"[track record] {n} picks resueltos automáticamente")
                    except Exception as e:
                        print(f"[track record] error al resolver: {e}")
                # si no hay partidos en ventana, NO consulta (ahorra cuota)
        except Exception as e:
            print(f"[marcadores vivo] error: {e}")
        await asyncio.sleep(MARCADORES_INTERVALO_MIN * 60)


# cada cuántas horas el job registra los picks del día de todas las ligas
PICKS_REGISTRO_INTERVALO_H = float(os.environ.get("PICKS_REGISTRO_INTERVALO_H", "6"))
LIGAS_PARA_PICKS = ["ligamx", "premier", "laliga", "seriea", "bundesliga", "ligue1"]


def _registrar_picks_todas_ligas(db) -> int:
    """Registra en el track los picks del día de todas las ligas, sin depender
    de que alguien abra la sección en la app. Devuelve cuántos picks tocó."""
    total = 0
    for liga in LIGAS_PARA_PICKS:
        try:
            partidos = _picks_de_liga(db, liga, 8)
            for pt in partidos:
                jornada = pt.get("jornada", "") or pt.get("fase", "")
                for pk in (pt.get("picks") or []):
                    _track_registrar(db, pt.get("partido", ""), pk.get("mercado", ""),
                                     pk.get("momio"), liga, "pick",
                                     pk.get("probEstimada"), jornada)
                    total += 1
        except Exception as e:
            print(f"[picks auto] {liga}: {e}")
    return total


async def _loop_registro_picks():
    """Tarea de fondo: registra los picks del día periódicamente, para que el
    track record no dependa de que un usuario abra la sección."""
    await asyncio.sleep(90)  # espera al arranque (después de otros loops)
    while True:
        try:
            db = get_db()
            n = _registrar_picks_todas_ligas(db)
            if n:
                print(f"[picks auto] {n} picks registrados en el track")
        except Exception as e:
            print(f"[picks auto] error: {e}")
        await asyncio.sleep(PICKS_REGISTRO_INTERVALO_H * 3600)


@app.on_event("startup")
async def _arrancar_marcadores():
    if not ENABLE_INTERNAL_SCHEDULER:
        print("[marcadores vivo] scheduler interno desactivado")
        return
    asyncio.create_task(_loop_marcadores())
    print(f"[marcadores vivo] scheduler activo: cada {MARCADORES_INTERVALO_MIN} min (solo si hay partidos)")
    asyncio.create_task(_loop_registro_picks())
    print(f"[picks auto] registro de picks activo: cada {PICKS_REGISTRO_INTERVALO_H}h")


def _guardar_alineacion_manual(body: dict) -> dict:
    """Carga manual de alineaciones. El control lo tienes tú: pegas los
    titulares de cada equipo y se guardan como CONFIRMADAS — armar_contexto_datos
    (fútbol) ya las lee solo de db.alineaciones por clave liga:local:visitante,
    así que en cuanto se guardan aquí, el siguiente análisis las usa sin más.
    body: {liga, local, visitante, formacionLocal, titularesLocal (texto con
    saltos de línea o comas), formacionVisitante, titularesVisitante}"""
    liga = body.get("liga", "")
    local = body.get("local", "")
    visitante = body.get("visitante", "")
    if not liga or not local or not visitante:
        return {"ok": False, "error": "Faltan liga/local/visitante"}

    def parsear(txt):
        if not txt:
            return []
        # acepta separados por salto de línea o coma
        crudo = str(txt).replace(",", "\n").split("\n")
        jug = []
        for linea in crudo:
            nombre = linea.strip()
            if nombre:
                jug.append({"nombre": nombre, "numero": "", "posicion": ""})
        return jug

    equipos = [
        {"nombre": local, "formacion": body.get("formacionLocal", ""),
         "titulares": parsear(body.get("titularesLocal", "")), "suplentes": []},
        {"nombre": visitante, "formacion": body.get("formacionVisitante", ""),
         "titulares": parsear(body.get("titularesVisitante", "")), "suplentes": []},
    ]
    hay = any(e["titulares"] for e in equipos)
    if not hay:
        return {"ok": False, "error": "No pegaste ningún titular"}

    db = get_db()
    clave = f"{liga}:{local}:{visitante}"
    db.alineaciones.update_one(
        {"clave": clave},
        {"$set": {
            "clave": clave, "liga": liga, "local": local, "visitante": visitante,
            "confirmada": True, "equipos": equipos, "fuente": "manual",
        }},
        upsert=True,
    )
    return {"ok": True, "guardada": True,
            "resumen": f"{local} vs {visitante}: alineación cargada manualmente"}


@app.post("/alineaciones/manual")
def alineaciones_manual(body: dict, _=Depends(verificar_bridge)):
    """Puente (pickazoapp) — llave de puente."""
    return _guardar_alineacion_manual(body)


@app.post("/alineaciones/manual_panel")
def alineaciones_manual_panel(body: dict, _=Depends(verificar_token)):
    """Mismo guardado, pero para el panel del motor (usa el token de admin,
    no la llave de puente, porque este panel no la tiene)."""
    return _guardar_alineacion_manual(body)


@app.post("/alineaciones/partido")
def endpoint_alineacion_partido(liga: str, local: str, visitante: str, _=Depends(verificar_token)):
    """Botón por partido (motor): trae la alineación de UN juego."""
    db = get_db()
    return actualizar_alineacion_partido(db, liga, local, visitante)


@app.post("/pickazo/traer_alineacion")
def pickazo_traer_alineacion(body: dict, _=Depends(verificar_bridge)):
    """Botón por partido (pickazoapp): trae la alineación de UN juego."""
    liga = body.get("liga", "ligamx")
    local = body.get("local", "")
    visitante = body.get("visitante", "")
    if not local or not visitante:
        return {"ok": False, "error": "Faltan equipos"}
    db = get_db()
    return actualizar_alineacion_partido(db, liga, local, visitante)


@app.get("/api_estado")
def api_estado(_=Depends(verificar_token)):
    """Muestra tu plan y cuota de API-Football (no gasta cuota de datos)."""
    return estado_cuenta_api()


# ═══════════════════════════════════════════════════════════════════
#   SCHEDULER AUTOMÁTICO DE NFL
#   Corre solo cada N horas y sincroniza la NFL (marcador + data
#   profunda: equipo, jugadores, drives, plays). Mismo switch que los
#   otros schedulers: ENABLE_INTERNAL_SCHEDULER. En producción puedes,
#   si prefieres, apuntar un cron externo de Railway a POST /nfl/sincronizar.
# ═══════════════════════════════════════════════════════════════════
NFL_INTERVALO_H = float(os.environ.get("NFL_INTERVALO_H", "6"))
NFL_TIPO_TEMPORADA = int(os.environ.get("NFL_TIPO_TEMPORADA", "1"))  # 1=pretemporada
NFL_AUTO_PLAYS = os.environ.get("NFL_AUTO_PLAYS", "true").lower() == "true"


def _sincronizar_nfl_auto():
    """Función bloqueante: sincroniza la NFL (semana actual). La corre el loop."""
    from nfl_scraper import obtener_partidos_nfl, obtener_detalle_partido
    import time as _t
    db = get_db()

    partidos = obtener_partidos_nfl(tipo_temporada=NFL_TIPO_TEMPORADA)
    guardados = detalle_ok = 0
    for i, doc in enumerate(partidos):
        filtro = {"liga": "nfl", "fuenteId": doc["fuenteId"]} if doc.get("fuenteId") else {
            "liga": "nfl", "local": doc["local"], "visitante": doc["visitante"], "inicio": doc["inicio"]}
        db.partidos.update_one(filtro, {"$set": doc}, upsert=True)
        guardados += 1

        gid = doc.get("fuenteId")
        if not gid or doc.get("estado") == "programado":
            continue
        det = obtener_detalle_partido(gid, incluir_plays=NFL_AUTO_PLAYS)
        if not det:
            continue
        db.nfl_raw.update_one({"fuenteId": gid},
            {"$set": {"fuenteId": gid, "capturado": det["capturado"], "raw": det["raw"]}}, upsert=True)
        db.nfl_partidos.update_one({"fuenteId": gid},
            {"$set": {"fuenteId": gid, "liga": "nfl", "deporte": "americano",
                      "capturado": det["capturado"], "resumen": det["resumen"],
                      "equipo_stats": det["equipo_stats"], "drives": det["drives"]}}, upsert=True)
        for j in det["jugadores"]:
            db.nfl_jugadores.update_one(
                {"fuenteId": gid, "jugadorId": j["jugadorId"], "categoria": j["categoria"]},
                {"$set": {**j, "fuenteId": gid, "liga": "nfl",
                          "tipo_temporada": doc.get("tipo_temporada")}}, upsert=True)
        if NFL_AUTO_PLAYS and det["plays"]:
            db.nfl_plays.delete_many({"fuenteId": gid})
            db.nfl_plays.insert_many([{**pl, "fuenteId": gid, "liga": "nfl"} for pl in det["plays"]])
        detalle_ok += 1
        if i < len(partidos) - 1:
            _t.sleep(1.0)

    try:
        _resolver_track(db)
    except Exception:
        pass
    return {"partidos": guardados, "detalle": detalle_ok}


async def _loop_nfl():
    """Tarea de fondo: sincroniza la NFL periódicamente."""
    await asyncio.sleep(30)  # respiro al arranque
    while True:
        try:
            res = await asyncio.to_thread(_sincronizar_nfl_auto)
            print(f"[nfl auto] {res}")
        except Exception as e:
            print(f"[nfl auto] error: {e}")
        await asyncio.sleep(NFL_INTERVALO_H * 3600)


@app.on_event("startup")
async def _arrancar_scheduler_nfl():
    if not ENABLE_INTERNAL_SCHEDULER:
        print("[nfl auto] scheduler interno desactivado")
        return
    asyncio.create_task(_loop_nfl())
    print(f"[nfl auto] scheduler activo: cada {NFL_INTERVALO_H}h, tipo temporada {NFL_TIPO_TEMPORADA}")
