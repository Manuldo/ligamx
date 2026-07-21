import express from "express";
import Standing from "../models/Standing.js";
import PlayerStat from "../models/PlayerStat.js";
import Match from "../models/Match.js";
import { requireAuth, requireAdmin } from "../middleware/auth.js";
import { ah } from "../asyncHandler.js";

const router = express.Router();

// Todo el contenido de liga requiere cuenta.
// (El muro de PRO aplica aparte, sobre los picks.)

// --- TABLA DE POSICIONES ---
router.get("/tabla", requireAuth, ah(async (req, res) => {
  const torneo = req.query.torneo || "Apertura 2026";
  const tabla = await Standing.find({ torneo }).sort({ pos: 1 }).lean();
  res.json(tabla);
}));

// --- JUGADORES CON BENCHMARK ---
// El benchmark compara a cada jugador contra el promedio de SU posicion.
// Un rating de 7.2 significa poco solo; comparado con el 6.8 promedio
// de los delanteros, ya dice algo.
router.get("/jugadores", requireAuth, ah(async (req, res) => {
  const torneo = req.query.torneo || "Apertura 2026";
  const orden = req.query.orden || "rating";
  const pos = req.query.pos;

  const q = { torneo };
  if (pos) q.posicion = pos;

  const campos = { rating: -1, goles: -1, asistencias: -1, xG: -1, minutos: -1 };
  const sort = campos[orden] ? { [orden]: -1 } : { rating: -1 };

  const [jugadores, todos] = await Promise.all([
    PlayerStat.find(q).sort(sort).limit(40).lean(),
    PlayerStat.find({ torneo }).lean()
  ]);

  // Promedios por posicion, solo con jugadores que tengan minutos reales
  const prom = {};
  for (const p of ["POR", "DEF", "MED", "DEL"]) {
    const g = todos.filter(x => x.posicion === p && x.minutos > 90);
    if (!g.length) continue;
    const avg = (k) => Number((g.reduce((a, x) => a + (x[k] || 0), 0) / g.length).toFixed(2));
    prom[p] = { rating: avg("rating"), goles: avg("goles"),
                asistencias: avg("asistencias"), xG: avg("xG"), minutos: avg("minutos") };
  }

  // Cada jugador lleva su comparacion contra el promedio de su posicion
  const conBench = jugadores.map(j => {
    const base = prom[j.posicion];
    const cmp = (k) => base && base[k] ? Number(((j[k] - base[k]) / base[k]).toFixed(3)) : null;
    return {
      ...j,
      bench: base ? {
        rating: cmp("rating"), goles: cmp("goles"),
        asistencias: cmp("asistencias"), xG: cmp("xG"),
        promedioPos: base
      } : null
    };
  });

  res.json({ jugadores: conBench, promedios: prom });
}));

// --- JORNADAS: la de la semana + las previas ---
router.get("/jornadas", requireAuth, ah(async (req, res) => {
  const partidos = await Match.find({}).sort({ kickoff: 1 }).lean();

  const porJornada = {};
  for (const m of partidos) {
    const j = m.jornada || "Sin jornada";
    (porJornada[j] = porJornada[j] || []).push(m);
  }

  // La jornada "actual" es la primera que tenga algun partido sin terminar
  const nombres = Object.keys(porJornada);
  let actual = null;
  for (const j of nombres) {
    if (porJornada[j].some(m => m.estado !== "finalizado")) { actual = j; break; }
  }
  if (!actual && nombres.length) actual = nombres[nombres.length - 1];

  const previas = nombres
    .filter(j => j !== actual)
    .map(j => ({ jornada: j, partidos: porJornada[j] }))
    .reverse();

  res.json({
    actual: actual ? { jornada: actual, partidos: porJornada[actual] } : null,
    previas
  });
}));

// --- ADMIN: cargar tabla ---
router.post("/tabla/sync", requireAdmin, ah(async (req, res) => {
  const torneo = req.body.torneo || "Apertura 2026";
  const filas = req.body.tabla || [];
  let n = 0;
  for (const f of filas) {
    if (!f.equipo) continue;
    await Standing.updateOne(
      { torneo, equipo: f.equipo },
      { $set: {
        abrev: f.abrev || f.equipo.slice(0, 3).toUpperCase(),
        color: f.color || "#605d5d",
        pos: f.pos ?? 0, jugados: f.jugados ?? 0,
        ganados: f.ganados ?? 0, empatados: f.empatados ?? 0, perdidos: f.perdidos ?? 0,
        golesFavor: f.golesFavor ?? 0, golesContra: f.golesContra ?? 0,
        dif: (f.golesFavor ?? 0) - (f.golesContra ?? 0),
        puntos: f.puntos ?? 0, forma: f.forma || [], actualizado: new Date()
      } },
      { upsert: true }
    );
    n++;
  }
  res.json({ ok: true, filas: n });
}));

// --- ADMIN: cargar jugadores ---
router.post("/jugadores/sync", requireAdmin, ah(async (req, res) => {
  const torneo = req.body.torneo || "Apertura 2026";
  const js = req.body.jugadores || [];
  let n = 0;
  for (const j of js) {
    if (!j.nombre || !j.equipo) continue;
    await PlayerStat.updateOne(
      { torneo, nombre: j.nombre, equipo: j.equipo },
      { $set: {
        abrevEquipo: j.abrevEquipo || j.equipo.slice(0, 3).toUpperCase(),
        posicion: j.posicion || "MED", dorsal: j.dorsal ?? null,
        partidos: j.partidos ?? 0, minutos: j.minutos ?? 0,
        goles: j.goles ?? 0, asistencias: j.asistencias ?? 0,
        xG: j.xG ?? 0, rating: j.rating ?? 0, actualizado: new Date()
      } },
      { upsert: true }
    );
    n++;
  }
  res.json({ ok: true, jugadores: n });
}));

export default router;
