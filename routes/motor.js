// routes/motor.js — Rutas que conectan pickazoapp con el motor de análisis
// Sigue el mismo patrón que tus otras rutas (auth.js, picks.js, etc.)

import express from "express";
import { analizarPartido, picksDelDia } from "../lib/motor.js";
import { requireAuth } from "../middleware/auth.js"; // tu middleware de auth existente

const router = express.Router();

/**
 * GET /api/motor/picks-hoy?liga=ligamx
 * GRATIS: los picks del día. Cualquiera los ve (el gancho).
 */
router.get("/picks-hoy", async (req, res) => {
  try {
    const liga = req.query.liga || "ligamx";
    const data = await picksDelDia(liga, 8);
    res.json(data);
  } catch (e) {
    console.error("Error picks-hoy:", e.message);
    res.status(502).json({ error: "El motor de análisis no responde ahora" });
  }
});

/**
 * POST /api/motor/analizar
 * Body: { local, visitante, liga }
 * PRO: análisis a fondo. Requiere estar logueado Y ser PRO.
 * Los no-PRO reciben el análisis básico (o un mensaje para hacerse PRO).
 */
router.post("/analizar", requireAuth, async (req, res) => {
  try {
    const { local, visitante, liga } = req.body;
    if (!local || !visitante) {
      return res.status(400).json({ error: "Faltan equipos" });
    }

    // ¿el usuario es PRO? (ajusta según cómo marques PRO en tu modelo de usuario)
    const esPro = req.user && (req.user.plan === "pro" || req.user.isPro === true);

    // PRO recibe análisis profundo; gratis recibe el básico
    const data = await analizarPartido(local, visitante, liga || "ligamx", esPro);

    res.json({
      ...data,
      esPro,
      // si no es PRO, el front puede mostrar un CTA "Hazte PRO para análisis a fondo"
      mensaje_pro: esPro ? null : "Hazte PRO para el análisis a fondo con más detalle",
    });
  } catch (e) {
    console.error("Error analizar:", e.message);
    res.status(502).json({ error: "El motor de análisis no responde ahora" });
  }
});

export default router;
