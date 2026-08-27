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

/**
 * GET /api/admin/metricas
 * Header: x-admin-key: TU_ADMIN_KEY
 * Panel de negocio: usuarios, PRO, ingresos estimados, conversión.
 * Los números REALES para tus reportes mensuales y el inversionista.
 */
router.get("/metricas", requireAdmin, ah(async (req, res) => {
  const PRECIO = 299; // precio mensual PRO en MXN
  const ahora = new Date();
  const hoy0 = new Date(ahora); hoy0.setHours(0, 0, 0, 0);
  const hace7 = new Date(ahora.getTime() - 7 * 864e5);
  const hace30 = new Date(ahora.getTime() - 30 * 864e5);

  const [total, proActivos, nuevosHoy, nuevos7, nuevos30, proMes] = await Promise.all([
    User.countDocuments({}),
    User.countDocuments({ isPro: true, subscriptionExpiresAt: { $gt: ahora } }),
    User.countDocuments({ createdAt: { $gte: hoy0 } }),
    User.countDocuments({ createdAt: { $gte: hace7 } }),
    User.countDocuments({ createdAt: { $gte: hace30 } }),
    User.countDocuments({ isPro: true, subscriptionExpiresAt: { $gt: ahora }, createdAt: { $gte: hace30 } }),
  ]);

  const conversion = total > 0 ? proActivos / total : 0;
  const mrr = proActivos * PRECIO;           // ingreso recurrente mensual estimado
  const arr = mrr * 12;                       // anualizado

  // serie de registros por día (últimos 14 días) para una mini gráfica
  const serie = [];
  for (let i = 13; i >= 0; i--) {
    const d0 = new Date(ahora.getTime() - i * 864e5); d0.setHours(0, 0, 0, 0);
    const d1 = new Date(d0.getTime() + 864e5);
    const n = await User.countDocuments({ createdAt: { $gte: d0, $lt: d1 } });
    serie.push({ dia: d0.toISOString().slice(5, 10), registros: n });
  }

  res.json({
    usuarios: { total, nuevosHoy, nuevos7, nuevos30 },
    pro: { activos: proActivos, nuevos30: proMes },
    conversion: Math.round(conversion * 1000) / 10, // % con 1 decimal
    ingresos: { precio: PRECIO, mrr, arr, moneda: "MXN" },
    serieRegistros: serie,
    generado: ahora.toISOString(),
  });
}));

export default router;
