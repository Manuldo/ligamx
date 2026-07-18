import express from "express";
import Pick from "../models/Pick.js";
import Vote from "../models/Vote.js";
import { requireAuth, requirePro } from "../middleware/auth.js";
import { ah } from "../asyncHandler.js";

const router = express.Router();

function hoy() {
  return new Date().toISOString().slice(0, 10);
}

// --- PICKS PUBLICOS: cualquiera los ve (funnel) ---
router.get("/public", ah(async (req, res) => {
  const fecha = req.query.fecha || hoy();
  const picks = await Pick.find({ fecha, tier: "public", activo: true })
    .sort({ edge: -1 })
    .lean();
  res.json(picks);
}));

// --- PICKS PRO: SOLO se envian si el usuario es PRO activo ---
// requirePro corta antes del query. Ni un byte del pick PRO
// sale del server para un no-PRO. Este es el candado real.
router.get("/pro", requireAuth, requirePro, ah(async (req, res) => {
  const fecha = req.query.fecha || hoy();
  const picks = await Pick.find({ fecha, tier: "pro", activo: true })
    .sort({ edge: -1 })
    .lean();
  res.json(picks);
}));

// --- VOTAR: un usuario, un voto por pick ---
router.post("/:id/vote", requireAuth, ah(async (req, res) => {
  const pick = await Pick.findById(req.params.id);
  if (!pick || !pick.activo) return res.status(404).json({ error: "Pick no encontrado" });

  // Si el pick es PRO, solo un PRO activo puede votarlo.
  // Validado en el server, no en el frontend.
  if (pick.tier === "pro" && !req.user.isProActive()) {
    return res.status(403).json({ error: "Requiere PRO para votar este pick" });
  }

  try {
    await Vote.create({ userId: req.user._id, pickId: pick._id });
    await Pick.updateOne({ _id: pick._id }, { $inc: { votos: 1 } });
    res.json({ ok: true, votos: pick.votos + 1 });
  } catch (err) {
    // El indice unico rechaza el voto duplicado
    if (err.code === 11000) {
      return res.status(409).json({ error: "Ya votaste este pick" });
    }
    throw err;
  }
}));

// --- PEOPLE'S CHOICE: el pick mas votado del dia ---
// Solo entre picks publicos (para no filtrar contenido PRO
// a la pantalla principal que ven todos).
router.get("/peoples-choice", ah(async (req, res) => {
  const fecha = req.query.fecha || hoy();
  const top = await Pick.findOne({ fecha, tier: "public", activo: true })
    .sort({ votos: -1, edge: -1 })
    .lean();
  res.json(top || null);
}));

export default router;
