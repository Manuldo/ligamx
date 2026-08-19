import express from "express";
import { trackRecord, resolverTrack } from "../lib/motor.js";
import { requireAuth, requirePro, requireAdmin } from "../middleware/auth.js";
import { ah } from "../asyncHandler.js";

const router = express.Router();

// RESUMEN PÚBLICO: efectividad, aciertos, racha. Sin historial detallado.
// Es el gancho de credibilidad: cualquiera puede verlo.
router.get("/resumen", ah(async (req, res) => {
  try {
    const liga = String(req.query.liga || "");
    const d = await trackRecord(liga, 0, req.id);
    res.json({
      total: d.total || 0,
      aciertos: d.aciertos || 0,
      fallos: d.fallos || 0,
      tasaAcierto: d.tasaAcierto,
      unidades: d.unidades,
      roi: d.roi,
      rachaActual: d.rachaActual || 0,
    });
  } catch (e) {
    res.status(502).json({ error: "El motor no responde" });
  }
}));

// DETALLE (PRO): resumen + historial completo con cada pick y su resultado.
router.get("/detalle", requireAuth, requirePro, ah(async (req, res) => {
  try {
    const liga = String(req.query.liga || "");
    const limite = Math.min(Number(req.query.limite) || 50, 200);
    const d = await trackRecord(liga, limite, req.id);
    res.json(d);
  } catch (e) {
    res.status(502).json({ error: "El motor no responde" });
  }
}));

// Disparar resolución manual (admin). Normalmente el motor lo hace solo.
router.post("/resolver", requireAuth, requireAdmin, ah(async (req, res) => {
  try {
    const d = await resolverTrack(req.id);
    res.json(d);
  } catch (e) {
    res.status(502).json({ error: "El motor no responde" });
  }
}));

export default router;
