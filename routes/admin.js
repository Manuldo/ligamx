import express from "express";
import Pick from "../models/Pick.js";
import Vote from "../models/Vote.js";
import { requireAdmin } from "../middleware/auth.js";
import { ah } from "../asyncHandler.js";

const router = express.Router();

function hoy() {
  return new Date().toISOString().slice(0, 10);
}

// Tu disparas esto desde el panel con el pick que TÚ decidiste publicar a
// mano ("Pick del equipo"). Es una llamada discrecional, no el motor
// algorítmico: ya no exigimos momio >= 1.70 ni edge > 0 para aceptarlo —
// esos filtros eran del diseño original (candidatos algorítmicos de donde
// se escogía el top 3) y bloqueaban cualquier pick manual razonable
// (favoritos con momio bajo, o sin una "probEstimada" bien calculada).
// Si mandas probEstimada, igual se calcula edge solo para mostrarlo en la
// tarjeta; si no, simplemente no se muestra ventaja.
//
// body: {
//   fecha?: "YYYY-MM-DD",
//   candidatos: [{ partido, mercado, momio, probEstimada?, verdicto, analisis, tier }]
// }
router.post("/publish", requireAdmin, ah(async (req, res) => {
  const fecha = req.body.fecha || hoy();
  const candidatos = req.body.candidatos || [];

  const validos = candidatos
    .filter((c) => c.partido && c.mercado && c.momio > 0)
    .map((c) => {
      const probImplicita = 1 / c.momio;
      const tieneProb = c.probEstimada != null && c.probEstimada > 0;
      return {
        ...c,
        probImplicita,
        edge: tieneProb ? c.probEstimada - probImplicita : null,
      };
    })
    // Si varios traen probEstimada, los de mayor ventaja van primero;
    // los que no, se quedan en el orden en que los capturaste.
    .sort((a, b) => (b.edge ?? -Infinity) - (a.edge ?? -Infinity));

  // Tope razonable para no saturar la tarjeta "Pick del equipo" de la app.
  const top = validos.slice(0, 5);
  if (top.length === 0) {
    return res.status(422).json({ error: "Llena al menos un candidato con partido, mercado y momio." });
  }

  // Desactiva los picks previos de la fecha para republicar limpio
  await Pick.updateMany({ fecha }, { activo: false });

  // Pick Lock: el primero de la lista que publicaste — tú decides cuál es
  // el más importante con el orden en que lo capturas.
  const creados = await Pick.insertMany(
    top.map((c, i) => ({
      fecha,
      partido: c.partido,
      mercado: c.mercado,
      momio: c.momio,
      probEstimada: c.probEstimada ?? null,
      probImplicita: c.probImplicita,
      edge: c.edge,
      verdicto: c.verdicto || "MANDAR",
      analisis: c.analisis || "",
      tier: c.tier === "pro" ? "pro" : "public",
      pickLock: i === 0,
      votos: 0,
      activo: true
    }))
  );

  res.json({ ok: true, fecha, publicados: creados.length, picks: creados });
}));

// --- LISTA DE PICKS (para el panel: ver y cerrar resultados) ---
router.get("/picks", requireAdmin, ah(async (req, res) => {
  const q = {};
  if (req.query.fecha) q.fecha = req.query.fecha;
  if (req.query.pendientes === "1") q.resultado = "pendiente";
  const picks = await Pick.find(q).sort({ fecha: -1, edge: -1 }).limit(120).lean();
  res.json(picks);
}));

// --- VERIFICAR LLAVE (para el login del panel) ---
router.get("/ping", requireAdmin, (req, res) => res.json({ ok: true }));

export default router;
