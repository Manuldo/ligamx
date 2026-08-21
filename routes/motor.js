// routes/motor.js — Conecta pickazoapp con el motor de análisis Pickazo MX
// Usa TUS middlewares reales: requireAuth, requirePro, requireAdmin.

import express from "express";
import { analizarPartido, picksDelDia, parlayMaestro, parlayComunidad, guardarBoletoComunidad, likeBoleto } from "../lib/motor.js";
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

/**
 * GET /api/motor/maestro?ligas=ligamx,laliga
 * Parlay Maestro: picks de varias ligas. GRATIS (el gancho).
 */
router.get("/maestro", async (req, res) => {
  try {
    const ligas = req.query.ligas || "ligamx";
    const data = await parlayMaestro(ligas, 4, req.id);
    res.json(data);
  } catch (e) {
    console.error("Error maestro:", e.message);
    res.status(502).json({ error: "El motor no responde ahora" });
  }
});

/**
 * GET /api/motor/comunidad — parlays más gustados de la banda.
 */
router.get("/comunidad", async (req, res) => {
  try {
    const data = await parlayComunidad(Number(req.query.top) || 8, req.id);
    res.json(data);
  } catch (e) {
    console.error("Error comunidad:", e.message);
    res.status(502).json({ error: "El motor no responde ahora" });
  }
});

/**
 * POST /api/motor/guardar-boleto — publicar boleto a la comunidad (login).
 */
router.post("/guardar-boleto", requireAuth, async (req, res) => {
  try {
    const { nombre, patas, liga } = req.body;
    if (!Array.isArray(patas) || !patas.length) {
      return res.status(400).json({ error: "El boleto necesita al menos una pata" });
    }
    const autor = req.user?.email ? req.user.email.split("@")[0] : "Anónimo";
    const data = await guardarBoletoComunidad({ nombre, patas, liga, autor }, req.id);
    res.json(data);
  } catch (e) {
    console.error("Error guardar-boleto:", e.message);
    res.status(502).json({ error: "El motor no responde ahora" });
  }
});

/**
 * POST /api/motor/like-boleto — dar like (login).
 */
router.post("/like-boleto", requireAuth, async (req, res) => {
  try {
    const { id } = req.body;
    if (!id) return res.status(400).json({ error: "Falta id" });
    const data = await likeBoleto(id, req.id);
    res.json(data);
  } catch (e) {
    console.error("Error like-boleto:", e.message);
    res.status(502).json({ error: "El motor no responde ahora" });
  }
});

export default router;
