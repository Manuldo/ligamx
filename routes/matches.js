import express from "express";
import MatchAnalysis from "../models/MatchAnalysis.js";
import { requireAuth, requirePro } from "../middleware/auth.js";
import { ah } from "../asyncHandler.js";
import { analizarPartido, partidosLiga, jugadoresEquipo, alineacionesPartido, jornadasLiga, todasLasLigas, traerAlineacion } from "../lib/motor.js";

const router = express.Router();
const hoy = () => new Date().toISOString().slice(0, 10);

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
    const data = await partidosLiga(liga, 400, req.id);
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

  // Pickazoapp NO cachea: siempre le pide al MOTOR, que responde con su
  // propio cache (barato, no gasta IA) o regenera si se le fuerza.
  // Así pickazoapp siempre muestra lo que el motor tiene actualizado.
  let motor;
  try {
    motor = await analizarPartido(local, visitante, liga, true, !!req.body.forzar, req.id);
  } catch (err) {
    console.error("Error motor:", err.message);
    return res.status(502).json({ error: "El motor de análisis no responde. Intenta de nuevo." });
  }

  res.json({
    partido: `${local} vs ${visitante}`,
    analisisMarkdown: motor.analisis_markdown || "",
    nivel: motor.nivel || "premium",
    cacheado: !!motor.desde_cache,   // si el MOTOR lo tenía guardado
  });
}));

// --- DETALLE DEL PARTIDO: top jugadores de cada equipo (PRO) ---
router.post("/detalle", requireAuth, requirePro, ah(async (req, res) => {
  const { liga, local, visitante } = parseId(req.body.id || "");
  if (!local || !visitante) return res.status(400).json({ error: "Partido inválido" });
  try {
    const [jl, jv] = await Promise.all([
      jugadoresEquipo(liga, local, 5, req.id),
      jugadoresEquipo(liga, visitante, 5, req.id),
    ]);
    res.json({
      ok: true,
      local: { equipo: local, jugadores: jl.jugadores || [] },
      visitante: { equipo: visitante, jugadores: jv.jugadores || [] },
    });
  } catch (e) {
    console.error("Error detalle:", e.message);
    res.status(502).json({ error: "El motor no responde" });
  }
}));

// --- ALINEACIONES DE UN PARTIDO (PRO) ---
router.post("/alineaciones", requireAuth, requirePro, ah(async (req, res) => {
  const { liga, local, visitante } = parseId(req.body.id || "");
  if (!local || !visitante) return res.status(400).json({ error: "Partido inválido" });
  try {
    const ali = await alineacionesPartido(liga, local, visitante, req.id);
    res.json(ali);
  } catch (e) {
    console.error("Error alineaciones:", e.message);
    res.status(502).json({ error: "El motor no responde" });
  }
}));

// --- JORNADAS de una liga (para el selector) ---
router.get("/jornadas-liga", ah(async (req, res) => {
  const liga = req.query.liga || "ligamx";
  try {
    const data = await jornadasLiga(liga, req.id);
    res.json(data);
  } catch (e) {
    console.error("Error jornadas-liga:", e.message);
    res.json({ liga, jornadas: [] });
  }
}));

// --- TODAS LAS LIGAS juntas ---
router.get("/todas", ah(async (req, res) => {
  try {
    const data = await todasLasLigas(300, req.id);
    const partidos = (data.partidos || []).map(p => ({
      _id: p.id, local: p.local, visitante: p.visitante, liga: p.liga,
      ligaNombre: p.ligaNombre, kickoff: p.inicio, estado: p.estado,
      enVivo: p.enVivo, minuto: p.minuto,
      golesLocal: p.golesLocal, golesVisitante: p.golesVisitante, jornada: p.jornada,
    }));
    res.json(partidos);
  } catch (e) {
    console.error("Error todas:", e.message);
    res.json([]);
  }
}));

// --- TRAER alineación de UN partido (botón por juego, solo PRO) ---
router.post("/traer-alineacion", requireAuth, requirePro, ah(async (req, res) => {
  const { liga, local, visitante } = parseId(req.body.id || "");
  if (!local || !visitante) return res.status(400).json({ error: "Partido inválido" });
  try {
    const r = await traerAlineacion(liga, local, visitante, req.id);
    res.json(r);
  } catch (e) {
    console.error("Error traer-alineacion:", e.message);
    res.status(502).json({ error: "El motor no responde" });
  }
}));

export default router;
