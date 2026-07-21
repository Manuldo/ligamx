import express from "express";
import Match from "../models/Match.js";
import MatchAnalysis from "../models/MatchAnalysis.js";
import Pick from "../models/Pick.js";
import { requireAuth, requirePro, requireAdmin } from "../middleware/auth.js";
import { ah } from "../asyncHandler.js";

const router = express.Router();
const hoy = () => new Date().toISOString().slice(0, 10);
const LIMITE_DIARIO = Number(process.env.ANALISIS_LIMITE_DIARIO || 5);

// --- LISTA DE PARTIDOS (publico) ---
// ?estado=proximos | finalizados | todos
router.get("/", ah(async (req, res) => {
  const estado = req.query.estado || "todos";
  const q = {};
  if (estado === "proximos") q.estado = { $in: ["programado", "en_juego"] };
  if (estado === "finalizados") q.estado = "finalizado";

  const orden = estado === "finalizados" ? { kickoff: -1 } : { kickoff: 1 };
  const partidos = await Match.find(q).sort(orden).limit(60).lean();
  res.json(partidos);
}));

// --- ANALISIS DE UN PARTIDO (solo PRO, con cuota) ---
router.post("/:id/analizar", requireAuth, requirePro, ah(async (req, res) => {
  const match = await Match.findById(req.params.id);
  if (!match) return res.status(404).json({ error: "Partido no encontrado" });

  const fecha = hoy();

  // 1) Cache: si ya existe analisis de hoy para este partido, se reusa.
  //    No gasta cuota ni llamada a la API.
  const cache = await MatchAnalysis.findOne({ matchId: match._id, fecha });
  if (cache) return res.json({ ...cache.toObject(), cacheado: true });

  // 2) Cuota diaria por usuario
  const u = req.user;
  if (u.analisisFecha !== fecha) { u.analisisFecha = fecha; u.analisisUsados = 0; }
  if (u.analisisUsados >= LIMITE_DIARIO) {
    return res.status(429).json({
      error: `Llegaste a tu límite de ${LIMITE_DIARIO} análisis por día`,
      usados: u.analisisUsados, limite: LIMITE_DIARIO
    });
  }

  if (!process.env.ANTHROPIC_API_KEY) {
    return res.status(503).json({ error: "El análisis no está disponible ahora" });
  }

  // 3) Contexto: picks que TU ya publicaste de este partido, si los hay.
  const nombre = `${match.local} vs ${match.visitante}`;
  const publicados = await Pick.find({
    fecha: match.fecha, activo: true,
    partido: { $regex: match.local, $options: "i" }
  }).lean();

  const ctx = publicados.length
    ? `Picks ya publicados por la casa para este partido:\n` +
      publicados.map(p => `- ${p.mercado} @ ${p.momio} (prob. estimada ${(p.probEstimada*100).toFixed(0)}%)`).join("\n")
    : "No hay picks publicados para este partido.";

  const prompt = `Eres analista de apuestas de Liga MX. Directo, sin humo, español de México.

Partido: ${nombre}
Fecha: ${match.fecha}
${match.jornada ? "Jornada: " + match.jornada : ""}

${ctx}

Da entre 1 y 3 mercados que valga la pena revisar para este partido.
IMPORTANTE: no inventes estadísticas ni números concretos que no tengas.
Si no tienes datos suficientes, dilo en el resumen y sé conservador.
Las probabilidades que des son estimaciones cualitativas, no cálculo con datos duros.

Responde SOLO JSON válido, sin markdown:
{"resumen":"2-3 frases sobre el partido y dónde puede estar el valor",
 "sugerencias":[{"mercado":"nombre del mercado","momioSugerido":1.85,"probEstimada":0.6,"razon":"una frase"}]}`;

  let data;
  try {
    const r = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": process.env.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01"
      },
      body: JSON.stringify({
        model: "claude-haiku-4-5-20251001",
        max_tokens: 700,
        messages: [{ role: "user", content: prompt }]
      })
    });
    const j = await r.json();
    const txt = (j.content?.[0]?.text || "").replace(/```json|```/g, "").trim();
    data = JSON.parse(txt);
  } catch (err) {
    console.error("Error analisis partido:", err.message);
    return res.status(502).json({ error: "No se pudo generar el análisis. Intenta de nuevo." });
  }

  // 4) Guarda y descuenta cuota
  const doc = await MatchAnalysis.create({
    matchId: match._id,
    fecha,
    partido: nombre,
    resumen: data.resumen || "",
    sugerencias: (data.sugerencias || []).slice(0, 3),
    aviso: "Estimación generada por IA, no sustituye el análisis publicado.",
    generadoPor: u._id
  });

  u.analisisUsados += 1;
  await u.save();

  res.json({ ...doc.toObject(), cacheado: false, usados: u.analisisUsados, limite: LIMITE_DIARIO });
}));

// --- ADMIN: sincronizar partidos (desde tu scraper local) ---
router.post("/sync", requireAdmin, ah(async (req, res) => {
  const partidos = req.body.partidos || [];
  let creados = 0, actualizados = 0;

  for (const p of partidos) {
    if (!p.local || !p.visitante || !p.fecha) continue;
    const r = await Match.updateOne(
      { local: p.local, visitante: p.visitante, fecha: p.fecha },
      {
        $set: {
          extId: p.extId || null,
          kickoff: p.kickoff ? new Date(p.kickoff) : null,
          golesLocal: p.golesLocal ?? null,
          golesVisitante: p.golesVisitante ?? null,
          estado: p.estado || "programado",
          jornada: p.jornada || "",
          actualizado: new Date()
        }
      },
      { upsert: true }
    );
    if (r.upsertedCount) creados++; else if (r.modifiedCount) actualizados++;
  }

  res.json({ ok: true, recibidos: partidos.length, creados, actualizados });
}));

export default router;
