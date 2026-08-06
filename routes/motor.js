// routes/motor.js — Conecta pickazoapp con el motor de análisis Pickazo MX
// Usa TUS middlewares reales: requireAuth, requirePro, requireAdmin.

import express from "express";
import { analizarPartido, picksDelDia } from "../lib/motor.js";
import { requireAuth, requirePro, requireAdmin } from "../middleware/auth.js";

const router = express.Router();

/**
 * GET /api/motor/picks-hoy?liga=ligamx
 * GRATIS — el gancho. Cualquiera ve los picks del día, sin login.
 */
router.get("/picks-hoy", async (req, res) => {
  try {
    const liga = req.query.liga || "ligamx";
    const data = await picksDelDia(liga, 8, req.id);
    res.json(data);
  } catch (e) {
    console.error("Error picks-hoy:", e.message);
    res.status(502).json({ error: "El motor de análisis no responde ahora" });
  }
});

/**
 * POST /api/motor/analizar-basico
 * Análisis BÁSICO — requiere login, NO ser PRO. Para que el gratis pruebe.
 */
router.post("/analizar-basico", requireAuth, async (req, res) => {
  try {
    const { local, visitante, liga } = req.body;
    if (!local || !visitante) return res.status(400).json({ error: "Faltan equipos" });
    const data = await analizarPartido(local, visitante, liga || "ligamx", false, false, req.id);
    res.json({
      ...data,
      mensaje_pro: "Hazte PRO para el análisis a fondo con todos los modelos y detalle",
    });
  } catch (e) {
    console.error("Error analizar-basico:", e.message);
    res.status(502).json({ error: "El motor de análisis no responde ahora" });
  }
});

/**
 * POST /api/motor/analizar
 * Análisis A FONDO (PRO) — requirePro corta a quien no pagó.
 */
router.post("/analizar", requireAuth, requirePro, async (req, res) => {
  try {
    const { local, visitante, liga } = req.body;
    if (!local || !visitante) return res.status(400).json({ error: "Faltan equipos" });
    const data = await analizarPartido(local, visitante, liga || "ligamx", true, false, req.id);
    res.json(data);
  } catch (e) {
    console.error("Error analizar:", e.message);
    res.status(502).json({ error: "El motor de análisis no responde ahora" });
  }
});

/**
 * POST /api/motor/generar-picks-dia
 * Solo ADMIN (tú). Header: x-admin-key: TU_ADMIN_KEY
 */
router.post("/generar-picks-dia", requireAdmin, async (req, res) => {
  try {
    const liga = req.body.liga || "ligamx";
    const data = await picksDelDia(liga, 12, req.id);
    res.json({ ok: true, generados: data });
  } catch (e) {
    console.error("Error generar-picks-dia:", e.message);
    res.status(502).json({ error: "El motor no responde ahora" });
  }
});

export default router;
