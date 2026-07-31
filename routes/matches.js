import express from "express";
import Match from "../models/Match.js";
import MatchAnalysis from "../models/MatchAnalysis.js";
import Pick from "../models/Pick.js";
import { requireAuth, requirePro, requireAdmin } from "../middleware/auth.js";
import { ah } from "../asyncHandler.js";
import { analizarPartido } from "../lib/motor.js";   // ← NUEVO: puente al motor

const router = express.Router();
const hoy = () => new Date().toISOString().slice(0, 10);
const LIMITE_DIARIO = Number(process.env.ANALISIS_LIMITE_DIARIO || 5);

// Mapea el nombre de liga de pickazoapp al slug que usa el motor.
// Si solo manejas Liga MX, se queda en "ligamx". Amplía si agregas ligas.
function ligaSlug(match) {
  const l = (match.liga || match.competicion || "").toLowerCase();
  if (l.includes("premier")) return "premier";
  if (l.includes("liga") && l.includes("esp")) return "laliga";
  if (l.includes("serie")) return "seriea";
  if (l.includes("bundes")) return "bundesliga";
  if (l.includes("ligue")) return "ligue1";
  return "ligamx"; // por defecto
}

// --- LISTA DE PARTIDOS (publico) ---
router.get("/", ah(async (req, res) => {
  const estado = req.query.estado || "todos";
  const q = {};
  if (estado === "proximos") q.estado = { $in: ["programado", "en_juego"] };
  if (estado === "finalizados") q.estado = "finalizado";
  const orden = estado === "finalizados" ? { kickoff: -1 } : { kickoff: 1 };
  const partidos = await Match.find(q).sort(orden).limit(60).lean();
  res.json(partidos);
}));

// --- ANALISIS DE UN PARTIDO (solo PRO, con cuota) — AHORA USA EL MOTOR ---
router.post("/:id/analizar", requireAuth, requirePro, ah(async (req, res) => {
  const match = await Match.findById(req.params.id);
  if (!match) return res.status(404).json({ error: "Partido no encontrado" });

  const fecha = hoy();

  // 1) Cache: si ya existe análisis de hoy, se reusa (no gasta cuota ni motor).
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

  // 3) Llamar al MOTOR PYTHON (sistema Manuldo: 10 índices, reglas duras, etc.)
  //    profundo=true porque es un usuario PRO.
  let motor;
  try {
    motor = await analizarPartido(match.local, match.visitante, ligaSlug(match), true);
  } catch (err) {
    console.error("Error motor:", err.message);
    return res.status(502).json({ error: "El motor de análisis no responde. Intenta de nuevo." });
  }

  // 4) Guardar en cache y descontar cuota.
  //    Guardamos el markdown completo del motor + un resumen corto.
  const nombre = `${match.local} vs ${match.visitante}`;
  const doc = await MatchAnalysis.create({
    matchId: match._id,
    fecha,
    partido: nombre,
    analisisMarkdown: motor.analisis_markdown || "",   // ← el análisis completo
    nivel: motor.nivel || "premium",
    resumen: (motor.analisis_markdown || "").slice(0, 300), // compat
    sugerencias: [],  // el motor ya trae todo en el markdown
    aviso: "Análisis generado por el motor Pickazo MX.",
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
      { $set: {
          extId: p.extId || null,
          kickoff: p.kickoff ? new Date(p.kickoff) : null,
          golesLocal: p.golesLocal ?? null,
          golesVisitante: p.golesVisitante ?? null,
          estado: p.estado || "programado",
          jornada: p.jornada || "",
          liga: p.liga || "ligamx",
          actualizado: new Date()
      } },
      { upsert: true }
    );
    if (r.upsertedCount) creados++; else if (r.modifiedCount) actualizados++;
  }
  res.json({ ok: true, recibidos: partidos.length, creados, actualizados });
}));

export default router;
