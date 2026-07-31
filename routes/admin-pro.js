// Activar/quitar PRO manualmente (admin). Ajustado a tu models/User.js real.
// Puedes pegar estas rutas dentro de tu routes/admin.js existente,
// o montar este archivo como ruta nueva en server.js:
//   import adminProRoutes from "./routes/admin-pro.js";
//   app.use("/api/admin", adminProRoutes);

import express from "express";
import User from "../models/User.js";
import { requireAdmin } from "../middleware/auth.js";
import { ah } from "../asyncHandler.js";

const router = express.Router();

/**
 * POST /api/admin/pro
 * Header: x-admin-key: TU_ADMIN_KEY
 * Body: { email: "usuario@correo.com", meses: 1 }
 * Activa PRO por N meses (default 1). Pone isPro=true y la vigencia.
 */
router.post("/pro", requireAdmin, ah(async (req, res) => {
  const { email, meses = 1 } = req.body;
  if (!email) return res.status(400).json({ error: "Falta email" });

  const user = await User.findOne({ email: email.toLowerCase().trim() });
  if (!user) return res.status(404).json({ error: "Usuario no encontrado" });

  const hasta = new Date();
  hasta.setMonth(hasta.getMonth() + Number(meses));
  user.isPro = true;
  user.subscriptionExpiresAt = hasta;
  await user.save();

  res.json({ ok: true, email: user.email, pro: true, vence: user.subscriptionExpiresAt });
}));

/**
 * POST /api/admin/quitar-pro
 * Header: x-admin-key: TU_ADMIN_KEY
 * Body: { email }
 */
router.post("/quitar-pro", requireAdmin, ah(async (req, res) => {
  const { email } = req.body;
  const user = await User.findOne({ email: (email || "").toLowerCase().trim() });
  if (!user) return res.status(404).json({ error: "Usuario no encontrado" });
  user.isPro = false;
  user.subscriptionExpiresAt = null;
  await user.save();
  res.json({ ok: true, email: user.email, pro: false });
}));

/**
 * GET /api/admin/usuario?email=...
 * Header: x-admin-key: TU_ADMIN_KEY
 * Para checar el estado PRO de alguien.
 */
router.get("/usuario", requireAdmin, ah(async (req, res) => {
  const user = await User.findOne({ email: (req.query.email || "").toLowerCase().trim() });
  if (!user) return res.status(404).json({ error: "Usuario no encontrado" });
  res.json({
    email: user.email,
    isPro: user.isPro,
    vence: user.subscriptionExpiresAt,
    activo: user.isProActive(),
  });
}));

export default router;
