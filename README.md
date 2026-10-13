# Modelo de Apuestas RAG — Manuldo

Migración de tu modelo de apuestas a **Python + RAG + Docker**, desplegable en Railway.

> "El que estudia cobra. El que adivina reza." — RAG es la parte de *estudiar*.

---

## Arquitectura en 3 capas

| Capa | Qué hace | Dónde vive | ¿RAG? |
|------|----------|-----------|-------|
| **1. Lógica dura** | Calcula tus 10 índices, aplica reglas duras (nunca under de goles, nunca línea fina), shrink de córners, desbalance motivacional | `models/betting.py` | ❌ Código puro |
| **2. Recuperación** | Guarda y recupera aprendizajes, post-mortems, perfiles de equipos por similitud | `rag/` + ChromaDB | ✅ Sí |
| **3. Generación** | El LLM recibe índices + contexto recuperado y produce el análisis | `main.py` (Anthropic) | ✅ Sí |

**Regla mental clave:** RAG *recupera* conocimiento por parecido. Nunca metas reglas duras al vector store — una regla se aplica SIEMPRE, no "cuando se parezca". Las reglas van en código.

**Embeddings:** API de OpenAI (`text-embedding-3-small`), ~$0.02/millón de tokens. No uses locales en producción: Railway no tiene GPU y meterían GB de PyTorch a la imagen.

---

## Estructura

```
proyecto-rag/
├── docker-compose.yml       # levanta todo local con un comando
├── .env.example             # copia a .env y pon tus llaves
├── .gitignore
└── backend/
    ├── Dockerfile           # receta de la imagen
    ├── requirements.txt
    ├── main.py              # FastAPI: orquesta el flujo RAG
    ├── rag/
    │   ├── embeddings.py    # texto -> vector (OpenAI)
    │   ├── vectorstore.py   # guardar/buscar (ChromaDB)
    │   └── retriever.py     # junta embeddings + vectorstore
    ├── models/
    │   └── betting.py       # TU lógica dura (rellena los stubs)
    └── data/
        └── ingest.py        # carga conocimiento a la memoria
```

---

## Cómo correrlo

### Opción A — Local sin Docker (para aprender rápido)

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Pon tus llaves como variables de entorno
export OPENAI_API_KEY=sk-...      # Windows: set OPENAI_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...

# Puebla la memoria (una vez)
python -m data.ingest

# Levanta el server
uvicorn main:app --reload --port 3131
```

Abre http://localhost:3131 — deberías ver el estado y el número de docs.

### Opción B — Con Docker (lo que aprendes para Railway)

```bash
cp .env.example .env             # y edita .env con tus llaves reales
docker compose up --build
```

Docker construye la imagen, levanta el contenedor y monta el volumen
de ChromaDB. Todo en un comando.

### Probar el análisis

```bash
curl -X POST http://localhost:3131/analizar \
  -H "Content-Type: application/json" \
  -d '{"local":"Barcelona","visitante":"Alaves","liga":"la_liga","contexto_extra":{"nota":"Barca ya campeon"}}'
```

---

## Deploy en Railway

Igual que tus otros proyectos, pero ahora con Docker:

1. Sube el repo a GitHub (el `.gitignore` ya protege `.env` y `chroma_data`).
2. En Railway: New Project → Deploy from GitHub → elige el repo.
3. Railway detecta el `Dockerfile` automáticamente.
4. En **Variables**: agrega `OPENAI_API_KEY` y `ANTHROPIC_API_KEY`.
5. En **Settings → Volumes**: monta un volumen en `/data/chroma`
   (así ChromaDB persiste, igual que tus volúmenes de node:sqlite).
6. Después del primer deploy, corre la ingesta una vez
   (con un servicio one-off o un endpoint temporal).

---

## Qué falta migrar (tus TODOs)

- `models/betting.py` → `calcular_indices()` está como stub.
  Porta aquí tu lógica real de los 10 índices desde Manuldo 2026 /
  Tipster 2025.
- `data/ingest.py` → reemplaza el conocimiento de ejemplo con tu base
  real: todos tus aprendizajes, post-mortems y perfiles.
- Conecta tu scraper de FotMob (`__NEXT_DATA__`) para alimentar
  `contexto_extra` con form, lineups y H2H reales.
- Considera un endpoint `/aprender` que meta post-mortems nuevos al
  vector store después de cada jornada (memoria que crece sola).

---

## Ruta de aprendizaje (orden sugerido)

**Semana 1 — Python moderno**
- venv, pip, requirements.txt
- Type hints y Pydantic (validación tipada)
- FastAPI básico: rutas GET/POST, `uvicorn --reload`
- Async/await (viniendo de Node se te hace fácil)

**Semana 2 — RAG a fondo**
- Qué es un embedding y por qué la búsqueda por similitud
- ChromaDB: colecciones, upsert, query, metadata/filtros
- Chunking: cómo partir documentos largos
- El flujo Retrieve → Augment → Generate completo

**Semana 3 — Docker**
- Imágenes vs contenedores
- Dockerfile capa por capa (y por qué el orden importa para el cache)
- docker-compose y volúmenes
- Variables de entorno y networking entre contenedores

**Semana 4 — Integración**
- Migrar tu lógica real de índices
- Poblar la memoria con tu base de conocimiento
- Deploy en Railway con Docker + volumen
- Endpoint de aprendizaje continuo

Concepto que más te va a costar: **entender que RAG no razona, recupera.**
El razonamiento lo hace el LLM con lo que le das. RAG solo decide *qué*
darle. Tus reglas duras y tus índices siguen siendo código.
```
