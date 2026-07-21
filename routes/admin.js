import express from "express";
import Pick from "../models/Pick.js";
import Vote from "../models/Vote.js";
import { requireAdmin } from "../middleware/auth.js";
import { ah } from "../asyncHandler.js";

const router = express.Router();

function hoy() {
  return new Date().toISOString().slice(0, 10);
}

// Tu disparas esto desde el backend con tu analisis ya hecho.
// Recibe candidatos, filtra momio >= 1.70, ordena por edge (valor real),
// toma Top 3 y los publica. Reparte tier public/pro segun mandes.
//
// body: {
//   fecha?: "YYYY-MM-DD",
//   candidatos: [{ partido, mercado, momio, probEstimada, verdicto, analisis, tier }]
// }
router.post("/publish", requireAdmin, ah(async (req, res) => {
  const fecha = req.body.fecha || hoy();
  const candidatos = req.body.candidatos || [];

  // Filtro de precio: piso 1.70 decimal
  const conValor = candidatos
    .filter((c) => c.momio >= 1.7)
    .map((c) => {
      const probImplicita = 1 / c.momio;
      return { ...c, probImplicita, edge: c.probEstimada - probImplicita };
    })
    // Valor real = edge positivo, ordenado de mayor a menor
    .filter((c) => c.edge > 0)
    .sort((a, b) => b.edge - a.edge);

  // Top 3 de mayor valor
  const top3 = conValor.slice(0, 3);
  if (top3.length === 0) {
    return res.status(422).json({ error: "Ningun candidato con valor positivo y momio >= 1.70" });
  }

  // Desactiva los picks previos de la fecha para republicar limpio
  await Pick.updateMany({ fecha }, { activo: false });

  const creados = await Pick.insertMany(
    top3.map((c) => ({
      fecha,
      partido: c.partido,
      mercado: c.mercado,
      momio: c.momio,
      probEstimada: c.probEstimada,
      probImplicita: c.probImplicita,
      edge: c.edge,
      verdicto: c.verdicto || "MANDAR",
      analisis: c.analisis || "",
      tier: c.tier === "pro" ? "pro" : "public",
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
