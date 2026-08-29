import express from "express";
import { partidosLiga, tablaLiga, jugadoresLiga } from "../lib/motor.js";
import { requireAuth } from "../middleware/auth.js";
import { ah } from "../asyncHandler.js";

const router = express.Router();
// --- TABLA (requiere login, como en tu app) ---
router.get("/tabla", requireAuth, ah(async (req, res) => {
  try {
    const liga = req.query.liga || "ligamx";
    const data = await tablaLiga(liga, req.id);
    // adaptar al formato que espera tu frontend
    const tabla = (data.tabla || []).map((f, i) => ({
      pos: f.pos ?? f.posicion ?? i + 1,
      equipo: f.equipo || f.nombre || "",
      color: null,
      jugados: f.jugados ?? f.pj ?? 0,
      ganados: f.ganados ?? f.g ?? 0,
      empatados: f.empatados ?? f.e ?? 0,
      perdidos: f.perdidos ?? f.p ?? 0,
      golesFavor: f.golesFavor ?? f.gf ?? 0,
      golesContra: f.golesContra ?? f.gc ?? 0,
      dif: (f.golesFavor ?? f.gf ?? 0) - (f.golesContra ?? f.gc ?? 0),
      puntos: f.puntos ?? f.pts ?? 0,
      forma: f.forma || [],
    }));
    res.json(tabla);
  } catch (e) {
    console.error("Error tabla:", e.message);
    res.json([]);
  }
}));

// --- JORNADAS (requiere login) ---
router.get("/jornadas", requireAuth, ah(async (req, res) => {
  try {
    const liga = req.query.liga || "ligamx";
    const data = await partidosLiga(liga, 400, req.id);
    const partidos = (data.partidos || []).map(p => ({
      _id: p.id, local: p.local, visitante: p.visitante,
      kickoff: p.inicio, estado: p.estado, fecha: (p.inicio || "").slice(0, 10),
      golesLocal: p.golesLocal, golesVisitante: p.golesVisitante, jornada: p.jornada,
      enVivo: p.enVivo, minuto: p.minuto,
    }));
    // agrupamos: los no finalizados = jornada actual; los finalizados = previas
    const prox = partidos.filter(p => p.estado !== "finalizado");
    const fin = partidos.filter(p => p.estado === "finalizado");
    res.json({
      actual: prox.length ? { jornada: "Próximos partidos", partidos: prox } : null,
      previas: fin.length ? [{ jornada: "Jugados", partidos: fin }] : [],
    });
  } catch (e) {
    console.error("Error jornadas:", e.message);
    res.json({ actual: null, previas: [] });
  }
}));

// --- JUGADORES (requiere login) ---
router.get("/jugadores", requireAuth, ah(async (req, res) => {
  try {
    const liga = req.query.liga || "ligamx";
    const data = await jugadoresLiga(liga, 60, req.id);
    const jugadores = (data.jugadores || []).map(j => ({
      nombre: j.nombre || "",
      equipo: j.equipo || "",
      posicion: j.posicion || "",
      partidos: j.partidos || 0,
      rating: j.rating || null,
      goles: j.goles || 0,
      asistencias: j.asistencias || 0,
      xG: j.xg || j.xG || null,
      minutos: j.minutos || 0,
      bench: null,
    }));
    res.json({ jugadores });
  } catch (e) {
    console.error("Error jugadores:", e.message);
    res.json({ jugadores: [] });
  }
}));

export default router;
