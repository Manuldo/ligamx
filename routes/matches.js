import express from "express";
import MatchAnalysis from "../models/MatchAnalysis.js";
import { requireAuth, requirePro } from "../middleware/auth.js";
import { ah } from "../asyncHandler.js";
import { analizarPartido, partidosLiga } from "../lib/motor.js";

const router = express.Router();
const hoy = () => new Date().toISOString().slice(0, 10);
const LIMITE_DIARIO = Number(process.env.ANALISIS_LIMITE_DIARIO || 5);

// El id de partido del motor viene como "liga:local:visitante".
// Lo partimos para saber qué analizar.
function parseId(id) {
  const [liga, local, visitante] = String(id).split(":");
  return { liga: liga || "ligamx", local: local || "", visitante: visitante || "" };
}

// --- LISTA DE PARTIDOS (público) — AHORA DEL MOTOR ---
router.get("/", ah(async (req, res) => {
  const liga = req.query.liga || "ligamx";
  try {
    const data = await partidosLiga(liga, 60);
    const estado = req.query.estado || "todos";
    let partidos = data.partidos || [];
    if (estado === "proximos") partidos = partidos.filter(p => p.estado !== "finalizado");
    if (estado === "finalizados") partidos = partidos.filter(p => p.estado === "finalizado");
    // adaptamos al formato que el frontend espera (con _id = id del motor)
    res.json(partidos.map(p => ({
      _id: p.id, local: p.local, visitante: p.visitante,
      kickoff: p.inicio, estado: p.estado, jornada: p.jornada,
      golesLocal: p.golesLocal, golesVisitante: p.golesVisitante, liga: p.liga,
    })));
  } catch (e) {
    console.error("Error partidos:", e.message);
    res.status(502).json({ error: "El motor no responde" });
  }
}));

// --- ANÁLISIS (solo PRO, con cuota) — USA EL MOTOR ---
// El id del partido ("liga:local:visitante") viene en el body para evitar
// problemas con ":" y espacios en la URL.
router.post("/analizar", requireAuth, requirePro, ah(async (req, res) => {
  const { liga, local, visitante } = parseId(req.body.id || "");
  if (!local || !visitante) return res.status(400).json({ error: "Partido inválido" });

  const fecha = hoy();
  const claveCache = `${liga}:${local}:${visitante}`;

  // 1) Cache del día
  const cache = await MatchAnalysis.findOne({ partido: claveCache, fecha });
  if (cache) return res.json({ ...cache.toObject(), cacheado: true });

  // 2) Cuota diaria
  const u = req.user;
  if (u.analisisFecha !== fecha) { u.analisisFecha = fecha; u.analisisUsados = 0; }
  if (u.analisisUsados >= LIMITE_DIARIO) {
    return res.status(429).json({
      error: `Llegaste a tu límite de ${LIMITE_DIARIO} análisis por día`,
      usados: u.analisisUsados, limite: LIMITE_DIARIO,
    });
  }

  // 3) Llamar al motor (profundo porque es PRO)
  let motor;
  try {
    motor = await analizarPartido(local, visitante, liga, true);
  } catch (err) {
    console.error("Error motor:", err.message);
    return res.status(502).json({ error: "El motor de análisis no responde. Intenta de nuevo." });
  }

  // 4) Guardar en cache + descontar cuota
  const doc = await MatchAnalysis.create({
    matchId: undefined,   // ya no usamos ObjectId de Match (los datos vienen del motor)
    fecha,
    partido: claveCache,
    analisisMarkdown: motor.analisis_markdown || "",
    nivel: motor.nivel || "premium",
    resumen: (motor.analisis_markdown || "").slice(0, 300),
    generadoPor: u._id,
  });
  u.analisisUsados += 1;
  await u.save();

  res.json({ ...doc.toObject(), cacheado: false, usados: u.analisisUsados, limite: LIMITE_DIARIO });
}));

export default router;
